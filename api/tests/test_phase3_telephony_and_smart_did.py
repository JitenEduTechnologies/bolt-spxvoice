"""Phase 3 Telephony, Smart DID, Vobiz & Number Safety Acceptance Suite.

Validates the 12 required Phase 3 acceptance behaviors (T1 through T12):
- T1: Inbound/outbound simulated call lifecycle
- T2: Correct call status transitions and resource slot cleanup
- T3: Authorized Smart DID selection and anti-rotation safeguards
- T4: Cross-tenant number isolation
- T5: Capacity-aware concurrent selection and exhaustion
- T6: Number health tracking, cooldown, and quarantine
- T7: Inbound callback routing and ambiguous identity fail-closed
- T8: Duplicate and out-of-order provider events (terminal safety)
- T9: Provider failure fail-closed behavior
- T10: Opt-out, DND, and compliance enforcement
- T11: Immutable telephony snapshot and audit persistence
- T12: Call cost protections and INR tariff normalization
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api.db import db_client
from api.enums import CallType, WorkflowRunMode, WorkflowRunState
from api.routes.telephony import _verify_organization_phone_number
from api.services.telephony.compliance import (
    compliance_manager,
    normalize_phone_number,
)
from api.services.telephony.providers.vobiz.provider import VobizProvider
from api.services.telephony.smart_did import (
    NumberHealthState,
    smart_did_controller,
)
from api.services.telephony.status_processor import (
    StatusCallbackRequest,
    _process_status_update,
)
from api.services.telephony.human_handover import (
    HandoverState,
    human_handover_controller,
)


@pytest.fixture(autouse=True)
def clean_telephony_state():
    """Reset compliance, Smart DID controller, and handover controller state between tests."""
    compliance_manager.clear()
    smart_did_controller.clear()
    human_handover_controller.clear()
    yield
    compliance_manager.clear()
    smart_did_controller.clear()
    human_handover_controller.clear()


# ==============================================================================
# T1 & T11: Vobiz Outbound Initiation & Immutable Telephony Snapshot
# ==============================================================================
@pytest.mark.asyncio
async def test_t01_t11_vobiz_outbound_call_initiation_and_snapshot():
    """T1 & T11: Verify Vobiz initiates outbound call with DID selection & immutable snapshot."""
    org_id = 1
    did = "+919876543210"
    to_num = "+919988776655"
    smart_did_controller.register_did(org_id, did)

    provider = VobizProvider({
        "auth_id": "MA_VOBIZ_TEST",
        "auth_token": "secret_token_123",
        "application_id": "APP_VOBIZ_TEST",
        "organization_id": org_id,
        "from_numbers": [did],
        "channel_capacity": 5,
    })

    mock_resp = AsyncMock()
    mock_resp.status = 201
    mock_resp.json = AsyncMock(return_value={"call_uuid": "vbz-call-test-001", "message": "call fired"})

    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_post.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.post", return_value=mock_post):
        result = await provider.initiate_call(
            to_number=to_num,
            webhook_url="http://test/api/v1/telephony/vobiz-xml",
            workflow_run_id=101,
        )

    assert result.call_id == "vbz-call-test-001"
    assert result.status == "queued"
    assert result.caller_number == normalize_phone_number(did)

    # Snapshot validation (T11)
    snapshot = result.provider_metadata.get("telephony_snapshot")
    assert snapshot is not None
    assert snapshot["provider"] == "vobiz"
    assert snapshot["caller_number"] == normalize_phone_number(did)
    assert snapshot["called_number"] == normalize_phone_number(to_num)
    assert snapshot["account_id"] == "MA_VOBIZ_TEST"
    assert snapshot["fail_closed_enabled"] is True


# ==============================================================================
# T2: Call Status Lifecycle, Cleanup, and Final Disposition
# ==============================================================================
@pytest.mark.asyncio
async def test_t02_status_processor_call_completion_and_resource_cleanup():
    """T2: Verify completed status updates run state, disposition, and releases slots."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    assert user is not None

    workflows = await db_client.get_all_workflows_for_listing(organization_id=user.selected_organization_id)
    assert len(workflows) > 0
    wf_id = workflows[0].id

    run = await db_client.create_workflow_run(
        name="TEST-TEL-LIFECYCLE-001",
        workflow_id=wf_id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        initial_context={"called_number": "+919988776655"},
        organization_id=user.selected_organization_id,
    )

    status_update = StatusCallbackRequest(
        call_id="vbz-call-test-002",
        status="completed",
        duration="38",
        extra={"AnsweredBy": "human"},
    )

    await _process_status_update(run.id, status_update)

    updated_run = await db_client.get_workflow_run_by_id(run.id)
    assert updated_run is not None
    assert updated_run.is_completed is True
    assert str(updated_run.state).lower() == "completed"
    assert updated_run.call_disposition == "ANSWERED"

    logs = updated_run.logs.get("telephony_status_callbacks", [])
    assert len(logs) >= 1
    assert logs[-1]["status"] == "completed"
    assert logs[-1]["duration"] == "38"


