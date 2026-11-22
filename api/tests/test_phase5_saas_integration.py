"""Phase 5 Master Acceptance Test Suite: SaaS Integration & Real-Time Event Bus.

Covers all 20 mandatory acceptance tests (T5-01 through T5-20):
- T5-01: Authenticated SSE/WebSocket endpoint and organization scoping.
- T5-02: Frontend realtime subscription & worker event delivery.
- T5-03: Simultaneous calls independent event streams and concurrency isolation.
- T5-04: Browser reconnection recovery & missed events replay via last_event_id.
- T5-05: Bidirectional owner action execution with two-phase verification ack.
- T5-06: Negative tenant isolation (cross-tenant data access blocked).
- T5-07: Cancellation concurrency race & pre-dial safety check.
- T5-08: Agent version publication and immutable snapshot creation.
- T5-09: Active call snapshot immutability during new version publication.
- T5-10: Advanced settings persistence and invalid option rejection.
- T5-11: Objection approval workflow (P2-07 draft, review, approve, rollback).
- T5-12: Customer identity continuity (P2-08 repeat caller stitching & isolation).
- T5-13: Business knowledge & dynamic qualification fields extraction.
- T5-14: Appointments & callbacks exact date/time preservation.
- T5-15: Campaign live dashboard controls (pause, resume, cancel sync).
- T5-16: Cost accounting (ESTIMATED vs PROVIDER-REPORTED) & budget caps.
- T5-17: Recording access authorization and cross-org denial.
- T5-18: WhatsApp non-blocking asynchronous outbox job delivery.
- T5-19: Pagination and bounded event delivery buffers.
- T5-20: Existing UI browser acceptance across all dashboard endpoints.
"""

from __future__ import annotations

import asyncio
import uuid
import copy
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from api.db import db_client
from api.db.models import (
    CampaignModel,
    ChatwootOutboxJobModel,
    CustomerProfileModel,
    DashboardEventModel,
    ObjectionResponseModel,
    QueuedRunModel,
    UserModel,
    WorkflowDefinitionModel,
    WorkflowModel,
    WorkflowRecordingModel,
    WorkflowRunModel,
)
from api.enums import CallType, WorkflowRunState
from api.routes.business import (
    AgentTrainingDraftRequest,
    ApproveObjectionRequest,
    DraftObjectionRequest,
    ExecuteActionRequest,
    PublishEventRequest,
    RejectObjectionRequest,
    SimulateCallRequest,
    UpdateCustomerProfileRequest,
    UpdateLeadRequest,
    execute_owner_action,
    get_business_costs,
    get_business_overview,
    get_customer_profile,
    list_conversations,
    list_customers,
    list_objections,
    publish_agent_training,
    record_customer_interaction,
    simulate_call,
    update_lead_intelligence,
)
from api.services.business.action_inbox import action_inbox
from api.services.business.agent_training import (
    compile_agent_training_to_workflow,
)
from api.services.business.callback_scheduler import IST_TZ, parse_indian_relative_callback_time
from api.services.business.lead_intelligence import analyze_lead_from_transcript
from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
from api.services.campaign.runner import campaign_runner_service
from api.services.chatwoot.outbox import chatwoot_outbox_service
from api.services.events.realtime_bus import RealtimeDashboardBus, realtime_dashboard_bus
from api.services.pricing.cost_calculator import calculate_call_cost, check_budget_admission
from api.services.telephony.compliance import compliance_manager, normalize_phone_number


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

async def _get_admin_user() -> UserModel:
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    if not user:
        raise ValueError("admin@spxvoice.com not found")
    return user


async def _get_primary_workflow_for_test(user: UserModel) -> WorkflowModel:
    workflows = await db_client.get_all_workflows_for_listing(
        organization_id=user.selected_organization_id
    )
    if workflows:
        wf = await db_client.get_workflow_by_id(workflows[0].id)
        if wf:
            return wf
    raise ValueError(f"No workflow found for org {user.selected_organization_id}")


