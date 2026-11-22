"""Targeted Acceptance Tests for Phase 5 Final Gap Closure.

Covers:
1. CSV Field Mapping with standard CRM headers, alias detection, explicit mapping, and ambiguity rejection.
2. Post-Call Lead Intelligence Trigger and idempotent annotations persistence.
3. Post-Call Action Triggers (brochure delivery, authoritative booking verification).
4. WhatsApp Template Registry (GET list, POST registration with status 'Registered — Live Meta verification pending', category validation).
5. Template Payload Validator custom template formatting with approved category.
"""

from __future__ import annotations

from datetime import UTC, datetime
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.future import select

from api.app import app
from api.constants import OSS_JWT_SECRET
from api.db import db_client
from api.db.models import OrganizationConfigurationModel, UserModel, WorkflowRunModel
from api.services.campaign.source_sync import CampaignSourceSyncService, ValidationError
from api.services.livekit.post_call_triggers import process_post_call_intelligence_and_triggers
from api.services.whatsapp.exceptions import InvalidTemplateError, UnconfirmedAppointmentError
from api.services.whatsapp.template_validator import APPROVED_TRANSACTIONAL_TEMPLATES, TemplatePayloadValidator


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
        if not user:
            user = UserModel(
                email="admin@spxvoice.com",
                first_name="Admin",
                last_name="User",
                selected_organization_id=org_id,
                is_active=True,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user


# ==============================================================================
# 1. CSV Field Mapping Tests
# ==============================================================================

def test_csv_field_mapping_alias_detection():
    """Confirms CRM headers like Mobile, Full Name, Budget are detected automatically."""
    headers = ["Mobile", "Full Name", "Budget", "Location"]
    res = CampaignSourceSyncService.resolve_column_mapping(headers)

    assert res["phone_col"] == "mobile"
    assert res["name_col"] == "full name"
    assert res["inferred_mapping"]["phone_number"] == "mobile"
    assert res["inferred_mapping"]["customer_name"] == "full name"


def test_csv_field_mapping_explicit_override():
    """Confirms explicit user field mapping overrides automatic inference."""
    headers = ["Work_Phone", "Personal_Mobile", "Contact_Name"]
    custom_mapping = {"phone_number": "Personal_Mobile", "customer_name": "Contact_Name"}
    res = CampaignSourceSyncService.resolve_column_mapping(headers, field_mapping=custom_mapping)

    assert res["phone_col"] == "personal_mobile"
    assert res["name_col"] == "contact_name"
    assert res["inferred_mapping"]["phone_number"] == "personal_mobile"


def test_csv_field_mapping_ambiguity_rejection():
    """Confirms ambiguous headers with multiple phone columns are rejected without explicit mapping."""
    headers = ["Phone", "Mobile", "Name"]
    with pytest.raises(ValidationError) as exc:
        CampaignSourceSyncService.resolve_column_mapping(headers)
    assert "Ambiguous phone number columns detected" in str(exc.value)

    # But succeeds with explicit mapping
    res = CampaignSourceSyncService.resolve_column_mapping(headers, field_mapping={"phone_number": "Mobile"})
    assert res["phone_col"] == "mobile"


def test_csv_validate_source_data_with_field_mapping():
    """Validates raw CSV contact dictionaries using resolved field mapping."""
    headers = ["Mobile", "Full Name", "Budget"]
    rows = [
        ["+919876543210", "Rajesh Kumar", "1.5 Cr"],
        ["+919876511111", "Priya Sharma", "2.0 Cr"],
    ]
    mapping = {"phone_number": "Mobile", "customer_name": "Full Name"}
    validation = CampaignSourceSyncService.validate_source_data(headers, rows, field_mapping=mapping)

    assert validation.is_valid is True
    assert validation.error is None


# ==============================================================================
# 2. Post-Call Lead Intelligence & Triggers
# ==============================================================================

@pytest.mark.asyncio
async def test_post_call_intelligence_and_triggers_execution():
    """Validates post-call analysis runs from livekit history, updates annotations, and enqueues outbox."""
    user = await _get_or_create_test_user(org_id=1)
    run = await db_client.create_workflow_run(
        name="PostCall Test Run",
        workflow_id=1,
        mode="test",
        user_id=user.id,
        organization_id=1,
        initial_context={"caller_number": "+919876543210", "customer_name": "Aakash Patel"},
    )
    run_id = run.id

    # Simulated LiveKit session history
    simulated_history = {
        "messages": [
            {"role": "assistant", "content": [{"text": "Hello, welcome to luxury residences. How can I assist you?"}]},
            {"role": "user", "content": [{"text": "I am interested in a 3BHK flat. My budget is 2.5 Crore. Please send me the brochure on WhatsApp."}]},
            {"role": "assistant", "content": [{"text": "Sure, I have noted that. Can we book a site visit for tomorrow at 3 PM?"}]},
            {"role": "user", "content": [{"text": "Yes, please schedule the visit for tomorrow at 3 PM."}]},
        ]
    }

    gathered_context = {
        "brochure_url": "https://cdn.example.com/brochures/luxury-3bhk.pdf",
    }

    result = await process_post_call_intelligence_and_triggers(
        workflow_run_id=run_id,
        livekit_history=simulated_history,
        gathered_context=gathered_context,
    )

    assert result["status"] == "success"
    intel = result["intel"]
    assert intel["lead_score"] >= 80
    assert intel["lead_classification"] == "Hot"
    assert "document_delivery" in result["actions_triggered"]

    # Verify run annotations were persisted in database
    run_after = await db_client.get_workflow_run_by_id(run_id)
    assert run_after is not None
    assert run_after.annotations.get("post_call_processed") is True
    assert run_after.annotations.get("lead_intelligence") is not None
    assert run_after.annotations["lead_intelligence"]["lead_classification"] == "Hot"


@pytest.mark.asyncio
async def test_post_call_appointment_confirmation_blocked_without_authoritative_booking():
    """Confirms unverified appointment request does NOT enqueue confirmation."""
    user = await _get_or_create_test_user(org_id=1)
    run = await db_client.create_workflow_run(
        name="Unverified Appt Run",
        workflow_id=1,
        mode="test",
        user_id=user.id,
        organization_id=1,
        initial_context={"caller_number": "+919876543211", "customer_name": "Kavita Rao"},
    )
    run_id = run.id

    simulated_history = {
        "messages": [
            {"role": "user", "content": [{"text": "I want to confirm an appointment for dental checkup tomorrow at 10 AM."}]}
        ]
    }

    # No authoritative booking verification passed
    result = await process_post_call_intelligence_and_triggers(
        workflow_run_id=run_id,
        livekit_history=simulated_history,
        gathered_context={},
    )

    assert result["status"] == "success"
    # Action triggers MUST NOT include appointment_confirmation
    assert "appointment_confirmation" not in result["actions_triggered"]


@pytest.mark.asyncio
async def test_post_call_appointment_confirmation_allowed_with_authoritative_booking():
    """Confirms verified booking triggers appointment confirmation outbox enqueue."""
    user = await _get_or_create_test_user(org_id=1)
    run = await db_client.create_workflow_run(
        name="Verified Appt Run",
        workflow_id=1,
        mode="test",
        user_id=user.id,
        organization_id=1,
        initial_context={"caller_number": "+919876543212", "customer_name": "Suresh Raina"},
    )
    run_id = run.id

    simulated_history = {
        "messages": [
            {"role": "user", "content": [{"text": "Please confirm my dental checkup appointment tomorrow at 10 AM."}]}
        ]
    }

    gathered_context = {
        "authoritative_booking_verified": True,
        "booking_id": "CAL-994821",
    }

    result = await process_post_call_intelligence_and_triggers(
        workflow_run_id=run_id,
        livekit_history=simulated_history,
        gathered_context=gathered_context,
    )

    assert result["status"] == "success"
    assert "appointment_confirmation" in result["actions_triggered"]


# ==============================================================================
# 3. WhatsApp Template Registry Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_whatsapp_template_registry_endpoints():
    """Tests GET and POST /api/v1/business/integrations/whatsapp/templates."""
    user = await _get_or_create_test_user(org_id=1)
    jwt_token = _generate_test_jwt(user.id, user.email, org_id=1)
    headers = {"Authorization": f"Bearer {jwt_token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET list (initial standard defaults)
        res = await ac.get("/api/v1/business/integrations/whatsapp/templates", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["total_count"] >= 6
        template_names = [t["template_name"] for t in data["templates"]]
        assert "appointment_confirmation" in template_names
        assert "document_delivery" in template_names

        # 2. POST registration of custom business template
        new_tpl = {
            "template_name": "acme_clinic_appointment_v1",
            "category": "appointment_confirmation",
            "language_code": "en",
        }
        res_post = await ac.post(
            "/api/v1/business/integrations/whatsapp/templates",
            json=new_tpl,
            headers=headers,
        )
        assert res_post.status_code == 200
        post_data = res_post.json()
        assert post_data["status"] == "success"
        assert post_data["template"]["status"] == "Registered — Live Meta verification pending"
        assert post_data["template"]["template_name"] == "acme_clinic_appointment_v1"

        # 3. Verify it shows up in GET
        res_after = await ac.get("/api/v1/business/integrations/whatsapp/templates", headers=headers)
        names_after = [t["template_name"] for t in res_after.json()["templates"]]
        assert "acme_clinic_appointment_v1" in names_after

        # 4. Reject registration with unapproved category
        res_bad = await ac.post(
            "/api/v1/business/integrations/whatsapp/templates",
            json={"template_name": "blast_promo", "category": "marketing_blast"},
            headers=headers,
        )
        assert res_bad.status_code == 400
        assert "Invalid category" in res_bad.json()["detail"]


def test_template_validator_custom_registered_template():
    """Tests that TemplatePayloadValidator accepts custom template names mapped to approved categories."""
    custom_params = {
        "category": "appointment_confirmation",
        "customer_name": "Vikram Seth",
        "status": "Confirmed",
        "is_confirmed": True,
        "authoritative_booking_verified": True,
        "booking_id": "APPT-5544",
        "appointment_date": "Tomorrow at 4 PM",
        "service_name": "General Checkup",
    }

    payload = TemplatePayloadValidator.validate_and_format(
        organization_id=1,
        recipient_phone="+919876543210",
        template_name="acme_clinic_appointment_v1",
        parameters=custom_params,
        language_code="en",
    )

    assert payload["messaging_product"] == "whatsapp"
    assert payload["to"] == "919876543210"
    assert payload["template"]["name"] == "acme_clinic_appointment_v1"
    assert len(payload["template"]["components"]) > 0

    # Rejects unapproved category on custom template
    with pytest.raises(InvalidTemplateError):
        TemplatePayloadValidator.validate_and_format(
            organization_id=1,
            recipient_phone="+919876543210",
            template_name="promo_offer_v1",
            parameters={"category": "bulk_promo"},
        )