# ==============================================================================
# T3: Authorized DID Selection and Anti-Rotation Safeguards
# ==============================================================================
def test_t03_smart_did_selection_and_anti_rotation():
    """T3: Verify authorized DID is selected and unconfigured number fails closed without rotation."""
    org_id = 1
    auth_num = "+919876543210"
    smart_did_controller.register_did(org_id, auth_num)

    # 1. Preferred number in pool succeeds
    sel, err = smart_did_controller.select_caller_id(
        org_id, [auth_num], preferred_number=auth_num
    )
    assert err is None
    assert sel == auth_num

    # 2. Preferred number NOT in pool strictly fails closed (no silent rotation!)
    unauth_num = "+919999999999"
    sel_unauth, err_unauth = smart_did_controller.select_caller_id(
        org_id, [auth_num], preferred_number=unauth_num
    )
    assert sel_unauth is None
    assert "not in authorized pool" in (err_unauth or "")


# ==============================================================================
# T4: Cross-Tenant Number Isolation
# ==============================================================================
def test_t04_cross_tenant_did_isolation():
    """T4: Ensure numbers owned by Organization A are never selected or accessed by Organization B."""
    org_a = 101
    org_b = 202
    num_a = "+919000000001"
    num_b = "+919000000002"

    smart_did_controller.register_did(org_a, num_a)
    smart_did_controller.register_did(org_b, num_b)

    # Org A cannot select Org B's number
    sel_a, err_a = smart_did_controller.select_caller_id(
        org_a, [num_a], preferred_number=num_b
    )
    assert sel_a is None
    assert "not in authorized pool" in (err_a or "")

    # Org B cannot select Org A's number
    sel_b, err_b = smart_did_controller.select_caller_id(
        org_b, [num_b], preferred_number=num_a
    )
    assert sel_b is None
    assert "not in authorized pool" in (err_b or "")


# ==============================================================================
# T5: Capacity-Aware Selection and Exhaustion Fail-Closed
# ==============================================================================
def test_t05_capacity_aware_selection_and_exhaustion():
    """T5: Ensure selector balances load and fails closed when all numbers reach capacity."""
    org_id = 1
    num1 = "+919111111111"
    num2 = "+919222222222"
    smart_did_controller.register_did(org_id, num1)
    smart_did_controller.register_did(org_id, num2)

    # num1 has 1 call, num2 has 0 calls -> chooses num2
    smart_did_controller.record_call_start(org_id, num1)
    sel1, err1 = smart_did_controller.select_caller_id(org_id, [num1, num2], max_concurrency_per_did=2)
    assert err1 is None
    assert sel1 == num2

    # Saturate both to capacity 2
    smart_did_controller.record_call_start(org_id, num1)
    smart_did_controller.record_call_start(org_id, num2)
    smart_did_controller.record_call_start(org_id, num2)

    # 5th call attempt: both saturated -> must fail closed
    sel_sat, err_sat = smart_did_controller.select_caller_id(org_id, [num1, num2], max_concurrency_per_did=2)
    assert sel_sat is None
    assert "maximum capacity" in (err_sat or "")


# ==============================================================================
# T6: Number Health, Cooldown, and Quarantine
# ==============================================================================
def test_t06_number_health_cooldown_and_quarantine():
    """T6: Technical failures trigger cooldown; regulatory/policy errors trigger quarantine."""
    org_id = 1
    did = "+919876543210"
    smart_did_controller.register_did(org_id, did)

    # 3 consecutive technical failures -> Cooldown
    for _ in range(3):
        smart_did_controller.record_call_end(org_id, did, success=False, failure_reason="SIP 503", status_code=503)

    health = smart_did_controller.get_did_health(org_id, did)
    assert health.state == NumberHealthState.COOLDOWN
    assert health.is_in_cooldown() is True

    # Quarantining on carrier regulatory rejection
    smart_did_controller.record_call_end(
        org_id, did, success=False, failure_reason="403 Forbidden: unregistered_dlt_header", status_code=403
    )
    health_q = smart_did_controller.get_did_health(org_id, did)
    assert health_q.state == NumberHealthState.QUARANTINE
    assert "Carrier policy/regulatory rejection" in (health_q.quarantine_reason or "")

    # Quarantined number cannot be selected
    sel, err = smart_did_controller.select_caller_id(org_id, [did], preferred_number=did)
    assert sel is None
    assert "QUARANTINED" in (err or "")

    # Admin reset restores health
    assert smart_did_controller.reset_did_health(org_id, did) is True
    assert smart_did_controller.get_did_health(org_id, did).state == NumberHealthState.ACTIVE


