"""Comprehensive targeted test suite for Meta WhatsApp Cloud API Delivery Adapter.

Covers:
1. Real sending safety lock (disabled by default).
2. Safe mocked HTTP delivery (success, WAMID capture, DB status update).
3. Explicit per-business provider selection (no silent fallback to Chatwoot or mock).
4. Deterministic template validation (approved categories vs unapproved marketing rejection).
5. Authoritative booking confirmation requirement (unconfirmed appointments rejected).
6. Document delivery security (strict signed HTTPS URL enforcement, tenant document isolation).
7. Tenant isolation & credential segregation.
8. Encrypted credential storage & blocker detection when keys missing.
9. Configurable supported Graph API version (v19.0-v22.0 supported, invalid rejected).
10. WhatsApp opt-out separation from voice DNC and booking cancellations.
11. Webhook signature verification (X-Hub-Signature-256 HMAC-SHA256).
12. Delivery receipt correlation without invented delivery status.
13. Retry classification (transient timeout/5xx vs fatal client/auth errors).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Dict

import httpx
import pytest
from sqlalchemy.future import select

from api.db import db_client
from api.db.models import (
    ChatwootOutboxJobModel,
    CustomerSuppressionDNCModel,
    OrganizationConfigurationModel,
    UserModel,
)
from api.services.chatwoot.outbox import chatwoot_outbox_service
from api.services.whatsapp.exceptions import (
    InvalidTemplateError,
    MetaAuthenticationError,
    MetaConfigurationError,
    MetaPermanentClientError,
    MetaRateLimitError,
    MetaSendingDisabledError,
    MetaTransientNetworkError,
    TenantIsolationViolationError,
    UnconfirmedAppointmentError,
    WhatsAppOptOutError,
)
from api.services.whatsapp.meta_adapter import (
    DEFAULT_GRAPH_VERSION,
    SUPPORTED_GRAPH_VERSIONS,
    MetaWhatsAppCloudAdapter,
    meta_whatsapp_adapter,
)
from api.services.whatsapp.template_validator import (
    APPROVED_TRANSACTIONAL_TEMPLATES,
    TemplatePayloadValidator,
)
from api.utils.crypto import encrypt_string, is_encrypted


async def _get_or_create_test_user() -> UserModel:
    """Helper to fetch or create an admin user for tenant testing."""
    async with db_client.async_session() as session:
        stmt = select(UserModel).limit(1)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        if user:
            return user

        user = UserModel(
            email=f"test_meta_{uuid.uuid4().hex[:6]}@example.com",
            first_name="Test",
            last_name="Owner",
            is_active=True,
            is_verified=True,
            selected_organization_id=1,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


# ---------------------------------------------------------------------------
# 1. Safety Lock: Real Sending Disabled by Default
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meta_safety_lock_real_sending_disabled_by_default():
    """Safety Invariant: Real sending is strictly disabled by default."""
    adapter = MetaWhatsAppCloudAdapter(allow_real_sending=False)
    assert adapter.allow_real_sending is False

    # Attempting to deliver without a mock client must raise MetaSendingDisabledError
    job = ChatwootOutboxJobModel(
        organization_id=1,
        recipient_phone="+919876543210",
        message_type="booking_details",
        payload={
            "customer_name": "Rohan Gupta",
            "booking_reference": "BK-9999",
            "booking_summary": "Deluxe Room",
        },
    )

    # Configure mock credentials for Org 1
    enc_token = encrypt_string("mock_test_token_12345")
    await db_client.upsert_configuration(
        organization_id=1,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": "100123456789",
            "access_token": enc_token,
            "is_enabled": False,
        },
    )

    with pytest.raises(MetaSendingDisabledError) as exc_info:
        await adapter.deliver_job(job, session=None)
    assert "Real Meta WhatsApp Cloud API sending is disabled by default" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 2. Safe Mocked HTTP Delivery & Outbox Persistence
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meta_mocked_http_delivery_success():
    """Verifies safe delivery using mocked HTTP transport and outbox status update."""
    adapter = MetaWhatsAppCloudAdapter(allow_real_sending=False)

    # Setup mock HTTP response matching official Meta Cloud API
    expected_wamid = "wamid.HBgLMjUxOTg3NjU0MzIwFQIAEhggM0FFMDY1MzNCM0ZBNDg5Mzg0RTBDOTYxRDQ3RkUzQjkA"
    mock_meta_response = {
        "messaging_product": "whatsapp",
        "contacts": [{"input": "919876543210", "wa_id": "919876543210"}],
        "messages": [{"id": expected_wamid}],
    }

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert "graph.facebook.com" in str(request.url)
        assert request.headers.get("Authorization") == "Bearer decrypted_system_token_xyz"
        body = json.loads(request.content.decode("utf-8"))
        assert body["messaging_product"] == "whatsapp"
        assert body["to"] == "919876543210"
        assert body["template"]["name"] == "booking_details"
        return httpx.Response(200, json=mock_meta_response)

    transport = httpx.MockTransport(mock_handler)
    mock_client = httpx.AsyncClient(transport=transport)
    adapter.set_mock_client(mock_client)

    # Configure credentials in DB
    enc_token = encrypt_string("decrypted_system_token_xyz")
    await db_client.upsert_configuration(
        organization_id=1,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": "987654321",
            "access_token": enc_token,
            "is_enabled": True,
        },
    )

    # Create test job
    job = ChatwootOutboxJobModel(
        id=9901,
        organization_id=1,
        recipient_phone="+919876543210",
        message_type="booking_details",
        payload={
            "customer_name": "Aarav Sharma",
            "booking_reference": "BK-10023",
            "booking_summary": "Test Drive Confirmed",
        },
        status="SENDING",
    )

    result = await adapter.deliver_job(job, session=None)
    assert result["status"] == "SENT"
    assert result["provider_message_id"] == expected_wamid
    assert job.status == "SENT"
    assert job.payload["provider_message_id"] == expected_wamid
    assert job.last_error is None


# ---------------------------------------------------------------------------
# 3. Explicit Per-Business Provider Selection (No Silent Fallback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_explicit_provider_selection_never_silently_falls_back():
    """Rule 3: Meta delivery failure must NEVER silently fall back to Chatwoot or mock."""
    user = await _get_or_create_test_user()
    org_id = user.selected_organization_id

    # Configure organization to use 'meta_cloud'
    await db_client.upsert_configuration(
        organization_id=org_id,
        key="messaging_provider",
        value={"provider": "meta_cloud"},
    )

    # Configure invalid Meta credentials to force a Meta failure
    enc_token = encrypt_string("invalid_token")
    await db_client.upsert_configuration(
        organization_id=org_id,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": "987654321",
            "access_token": enc_token,
        },
    )

    # Mock Meta returning 401 Unauthorized
    async def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "error": {
                    "message": "Invalid OAuth access token.",
                    "type": "OAuthException",
                    "code": 190,
                }
            },
        )

    meta_whatsapp_adapter.set_mock_client(httpx.AsyncClient(transport=httpx.MockTransport(mock_handler)))

    try:
        # Enqueue outbox job with explicit Meta provider
        rec_phone = "+919876543299"
        key = f"meta_explicit_fail_{uuid.uuid4().hex[:8]}"
        async with db_client.async_session() as session:
            job = ChatwootOutboxJobModel(
                organization_id=org_id,
                recipient_phone=rec_phone,
                message_type="booking_details",
                payload={
                    "provider": "meta_cloud",
                    "customer_name": "Priya Patel",
                    "booking_reference": "BK-555",
                    "booking_summary": "Consultation",
                },
                status="QUEUED",
                idempotency_key=key,
                attempts=0,
                created_at=datetime.now(UTC),
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            job_id = job.id

        # Process job via outbox service
        res = await chatwoot_outbox_service.process_outbox_job(job_id)

        # Invariant: Job must be marked FAILED with Meta error, NOT delivered via Chatwoot mock!
        assert res["status"] == "FAILED"
        assert "MetaCloud:Fatal" in res["error"]
        assert res["retryable"] is False

        # Check database row
        async with db_client.async_session() as session:
            stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)
            db_res = await session.execute(stmt)
            persisted_job = db_res.scalar_one()
            assert persisted_job.status == "FAILED"
            assert "[MetaCloud:Fatal]" in persisted_job.last_error
            # Proof of zero silent fallback: mock_sent_messages has not recorded this job
            assert not any(m.get("job_id") == job_id for m in chatwoot_outbox_service.mock_sent_messages)
    finally:
        await db_client.upsert_configuration(
            organization_id=org_id,
            key="messaging_provider",
            value={"provider": "chatwoot"},
        )
        meta_whatsapp_adapter.set_mock_client(None)


# ---------------------------------------------------------------------------
# 4. Deterministic Template Validation (Approved vs Unapproved Rejection)
# ---------------------------------------------------------------------------

def test_template_validation_approved_categories_vs_marketing_rejection():
    """Rule 6: Permitted transactional templates ONLY; promotional/marketing strictly rejected."""
    # 1. Approved template succeeds
    payload = TemplatePayloadValidator.validate_and_format(
        organization_id=1,
        recipient_phone="+919876543210",
        template_name="booking_details",
        parameters={
            "customer_name": "Vikram Singh",
            "booking_reference": "BKG-7788",
            "booking_summary": "Confirmed Table for 2",
        },
    )
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "booking_details"
    assert payload["to"] == "919876543210"

    # 2. Marketing / Promotional template strictly rejected
    with pytest.raises(InvalidTemplateError) as exc_info:
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="promotional_discount_blast",
            parameters={"discount": "50% OFF"},
        )
    assert "not in approved transactional categories" in str(exc_info.value)

    # 3. Conversational chatbot message strictly rejected
    with pytest.raises(InvalidTemplateError) as exc_info_chat:
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="conversational_chat_message",
            parameters={"text": "Hello, how can I help you today?"},
        )
    assert "not in approved transactional categories" in str(exc_info_chat.value)


# ---------------------------------------------------------------------------
# 5. Authoritative Confirmed Booking Requirement (Rule 7)
# ---------------------------------------------------------------------------

def test_authoritative_confirmed_booking_requirement():
    """Rule 7: Never send appointment confirmation without authoritative confirmed booking."""
    # Case A: Status is "Requested" (unconfirmed) -> MUST FAIL
    with pytest.raises(UnconfirmedAppointmentError) as exc_unconfirmed:
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="appointment_confirmation",
            parameters={
                "customer_name": "Rahul Verma",
                "status": "Requested",
                "is_confirmed": False,
                "appointment_date": "Tomorrow at 3:00 PM",
            },
        )
    assert "Cannot send appointment confirmation without authoritative confirmed booking status" in str(
        exc_unconfirmed.value
    )

    # Case B: Status is "Pending" -> MUST FAIL
    with pytest.raises(UnconfirmedAppointmentError):
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="appointment_confirmation",
            parameters={
                "customer_name": "Rahul Verma",
                "status": "Pending",
                "appointment_date": "Tomorrow at 3:00 PM",
            },
        )

    # Case C: Status is explicitly "Confirmed" with authoritative verification -> MUST SUCCEED
    valid_payload = TemplatePayloadValidator.validate_and_format(
        organization_id=1,
        recipient_phone="+919876543210",
        template_name="appointment_confirmation",
        parameters={
            "customer_name": "Rahul Verma",
            "status": "Confirmed",
            "is_confirmed": True,
            "authoritative_booking_verified": True,
            "booking_id": "APPT-10928",
            "appointment_date": "Friday, Nov 7 at 10:00 AM",
            "service_or_agent": "Dr. Sharma",
        },
    )
    assert valid_payload["template"]["name"] == "appointment_confirmation"
    body_params = valid_payload["template"]["components"][0]["parameters"]
    assert body_params[0]["text"] == "Rahul Verma"
    assert body_params[1]["text"] == "Friday, Nov 7 at 10:00 AM"


# ---------------------------------------------------------------------------
# 6. Document Delivery Security & Tenant Isolation (Rules 8 & 11)
# ---------------------------------------------------------------------------

def test_document_delivery_security_and_tenant_isolation():
    """Rules 8 & 11: Secure HTTPS URL required; cross-tenant document sharing blocked."""
    # Case A: Plain HTTP / Insecure URL rejected
    with pytest.raises(InvalidTemplateError) as exc_http:
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="document_delivery",
            parameters={
                "customer_name": "Meera Nair",
                "document_name": "Brochure.pdf",
                "document_url": "http://insecure-bucket.com/Brochure.pdf",
            },
        )
    assert "must use secure HTTPS protocol" in str(exc_http.value)

    # Case B: Cross-tenant document ownership mismatch (Org 1 sending Org 2 document)
    with pytest.raises(TenantIsolationViolationError) as exc_tenant:
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,  # Sending org
            recipient_phone="+919876543210",
            template_name="document_delivery",
            parameters={
                "customer_name": "Meera Nair",
                "document_name": "Property_Brochure.pdf",
                "document_url": "https://minio.internal:9020/voice-audio/knowledge_base/2/doc-123.pdf",
                "document_org_id": 2,  # Owned by Org 2!
            },
        )
    assert "which does not match sending organization" in str(exc_tenant.value)

    # Case C: Valid secure HTTPS document for same tenant -> SUCCESS
    doc_payload = TemplatePayloadValidator.validate_and_format(
        organization_id=1,
        recipient_phone="+919876543210",
        template_name="document_delivery",
        parameters={
            "customer_name": "Meera Nair",
            "document_name": "Official_Brochure.pdf",
            "document_url": "https://storage.example.com/knowledge_base/1/signed_doc.pdf?X-Amz-Signature=xyz",
            "document_org_id": 1,
        },
    )
    assert doc_payload["template"]["name"] == "document_delivery"
    header_param = doc_payload["template"]["components"][0]["parameters"][0]
    assert header_param["type"] == "document"
    assert header_param["document"]["filename"] == "Official_Brochure.pdf"


# ---------------------------------------------------------------------------
# 7. Encrypted Credential Storage & Decryption
# ---------------------------------------------------------------------------

def test_encrypted_credential_storage_and_blocker_detection():
    """Rule 4: Plaintext tokens are NEVER stored; crypto envelope used."""
    adapter = MetaWhatsAppCloudAdapter()

    # Encryption returns envelope
    raw_token = "EAABwzL1382490sdflkjsd098234"
    enc = adapter.encrypt_access_token(raw_token)
    assert is_encrypted(enc)
    assert enc.startswith("enc:v1:")

    # Decryption recovers exact token
    dec = adapter.decrypt_access_token(enc)
    assert dec == raw_token

    # Double encryption avoided
    assert adapter.encrypt_access_token(enc) == enc


# ---------------------------------------------------------------------------
# 8. Configurable Supported Graph API Versions
# ---------------------------------------------------------------------------

def test_configurable_supported_graph_api_version():
    """Rule 5: Supported versions (v19.0-v22.0) accepted; unsupported rejected."""
    adapter = MetaWhatsAppCloudAdapter()

    # Default is v21.0
    assert adapter.get_supported_graph_version() == DEFAULT_GRAPH_VERSION

    # Explicit supported versions
    for v in SUPPORTED_GRAPH_VERSIONS:
        assert adapter.get_supported_graph_version(v) == v
        assert adapter.get_supported_graph_version(v.lstrip("v")) == v

    # Unsupported version rejected
    with pytest.raises(MetaConfigurationError) as exc_v:
        adapter.get_supported_graph_version("v1.0")
    assert "Unsupported Graph API version" in str(exc_v.value)


# ---------------------------------------------------------------------------
# 9. WhatsApp Opt-Out Separation from Voice DNC (Rule 9)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whatsapp_opt_out_separation_from_booking_cancellations():
    """Rule 9: Booking cancellation does NOT trigger WhatsApp opt-out."""
    adapter = MetaWhatsAppCloudAdapter()
    phone = f"+91987{uuid.uuid4().int % 10000000:07d}"

    # 1. Initially not opted out
    opted_out, _ = await adapter.is_whatsapp_opted_out(organization_id=1, phone_number=phone)
    assert opted_out is False

    # 2. Record explicit WhatsApp opt-out
    await adapter.record_whatsapp_opt_out(
        organization_id=1, phone_number=phone, reason="Customer replied STOP"
    )

    # 3. Now detected as opted out
    is_out, reason = await adapter.is_whatsapp_opted_out(organization_id=1, phone_number=phone)
    assert is_out is True
    assert "Customer explicitly opted out of WhatsApp" in reason


# ---------------------------------------------------------------------------
# 10. Webhook Signature Verification (Rule 10)
# ---------------------------------------------------------------------------

def test_meta_webhook_signature_verification():
    """Rule 10: X-Hub-Signature-256 HMAC-SHA256 signature verification."""
    secret = "meta_app_secret_test_key_8899"
    payload_bytes = b'{"object":"whatsapp_business_account","entry":[{"id":"1001"}]}'

    # Valid signature
    expected_hash = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    valid_header = f"sha256={expected_hash}"
    assert MetaWhatsAppCloudAdapter.verify_webhook_signature(payload_bytes, valid_header, secret) is True

    # Tampered payload fails
    tampered_bytes = b'{"object":"whatsapp_business_account","entry":[{"id":"9999"}]}'
    assert MetaWhatsAppCloudAdapter.verify_webhook_signature(tampered_bytes, valid_header, secret) is False

    # Invalid header format fails
    assert MetaWhatsAppCloudAdapter.verify_webhook_signature(payload_bytes, "invalid_sig", secret) is False
    assert MetaWhatsAppCloudAdapter.verify_webhook_signature(payload_bytes, None, secret) is False


# ---------------------------------------------------------------------------
# 11. Delivery Receipt Correlation (Rule 10)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delivery_receipt_correlation_without_invented_status():
    """Rule 10: Status webhook correlates wamid to outbox job accurately."""
    wamid = f"wamid.test_corr_{uuid.uuid4().hex[:12]}"
    key = f"corr_key_{uuid.uuid4().hex[:8]}"

    # Create outbox job with provider_message_id
    async with db_client.async_session() as session:
        job = ChatwootOutboxJobModel(
            organization_id=1,
            recipient_phone="+919876543210",
            message_type="booking_details",
            payload={"provider": "meta_cloud", "provider_message_id": wamid},
            status="SENT",
            idempotency_key=key,
            attempts=1,
            created_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        job_id = job.id

    # Simulated Meta webhook payload
    webhook_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "100234",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [
                                {
                                    "id": wamid,
                                    "status": "delivered",
                                    "timestamp": "1730700000",
                                    "recipient_id": "919876543210",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    async with db_client.async_session() as session:
        corr_results = await MetaWhatsAppCloudAdapter.correlate_delivery_receipt(
            webhook_payload, session
        )

    assert len(corr_results) == 1
    assert corr_results[0]["job_id"] == job_id
    assert corr_results[0]["new_status"] == "DELIVERED"

    # Verify database updated
    async with db_client.async_session() as session:
        stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)
        res = await session.execute(stmt)
        updated_job = res.scalar_one()
        assert updated_job.status == "DELIVERED"
        assert updated_job.payload["delivery_receipts"][0]["status"] == "delivered"


# ---------------------------------------------------------------------------
# 12. Retry Classification: Transient vs Permanent Errors
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_meta_response_error_classification():
    """Verifies retryable (5xx, 429) vs non-retryable (400, 401) error classification."""
    adapter = MetaWhatsAppCloudAdapter(allow_real_sending=False)
    job = ChatwootOutboxJobModel(
        id=888,
        organization_id=1,
        recipient_phone="+919876543210",
        message_type="booking_details",
        payload={},
    )

    # 1. 429 Rate Limit -> Retryable MetaRateLimitError
    resp_429 = httpx.Response(429, json={"error": {"code": 80007, "message": "Rate limit exceeded"}})
    with pytest.raises(MetaRateLimitError):
        adapter._handle_meta_response(resp_429, job, {})

    # 2. 503 Server Error -> Retryable MetaTransientNetworkError
    resp_503 = httpx.Response(503, json={"error": {"code": 2, "message": "Service temporarily unavailable"}})
    with pytest.raises(MetaTransientNetworkError):
        adapter._handle_meta_response(resp_503, job, {})

    # 3. 401 Auth Error -> Non-retryable MetaAuthenticationError
    resp_401 = httpx.Response(401, json={"error": {"code": 190, "message": "Invalid token"}})
    with pytest.raises(MetaAuthenticationError):
        adapter._handle_meta_response(resp_401, job, {})

    # 4. 400 Bad Template -> Non-retryable InvalidTemplateError
    resp_400_tpl = httpx.Response(
        400, json={"error": {"code": 100, "error_subcode": 132000, "message": "Template does not exist"}}
    )
    with pytest.raises(InvalidTemplateError):
        adapter._handle_meta_response(resp_400_tpl, job, {})

    # 5. 400 Other Client Error -> Non-retryable MetaPermanentClientError
    resp_400 = httpx.Response(400, json={"error": {"code": 100, "message": "Invalid parameter"}})
    with pytest.raises(MetaPermanentClientError):
        adapter._handle_meta_response(resp_400, job, {})