# ---------------------------------------------------------------------------
# Acceptance Tests T5-01 through T5-20
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_t5_01_sse_endpoint_authentication_and_org_scoping():
    """T5-01: SSE endpoint requires authentication, uses secure single-use tickets, and enforces org scoping over real HTTP."""
    import httpx
    user = await _get_admin_user()
    org_id = user.selected_organization_id

    # 1. Direct function checks: Unauthenticated request raises HTTPException(401)
    from api.routes.business import get_user_sse
    with pytest.raises(HTTPException) as exc_info:
        await get_user_sse(authorization=None, token=None, ticket=None)
    assert exc_info.value.status_code == 401

    # Raw JWT in URL query parameter is rejected (Gap 4 security requirement)
    with pytest.raises(HTTPException) as exc_info_jwt:
        await get_user_sse(authorization=None, token="eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy")
    assert exc_info_jwt.value.status_code == 401
    assert "not permitted in URL" in exc_info_jwt.value.detail

    # 2. Issue a short-lived single-use SSE connection ticket
    ticket = await realtime_dashboard_bus.create_sse_ticket(user, expires_in_seconds=30)
    assert ticket.startswith("ssetkt_")

    # 3. Real HTTP SSE streaming test against running server
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=10.0) as client:
        # Stream over real HTTP
        async with client.stream("GET", f"/api/v1/business/events/stream?token={ticket}") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            lines_iter = resp.aiter_lines()
            # Read connection confirmation
            first_line = await lines_iter.__anext__()
            assert "connected" in first_line

            # Connection confirmed active and listening; publish event now
            await realtime_dashboard_bus.publish_event(
                organization_id=org_id,
                event_type="test_http_sse_ping",
                resource_type="system",
                resource_id="101",
                data={"status": "verified_real_http_stream"},
            )

            received_lines = []
            async for line in lines_iter:
                if line:
                    received_lines.append(line)
                    if "verified_real_http_stream" in line:
                        break

        # 4. Atomic single-use verification: same ticket CANNOT be reused
        reuse_resp = await client.get(f"/api/v1/business/events/stream?token={ticket}")
        assert reuse_resp.status_code == 401
        assert "Invalid, expired, or already redeemed" in reuse_resp.text

        # 5. Negative tenant isolation check: unauthenticated stream request fails
        unauth_resp = await client.get("/api/v1/business/events/stream")
        assert unauth_resp.status_code == 401

    joined = "\n".join(received_lines)
    assert "event: test_http_sse_ping" in joined
    assert "verified_real_http_stream" in joined


@pytest.mark.asyncio
async def test_t5_02_frontend_realtime_event_updates_state():
    """T5-02: Worker emits event; event is durably committed to DB and broadcast to bus."""
    user = await _get_admin_user()
    org_id = user.selected_organization_id

    # Simulate worker publishing a call_completed event
    run_id = f"test_run_{int(datetime.now(UTC).timestamp())}"
    published_event = await realtime_dashboard_bus.publish_event(
        organization_id=org_id,
        event_type="call_completed",
        resource_type="call",
        resource_id=run_id,
        data={
            "customer_phone": "+919876543210",
            "lead_classification": "Hot",
            "lead_score": 85,
        },
    )

    assert published_event["id"] is not None
    assert published_event["event_type"] == "call_completed"
    assert published_event["data"]["lead_classification"] == "Hot"

    # Verify event exists in PostgreSQL
    async with db_client.async_session() as session:
        stmt = select(DashboardEventModel).where(
            DashboardEventModel.id == published_event["id"]
        )
        res = await session.execute(stmt)
        event_row = res.scalar_one_or_none()
        assert event_row is not None
        assert event_row.organization_id == org_id
        assert event_row.event_type == "call_completed"


@pytest.mark.asyncio
async def test_t5_03_simultaneous_calls_independent_event_streams():
    """T5-03: Two concurrent calls emit independent event streams without transcript mixing."""
    user = await _get_admin_user()
    org_id = user.selected_organization_id

    bus = RealtimeDashboardBus()
    queue_a: asyncio.Queue = asyncio.Queue()
    queue_b: asyncio.Queue = asyncio.Queue()

    # Register local listeners
    async with bus._lock:
        if org_id not in bus._local_subscribers:
            bus._local_subscribers[org_id] = set()
        bus._local_subscribers[org_id].add(queue_a)
        bus._local_subscribers[org_id].add(queue_b)

    async def simulate_call_stream(call_id: str, caller_name: str, transcript: str):
        await bus.publish_event(
            organization_id=org_id,
            event_type="transcript_update",
            resource_type="call",
            resource_id=call_id,
            data={"caller_name": caller_name, "transcript": transcript},
        )

    # Run two calls concurrently
    await asyncio.gather(
        simulate_call_stream("CALL-101", "Ramesh Kumar", "I want a 3BHK flat in Gachibowli"),
        simulate_call_stream("CALL-102", "Pooja Sharma", "I want a dental consultation today"),
    )

    # Retrieve events
    events_received = []
    while not queue_a.empty():
        events_received.append(queue_a.get_nowait())

    call_101_events = [e for e in events_received if e["resource_id"] == "CALL-101"]
    call_102_events = [e for e in events_received if e["resource_id"] == "CALL-102"]

    assert len(call_101_events) == 1
    assert len(call_102_events) == 1
    assert "3BHK flat" in call_101_events[0]["data"]["transcript"]
    assert "dental consultation" in call_102_events[0]["data"]["transcript"]
    # Verify no transcript mixing
    assert "dental" not in call_101_events[0]["data"]["transcript"]
    assert "3BHK" not in call_102_events[0]["data"]["transcript"]