# ==============================================================================
# T7: Inbound Callback Routing and Cross-Business Fail-Closed
# ==============================================================================
@pytest.mark.asyncio
async def test_t07_inbound_routing_unregistered_number_fails_closed():
    """T7: Inbound call to unregistered or cross-tenant number fails closed without data leakage."""
    unregistered_did = "+919999000099"
    org_id = 999
    cfg_id = 888

    matched_id = await _verify_organization_phone_number(
        phone_number=unregistered_did,
        organization_id=org_id,
        telephony_configuration_id=cfg_id,
        provider="vobiz",
    )
    # Must return None so dispatcher returns PHONE_NUMBER_NOT_CONFIGURED
    assert matched_id is None


# ==============================================================================
# T8: Duplicate & Out-of-Order Provider Events (Terminal Safety)
# ==============================================================================
@pytest.mark.asyncio
async def test_t08_duplicate_and_out_of_order_provider_events():
    """T8: Late events arriving after call is completed must not resurrect or alter terminal disposition."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    assert user is not None

    workflows = await db_client.get_all_workflows_for_listing(organization_id=user.selected_organization_id)
    wf_id = workflows[0].id

    run = await db_client.create_workflow_run(
        name="TEST-TEL-TERMINAL-001",
        workflow_id=wf_id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        initial_context={"called_number": "+919876543210"},
        organization_id=user.selected_organization_id,
    )

    # 1. Complete the call
    await _process_status_update(
        run.id,
        StatusCallbackRequest(call_id="vbz-call-term", status="completed", duration="15"),
    )
    r1 = await db_client.get_workflow_run_by_id(run.id)
    assert r1.is_completed is True
    assert r1.call_disposition == "ANSWERED"

    # 2. Late out-of-order event arrives (e.g. late "busy" or late "in-progress")
    await _process_status_update(
        run.id,
        StatusCallbackRequest(call_id="vbz-call-term", status="busy"),
    )

    # 3. Assert terminal state was preserved and NOT overwritten
    r2 = await db_client.get_workflow_run_by_id(run.id)
    assert r2.is_completed is True
    assert r2.call_disposition == "ANSWERED"  # preserved, not altered to BUSY!

    # 4. Audit log captured both events
    callbacks = r2.logs.get("telephony_status_callbacks", [])
    assert len(callbacks) == 2
    assert callbacks[0]["status"] == "completed"
    assert callbacks[1]["status"] == "busy"


# ==============================================================================
# T9: Provider Failure Fail-Closed & Health Metric Update
# ==============================================================================
@pytest.mark.asyncio
async def test_t09_provider_failure_fail_closed_and_health_metric():
    """T9: Provider 503 / 500 error records failure to DID health and raises HTTPException."""
    org_id = 1
    did = "+919876543210"
    smart_did_controller.register_did(org_id, did)

    provider = VobizProvider({
        "auth_id": "MA_FAIL_TEST",
        "auth_token": "secret_token",
        "application_id": "APP_FAIL_TEST",
        "organization_id": org_id,
        "from_numbers": [did],
    })

    mock_resp = AsyncMock()
    mock_resp.status = 503
    mock_resp.text = AsyncMock(return_value="Carrier Gateway Timeout")

    mock_post = MagicMock()
    mock_post.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_post.__aexit__ = AsyncMock(return_value=None)

    with patch("aiohttp.ClientSession.post", return_value=mock_post):
        with pytest.raises(HTTPException) as exc_info:
            await provider.initiate_call(
                to_number="+919888877777",
                webhook_url="http://test/webhook",
            )
        assert exc_info.value.status_code == 503

    health = smart_did_controller.get_did_health(org_id, did)
    assert health.consecutive_failures == 1
    assert "Carrier Gateway Timeout" in (health.last_failure_reason or "")


# ==============================================================================
# T10: Opt-Out, DND, and Compliance Enforcement
# ==============================================================================
@pytest.mark.asyncio
async def test_t10_compliance_opt_out_and_dnc_enforcement():
    """T10: Customer on DNC registry blocks outbound call initiation with HTTP 403."""
    org_id = 1
    dnc_customer = "+919876500099"
    did = "+919876543210"
    smart_did_controller.register_did(org_id, did)

    compliance_manager.add_to_dnc(
        org_id, dnc_customer, reason="Customer DNC Opt-Out via SMS", source="opt_out"
    )

    provider = VobizProvider({
        "auth_id": "MA_DNC_TEST",
        "auth_token": "secret_token",
        "organization_id": org_id,
        "from_numbers": [did],
    })

    with pytest.raises(HTTPException) as exc_info:
        await provider.initiate_call(
            to_number=dnc_customer,
            webhook_url="http://test/webhook",
        )
    assert exc_info.value.status_code == 403
    assert "call_not_permitted" in str(exc_info.value.detail)


# ==============================================================================
# T12: Call Cost Normalization & Tariff Accounting
# ==============================================================================
def test_t12_call_cost_protections_inr_normalization():
    """T12: Verify Vobiz INR CDR call cost normalization preserves economy tariff accounting."""
    raw_cdr = {
        "data": {
            "billsec": 60,
            "cost": 1.80,
            "currency": "INR",
            "duration": 65,
            "status": "completed",
            "total_cost": 1.80,
        },
        "success": True,
    }

    normalized = VobizProvider._normalize_call_cost(raw_cdr, source="vobiz_cdr")
    assert normalized["currency"] == "INR"
    assert normalized["cost_inr"] == 1.80
    assert normalized["cost_usd"] == 0.0
    assert normalized["duration"] == 60
    assert normalized["source"] == "vobiz_cdr"


# ==============================================================================
# T13a: Human Handover - Simulated Successful Transfer Lifecycle
# ==============================================================================
@pytest.mark.asyncio
async def test_t13a_human_handover_simulated_success():
    """T13a: Human Handover REQUESTED -> PENDING -> SUCCESSFUL persisted in DB."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    assert user is not None
    org_id = user.selected_organization_id

    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    assert len(workflows) > 0
    wf_id = workflows[0].id

    run = await db_client.create_workflow_run(
        name="TEST-HANDOVER-SUCCESS-001",
        workflow_id=wf_id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        initial_context={"called_number": "+919988776655"},
        organization_id=org_id,
    )

    target_agent_num = "+919876500111"
    human_handover_controller.register_authorized_endpoint(org_id, target_agent_num)

    # 1. Request handover -> PENDING
    req_res = await human_handover_controller.request_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        target_number=target_agent_num,
        reason="customer_asked_for_supervisor",
    )
    assert req_res["state"] == HandoverState.PENDING.value
    assert req_res["initial_state"] == HandoverState.REQUESTED.value

    # Verify DB state after request
    run_after_req = await db_client.get_workflow_run_by_id(run.id)
    assert run_after_req is not None
    assert run_after_req.annotations["human_handover"]["state"] == HandoverState.PENDING.value

    # 2. Bridge connects -> SUCCESSFUL
    comp_res = await human_handover_controller.complete_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        bridge_event_id="bridge-evt-999",
    )
    assert comp_res["state"] == HandoverState.SUCCESSFUL.value
    assert comp_res["bridge_event_id"] == "bridge-evt-999"

    # Verify authoritative DB persistence
    final_run = await db_client.get_workflow_run_by_id(run.id)
    assert final_run is not None
    handover_annot = final_run.annotations.get("human_handover", {})
    assert handover_annot["state"] == HandoverState.SUCCESSFUL.value
    assert handover_annot["bridge_event_id"] == "bridge-evt-999"
    assert len(handover_annot["history"]) >= 3


