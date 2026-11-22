"""
Phase C2 Acceptance Tests
Covers all 13 Part E scenarios:
1. Standard call cost calculation, pulse telecom + Gemini Live + infra itemization, rate card version.
2. Premium call cost calculation, pulse telecom + Deepgram STT + DeepSeek LLM + Cartesia TTS.
3. Budget / IVR call cost, pulse telecom + platform infra + ₹0.0 AI compute during IVR.
4. Missing CDR billing status is PENDING_RECONCILIATION.
5. Duplicate CDR idempotent handling (no double-billing, returns cached result).
6. Concurrent / duplicate reconciliation protection via ActionIdempotencyRecordModel.
7. Simulated run protection: rejects commercial carrier billing (REJECTED_SIMULATION), is_simulated=True.
8. Admin configuration change audit: records actor ID, org ID, before/after values to dashboard_events.
9. Agent publish audit: records actor ID, org ID, version number, draft ID.
10. Credential change audit with secret redaction: verifies secrets are replaced with [REDACTED_SECRET].
11. Tenant isolation: get_audit_trail raises PermissionError on cross-tenant access.
12. Audit event durability and querying.
13. Audit logging failure handling: fail_closed=False handles errors gracefully, fail_closed=True raises RuntimeError.
"""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from api.services.pricing.cost_calculator import (
    calculate_call_cost,
    RATE_CARD_VERSION,
    CEILING_STANDARD_GEMINI_INR,
    CEILING_PREMIUM_DEEPSEEK_CARTESIA_INR,
    CEILING_IVR_ONLY_INR,
)
from api.services.pricing.workflow_run_cost import (
    BillingStatus,
    is_simulated_run,
    reconcile_workflow_run_cdr,
    _build_usage_cost_snapshot,
)
from api.services.audit.audit_service import (
    AuditService,
    mask_phone_number,
    sanitize_audit_payload,
)


# ==============================================================================
# 1. SCENARIO 1: Standard Voice Call Cost Itemization
# ==============================================================================
def test_scenario_01_standard_call_cost_itemization():
    """
    Standard voice call (75s):
    - Telecom: 2 pulses (ceiling(75/60)=2)
    - Gemini Live AI compute + infra + telecom itemized
    - Rate card version: 2026.05
    """
    res = calculate_call_cost(
        mode="standard",
        duration_seconds=75.0,
        is_outbound=False,
    )

    assert res["rate_card_version"] == "2026.05"
    assert res["billed_pulses"] == 2
    assert res["is_within_ceiling"] is True
    assert "carrier_telecom" in res["itemized"]
    assert "infrastructure" in res["itemized"]
    assert "gemini_live_ai" in res["itemized"]
    assert res["currency"] == "INR"
    # Cost per minute must not exceed ceiling
    assert res["effective_rate_per_min"] <= (CEILING_STANDARD_GEMINI_INR * 1.05)


# ==============================================================================
# 2. SCENARIO 2: Premium Voice Call Cost Itemization
# ==============================================================================
def test_scenario_02_premium_call_cost_itemization():
    """
    Premium voice call (90s = 1.5 min):
    - Telecom: 2 pulses (ceiling(90/60)=2)
    - Deepgram STT, DeepSeek LLM, Cartesia TTS itemized
    """
    res = calculate_call_cost(
        mode="premium",
        duration_seconds=90.0,
        is_outbound=False,
    )

    assert res["rate_card_version"] == "2026.05"
    assert res["billed_pulses"] == 2
    assert res["is_within_ceiling"] is True
    assert "carrier_telecom" in res["itemized"]
    assert "infrastructure" in res["itemized"]
    assert "deepgram_stt" in res["itemized"]
    assert "deepseek_llm" in res["itemized"]
    assert "cartesia_tts" in res["itemized"]
    assert res["effective_rate_per_min"] <= (CEILING_PREMIUM_DEEPSEEK_CARTESIA_INR * 1.05)


# ==============================================================================
# 3. SCENARIO 3: Budget IVR Call Cost Itemization (₹0.0 AI Compute)
# ==============================================================================
def test_scenario_03_budget_ivr_zero_ai_compute():
    """
    Budget IVR call (45s = 0.75 min):
    - Telecom: 1 pulse
    - AI compute: ₹0.00 (IVR rule: zero LLM/STT/TTS cost during IVR)
    """
    res = calculate_call_cost(
        mode="ivr",
        duration_seconds=45.0,
        is_outbound=False,
        ivr_only=True,
    )

    assert res["rate_card_version"] == "2026.05"
    assert res["billed_pulses"] == 1
    assert res["itemized"]["ai_engine"] == 0.0
    assert res["is_within_ceiling"] is True
    assert res["effective_rate_per_min"] <= CEILING_IVR_ONLY_INR