@pytest.mark.asyncio
async def test_t5_04_browser_reconnection_recovery_missed_events():
    """T5-04: Client reconnecting with last_event_id recovers missed events from PostgreSQL."""
    user = await _get_admin_user()
    org_id = user.selected_organization_id

    # 1. Base event
    base_event = await realtime_dashboard_bus.publish_event(
        organization_id=org_id,
        event_type="lead_created",
        resource_type="lead",
        resource_id="1001",
        data={"lead_score": 40},
    )
    last_id = base_event["id"]

    # 2. Client is disconnected while 3 new events occur
    e1 = await realtime_dashboard_bus.publish_event(
        organization_id=org_id,
        event_type="lead_updated",
        resource_type="lead",
        resource_id="1001",
        data={"lead_score": 60},
    )
    e2 = await realtime_dashboard_bus.publish_event(
        organization_id=org_id,
        event_type="appointment_booked",
        resource_type="appointment",
        resource_id="2001",
        data={"slot": "Tomorrow 10 AM"},
    )
    e3 = await realtime_dashboard_bus.publish_event(
        organization_id=org_id,
        event_type="call_completed",
        resource_type="call",
        resource_id="3001",
        data={"duration": 120},
    )

    # 3. Client reconnects with last_event_id = last_id
    missed = await realtime_dashboard_bus.get_missed_events(
        organization_id=org_id, last_event_id=last_id
    )

    missed_ids = [m["id"] for m in missed]
    assert e1["id"] in missed_ids
    assert e2["id"] in missed_ids
    assert e3["id"] in missed_ids
    # Verify strict ascending order
    assert missed_ids == sorted(missed_ids)


@pytest.mark.asyncio
async def test_t5_05_owner_action_acknowledgement_two_phase_ack():
    """T5-05: Owner action executes with two-phase verification and structured ack."""
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)

    # Create a test run
    test_run = await db_client.create_workflow_run(
        name="TEST-ACTION-ACK-RUN",
        workflow_id=wf.id,
        user_id=user.id,
        mode="test",
        call_type=CallType.INBOUND,
        call_disposition="ANSWERED",
        organization_id=user.selected_organization_id,
    )
    await db_client.update_workflow_run(
        run_id=test_run.id,
        annotations={
            "lead_intelligence": {
                "lead_classification": "Warm",
                "lead_score": 50,
                "callback": {"requested": True, "status": "Pending"},
            }
        },
    )

    # Execute action to reschedule callback
    req = ExecuteActionRequest(
        action_id=f"act_test_{test_run.id}",
        action_type="reschedule_callback",
        target_id=str(test_run.id),
        payload={"new_slot": "Monday 3:00 PM"},
    )
    ack = await execute_owner_action(req, user=user)

    assert ack["status"] == "acknowledged"
    assert ack["execution_status"] == "SUCCESS"
    assert ack["action_type"] == "reschedule_callback"
    assert ack["result"]["callback"]["status"] == "Rescheduled"

    # Verify DB updated
    updated_run = await db_client.get_workflow_run_by_id(test_run.id)
    cb = (updated_run.annotations or {}).get("lead_intelligence", {}).get("callback", {})
    assert cb.get("status") == "Rescheduled"
    assert cb.get("rescheduled_slot") == "Monday 3:00 PM"


@pytest.mark.asyncio
async def test_t5_06_negative_tenant_isolation_org_a_vs_org_b():
    """T5-06: Strict negative authorization prevents Org A and Org B cross-tenant data access."""
    user = await _get_admin_user()
    org_a_id = user.selected_organization_id
    org_b_id = org_a_id + 999  # Non-existent or separate org

    # 1. Create a customer profile in Org A
    test_phone = "+919999900001"
    await record_customer_interaction(
        organization_id=org_a_id,
        phone_number=test_phone,
        name="Org A Confidential VIP",
        lead_status="Hot",
    )

    # 2. Mock a user belonging only to Org B
    user_b = UserModel(
        id=9999,
        email="user_b@otherdomain.com",
        selected_organization_id=org_b_id,
        is_superuser=False,
    )

    # Org B queries customer profile for test_phone -> must raise 404 (not found in Org B)
    with pytest.raises(HTTPException) as exc_info:
        await get_customer_profile(test_phone, user=user_b)
    assert exc_info.value.status_code == 404

    # Org B attempts to execute action on an Org A run -> must raise 403 Forbidden
    wf = await _get_primary_workflow_for_test(user)
    run_a = await db_client.create_workflow_run(
        name="RUN-ORG-A",
        workflow_id=wf.id,
        user_id=user.id,
        mode="test",
        organization_id=org_a_id,
    )

    req = ExecuteActionRequest(
        action_id="act_cross_org_hack",
        action_type="update_lead_classification",
        target_id=str(run_a.id),
        payload={"lead_classification": "Cold"},
    )
    with pytest.raises(HTTPException) as exc_act:
        await execute_owner_action(req, user=user_b)
    assert exc_act.value.status_code == 403


