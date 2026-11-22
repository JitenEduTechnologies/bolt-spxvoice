"""Meta WhatsApp Cloud API Delivery Adapter (Disabled by Default for Real Sending).

Architectural Safeguards:
1. Strict Safety Lock: ALLOW_REAL_SENDING is hardcoded False by default. Real outbound network
   requests to Meta Graph API are strictly blocked until authorized and configured.
2. Configurable Supported Graph API Version: Supported versions (v19.0, v20.0, v21.0, v22.0) with
   configurable override per organization or environment (default: v21.0).
3. Encrypted Secret Storage: Access tokens MUST be encrypted at rest using api.utils.crypto (Fernet).
   If encryption keys are not configured, credential persistence is disabled and reported as a blocker.
4. Deterministic Transactional Templates: Only approved transactional templates are accepted.
5. Authoritative Booking Check: Confirmed booking required for appointment confirmation templates.
6. WhatsApp Opt-Out Separation: WhatsApp opt-out is maintained separately from voice-call DNC. Ordinary
   booking cancellation does NOT trigger messaging opt-out.
7. Webhook Signature Verification: Validates X-Hub-Signature-256 HMAC-SHA256 signatures.
8. Delivery Correlation: Correlates Meta wamid receipts to outbox jobs without inventing delivery status.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import ChatwootOutboxJobModel, CustomerSuppressionDNCModel
from api.services.telephony.compliance import compliance_manager, normalize_phone_number
from api.services.whatsapp.exceptions import (
    InvalidTemplateError,
    MetaAuthenticationError,
    MetaConfigurationError,
    MetaPermanentClientError,
    MetaRateLimitError,
    MetaSendingDisabledError,
    MetaTransientNetworkError,
    MetaWhatsAppError,
    TenantIsolationViolationError,
    UnconfirmedAppointmentError,
    WhatsAppOptOutError,
)
from api.services.whatsapp.template_validator import TemplatePayloadValidator
from api.utils.crypto import (
    CryptoConfigurationError,
    CryptoDecryptionError,
    decrypt_string,
    encrypt_string,
    is_encrypted,
)

# Supported Meta Graph API versions
SUPPORTED_GRAPH_VERSIONS = ("v19.0", "v20.0", "v21.0", "v22.0")
DEFAULT_GRAPH_VERSION = "v21.0"


class MetaWhatsAppCloudAdapter:
    """Isolated delivery adapter for Meta WhatsApp Cloud API."""

    def __init__(self, allow_real_sending: bool = False):
        # Strict safety lock: real sending disabled by default
        self.allow_real_sending = allow_real_sending
        self._mock_client: Optional[httpx.AsyncClient] = None
        self.mock_sent_messages: List[Dict[str, Any]] = []

    def set_mock_client(self, client: Optional[httpx.AsyncClient]) -> None:
        """Injects a mock HTTP client for isolated test runs without real network calls."""
        self._mock_client = client

    def get_supported_graph_version(self, requested_version: Optional[str] = None) -> str:
        """Validates and resolves the supported Meta Graph API version."""
        version = requested_version or os.getenv("META_GRAPH_API_VERSION", DEFAULT_GRAPH_VERSION)
        version = version.strip()
        if not version.startswith("v"):
            version = f"v{version}"
        if version not in SUPPORTED_GRAPH_VERSIONS:
            raise MetaConfigurationError(
                f"Unsupported Graph API version '{version}'. Supported versions: {SUPPORTED_GRAPH_VERSIONS}"
            )
        return version

    # -----------------------------------------------------------------------
    # Credential Management & Encryption at Rest
    # -----------------------------------------------------------------------

    def encrypt_access_token(self, token: str) -> str:
        """Encrypts a Meta System User Access Token using authenticated encryption.
        
        If encryption keys are not configured, refuses to persist plaintext tokens and raises an error.
        """
        if not token:
            raise MetaConfigurationError("Access token cannot be empty.")
        if is_encrypted(token):
            return token
        try:
            return encrypt_string(token)
        except CryptoConfigurationError as e:
            logger.error(
                "[MetaWhatsAppAdapter] Blocker: TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS is not configured. "
                "Plaintext token storage is prohibited."
            )
            raise MetaConfigurationError(
                "Credential encryption is not configured. Credential persistence disabled to prevent plaintext storage."
            ) from e

    def decrypt_access_token(self, stored_token: str) -> str:
        """Decrypts a stored Meta access token, rejecting unencrypted tokens in strict mode."""
        if not stored_token:
            raise MetaConfigurationError("No access token provided.")
        if is_encrypted(stored_token):
            try:
                return decrypt_string(stored_token)
            except CryptoDecryptionError as e:
                raise MetaAuthenticationError(f"Failed to decrypt Meta access token: {e}") from e
        
        # Unencrypted token encountered
        is_strict = os.getenv("STRICT_CREDENTIAL_ENCRYPTION", "true").lower() == "true"
        if is_strict:
            raise MetaConfigurationError(
                "Unencrypted Meta access token detected. Strict credential encryption is required."
            )
        return stored_token

    async def get_organization_meta_credentials(
        self, organization_id: int
    ) -> Dict[str, Any]:
        """Loads and validates Meta Cloud API credentials for a specific organization.
        
        Enforces tenant isolation: credentials are strictly filtered by organization_id.
        """
        config_model = await db_client.get_configuration(organization_id, "whatsapp_meta_cloud")
        if not config_model or not config_model.value:
            raise MetaConfigurationError(
                f"No Meta WhatsApp Cloud API credentials configured for organization {organization_id}."
            )

        creds = config_model.value
        phone_number_id = creds.get("phone_number_id")
        waba_id = creds.get("waba_id")
        raw_token = creds.get("access_token")
        graph_version = self.get_supported_graph_version(creds.get("graph_api_version"))

        if not phone_number_id or not raw_token:
            raise MetaConfigurationError(
                f"Incomplete Meta WhatsApp credentials for organization {organization_id}. "
                "phone_number_id and access_token are required."
            )

        decrypted_token = self.decrypt_access_token(raw_token)

        return {
            "organization_id": organization_id,
            "phone_number_id": str(phone_number_id).strip(),
            "waba_id": str(waba_id).strip() if waba_id else None,
            "access_token": decrypted_token,
            "graph_api_version": graph_version,
            "app_secret": creds.get("app_secret"),
            "verify_token": creds.get("verify_token"),
            "is_enabled": bool(creds.get("is_enabled", False)),
        }

    # -----------------------------------------------------------------------
    # WhatsApp Opt-Out Separation (Rule 9)
    # -----------------------------------------------------------------------

    async def is_whatsapp_opted_out(self, organization_id: int, phone_number: str) -> Tuple[bool, Optional[str]]:
        """Checks if recipient has explicitly opted out of WhatsApp messaging.
        
        Maintained independently of general voice-call DNC and booking cancellations.
        Ordinary appointment cancellation does NOT constitute a WhatsApp opt-out.
        """
        norm_phone = normalize_phone_number(phone_number)

        # 1. Check in-memory registry first
        with compliance_manager._lock:
            for key in [(organization_id, norm_phone), (0, norm_phone)]:
                if key in compliance_manager._dnc_registry:
                    rec = compliance_manager._dnc_registry[key]
                    if not rec.is_expired() and (
                        rec.reason == "whatsapp_opt_out"
                        or (rec.evidence and rec.evidence.get("channel") == "whatsapp")
                    ):
                        return True, f"Customer explicitly opted out of WhatsApp: {rec.reason}"

        # 2. Check PostgreSQL database
        async with db_client.async_session() as session:
            stmt = select(CustomerSuppressionDNCModel).where(
                CustomerSuppressionDNCModel.phone_number == norm_phone,
                CustomerSuppressionDNCModel.organization_id.in_([organization_id, 0]),
            )
            res = await session.execute(stmt)
            records = res.scalars().all()
            for r in records:
                if r.reason == "whatsapp_opt_out" or (r.evidence and r.evidence.get("channel") == "whatsapp"):
                    if r.expires_at and datetime.now(UTC) > r.expires_at:
                        continue
                    return True, f"Customer explicitly opted out of WhatsApp: {r.reason}"
        return False, None

    async def record_whatsapp_opt_out(
        self, organization_id: int, phone_number: str, reason: str = "customer_requested_opt_out"
    ) -> None:
        """Records an explicit WhatsApp messaging opt-out without mutating voice call records."""
        norm_phone = normalize_phone_number(phone_number)
        await compliance_manager.add_to_dnc_db(
            organization_id=organization_id,
            phone_number=norm_phone,
            reason="whatsapp_opt_out",
            source="whatsapp_webhook",
            evidence={"channel": "whatsapp", "opt_out_type": "messaging_only", "detail": reason},
        )
        logger.info(f"[MetaWhatsAppAdapter] Recorded WhatsApp messaging opt-out for {norm_phone} (Org {organization_id})")

    # -----------------------------------------------------------------------
    # Delivery Execution
    # -----------------------------------------------------------------------

    async def deliver_job(
        self,
        job: ChatwootOutboxJobModel,
        session: Any,
    ) -> Dict[str, Any]:
        """Processes and delivers an outbox job via Meta WhatsApp Cloud API.
        
        Strict Safety Invariants:
        1. Never silently falls back to Chatwoot or mock provider.
        2. Raises explicit Meta error or records failure directly.
        3. Real outbound sending is blocked unless allow_real_sending is explicitly enabled.
        """
        org_id = job.organization_id
        rec_phone = job.recipient_phone
        msg_type = job.message_type
        payload = copy.deepcopy(job.payload) if job.payload else {}

        # 1. Check WhatsApp-specific opt-out
        opted_out, opt_reason = await self.is_whatsapp_opted_out(org_id, rec_phone)
        if opted_out:
            raise WhatsAppOptOutError(f"Delivery blocked: recipient {rec_phone} has opted out of WhatsApp ({opt_reason}).")

        # 2. Load tenant credentials (enforcing tenant isolation)
        creds = await self.get_organization_meta_credentials(org_id)
        phone_number_id = creds["phone_number_id"]
        graph_version = creds["graph_api_version"]
        access_token = creds["access_token"]

        # 3. Deterministically validate template payload
        template_params = payload.get("parameters") or payload.get("template_data") or payload
        meta_body = TemplatePayloadValidator.validate_and_format(
            organization_id=org_id,
            recipient_phone=rec_phone,
            template_name=msg_type,
            parameters=template_params,
            language_code=payload.get("language_code", "en"),
        )

        # 4. Check Safety Lock: real sending disabled by default
        is_real_sending_env = os.getenv("ENABLE_META_WHATSAPP_REAL_SENDING", "false").lower() == "true"
        if not self.allow_real_sending and not is_real_sending_env and self._mock_client is None:
            # Safety lock active: real network calls to Meta are strictly blocked
            raise MetaSendingDisabledError(
                "Real Meta WhatsApp Cloud API sending is disabled by default. "
                "Enable ALLOW_REAL_SENDING or provide a mock client for testing."
            )

        # 5. Execute HTTP Request
        url = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        # Use injected mock client if provided, else use real client only if real sending enabled
        client = self._mock_client
        created_local_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=8.0)
            created_local_client = True

        try:
            resp = await client.post(url, json=meta_body, headers=headers)
        except httpx.TimeoutException as e:
            logger.warning(f"[MetaWhatsAppAdapter] Timeout connecting to Meta Graph API for job {job.id}: {e}")
            raise MetaTransientNetworkError(f"Timeout connecting to Meta Graph API: {e}") from e
        except httpx.NetworkError as e:
            logger.warning(f"[MetaWhatsAppAdapter] Network error connecting to Meta Graph API for job {job.id}: {e}")
            raise MetaTransientNetworkError(f"Transient network error connecting to Meta Graph API: {e}") from e
        finally:
            if created_local_client:
                await client.aclose()

        # 6. Parse and Classify Meta API Response
        return self._handle_meta_response(resp, job, meta_body)

    def _handle_meta_response(
        self, resp: httpx.Response, job: ChatwootOutboxJobModel, meta_body: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Parses Meta Cloud API response and classifies into success, retryable, or non-retryable errors."""
        status_code = resp.status_code
        try:
            body = resp.json()
        except Exception:
            body = {"raw_text": resp.text}

        if status_code == 200:
            # Meta returns: {"messaging_product": "whatsapp", "contacts": [...], "messages": [{"id": "wamid.HBg..."}]}
            messages = body.get("messages", [])
            wamid = messages[0].get("id") if messages else f"wamid.unknown_{uuid.uuid4().hex[:8]}"
            logger.info(f"[MetaWhatsAppAdapter] Job {job.id} dispatched via Meta Cloud API. WAMID: {wamid}")

            # Store provider message ID in job payload for delivery receipt correlation
            updated_payload = dict(job.payload or {})
            updated_payload["provider"] = "meta_cloud"
            updated_payload["provider_message_id"] = wamid
            updated_payload["meta_response"] = body
            job.payload = updated_payload
            job.status = "SENT"
            job.processed_at = datetime.now(UTC)
            job.last_error = None

            delivered_record = {
                "job_id": job.id,
                "status": "SENT",
                "provider": "meta_cloud",
                "provider_message_id": wamid,
                "delivered_at": datetime.now(UTC).isoformat(),
            }
            self.mock_sent_messages.append(delivered_record)
            return delivered_record

        # Error Classification
        error_info = body.get("error", {})
        err_msg = error_info.get("message", resp.text)
        err_code = error_info.get("code")
        err_subcode = error_info.get("error_subcode")

        logger.warning(
            f"[MetaWhatsAppAdapter] Meta API error for job {job.id} (HTTP {status_code}, code={err_code}, subcode={err_subcode}): {err_msg}"
        )

        if status_code in (401, 403):
            raise MetaAuthenticationError(f"Meta authentication error (HTTP {status_code}): {err_msg}")
        elif status_code == 429 or err_code == 80007:
            # Rate limit or tier restriction (retryable)
            raise MetaRateLimitError(f"Meta messaging rate limit / tier restriction: {err_msg}")
        elif status_code in (500, 502, 503, 504):
            # Transient server error (retryable)
            raise MetaTransientNetworkError(f"Meta Cloud API server error (HTTP {status_code}): {err_msg}")
        elif status_code == 400:
            if err_subcode == 132000 or "template" in err_msg.lower():
                raise InvalidTemplateError(f"Meta template error (subcode {err_subcode}): {err_msg}")
            raise MetaPermanentClientError(f"Meta client error (HTTP 400, code {err_code}): {err_msg}")
        else:
            raise MetaPermanentClientError(f"Meta request failed with HTTP {status_code}: {err_msg}")

    # -----------------------------------------------------------------------
    # Webhook Verification & Delivery Correlation (Rule 10)
    # -----------------------------------------------------------------------

    @classmethod
    def verify_webhook_signature(
        cls, raw_payload: bytes, signature_header: Optional[str], app_secret: str
    ) -> bool:
        """Validates Meta's X-Hub-Signature-256 header using constant-time HMAC-SHA256 comparison."""
        if not signature_header or not app_secret:
            return False
        if not signature_header.startswith("sha256="):
            return False
        expected_hash = signature_header[7:].strip()
        computed_hash = hmac.new(
            app_secret.encode("utf-8"), raw_payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_hash, computed_hash)

    @classmethod
    async def correlate_delivery_receipt(
        cls, webhook_payload: Dict[str, Any], session: Any
    ) -> List[Dict[str, Any]]:
        """Correlates incoming Meta delivery status updates to outbox jobs without inventing delivery status.
        
        Extracts entry[].changes[].value.statuses[]:
        - status: 'sent', 'delivered', 'read', 'failed'
        - updates ChatwootOutboxJobModel where payload->>'provider_message_id' matches wamid.
        """
        results: List[Dict[str, Any]] = []
        entries = webhook_payload.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                statuses = value.get("statuses", [])
                for status_item in statuses:
                    wamid = status_item.get("id")
                    meta_status = status_item.get("status")  # 'sent', 'delivered', 'read', 'failed'
                    timestamp_str = status_item.get("timestamp")
                    errors = status_item.get("errors")

                    if not wamid:
                        continue

                    # Query outbox job with matching provider_message_id
                    from sqlalchemy import String, cast
                    stmt = select(ChatwootOutboxJobModel).where(
                        cast(ChatwootOutboxJobModel.payload["provider_message_id"], String).ilike(f'%{wamid}%')
                    )
                    res = await session.execute(stmt)
                    job = res.scalar_one_or_none()

                    if not job:
                        logger.debug(f"[MetaWhatsAppAdapter] No matching job for wamid '{wamid}'")
                        continue

                    # Update status based on authentic Meta receipt (no invented status)
                    if meta_status == "delivered":
                        job.status = "DELIVERED"
                    elif meta_status == "read":
                        job.status = "READ"
                    elif meta_status == "failed":
                        job.status = "FAILED"
                        job.last_error = f"Meta delivery failed: {errors}"

                    # Update payload with receipt history
                    p = dict(job.payload or {})
                    receipts = p.get("delivery_receipts", [])
                    receipts.append({
                        "status": meta_status,
                        "timestamp": timestamp_str,
                        "errors": errors,
                        "recorded_at": datetime.now(UTC).isoformat(),
                    })
                    p["delivery_receipts"] = receipts
                    job.payload = p
                    job.processed_at = datetime.now(UTC)

                    results.append({
                        "job_id": job.id,
                        "wamid": wamid,
                        "new_status": job.status,
                    })

        if results:
            await session.commit()
            logger.info(f"[MetaWhatsAppAdapter] Correlated {len(results)} Meta delivery receipt(s)")

        return results


# Global singleton instance (real sending disabled by default)
meta_whatsapp_adapter = MetaWhatsAppCloudAdapter(allow_real_sending=False)
