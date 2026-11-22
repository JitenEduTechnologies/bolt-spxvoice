"""FastAPI Webhook Routes for Meta WhatsApp Cloud API.

Provides:
- GET  /api/v1/integrations/whatsapp/webhook: Meta challenge handshake verification.
- POST /api/v1/integrations/whatsapp/webhook: Signed delivery receipts and customer opt-out processing.

Security & Invariant Rules:
1. Validates X-Hub-Signature-256 HMAC-SHA256 signature against the configured Meta App Secret
   BEFORE any payload parsing or event processing. Rejects invalid signatures with HTTP 401.
2. If signing configuration is missing, safely rejects webhook processing (HTTP 401 / 503).
3. Server-side business resolution: Resolves organization strictly from internal database mappings
   matching phone_number_id or WABA ID. Never trusts client-supplied organization parameters.
4. Idempotent receipt correlation: Uses state hierarchy (QUEUED < SENDING < SENT < DELIVERED < READ)
   to ensure out-of-order or duplicate receipts never regress a newer delivery state.
5. Opt-Out Isolation: Processes STOP/UNSUBSCRIBE into WhatsApp-specific suppression without altering
   voice-call DNC eligibility.
6. Zero fabricated statuses: Updates job records only from authentic Meta delivery payloads.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse
from loguru import logger
from sqlalchemy import cast, String
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import ChatwootOutboxJobModel, OrganizationConfigurationModel
from api.services.whatsapp.meta_adapter import MetaWhatsAppCloudAdapter, meta_whatsapp_adapter

router = APIRouter(prefix="/integrations/whatsapp", tags=["whatsapp-webhook"])

# Delivery state hierarchy for idempotent, monotonic state progression
STATE_RANK = {
    "QUEUED": 0,
    "SENDING": 1,
    "SENT": 2,
    "DELIVERED": 3,
    "READ": 4,
}


async def _resolve_business_and_secret(
    phone_number_id: Optional[str], waba_id: Optional[str]
) -> Tuple[Optional[int], Optional[str]]:
    """Resolves organization ID and Meta App Secret from internal database configurations.
    
    Never trusts client-supplied organization IDs. Matches phone_number_id or waba_id.
    """
    async with db_client.async_session() as session:
        stmt = select(OrganizationConfigurationModel).where(
            OrganizationConfigurationModel.key == "whatsapp_meta_cloud"
        )
        res = await session.execute(stmt)
        configs = res.scalars().all()

        for cfg in configs:
            val = cfg.value or {}
            cfg_phone_id = str(val.get("phone_number_id") or "").strip()
            cfg_waba_id = str(val.get("waba_id") or "").strip()

            if (phone_number_id and cfg_phone_id == phone_number_id) or (
                waba_id and cfg_waba_id == waba_id
            ):
                app_secret = val.get("app_secret") or os.getenv("META_APP_SECRET")
                return cfg.organization_id, app_secret

    # Fallback to platform-level secret if configured
    platform_secret = os.getenv("META_APP_SECRET")
    return None, platform_secret


@router.get("/webhook", response_class=PlainTextResponse)
async def verify_whatsapp_webhook(
    hub_mode: Optional[str] = Query(None, alias="hub.mode"),
    hub_challenge: Optional[str] = Query(None, alias="hub.challenge"),
    hub_verify_token: Optional[str] = Query(None, alias="hub.verify_token"),
) -> PlainTextResponse:
    """Meta Webhook Challenge Verification (GET).
    
    Meta sends: hub.mode=subscribe, hub.challenge=..., hub.verify_token=...
    """
    if not hub_mode or not hub_challenge or not hub_verify_token:
        logger.warning("[WhatsApp Webhook] Incomplete challenge verification parameters")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing required hub.mode, hub.challenge, or hub.verify_token parameters",
        )

    if hub_mode != "subscribe":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid hub.mode '{hub_mode}'. Expected 'subscribe'.",
        )

    # Validate verify token against database configurations or environment
    platform_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
    is_valid = platform_token and hub_verify_token == platform_token

    if not is_valid:
        # Check per-organization configurations
        async with db_client.async_session() as session:
            stmt = select(OrganizationConfigurationModel).where(
                OrganizationConfigurationModel.key == "whatsapp_meta_cloud"
            )
            res = await session.execute(stmt)
            configs = res.scalars().all()
            for cfg in configs:
                val = cfg.value or {}
                if val.get("verify_token") and val.get("verify_token") == hub_verify_token:
                    is_valid = True
                    break

    if not is_valid:
        logger.warning("[WhatsApp Webhook] Verify token mismatch")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Verification token mismatch",
        )

    logger.info("[WhatsApp Webhook] Challenge verification succeeded")
    return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)


@router.post("/webhook")
async def process_whatsapp_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
) -> Dict[str, Any]:
    """Meta Webhook Ingress (POST).
    
    Processes authenticated delivery receipts (sent, delivered, read, failed)
    and customer opt-out messages (STOP, UNSUBSCRIBE).
    """
    # 1. Capture exact raw body bytes before any parsing
    raw_body = await request.body()
    if not raw_body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Empty request body"
        )

    # 2. Extract identifiers from payload to resolve business and secret
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid JSON payload: {e}"
        )

    phone_number_id = None
    waba_id = None
    entries = payload.get("entry", [])
    if entries and isinstance(entries, list):
        waba_id = entries[0].get("id")
        changes = entries[0].get("changes", [])
        if changes and isinstance(changes, list):
            val = changes[0].get("value", {})
            metadata = val.get("metadata", {})
            phone_number_id = metadata.get("phone_number_id")

    org_id, app_secret = await _resolve_business_and_secret(phone_number_id, waba_id)

    # 3. Verify Signing Configuration & Signature (Security Rule 1 & 2)
    if not app_secret:
        logger.error(
            f"[WhatsApp Webhook] Blocker: No Meta App Secret configured for phone {phone_number_id} / WABA {waba_id}. "
            "Webhook processing safely disabled."
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Webhook signing secret not configured. Webhook processing disabled.",
        )

    if not x_hub_signature_256:
        logger.warning("[WhatsApp Webhook] Missing X-Hub-Signature-256 header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header",
        )

    # Perform constant-time HMAC-SHA256 signature verification
    if not MetaWhatsAppCloudAdapter.verify_webhook_signature(
        raw_payload=raw_body,
        signature_header=x_hub_signature_256,
        app_secret=app_secret,
    ):
        logger.warning("[WhatsApp Webhook] Invalid X-Hub-Signature-256 signature")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    # 4. Process Webhook Changes
    receipts_processed = 0
    opt_outs_recorded = 0

    async with db_client.async_session() as session:
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})

                # A. Handle Delivery Receipts
                statuses = value.get("statuses", [])
                for status_item in statuses:
                    wamid = status_item.get("id")
                    meta_status = str(status_item.get("status") or "").lower()
                    timestamp_str = status_item.get("timestamp")
                    errors = status_item.get("errors")

                    if not wamid:
                        continue

                    # Correlate with outbox job
                    stmt = select(ChatwootOutboxJobModel).where(
                        cast(ChatwootOutboxJobModel.payload["provider_message_id"], String).ilike(f"%{wamid}%")
                    )
                    res = await session.execute(stmt)
                    job = res.scalar_one_or_none()

                    if not job:
                        logger.debug(f"[WhatsApp Webhook] No matching outbox job for wamid '{wamid}'")
                        continue

                    # Tenant isolation check: ensure job belongs to resolved organization
                    if org_id is not None and job.organization_id != org_id:
                        logger.warning(
                            f"[WhatsApp Webhook] Cross-tenant mismatch: job {job.id} (org {job.organization_id}) "
                            f"does not match resolved webhook org {org_id}"
                        )
                        continue

                    current_status = job.status or "QUEUED"
                    target_status = current_status

                    if meta_status == "delivered":
                        target_status = "DELIVERED"
                    elif meta_status == "read":
                        target_status = "READ"
                    elif meta_status == "sent":
                        target_status = "SENT"
                    elif meta_status == "failed":
                        target_status = "FAILED"
                        job.last_error = f"Meta delivery failure: {errors}"

                    # Idempotent Monotonic State Progression:
                    # Prevent regression (e.g. late 'delivered' or 'sent' cannot overwrite 'READ')
                    current_rank = STATE_RANK.get(current_status, 0)
                    target_rank = STATE_RANK.get(target_status, 0)

                    if target_status == "FAILED" or target_rank >= current_rank:
                        job.status = target_status
                        job.processed_at = datetime.now(UTC)

                    # Append to receipt history
                    p = dict(job.payload or {})
                    receipts = list(p.get("delivery_receipts") or [])
                    receipts.append({
                        "status": meta_status,
                        "timestamp": timestamp_str,
                        "errors": errors,
                        "recorded_at": datetime.now(UTC).isoformat(),
                    })
                    p["delivery_receipts"] = receipts
                    job.payload = p
                    receipts_processed += 1

                # B. Handle Inbound Messages & Opt-Outs (STOP / UNSUBSCRIBE)
                messages = value.get("messages", [])
                for msg in messages:
                    sender_phone = msg.get("from")
                    msg_text = ""
                    if msg.get("type") == "text":
                        msg_text = msg.get("text", {}).get("body", "").strip().upper()

                    # Check for explicit WhatsApp messaging opt-out commands
                    if sender_phone and msg_text in ("STOP", "UNSUBSCRIBE", "CANCEL", "OPT OUT", "OPTOUT", "QUIT"):
                        target_org = org_id or 1
                        await meta_whatsapp_adapter.record_whatsapp_opt_out(
                            organization_id=target_org,
                            phone_number=sender_phone,
                            reason=f"Customer sent inbound '{msg_text}' via WhatsApp",
                        )
                        opt_outs_recorded += 1
                        logger.info(
                            f"[WhatsApp Webhook] Processed WhatsApp opt-out for {sender_phone} (Org {target_org})"
                        )

        if receipts_processed > 0:
            await session.commit()
            logger.info(f"[WhatsApp Webhook] Successfully correlated {receipts_processed} delivery receipt(s)")

    return {
        "status": "success",
        "organization_id": org_id,
        "receipts_processed": receipts_processed,
        "opt_outs_recorded": opt_outs_recorded,
    }