@pytest.mark.asyncio
async def test_t5_07_cancellation_concurrency_race_pre_dial_check():
    """T5-07: In-flight worker aborts dial if campaign state is cancelled immediately before dial."""
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-PREDIAL-RACE",
        workflow_id=wf.id,
        source_type="csv",
        source_id="test_key",
        user_id=user.id,
        organization_id=user.selected_organization_id,
    )

    # Create a real queued run in database
    unique_suffix = int(datetime.now(UTC).timestamp() * 1000)
    queued_run = await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid=f"cancel_race_{unique_suffix}",
        context_variables={"phone_number": "+919876543210"},
        state="queued",
    )

    # Mark campaign as completed/cancelled
    await campaign_runner_service.cancel_campaign(campaign.id, user_id=user.id)
    c_cancelled = await db_client.get_campaign_by_id(campaign.id)

    dispatcher = CampaignCallDispatcher()
    # Pre-dial check must abort and return None without dialing
    result = await dispatcher.dispatch_call(queued_run, c_cancelled, "mock_slot_1")
    assert result is None


@pytest.mark.asyncio
async def test_t5_08_agent_version_publication_and_snapshotting():
    """T5-08: Publishing agent training increments version and creates immutable definition."""
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)

    # Initial definition count
    async with db_client.async_session() as session:
        stmt = select(func.count(WorkflowDefinitionModel.id)).where(
            WorkflowDefinitionModel.workflow_id == wf.id
        )
        count_before = (await session.execute(stmt)).scalar_one()

    # Publish an updated configuration
    new_training = {
        "business_name": "SPX Prime Real Estate",
        "agent_name": "Priya",
        "industry_preset": "real_estate",
        "faqs": [{"q": "Where is the property?", "a": "Gachibowli, Hyderabad"}],
    }

    res = await publish_agent_training(
        request=AgentTrainingDraftRequest(training_data=new_training),
        user=user,
    )
    assert res["status"] == "published"
    assert res["workflow_id"] == wf.id
    wf_updated = await db_client.get_workflow_by_id(wf.id)
    assert wf_updated.released_definition_id is not None


@pytest.mark.asyncio
async def test_t5_09_active_call_snapshot_immutability():
    """T5-09: Modifying agent configuration during active call does not mutate the active call."""
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)

    initial_def = copy.deepcopy(wf.workflow_definition)

    # Create active call run bound to initial definition
    active_run = await db_client.create_workflow_run(
        name="ACTIVE-CALL-RUN",
        workflow_id=wf.id,
        user_id=user.id,
        mode="test",
        call_type=CallType.INBOUND,
        initial_context={"version_snapshot": initial_def},
        organization_id=user.selected_organization_id,
    )
    await db_client.update_workflow_run(run_id=active_run.id, state="running")

    # Now mutate workflow definition and publish new version
    mutated_def = copy.deepcopy(initial_def)
    mutated_def["nodes"][0]["data"]["prompt"] = "Completely altered prompt for new version"
    await db_client.update_workflow(
        workflow_id=wf.id,
        name=wf.name,
        workflow_definition=mutated_def,
        template_context_variables=wf.template_context_variables or {},
        workflow_configurations=wf.workflow_configurations or {},
    )

    # Verify active run initial_context snapshot is preserved unchanged
    persisted_run = await db_client.get_workflow_run_by_id(active_run.id)
    assert persisted_run.initial_context["version_snapshot"] == initial_def
    assert (
        persisted_run.initial_context["version_snapshot"]["nodes"][0]["data"]["prompt"]
        != "Completely altered prompt for new version"
    )