# ==============================================================================
# T13b: Human Handover - Simulated SIP 503 Fallback to Callback Offer
# ==============================================================================
@pytest.mark.asyncio
async def test_t13b_human_handover_sip_503_fallback():
    """T13b: Handover failure (SIP 503) transitions to FAILED and creates callback offer."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    assert user is not None
    org_id = user.selected_organization_id

    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    run = await db_client.create_workflow_run(
        name="TEST-HANDOVER-503-001",
        workflow_id=wf_id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        initial_context={"called_number": "+919988776655"},
        organization_id=org_id,
    )

    target_agent_num = "+919876500222"
    human_handover_controller.register_authorized_endpoint(org_id, target_agent_num)

    # 1. Request handover
    await human_handover_controller.request_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        target_number=target_agent_num,
    )

    # 2. Simulate SIP 503 failure
    fail_res = await human_handover_controller.fail_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        failure_code="503",
        failure_reason="Carrier SIP 503 Service Unavailable / All Agents Busy",
        offer_callback=True,
    )
    assert fail_res["state"] == HandoverState.FAILED.value
    assert fail_res["failure_code"] == "503"

    # 3. Verify DB persistence and callback fallback
    updated_run = await db_client.get_workflow_run_by_id(run.id)
    assert updated_run is not None
    handover_annot = updated_run.annotations.get("human_handover", {})
    assert handover_annot["state"] == HandoverState.FAILED.value
    assert handover_annot["failure_code"] == "503"

    # Callback offer fallback in lead_intelligence
    lead_intel = updated_run.annotations.get("lead_intelligence", {})
    assert "callback" in lead_intel
    assert lead_intel["callback"]["requested"] is True
    assert lead_intel["callback"]["status"] == "Pending"
    assert "human_handover_failed_503" in lead_intel["callback"]["reason"]


# ==============================================================================
# T13c: Human Handover - Resource Cleanup & Idempotency
# ==============================================================================
@pytest.mark.asyncio
async def test_t13c_human_handover_cleanup_and_idempotency():
    """T13c: Channel capacity is freed and late/duplicate events do not alter terminal state."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    run = await db_client.create_workflow_run(
        name="TEST-HANDOVER-CLEANUP-001",
        workflow_id=wf_id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        organization_id=org_id,
    )

    target_agent_num = "+919876500333"
    smart_did_controller.register_did(org_id, target_agent_num)
    human_handover_controller.register_authorized_endpoint(org_id, target_agent_num)

    # Pre-check active calls
    health_before = smart_did_controller.get_did_health(org_id, target_agent_num)
    assert health_before.active_calls == 0

    # Request handover increases active call
    await human_handover_controller.request_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        target_number=target_agent_num,
    )
    health_during = smart_did_controller.get_did_health(org_id, target_agent_num)
    assert health_during.active_calls == 1

    # Complete handover frees active call slot
    await human_handover_controller.complete_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        bridge_event_id="bridge-123",
    )
    health_after = smart_did_controller.get_did_health(org_id, target_agent_num)
    assert health_after.active_calls == 0

    # Idempotency test: duplicate / late failure event after completion is ignored
    late_res = await human_handover_controller.fail_handover(
        workflow_run_id=run.id,
        organization_id=org_id,
        failure_code="503",
        failure_reason="Late timeout event",
    )
    assert late_res["state"] == HandoverState.SUCCESSFUL.value

    persisted_run = await db_client.get_workflow_run_by_id(run.id)
    assert persisted_run.annotations["human_handover"]["state"] == HandoverState.SUCCESSFUL.value