# ==============================================================================
# 4. SCENARIO 4: Missing CDR Status is PENDING_RECONCILIATION
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_04_missing_cdr_pending_reconciliation():
    """
    Workflow run cost snapshot for an un-reconciled telephony call must
    record billing_status as PENDING_RECONCILIATION, not VERIFIED.
    """
    workflow_run = SimpleNamespace(
        id=701,
        mode="voice",
        call_type="inbound",
        created_at=datetime.now(UTC),
        usage_info={"call_duration_seconds": 60.0},
        cost_info={},
        gathered_context={"call_id": "CALL-701"},
        initial_context={},
    )

    snapshot = await _build_usage_cost_snapshot(
        usage_info={"call_duration_seconds": 60.0},
        workflow_run=workflow_run,
        include_telephony_cost=False,
    )
    assert snapshot["billing_status"] == BillingStatus.PENDING_RECONCILIATION
    assert snapshot["is_simulated"] is False
    assert snapshot["rate_card_version"] == "2026.05"
    assert snapshot["billable_pulses"] == 1


# ==============================================================================
# 5. SCENARIO 5: Duplicate CDR Idempotent Handling
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_05_duplicate_cdr_idempotent_handling():
    """
    Reconciling twice with identical call_id and cdr_event_id must not double-bill.
    Second call finds the completed ActionIdempotencyRecordModel and returns existing result.
    """
    call_id = "CALL-IDEMP-555"
    event_id = "EVT-001"
    run_mock = SimpleNamespace(
        id=555,
        workflow_id=1,
        cost_info={
            "billing_status": BillingStatus.PENDING_RECONCILIATION,
            "cost_inr": 2.45,
            "reconciliation_history": [],
        },
        gathered_context={"call_id": call_id},
        initial_context={},
        mode="vobiz",
        call_type="inbound",
    )

    cdr_data = {
        "call_id": call_id,
        "record_id": event_id,
        "billable_duration_seconds": 75,
        "carrier_charge_inr": 1.20,
    }

    mock_db = MagicMock()
    mock_session = AsyncMock()

    # Existing completed idempotency record
    existing_idemp = SimpleNamespace(
        idempotency_key=f"cdr:vobiz:{call_id}:{event_id}",
        status="COMPLETED",
        result={"status": "RECONCILED", "billing_status": BillingStatus.VERIFIED},
    )
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=existing_idemp)))
    mock_db.async_session.return_value.__aenter__.return_value = mock_session
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=run_mock)

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db):
        res = await reconcile_workflow_run_cdr(555, cdr_data, provider_name="vobiz")
        assert res["status"] == "ALREADY_RECONCILED"
        assert res["idempotent"] is True
        assert res["cost_info"]["billing_status"] == BillingStatus.VERIFIED


# ==============================================================================
# 6. SCENARIO 6: Concurrent / Duplicate Reconciliation Protection Key Shape
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_06_idempotency_key_shape():
    """
    Idempotency key must incorporate provider_name, call_id, and cdr_event_id.
    """
    provider = "voicelink"
    call_id = "VL-9999"
    event_id = "CDR-8888"
    expected_key = f"cdr:{provider}:{call_id}:{event_id}"

    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    # First query returns None (not yet reconciled)
    mock_session.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
    mock_session.commit = AsyncMock()
    mock_db.async_session.return_value.__aenter__.return_value = mock_session
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=SimpleNamespace(
        id=999,
        workflow_id=1,
        cost_info={"billing_status": BillingStatus.PENDING_RECONCILIATION, "reconciliation_history": []},
        gathered_context={"call_id": call_id},
        initial_context={},
        mode="vobiz",
        call_type="outbound",
    ))

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db), \
         patch("api.services.pricing.workflow_run_cost.save_workflow_run_cost_info", AsyncMock()), \
         patch("api.services.audit.audit_service.AuditService.record_event", AsyncMock()):
        await reconcile_workflow_run_cdr(
            999,
            {"call_id": call_id, "record_id": event_id, "billable_duration_seconds": 60, "carrier_charge_inr": 0.60},
            provider_name=provider,
        )

    # Verify an ActionIdempotencyRecordModel was added with the expected key
    added_objects = [call[0][0] for call in mock_session.add.call_args_list]
    idemp_records = [o for o in added_objects if hasattr(o, "idempotency_key")]
    assert len(idemp_records) > 0
    assert idemp_records[0].idempotency_key == expected_key
    assert idemp_records[0].status == "IN_PROGRESS"