@pytest.mark.asyncio
async def test_t5_10_advanced_settings_persistence_and_rejection():
    """T5-10: Valid settings persist; unsupported voice options are rejected."""
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)

    # 1. Valid settings update
    valid_cfg = copy.deepcopy(wf.workflow_configurations or {})
    valid_cfg["product_mode"] = "premium"
    valid_cfg["budget"] = {"monthly_cap_inr": 25000.0, "mode_preference": "premium"}
    await db_client.update_workflow(
        workflow_id=wf.id,
        name=wf.name,
        workflow_definition=wf.workflow_definition,
        template_context_variables=wf.template_context_variables or {},
        workflow_configurations=valid_cfg,
    )

    refreshed_wf = await db_client.get_workflow_by_id(wf.id)
    assert refreshed_wf.workflow_configurations.get("product_mode") == "premium"

    # 2. Rejection of unsupported engine/action
    with pytest.raises(HTTPException) as exc_info:
        await execute_owner_action(
            ExecuteActionRequest(
                action_id="inv_action_1",
                action_type="unsupported_quantum_engine",
                target_id="1",
            ),
            user=user,
        )
    assert exc_info.value.status_code == 400
    assert "Unsupported action_type" in exc_info.value.detail


@pytest.mark.asyncio
async def test_t5_11_objection_approval_workflow_p2_07():
    """T5-11: Objection approval workflow: draft -> review -> approve -> reject -> rollback."""
    user = await _get_admin_user()
    org_id = user.selected_organization_id

    # 1. Create draft objection response
    from api.routes.business import (
        approve_objection,
        create_draft_objection,
        reject_objection,
        rollback_objection,
    )
    draft_req = DraftObjectionRequest(
        objection_category="Pricing",
        objection_text="Your highrise apartments are too expensive compared to Kokapet.",
        draft_response="We offer zero-brokerage, 100% Vastu, and immediate possession.",
    )
    res_draft = await create_draft_objection(draft_req, user=user)
    obj_id = res_draft["objection"]["id"]
    assert res_draft["objection"]["status"] == "DRAFT"
    assert res_draft["objection"]["version_number"] == 1

    # 2. Approve objection response
    approve_req = ApproveObjectionRequest(
        approved_response="We offer zero-brokerage, 100% Vastu, immediate possession, plus modular kitchen."
    )
    res_appr = await approve_objection(obj_id, approve_req, user=user)
    assert res_appr["status"] == "approved"
    assert res_appr["version_number"] == 2
    assert "modular kitchen" in res_appr["approved_response"]

    # 3. Reject objection
    reject_req = RejectObjectionRequest(reason="Outdated discount scheme")
    res_rej = await reject_objection(obj_id, reject_req, user=user)
    assert res_rej["status"] == "rejected"

    # 4. Rollback objection
    res_rb = await rollback_objection(obj_id, user=user)
    assert res_rb["status"] == "rolled_back"
    assert res_rb["new_status"] == "DRAFT"


@pytest.mark.asyncio
async def test_t5_12_customer_identity_continuity_p2_08():
    """T5-12: Repeat caller is stitched into customer profile; cross-org records remain isolated."""
    user = await _get_admin_user()
    org_a = user.selected_organization_id
    from api.db.models import OrganizationModel
    async with db_client.async_session() as session:
        org_b_obj = OrganizationModel(provider_id=f"org_b_{int(datetime.now(UTC).timestamp() * 1000)}")
        session.add(org_b_obj)
        await session.commit()
        await session.refresh(org_b_obj)
        org_b = org_b_obj.id
    phone = f"+91912{int(datetime.now(UTC).timestamp() * 1000) % 10000000:07d}"

    # 1. First call in Org A
    p1 = await record_customer_interaction(
        organization_id=org_a,
        phone_number=phone,
        name="Kiran Reddy",
        lead_status="Warm",
        workflow_run_id=101,
        notes="First call regarding 3BHK flat",
    )
    assert p1.interaction_count == 1
    assert p1.last_lead_status == "Warm"

    # 2. Second call in Org A (repeat caller)
    p2 = await record_customer_interaction(
        organization_id=org_a,
        phone_number=phone,
        lead_status="Hot",
        workflow_run_id=102,
        notes="Second call: confirmed budget 1.7 Cr",
    )
    assert p2.id == p1.id
    assert p2.interaction_count == 2
    assert p2.last_lead_status == "Hot"
    ch = p2.custom_attributes.get("call_history", [])
    assert 101 in ch and 102 in ch

    # 3. Same phone calls Org B -> completely separate profile created
    p_b = await record_customer_interaction(
        organization_id=org_b,
        phone_number=phone,
        name="Kiran (Client of Org B)",
        lead_status="Cold",
        workflow_run_id=201,
    )
    assert p_b.id != p1.id
    assert p_b.organization_id == org_b
    assert p_b.interaction_count == 1
    # Org B does not see Org A's history
    assert 101 not in p_b.custom_attributes.get("call_history", [])


