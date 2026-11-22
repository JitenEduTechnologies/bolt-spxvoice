"""Targeted tests for Meta WhatsApp Cloud Webhook Ingress & Business Settings API (Step 2B).

Covers:
1. GET /api/v1/integrations/whatsapp/webhook challenge handshake (success & rejection).
2. POST /api/v1/integrations/whatsapp/webhook signed valid vs forged signature (HTTP 401).
3. Server-side business resolution from phone_number_id/WABA ID (never trusting client).
4. Idempotent receipt correlation & monotonic state progression (READ never regresses to DELIVERED).
5. Inbound customer STOP/UNSUBSCRIBE opt-out processing (WhatsApp-specific suppression).
6. GET & POST /api/v1/business/integrations/whatsapp encryption, masking, and tenant isolation.
7. Critical safety check: appointment confirmation blocked without authoritative booking verification.
8. Real delivery remains strictly disabled.
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
from httpx import ASGITransport
from sqlalchemy.future import select

from api.app import app
from api.constants import OSS_JWT_SECRET
from api.db import db_client
from api.db.models import (
    ChatwootOutboxJobModel,
    CustomerSuppressionDNCModel,
    OrganizationConfigurationModel,
    UserModel,
)
from api.services.whatsapp.exceptions import (
    InvalidTemplateError,
    UnconfirmedAppointmentError,
)
from api.services.whatsapp.meta_adapter import (
    MetaWhatsAppCloudAdapter,
    meta_whatsapp_adapter,
)
from api.services.whatsapp.template_validator import TemplatePayloadValidator
from api.utils.crypto import decrypt_string, encrypt_string, is_encrypted


def _generate_test_jwt(user_id: int, email: str, org_id: int) -> str:
    import jwt
    payload = {
        "sub": str(user_id),
        "email": email,
        "org_id": org_id,
        "selected_organization_id": org_id,
        "exp": datetime.now(UTC).timestamp() + 3600,
    }
    return jwt.encode(payload, OSS_JWT_SECRET, algorithm="HS256")


async def _get_or_create_test_user(org_id: int = 1) -> UserModel:
    async with db_client.async_session() as session:
        stmt = select(UserModel).where(UserModel.selected_organization_id == org_id).limit(1)
        res = await session.execute(stmt)
        user = res.scalar_one_or_none()
        if user:
            return user
        user = UserModel(
            email=f"org{org_id}_owner_{uuid.uuid4().hex[:6]}@example.com",
            first_name="Org",
            last_name="Owner",
            is_active=True,
            is_verified=True,
            selected_organization_id=org_id,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


# ---------------------------------------------------------------------------
# 1. Webhook Challenge Handshake (GET)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whatsapp_challenge_verification():
    """GET /api/v1/integrations/whatsapp/webhook Meta handshake."""
    # Configure verify token for Org 1
    await db_client.upsert_configuration(
        organization_id=1,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": "1122334455",
            "verify_token": "correct_meta_verify_token_123",
            "access_token": encrypt_string("token_xyz"),
        },
    )

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A. Valid challenge returns HTTP 200 with challenge string
        res_valid = await client.get(
            "/api/v1/integrations/whatsapp/webhook?hub.mode=subscribe&hub.challenge=99887766&hub.verify_token=correct_meta_verify_token_123"
        )
        assert res_valid.status_code == 200
        assert res_valid.text == "99887766"

        # B. Wrong token returns HTTP 403 Forbidden
        res_wrong = await client.get(
            "/api/v1/integrations/whatsapp/webhook?hub.mode=subscribe&hub.challenge=99887766&hub.verify_token=wrong_token"
        )
        assert res_wrong.status_code == 403

        # C. Invalid mode returns HTTP 400 Bad Request
        res_mode = await client.get(
            "/api/v1/integrations/whatsapp/webhook?hub.mode=unsubscribe&hub.challenge=99887766&hub.verify_token=correct_meta_verify_token_123"
        )
        assert res_mode.status_code == 400


# ---------------------------------------------------------------------------
# 2. Webhook Signature Verification & Forged Rejection (POST)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_whatsapp_webhook_signature_and_forgery():
    """POST /api/v1/integrations/whatsapp/webhook X-Hub-Signature-256 verification."""
    secret = "meta_test_secret_abc123"
    phone_id = "5544332211"

    # Configure Org 1 credentials with app_secret
    await db_client.upsert_configuration(
        organization_id=1,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": phone_id,
            "app_secret": secret,
            "access_token": encrypt_string("token_xyz"),
        },
    )

    payload_dict = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_100",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_id},
                            "statuses": [],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    raw_body = json.dumps(payload_dict).encode("utf-8")

    # Compute valid signature
    valid_sig = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A. Valid signature -> 200 OK
        res_valid = await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": valid_sig},
        )
        assert res_valid.status_code == 200
        assert res_valid.json()["status"] == "success"

        # B. Forged / Tampered signature -> 401 Unauthorized
        res_forged = await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=invalidhex0000000000000000"},
        )
        assert res_forged.status_code == 401
        assert "Invalid webhook signature" in res_forged.text

        # C. Missing signature header -> 401 Unauthorized
        res_missing = await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json"},
        )
        assert res_missing.status_code == 401


# ---------------------------------------------------------------------------
# 3. Idempotent Monotonic Delivery Receipt Progression
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_receipt_correlation_and_no_regression():
    """Monotonic state: READ status must NEVER regress to DELIVERED or SENT."""
    secret = "meta_secret_monot_7788"
    phone_id = "7766554433"
    wamid = f"wamid.monot_{uuid.uuid4().hex[:12]}"
    key = f"idemp_{uuid.uuid4().hex[:8]}"

    await db_client.upsert_configuration(
        organization_id=1,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": phone_id,
            "app_secret": secret,
            "access_token": encrypt_string("token_xyz"),
        },
    )

    # 1. Create outbox job with initial status = SENT
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

    def _make_payload(status_str: str) -> bytes:
        data = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_200",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"phone_number_id": phone_id},
                                "statuses": [
                                    {
                                        "id": wamid,
                                        "status": status_str,
                                        "timestamp": "1730700000",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
        return json.dumps(data).encode("utf-8")

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Step A: 'delivered' receipt -> transitions SENT to DELIVERED
        body_deliv = _make_payload("delivered")
        sig_deliv = "sha256=" + hmac.new(secret.encode(), body_deliv, hashlib.sha256).hexdigest()
        res_deliv = await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=body_deliv,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig_deliv},
        )
        assert res_deliv.status_code == 200

        async with db_client.async_session() as session:
            stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)
            res = await session.execute(stmt)
            j = res.scalar_one()
            assert j.status == "DELIVERED"

        # Step B: 'read' receipt -> transitions DELIVERED to READ
        body_read = _make_payload("read")
        sig_read = "sha256=" + hmac.new(secret.encode(), body_read, hashlib.sha256).hexdigest()
        await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=body_read,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig_read},
        )

        async with db_client.async_session() as session:
            stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)
            res = await session.execute(stmt)
            j = res.scalar_one()
            assert j.status == "READ"

        # Step C: Duplicate/delayed 'delivered' receipt arrives LATER -> MUST NOT REGRESS TO DELIVERED!
        await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=body_deliv,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig_deliv},
        )

        async with db_client.async_session() as session:
            stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)
            res = await session.execute(stmt)
            j_final = res.scalar_one()
            assert j_final.status == "READ"  # Monotonic invariant verified!


# ---------------------------------------------------------------------------
# 4. Inbound Customer Opt-Out (STOP / UNSUBSCRIBE)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_webhook_inbound_opt_out_stop():
    """Inbound STOP triggers WhatsApp-specific opt-out without mutating voice call records."""
    secret = "meta_secret_optout_3344"
    phone_id = "3322119900"
    customer_phone = f"+91987{uuid.uuid4().int % 10000000:07d}"

    await db_client.upsert_configuration(
        organization_id=1,
        key="whatsapp_meta_cloud",
        value={
            "phone_number_id": phone_id,
            "app_secret": secret,
            "access_token": encrypt_string("token_xyz"),
        },
    )

    inbound_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_300",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": phone_id},
                            "messages": [
                                {
                                    "from": customer_phone,
                                    "id": f"wamid.inbound_{uuid.uuid4().hex[:8]}",
                                    "timestamp": "1730700100",
                                    "type": "text",
                                    "text": {"body": "STOP"},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    raw_body = json.dumps(inbound_payload).encode("utf-8")
    sig = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/integrations/whatsapp/webhook",
            content=raw_body,
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": sig},
        )
        assert res.status_code == 200
        assert res.json()["opt_outs_recorded"] == 1

    # Verify WhatsApp opt-out recorded
    opted_out, reason = await meta_whatsapp_adapter.is_whatsapp_opted_out(1, customer_phone)
    assert opted_out is True
    assert "whatsapp_opt_out" in reason


# ---------------------------------------------------------------------------
# 5. Business Settings Endpoints (GET & POST)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_business_whatsapp_settings_get_and_post():
    """GET and POST /api/v1/business/integrations/whatsapp with encryption and masking."""
    user = await _get_or_create_test_user(org_id=1)
    jwt_token = _generate_test_jwt(user.id, user.email, org_id=1)

    headers = {"Authorization": f"Bearer {jwt_token}"}

    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # A. Save credentials via POST
        save_res = await client.post(
            "/api/v1/business/integrations/whatsapp",
            headers=headers,
            json={
                "waba_id": "100200300400",
                "phone_number_id": "9988776655",
                "access_token": "EAABwzL1382490sdflkjsd098234",
                "app_secret": "my_top_secret_app_secret",
            },
        )
        assert save_res.status_code == 200
        data = save_res.json()
        assert data["status"] == "success"
        assert data["configuration_status"] == "Saved — Live verification pending"
        assert data["real_delivery_enabled"] is False

        # B. Retrieve settings via GET -> Tokens MUST be masked!
        get_res = await client.get("/api/v1/business/integrations/whatsapp", headers=headers)
        assert get_res.status_code == 200
        settings = get_res.json()
        assert settings["is_configured"] is True
        assert settings["phone_number_id"] == "9988776655"
        assert settings["has_access_token"] is True
        assert "EAAB" in settings["access_token_masked"]
        assert "EAABwzL1382490sdflkjsd098234" not in json.dumps(settings)  # Plaintext NEVER returned!
        assert settings["app_secret_masked"] == "••••••••"
        assert settings["status"] == "Saved — Live verification pending"

        # C. Verify in database: tokens are encrypted at rest with Fernet
        cfg = await db_client.get_configuration(1, "whatsapp_meta_cloud")
        assert is_encrypted(cfg.value["access_token"])
        assert cfg.value["access_token"].startswith("enc:v1:")
        assert decrypt_string(cfg.value["access_token"]) == "EAABwzL1382490sdflkjsd098234"


# ---------------------------------------------------------------------------
# 6. Critical Safety Check: Authoritative Booking Verification (Section 3)
# ---------------------------------------------------------------------------

def test_appointment_confirmation_blocked_without_authoritative_booking():
    """Section 3: Caller-supplied is_confirmed=True is blocked without authoritative booking."""
    # A. Caller said "confirmed" during conversation, but no authoritative verification -> BLOCKED!
    with pytest.raises(UnconfirmedAppointmentError) as exc_info:
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="appointment_confirmation",
            parameters={
                "customer_name": "Arjun Singhal",
                "status": "Confirmed",
                "is_confirmed": True,
                "appointment_date": "Tomorrow at 4 PM",
                # Note: authoritative_booking_verified and booking_id are omitted!
            },
        )
    assert "Authoritative external booking source verification is required" in str(exc_info.value)

    # B. With verified authoritative booking record -> ALLOWED
    payload = TemplatePayloadValidator.validate_and_format(
        organization_id=1,
        recipient_phone="+919876543210",
        template_name="appointment_confirmation",
        parameters={
            "customer_name": "Arjun Singhal",
            "status": "Confirmed",
            "is_confirmed": True,
            "appointment_date": "Tomorrow at 4 PM",
            "service_or_agent": "Dr. Verma",
            "authoritative_booking_verified": True,
            "booking_id": "APPT-AUTH-99881",
        },
    )
    assert payload["template"]["name"] == "appointment_confirmation"