# ==============================================================================
# 7. SCENARIO 7: Simulated Run Protection
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_07_simulated_run_protection():
    """
    Simulated calls (e.g. browser test, manual test runner) must be protected
    from carrier reconciliation, have is_simulated=True, status=ESTIMATED,
    and return REJECTED_SIMULATION.
    """
    sim_run = SimpleNamespace(
        id=777,
        mode="textchat",
        call_type="simulated",
        is_simulation=True,
        annotations={"is_simulation": True},
        gathered_context={},
        initial_context={"caller_name": "E2E Test Runner #173"},
        cost_info={},
    )
    assert is_simulated_run(sim_run) is True

    mock_db = MagicMock()
    mock_db.get_workflow_run_by_id = AsyncMock(return_value=sim_run)

    with patch("api.services.pricing.workflow_run_cost.db_client", mock_db):
        res = await reconcile_workflow_run_cdr(
            777,
            {"call_id": "SIM-777", "carrier_charge_inr": 1.20},
            provider_name="vobiz",
        )
        assert res["status"] == "REJECTED_SIMULATION"
        assert "Simulated calls cannot be billed" in res["reason"]


# ==============================================================================
# 8. SCENARIO 8: Admin Configuration Change Audit
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_08_admin_config_change_audit():
    """
    Admin configuration changes (budget or industry update) record durable
    audit events in dashboard_events with actor_id, org_id, and details.
    """
    audit_svc = AuditService()
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    with patch("api.services.audit.audit_service.db_client", mock_db):
        evt = await audit_svc.record_event(
            organization_id=42,
            action_type="audit.operations.budget_changed",
            target_resource="business_budget",
            target_id="org-42",
            actor_id="user-owner-101",
            actor_role="business_owner",
            changed_fields={
                "previous_monthly_cap_inr": 5000.0,
                "new_monthly_cap_inr": 10000.0,
                "currency": "INR",
            },
            outcome="SUCCESS",
        )

    assert evt is not None
    assert evt["actor_id"] == "user-owner-101"
    assert evt["changed_fields"]["new_monthly_cap_inr"] == 10000.0
    assert evt["outcome"] == "SUCCESS"
    mock_session.add.assert_called_once()
    mock_session.commit.assert_called_once()


# ==============================================================================
# 9. SCENARIO 9: Agent Publish Audit
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_09_agent_publish_audit():
    """
    Publishing an agent records audit.config.agent_published with actor_id,
    organization_id, version number, and draft_id.
    """
    audit_svc = AuditService()
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    with patch("api.services.audit.audit_service.db_client", mock_db):
        evt = await audit_svc.record_event(
            organization_id=10,
            action_type="audit.config.agent_published",
            target_resource="workflow",
            target_id="wf-99",
            actor_id="admin-dev-01",
            actor_role="admin",
            changed_fields={
                "workflow_id": 99,
                "version": 4,
                "draft_id": 12,
                "nodes_count": 8,
            },
            outcome="SUCCESS",
        )

    assert evt["changed_fields"]["version"] == 4
    assert evt["changed_fields"]["workflow_id"] == 99
    assert evt["actor_id"] == "admin-dev-01"