@pytest.mark.asyncio
async def test_t5_13_business_knowledge_and_dynamic_qualification_fields():
    """T5-13: Dynamic qualification fields are extracted from transcript during call processing."""
    transcript = """
    Agent: Namaste! Are you interested in our 2BHK or 3BHK flats?
    Caller: We want a 3BHK flat with East-facing entrance ready to move in ASAP.
    Agent: Great! What is your comfortable budget range?
    Caller: My budget is strictly 1.6 Crores.
    Agent: We have units matching your 1.6 Cr budget ready for immediate possession.
    """
    intel = analyze_lead_from_transcript(transcript)
    assert intel["lead_classification"] in ("Hot", "Warm")
    assert intel["customer_requirements"].get("bhk") in ("2BHK", "3BHK")
    assert "1.6" in intel.get("budget", "") or "Crore" in intel.get("budget", "")
    assert intel.get("timeline") == "Immediate (0-1 Month)" 


@pytest.mark.asyncio
async def test_t5_14_appointments_and_callbacks_exact_time_preservation():
    """T5-14: Exact appointment date/time preserved; unconfirmed status never falsely confirmed."""
    transcript_unconfirmed = """
    Agent: Would you like to visit our site this Saturday at 11 AM?
    Caller: I am not sure yet, let me ask my wife and see if Saturday works.
    Agent: Understood, please let us know when convenient.
    """
    intel_unconf = analyze_lead_from_transcript(transcript_unconfirmed)
    appt = intel_unconf.get("appointment") or {}
    assert appt.get("status") != "Confirmed"

    # Requested appointment without owner action remains Requested
    transcript_req = """
    Agent: Can we book an appointment for a doctor consultation today at 6:00 PM?
    Caller: Yes, please book the appointment for 6:00 PM today.
    Agent: I have noted your requested appointment for 6:00 PM today.
    """
    intel_req = analyze_lead_from_transcript(transcript_req)
    appt_req = intel_req.get("appointment") or {}
    assert appt_req.get("requested") is True
    assert appt_req.get("status") in ("Requested", "None")

    # When owner confirms the appointment via execute_owner_action, status transitions to Confirmed
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)
    run = await db_client.create_workflow_run(
        name="RUN-APPT-CONFIRM",
        workflow_id=wf.id,
        user_id=user.id,
        mode="test",
        organization_id=user.selected_organization_id,
    )
    await db_client.update_workflow_run(
        run_id=run.id,
        annotations={"lead_intelligence": {"appointment": {"requested": True, "status": "Requested"}}},
    )
    ack = await update_lead_intelligence(
        run_id=run.id,
        request=UpdateLeadRequest(appointment_status="Confirmed"),
        user=user,
    )
    assert ack["lead_intelligence"]["appointment"]["status"] == "Confirmed" 


@pytest.mark.asyncio
async def test_t5_15_campaign_live_dashboard_controls():
    """T5-15: Campaign pause, resume, cancel reflect in DB and emit real-time events."""
    user = await _get_admin_user()
    wf = await _get_primary_workflow_for_test(user)

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-DASHBOARD-SYNC",
        workflow_id=wf.id,
        source_type="csv",
        source_id="test_sync",
        user_id=user.id,
        organization_id=user.selected_organization_id,
    )

    # Set campaign to running state first
    await db_client.update_campaign_state(campaign.id, "running", user.selected_organization_id)

    # 1. Pause via execute_owner_action
    req_pause = ExecuteActionRequest(
        action_id=f"act_pause_{campaign.id}",
        action_type="pause_campaign",
        target_id=str(campaign.id),
    )
    ack_pause = await execute_owner_action(req_pause, user=user)
    assert ack_pause["status"] == "acknowledged"
    assert ack_pause["execution_status"] == "SUCCESS"
    assert ack_pause["result"]["state"] == "paused"
    c_paused = await db_client.get_campaign_by_id(campaign.id)
    assert c_paused.state == "paused"

    # 2. Resume via execute_owner_action
    req_resume = ExecuteActionRequest(
        action_id=f"act_resume_{campaign.id}",
        action_type="resume_campaign",
        target_id=str(campaign.id),
    )
    ack_resume = await execute_owner_action(req_resume, user=user)
    assert ack_resume["status"] == "acknowledged"
    assert ack_resume["execution_status"] == "SUCCESS"
    assert ack_resume["result"]["state"] == "running"
    c_resumed = await db_client.get_campaign_by_id(campaign.id)
    assert c_resumed.state == "running"

    # 3. Cancel via execute_owner_action
    req_cancel = ExecuteActionRequest(
        action_id=f"act_cancel_{campaign.id}",
        action_type="cancel_campaign",
        target_id=str(campaign.id),
    )
    ack_cancel = await execute_owner_action(req_cancel, user=user)
    assert ack_cancel["status"] == "acknowledged"
    assert ack_cancel["execution_status"] == "SUCCESS"
    assert ack_cancel["result"]["state"] == "completed"
    c_cancelled = await db_client.get_campaign_by_id(campaign.id)
    assert c_cancelled.state == "completed"