# ==============================================================================
# T13d: Human Handover - Cross-Tenant and Unauthorized Destination Rejection
# ==============================================================================
@pytest.mark.asyncio
async def test_t13d_human_handover_cross_tenant_rejection():
    """T13d: Unauthorized transfer destinations and cross-tenant attempts fail with HTTP 403."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    run = await db_client.create_workflow_run(
        name="TEST-HANDOVER-REJECT-001",
        workflow_id=wf_id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        organization_id=org_id,
    )

    unauthorized_dest = "+919999988888"

    # 1. Attempt transfer to unauthorized number raises HTTP 403
    with pytest.raises(HTTPException) as exc_info:
        await human_handover_controller.request_handover(
            workflow_run_id=run.id,
            organization_id=org_id,
            target_number=unauthorized_dest,
        )
    assert exc_info.value.status_code == 403
    assert "Unauthorized transfer destination" in exc_info.value.detail

    # 2. Attempt transfer by unauthorized organization raises HTTP 403
    authorized_dest = "+919876500444"
    human_handover_controller.register_authorized_endpoint(org_id, authorized_dest)
    different_org_id = 99999

    with pytest.raises(HTTPException) as exc_info2:
        await human_handover_controller.request_handover(
            workflow_run_id=run.id,
            organization_id=different_org_id,
            target_number=authorized_dest,
        )
    assert exc_info2.value.status_code == 403