# ==============================================================================
# 10. SCENARIO 10: Credential Change Audit with Secret Redaction
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_10_credential_change_secret_redaction():
    """
    Credential creation and updates must redact sensitive keys/passwords,
    replacing them with [REDACTED_SECRET].
    """
    sensitive_input = {
        "provider": "deepgram",
        "api_key": "dg_live_super_secret_key_12345",
        "auth_token": "bearer_abc_secret",
        "password": "Password123!",
        "customer_phone": "+919876543210",
        "metadata": {
            "key_alias": "primary_stt",
            "nested_secret": "sensitive_val",
        },
    }

    scrubbed = sanitize_audit_payload(sensitive_input)
    assert scrubbed["api_key"] == "[REDACTED_SECRET]"
    assert scrubbed["auth_token"] == "[REDACTED_SECRET]"
    assert scrubbed["password"] == "[REDACTED_SECRET]"
    assert scrubbed["metadata"]["nested_secret"] == "[REDACTED_SECRET]"
    assert scrubbed["metadata"]["key_alias"] == "primary_stt"
    assert scrubbed["customer_phone"] == "+9198****10"

    # Also test audit record creation with sensitive details
    audit_svc = AuditService()
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.add = MagicMock()
    mock_session.commit = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    with patch("api.services.audit.audit_service.db_client", mock_db):
        evt = await audit_svc.record_event(
            organization_id=1,
            action_type="audit.security.credential_created",
            target_resource="service_key",
            target_id="key-dg-1",
            actor_id="admin-01",
            actor_role="admin",
            changed_fields=sensitive_input,
            outcome="SUCCESS",
        )

    assert evt["changed_fields"]["api_key"] == "[REDACTED_SECRET]"
    assert "dg_live_super_secret_key_12345" not in str(evt)


# ==============================================================================
# 11. SCENARIO 11: Cross-Tenant Audit Trail Access Blocked
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_11_cross_tenant_access_blocked():
    """
    AuditService.get_audit_trail must raise PermissionError when user.selected_organization_id
    does not match the target organization_id.
    """
    audit_svc = AuditService()
    user_tenant_100 = SimpleNamespace(
        id=1,
        selected_organization_id=100,
        is_superuser=False,
    )

    with pytest.raises(PermissionError) as exc_info:
        await audit_svc.get_audit_trail(
            organization_id=999,
            user=user_tenant_100,
        )
    assert "forbidden" in str(exc_info.value).lower()


# ==============================================================================
# 12. SCENARIO 12: Audit Event Querying
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_12_audit_trail_querying():
    """
    Audit trail can be queried within an organization with optional event_type prefix filtering.
    """
    audit_svc = AuditService()
    mock_db = MagicMock()
    mock_session = AsyncMock()

    mock_row1 = SimpleNamespace(
        id=101,
        organization_id=42,
        event_type="audit.config.agent_published",
        resource_type="audit",
        resource_id="wf-1",
        data={
            "actor_id": "user-1",
            "actor_role": "admin",
            "outcome": "SUCCESS",
            "target_resource": "workflow",
            "target_id": "wf-1",
            "request_id": "req-1",
            "changed_fields": {},
        },
        created_at=datetime.now(UTC),
    )
    mock_session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[mock_row1])))))
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    user_tenant_42 = SimpleNamespace(
        id=1,
        selected_organization_id=42,
        is_superuser=False,
    )

    with patch("api.services.audit.audit_service.db_client", mock_db):
        records = await audit_svc.get_audit_trail(
            organization_id=42,
            user=user_tenant_42,
            action_prefix="audit.config",
            limit=10,
        )

    assert len(records) == 1
    assert records[0]["action_type"] == "audit.config.agent_published"
    assert records[0]["actor_id"] == "user-1"


# ==============================================================================
# 13. SCENARIO 13: Audit Logging Failure Handling (Fail-Closed vs Fail-Open)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_13_audit_logging_failure_handling():
    """
    When the database is unavailable:
    - fail_closed=False must log an observable error without crashing the caller.
    - fail_closed=True must raise a clean RuntimeError to prevent sensitive
      actions from silently proceeding without an audit record.
    """
    audit_svc = AuditService()
    mock_db = MagicMock()
    mock_session = AsyncMock()
    mock_session.commit.side_effect = Exception("DB Connection Lost")
    mock_db.async_session.return_value.__aenter__.return_value = mock_session

    with patch("api.services.audit.audit_service.db_client", mock_db):
        # 1. fail_closed=False should return payload with persistence_error, not raise
        res_open = await audit_svc.record_event(
            organization_id=1,
            action_type="audit.security.login_failed",
            target_resource="auth",
            target_id="anonymous",
            actor_id="anonymous",
            fail_closed=False,
        )
        assert res_open is not None
        assert res_open["event_id"] is None
        assert "persistence_error" in res_open

        # 2. fail_closed=True must raise RuntimeError
        with pytest.raises(RuntimeError) as exc_info:
            await audit_svc.record_event(
                organization_id=1,
                action_type="audit.security.credential_created",
                target_resource="service_key",
                target_id="key-1",
                actor_id="admin-01",
                fail_closed=True,
            )
        assert "Administrative action blocked" in str(exc_info.value)