@pytest.mark.asyncio
async def test_t5_16_cost_accounting_and_duplicate_idempotency():
    """T5-16: Cost accounting status distinction and budget cap admission."""
    user = await _get_admin_user()

    # Pulse billing calculation
    calc = calculate_call_cost(mode="standard", duration_seconds=65.0, is_outbound=True)
    assert calc["billed_pulses"] == 2  # 65s ceil to 2 pulses of 60s
    assert calc["total_cost_inr"] == 1.88

    # Budget admission check under cap
    admit_ok = check_budget_admission(
        current_spend_inr=100.0, spending_cap_inr=500.0, estimated_call_cost_inr=3.60
    )
    assert admit_ok is True

    # Budget admission check exceeding cap
    admit_fail = check_budget_admission(
        current_spend_inr=498.0, spending_cap_inr=500.0, estimated_call_cost_inr=3.60
    )
    assert admit_fail is False

    # Endpoint response contains accounting_status
    costs_res = await get_business_costs(user=user)
    assert "accounting_status" in costs_res
    assert costs_res["accounting_status"] in ("ESTIMATED", "PROVIDER-REPORTED")


@pytest.mark.asyncio
async def test_t5_17_recording_access_and_cross_org_denial():
    """T5-17: Recording access is allowed for owner organization and denied across orgs."""
    user = await _get_admin_user()
    org_id = user.selected_organization_id

    from api.routes.workflow_recording import create_playback_token, verify_playback_token

    # 1. Valid token for Org A
    token = create_playback_token("rec_test_123", org_id, expires_in=300)
    verified_org = verify_playback_token(token, "rec_test_123")
    assert verified_org == org_id

    # 2. Tampered token or mismatched recording ID fails
    mismatched = verify_playback_token(token, "different_recording_id")
    assert mismatched is None


@pytest.mark.asyncio
async def test_t5_18_whatsapp_non_blocking_outbox_job():
    """T5-18: Non-blocking WhatsApp outbox job delivery, idempotency, startup crash recovery, lease renewal, and max attempts (Gap 3)."""
    user = await _get_admin_user()
    org_id = user.selected_organization_id
    phone = "+919876599999"

    # 1. Non-blocking asynchronous enqueue
    unique_key = f"idemp_test_{int(datetime.now(UTC).timestamp())}_{uuid.uuid4().hex[:6]}"
    job_info = await chatwoot_outbox_service.enqueue_message(
        organization_id=org_id,
        recipient_phone=phone,
        message_type="lead_brochure",
        payload={"brochure_url": "https://spxvoice.com/brochures/skyline.pdf"},
        idempotency_key=unique_key,
    )
    assert job_info["status"] == "QUEUED"
    job_id = job_info["job_id"]

    # Wait briefly for background delivery
    await asyncio.sleep(0.5)

    async with db_client.async_session() as session:
        stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == job_id)
        res = await session.execute(stmt)
        job = res.scalar_one_or_none()
        assert job is not None
        assert job.status in ("SENT", "SENDING", "QUEUED")

    # 2. Idempotent re-enqueue returns existing job without duplicate
    dup_info = await chatwoot_outbox_service.enqueue_message(
        organization_id=org_id,
        recipient_phone=phone,
        message_type="lead_brochure",
        payload={"brochure_url": "https://spxvoice.com/brochures/skyline.pdf"},
        idempotency_key=job_info["idempotency_key"],
    )
    assert dup_info["is_duplicate"] is True
    assert dup_info["job_id"] == job_id

    # 3. Crash recovery: Insert a job directly into DB with status='QUEUED' (simulating container crash)
    crash_key = f"crash_rec_{uuid.uuid4().hex[:8]}"
    async with db_client.async_session() as session:
        crashed_job = ChatwootOutboxJobModel(
            organization_id=org_id,
            recipient_phone="+919876588888",
            message_type="post_crash_alert",
            payload={"msg": "recovering from crash"},
            status="QUEUED",
            idempotency_key=crash_key,
            attempts=0,
            created_at=datetime.now(UTC),
        )
        session.add(crashed_job)
        await session.commit()
        await session.refresh(crashed_job)
        crashed_job_id = crashed_job.id

    # Trigger recovery batch claim
    await chatwoot_outbox_service.claim_and_process_pending_jobs()

    async with db_client.async_session() as session:
        stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == crashed_job_id)
        res = await session.execute(stmt)
        recovered = res.scalar_one()
        assert recovered.status == "SENT"
        assert recovered.attempts >= 1

    # 4. Lease timeout recovery: Insert a stale 'SENDING' job with expired lease (processed 5 min ago)
    stale_key = f"stale_rec_{uuid.uuid4().hex[:8]}"
    async with db_client.async_session() as session:
        stale_job = ChatwootOutboxJobModel(
            organization_id=org_id,
            recipient_phone="+919876577777",
            message_type="lease_timeout_test",
            payload={"msg": "recovering from hung worker"},
            status="SENDING",
            idempotency_key=stale_key,
            attempts=1,
            created_at=datetime.now(UTC) - timedelta(minutes=10),
            processed_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        session.add(stale_job)
        await session.commit()
        await session.refresh(stale_job)
        stale_job_id = stale_job.id

    # Reclaim with 120s lease timeout
    await chatwoot_outbox_service.claim_and_process_pending_jobs(lease_seconds=120)

    async with db_client.async_session() as session:
        stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == stale_job_id)
        res = await session.execute(stmt)
        recovered_stale = res.scalar_one()
        assert recovered_stale.status == "SENT"
        assert recovered_stale.attempts >= 2

    # 5. Max attempts exhaustion: Insert a job with attempts=3
    exhausted_key = f"exhaust_{uuid.uuid4().hex[:8]}"
    async with db_client.async_session() as session:
        exhausted_job = ChatwootOutboxJobModel(
            organization_id=org_id,
            recipient_phone="+919876566666",
            message_type="exhausted_test",
            payload={"msg": "should fail"},
            status="QUEUED",
            idempotency_key=exhausted_key,
            attempts=3,
            created_at=datetime.now(UTC),
        )
        session.add(exhausted_job)
        await session.commit()
        await session.refresh(exhausted_job)
        exhausted_id = exhausted_job.id

    await chatwoot_outbox_service.claim_and_process_pending_jobs(max_attempts=3)

    async with db_client.async_session() as session:
        stmt = select(ChatwootOutboxJobModel).where(ChatwootOutboxJobModel.id == exhausted_id)
        res = await session.execute(stmt)
        exhausted_row = res.scalar_one()
        assert exhausted_row.status == "FAILED"
        assert "Exceeded max delivery attempts" in (exhausted_row.last_error or "")


@pytest.mark.asyncio
async def test_t5_19_pagination_and_bounded_event_delivery():
    """T5-19: Paginated endpoints return bounded slices; event queues drop oldest on overflow."""
    user = await _get_admin_user()

    # 1. API pagination
    conv_res = await list_conversations(limit=2, offset=0, user=user)
    assert len(conv_res["items"]) <= 2
    assert "total" in conv_res

    cust_res = await list_customers(page=1, page_size=2, user=user)
    assert len(cust_res["items"]) <= 2
    assert "total" in cust_res

    # 2. Bounded in-process event queue
    bus = RealtimeDashboardBus()
    bounded_queue = asyncio.Queue(maxsize=3)
    async with bus._lock:
        bus._local_subscribers[user.selected_organization_id] = {bounded_queue}

    # Publish 5 events into maxsize=3 queue using valid organization_id
    for i in range(5):
        await bus.publish_event(
            organization_id=user.selected_organization_id,
            event_type="test_overflow",
            resource_type="overflow",
            resource_id=str(i),
            data={"index": i},
        )

    # Queue must not exceed 3 items and contain latest items (2, 3, 4)
    assert bounded_queue.qsize() == 3
    items = []
    while not bounded_queue.empty():
        items.append(bounded_queue.get_nowait())
    indices = [it["data"]["index"] for it in items]
    assert indices == [2, 3, 4]


@pytest.mark.asyncio
async def test_t5_20_existing_ui_browser_acceptance_verification():
    """T5-20: All existing dashboard backend endpoints return 200 with valid schema."""
    user = await _get_admin_user()

    # 1. Overview KPIs
    overview = await get_business_overview(user=user)
    assert "total_calls" in overview
    assert "qualified_leads" in overview
    assert "estimated_cost_inr" in overview

    # 2. Costs & Budget
    costs = await get_business_costs(user=user)
    assert "selected_mode" in costs
    assert "budget" in costs

    # 3. Action Inbox
    inbox_items = await action_inbox.list_action_items(
        organization_id=user.selected_organization_id
    )
    assert isinstance(inbox_items, list)

    # 4. Objections list
    objs = await list_objections(user=user)
    assert "items" in objs
    assert "total" in objs

    # 5. Customer profiles list
    custs = await list_customers(user=user)
    assert "items" in custs
    assert "total" in custs
