"""Phase 4 Bulk Campaigns, Concurrency, Smart DID Safety & Real-Time SaaS Suite.

Validates the 16 required Phase 4 acceptance behaviors (T4-01 through T4-16):
- T4-01: 20,000-contact synthetic import & metrics (bounded chunking, no auto-dial)
- T4-02: Idempotent re-import preserves recipients without duplicate queued runs
- T4-03: Consent, DND & permanent suppression enforcement before dispatch
- T4-04: Campaign publish, scheduling windows, and draft isolation
- T4-05: Multiworker concurrency and SELECT FOR UPDATE SKIP LOCKED
- T4-06: Vobiz-first provider configuration & Smart DID capacity limits
- T4-07: Controlled retries with bounded backoff; no retry on refusal/opt-out
- T4-08: Automatic campaign safety pause via circuit breaker
- T4-09: Manual pause, resume, cancel, and emergency stop controls
- T4-10: Worker failure recovery and no orphaned queued runs
- T4-11: Callback time-of-day preservation ("tomorrow at 3 PM" -> 15:00 IST)
- T4-12: Script version pinning and active call definition immutability
- T4-13: Multi-tenant isolation for campaigns, recipients, and DIDs
- T4-14: Existing SaaS dashboard integration & stats persistence
- T4-15: Financial budget admission and spending cap protections
- T4-16: Campaign objection outcomes and analytics integrity
"""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from api.db import db_client
from api.db.models import CampaignModel, QueuedRunModel, WorkflowRunModel
from api.enums import CallType, WorkflowRunMode, WorkflowRunState
from api.services.business.callback_scheduler import (
    IST_TZ,
    parse_indian_relative_callback_time,
)
from api.services.business.lead_intelligence import analyze_lead_from_transcript
from api.services.campaign.campaign_call_dispatcher import (
    CampaignCallDispatcher,
    campaign_call_dispatcher,
)
from api.services.campaign.campaign_event_protocol import (
    CircuitBreakerTrippedEvent,
    RetryNeededEvent,
)
from api.services.campaign.campaign_orchestrator import CampaignOrchestrator
from api.services.campaign.circuit_breaker import circuit_breaker
from api.services.campaign.rate_limiter import rate_limiter
from api.services.campaign.runner import campaign_runner_service
from api.services.campaign.sources.csv import CSVSyncService
from api.services.pricing.cost_controller import (
    BudgetConfig,
    VoiceProductMode,
    calculate_comprehensive_call_cost,
    check_financial_admission,
)
from api.services.telephony.compliance import (
    compliance_manager,
    normalize_phone_number,
)
from api.services.telephony.smart_did import (
    NumberHealthState,
    smart_did_controller,
)


@pytest.fixture(autouse=True)
def clean_campaign_state():
    """Reset compliance, Smart DID controller, and rate limiter state between tests."""
    compliance_manager.clear()
    smart_did_controller.clear()
    yield
    compliance_manager.clear()
    smart_did_controller.clear()


# ==============================================================================
# T4-01 & T4-02: 20,000-Contact Synthetic Import & Idempotent Re-Import
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_01_synthetic_20k_import_and_metrics():
    """T4-01: 20,000-contact synthetic import processes in bounded chunks without dialing calls."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    assert user is not None
    org_id = user.selected_organization_id

    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    assert len(workflows) > 0
    wf_id = workflows[0].id

    # Create test campaign
    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-20K-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="test_20k_source_key",
        user_id=user.id,
        organization_id=org_id,
    )

    # 1. Add 1 contact to DNC registry to verify suppression during 20k import
    suppressed_phone = "+919876500001"
    compliance_manager.add_to_dnc(
        org_id, suppressed_phone, reason="DNC Customer Opt-out", source="opt_out"
    )

    # 2. Build 20,000 synthetic rows + 1 header:
    # - 19,998 unique valid rows
    # - 1 suppressed row
    # - 1 in-batch duplicate row
    headers = ["phone_number", "first_name", "city"]
    rows = []
    # Suppressed row
    rows.append([suppressed_phone, "Suppressed User", "Mumbai"])
    # In-batch duplicate of the first valid user
    rows.append(["+919876500002", "Duplicate User", "Delhi"])
    rows.append(["+919876500002", "Duplicate User Copy", "Delhi"])
    # Remaining unique rows up to 20,000
    for i in range(3, 20001):
        rows.append([f"+91987{i:07d}", f"User {i}", "Bangalore"])

    csv_data = [headers] + rows
    sync_service = CSVSyncService()

    with patch.object(sync_service, "_fetch_csv_data", return_value=csv_data):
        inserted_count = await sync_service.sync_source_data(campaign.id)

    # Verification:
    # 20,000 rows - 1 suppressed - 1 in-batch duplicate = 19,999 valid queued runs
    assert inserted_count == 19999

    # Verify campaign total_rows in DB
    updated_campaign = await db_client.get_campaign_by_id(campaign.id)
    assert updated_campaign.total_rows == 19999
    assert updated_campaign.source_sync_status == "completed"

    # CRITICAL: Verify ZERO telephone calls / workflow runs were initiated during import!
    call_count = await db_client.get_campaign_runs(campaign.id, org_id)
    assert len(call_count) == 0


@pytest.mark.asyncio
async def test_t4_02_idempotent_reimport_preserves_recipients():
    """T4-02: Re-running sync on an already-synced campaign is idempotent and creates 0 duplicates."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-IDEMPOTENT-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="test_idempotent_source_key",
        user_id=user.id,
        organization_id=org_id,
    )

    headers = ["phone_number", "first_name"]
    rows = [[f"+9198888{i:05d}", f"User {i}"] for i in range(10)]
    csv_data = [headers] + rows
    sync_service = CSVSyncService()

    with patch.object(sync_service, "_fetch_csv_data", return_value=csv_data):
        first_insert = await sync_service.sync_source_data(campaign.id)
        assert first_insert == 10

        # Re-import identical data
        second_insert = await sync_service.sync_source_data(campaign.id)
        assert second_insert == 0  # Zero new records inserted!

    # Authoritative DB count remains exactly 10
    total_in_db = await db_client.count_queued_runs(campaign.id)
    assert total_in_db == 10


# ==============================================================================
# T4-03: Consent, DND & Permanent Suppression Pre-Flight
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_03_consent_and_dnd_suppression_enforcement():
    """T4-03: Contacts on DNC registry are rejected before outbound call initiation."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-DND-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )

    dnc_phone = "+919876543299"
    compliance_manager.add_to_dnc(
        org_id, dnc_phone, reason="Customer DNC Opt-Out via SMS", source="opt_out"
    )

    queued_run = await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="csv_row_dnc_1",
        context_variables={"phone_number": dnc_phone, "first_name": "OptedOut"},
        state="queued",
    )

    dispatcher = CampaignCallDispatcher()
    # Mock slot acquisition
    slot_id = "test_slot_001"
    with patch.object(rate_limiter, "release_concurrent_slot", new=AsyncMock(return_value=True)):
        result = await dispatcher.dispatch_call(queued_run, campaign, slot_id)

    # Call dispatch blocked
    assert result is None

    # Queued run marked as failed with dnc_suppressed reason in DB
    updated_run = await db_client.get_queued_run_by_id(queued_run.id)
    assert updated_run.state == "failed"
    assert "dnc_suppressed" in (updated_run.retry_reason or "")


# ==============================================================================
# T4-04: Campaign Scheduling, Windows, and Draft Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_04_campaign_publish_scheduling_and_draft_isolation():
    """T4-04: Campaign in created/draft state does not dispatch calls; schedule windows respected."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    # 1. Draft/created state
    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-DRAFT-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )
    assert campaign.state == "created"

    dispatcher = CampaignCallDispatcher()
    processed = await dispatcher.process_batch(campaign.id, batch_size=5)
    assert processed == 0  # Created/draft campaigns never process or dispatch calls!

    # 2. Schedule window evaluation
    orchestrator = CampaignOrchestrator(redis_client=MagicMock())
    # Configure calling window: 10:00 to 18:00 IST on weekdays
    campaign.orchestrator_metadata = {
        "schedule_config": {
            "enabled": True,
            "timezone": "Asia/Kolkata",
            "slots": [
                {
                    "day_of_week": 0,  # Monday
                    "start_time": "10:00",
                    "end_time": "18:00",
                }
            ],
        }
    }

    # Frozen time inside slot: Monday 14:00 IST
    monday_open = datetime(2026, 10, 19, 14, 0, 0, tzinfo=IST_TZ)
    with patch("api.services.campaign.campaign_orchestrator.datetime") as mock_dt:
        mock_dt.now.return_value = monday_open
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        is_open = orchestrator._is_within_schedule(campaign)
        assert is_open is True

    # Frozen time outside slot: Monday 21:00 IST (after hours)
    monday_closed = datetime(2026, 10, 19, 21, 0, 0, tzinfo=IST_TZ)
    with patch("api.services.campaign.campaign_orchestrator.datetime") as mock_dt:
        mock_dt.now.return_value = monday_closed
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
        is_open = orchestrator._is_within_schedule(campaign)
        assert is_open is False


# ==============================================================================
# T4-05: Multiworker Concurrency & SELECT FOR UPDATE SKIP LOCKED
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_05_multiworker_concurrency_and_skip_locked():
    """T4-05: Multiple concurrent workers claim disjoint queued runs without race conditions."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-CONCURRENCY-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )

    # Create 5 queued runs
    for i in range(5):
        await db_client.create_queued_run(
            campaign_id=campaign.id,
            source_uuid=f"concurrent_test_row_{i}",
            context_variables={"phone_number": f"+9198760000{i}"},
            state="queued",
        )

    # Worker 1 claims 3 runs, Worker 2 claims 3 runs concurrently
    now = datetime.now(UTC)
    w1_runs, w2_runs = await asyncio.gather(
        db_client.claim_queued_runs_for_processing(campaign.id, scheduled_before=now, limit=3),
        db_client.claim_queued_runs_for_processing(campaign.id, scheduled_before=now, limit=3),
    )

    w1_ids = {r.id for r in w1_runs}
    w2_ids = {r.id for r in w2_runs}

    # Assert workers claimed disjoint sets of runs (zero overlap / zero double-dialing)
    assert len(w1_ids.intersection(w2_ids)) == 0
    assert len(w1_ids) + len(w2_ids) == 5


# ==============================================================================
# T4-06: Vobiz Provider First & Smart DID Capacity
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_06_provider_smart_did_capacity_vobiz_first():
    """T4-06: Resolves Vobiz provider by default and enforces Smart DID channel limits."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-PROVIDER-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )

    dispatcher = CampaignCallDispatcher()
    provider = await dispatcher.get_provider_for_campaign(campaign)
    # Default is Vobiz
    assert provider.PROVIDER_NAME == "vobiz"

    # Capacity enforcement: DID capacity = 1
    did = "+919876543210"
    smart_did_controller.register_did(org_id, did)
    smart_did_controller.record_call_start(org_id, did)

    sel_num, err = smart_did_controller.select_caller_id(
        org_id, [did], max_concurrency_per_did=1
    )
    assert sel_num is None
    assert "maximum capacity" in (err or "")


# ==============================================================================
# T4-07: Safe Retries & Bounded Backoff
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_07_safe_retries_and_bounded_backoff():
    """T4-07: Technical failures trigger scheduled retry with backoff; max retries respected."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-RETRY-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
        retry_config={
            "enabled": True,
            "max_retries": 1,
            "retry_on_busy": True,
            "retry_delay_seconds": 60,
        },
    )

    queued_run = await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="retry_test_001",
        context_variables={"phone_number": "+919988771122"},
        state="processing",
        retry_count=0,
    )

    orchestrator = CampaignOrchestrator(redis_client=MagicMock())

    # 1. First failure on busy -> schedules retry
    event_busy = RetryNeededEvent(
        campaign_id=campaign.id,
        queued_run_id=queued_run.id,
        reason="busy",
    )
    await orchestrator._handle_retry_event(event_busy)

    # Verify scheduled retry was created (state="queued", retry_count=1)
    queued_count = await db_client.count_queued_runs(campaign.id, state="queued")
    assert queued_count == 1

    # Query the retry run that was automatically created by _schedule_retry
    async with db_client.async_session() as session:
        from sqlalchemy.future import select
        res = await session.execute(
            select(QueuedRunModel).where(
                QueuedRunModel.campaign_id == campaign.id,
                QueuedRunModel.parent_queued_run_id == queued_run.id,
            )
        )
        retry_run = res.scalar_one()

    # 2. Second failure when retry_count == max_retries (1) -> marks final failure
    event_max = RetryNeededEvent(
        campaign_id=campaign.id,
        queued_run_id=retry_run.id,
        reason="busy",
    )
    await orchestrator._handle_retry_event(event_max)

    # No further retry scheduled
    total_queued = await db_client.count_queued_runs(campaign.id, state="queued")
    assert total_queued == 1  # Still 1, no second retry added


# ==============================================================================
# T4-08: Automatic Campaign Safety Pause via Circuit Breaker
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_08_automatic_safety_pause_circuit_breaker():
    """T4-08: High consecutive failure rate trips the circuit breaker into paused state."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-CB-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
        circuit_breaker={
            "enabled": True,
            "min_calls_in_window": 3,
            "failure_threshold": 0.5,
            "window_seconds": 60,
        },
    )
    await db_client.update_campaign_state(campaign.id, "running", org_id)

    # Record 4 consecutive failures
    for i in range(4):
        await circuit_breaker.record_and_evaluate(
            campaign_id=campaign.id,
            is_failure=True,
            workflow_run_id=100 + i,
            reason="sip_503_service_unavailable",
        )

    # Verify campaign was safely moved to paused state by circuit breaker
    tripped_campaign = await db_client.get_campaign_by_id(campaign.id)
    assert tripped_campaign.state == "paused"


# ==============================================================================
# T4-09: Manual Pause, Resume, Cancel & Emergency Stop
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_09_manual_pause_resume_cancel_controls():
    """T4-09: Authenticated pause, resume, and cancel commands transition campaign states."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-CONTROLS-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )
    await db_client.update_campaign_state(campaign.id, "running", org_id)

    # 1. Pause
    await campaign_runner_service.pause_campaign(campaign.id)
    c_paused = await db_client.get_campaign_by_id(campaign.id)
    assert c_paused.state == "paused"

    # 2. Resume
    await campaign_runner_service.resume_campaign(campaign.id)
    c_resumed = await db_client.get_campaign_by_id(campaign.id)
    assert c_resumed.state == "running"

    # Create a pending queued run AND a claimed in-flight run to verify cancellation transitions both
    q_run = await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="cancel_test_row_1",
        context_variables={"phone_number": "+919876543210"},
        state="queued",
    )
    q_inflight = await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="cancel_test_row_2_inflight",
        context_variables={"phone_number": "+919876543211"},
        state="processing",
    )

    # 3. Cancel with authenticated user_id and explicit reason
    await campaign_runner_service.cancel_campaign(
        campaign.id, user_id=user.id, reason="operator_cancelled_test"
    )
    c_cancelled = await db_client.get_campaign_by_id(campaign.id)
    # Persisted DB enum preserves 'completed'
    assert c_cancelled.state == "completed"

    # Durable auditability: metadata and logs preserve actor, reason, and timestamp
    meta = c_cancelled.orchestrator_metadata or {}
    assert meta.get("cancellation_reason") == "operator_cancelled_test"
    assert meta.get("cancelled_by_user_id") == user.id
    assert "cancelled_at" in meta
    cancel_logs = [
        entry for entry in (c_cancelled.logs or [])
        if isinstance(entry, dict) and entry.get("event") == "campaign_cancelled"
    ]
    assert len(cancel_logs) >= 1
    assert cancel_logs[0]["details"]["cancelled_by_user_id"] == user.id

    # Verify both queued and in-flight processing runs are marked failed with campaign_cancelled
    q_cancelled = await db_client.get_queued_run_by_id(q_run.id)
    assert q_cancelled.state == "failed"
    assert q_cancelled.retry_reason == "campaign_cancelled"

    q_inflight_cancelled = await db_client.get_queued_run_by_id(q_inflight.id)
    assert q_inflight_cancelled.state == "failed"
    assert q_inflight_cancelled.retry_reason == "campaign_cancelled"

    # Verify API / runner status distinguishes 'cancelled' from successfully 'completed'
    status = await campaign_runner_service.get_campaign_status(campaign.id)
    assert status["state"] == "cancelled"
    # Uncalled cancelled contacts do NOT inflate carrier failure metrics
    assert status["failed_calls"] == 0
    assert c_cancelled.failed_rows == 0

    # Race condition protection: dispatcher immediately aborts dialing if campaign is cancelled
    dispatcher = CampaignCallDispatcher()
    dispatched_run = await dispatcher.dispatch_call(q_cancelled, c_cancelled, "mock_slot_1")
    assert dispatched_run is None  # Aborted dial, zero carrier call created


# ==============================================================================
# T4-10: Worker Failure and Queue Recovery
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_10_worker_failure_and_queue_recovery():
    """T4-10: On unhandled worker exception, queued runs are safely reverted to prevent stuck state."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-CAMPAIGN-RECOVERY-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )
    await db_client.update_campaign_state(campaign.id, "running", org_id)

    queued_run = await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="recovery_run_01",
        context_variables={"phone_number": "+919876543210"},
        state="queued",
    )

    dispatcher = CampaignCallDispatcher()
    # Mock dispatch_call to raise an exception simulating worker failure
    with patch.object(dispatcher, "dispatch_call", side_effect=RuntimeError("Worker socket timeout")):
        with patch.object(dispatcher, "acquire_concurrent_slot", new=AsyncMock(return_value="slot_rec")):
            with patch.object(dispatcher, "apply_rate_limit", new=AsyncMock()):
                await dispatcher.process_batch(campaign.id, batch_size=1)

    # Queued run was handled and marked failed (not stuck in processing)
    rec_run = await db_client.get_queued_run_by_id(queued_run.id)
    assert rec_run.state == "failed"


# ==============================================================================
# T4-11: Callback Time-of-Day Preservation ("Tomorrow at 3 PM")
# ==============================================================================
def test_t4_11_callback_time_of_day_preservation():
    """T4-11: Verifies 'tomorrow at 3 PM' preserves both date and time (15:00 IST)."""
    transcript = (
        "Agent: Hello, I am calling from SPX Voice.\n"
        "Caller: I am busy right now, please call me tomorrow at 3 PM.\n"
        "Agent: Understood, I will schedule that callback."
    )

    lead_intel = analyze_lead_from_transcript(transcript)

    callback_data = lead_intel.get("callback", {})
    assert callback_data.get("requested") is True
    assert callback_data.get("status") == "Pending"

    # Verify time-of-day is preserved in preferred_slot!
    preferred_slot = callback_data.get("preferred_slot")
    assert "Tomorrow At 3 Pm" in preferred_slot

    # Verify structured scheduled_time has hour=15 (3 PM)
    scheduled_iso = callback_data.get("scheduled_time")
    assert scheduled_iso is not None
    scheduled_dt = datetime.fromisoformat(scheduled_iso)
    assert scheduled_dt.hour == 15
    assert scheduled_dt.minute == 0


# ==============================================================================
# T4-12: Script Version Pinning on Dispatched Calls
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_12_script_version_pinning_immutability():
    """T4-12: Workflow runs pin the exact released_definition_id at creation."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf = workflows[0]

    run = await db_client.create_workflow_run(
        name="TEST-CAMPAIGN-PINNING-001",
        workflow_id=wf.id,
        mode="vobiz",
        user_id=user.id,
        call_type=CallType.OUTBOUND,
        initial_context={"called_number": "+919988776655"},
        organization_id=org_id,
    )

    target_wf = await db_client.get_workflow_by_id(wf.id)
    # Pinned definition matches workflow's released definition ID
    assert run.definition_id == target_wf.released_definition_id
    assert run.definition_id is not None


# ==============================================================================
# T4-13: Multi-Tenant Campaign Isolation
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_13_multi_tenant_campaign_isolation():
    """T4-13: Campaigns belonging to Org A cannot be fetched or manipulated by Org B."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_a = user.selected_organization_id
    org_b = 99999
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_a)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-TENANT-ISOLATION-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_a,
    )

    # Org B attempts to fetch Org A's campaign -> fails closed (returns None)
    c_isolated = await db_client.get_campaign(campaign.id, organization_id=org_b)
    assert c_isolated is None


# ==============================================================================
# T4-14: Existing SaaS Dashboard Integration
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_14_existing_dashboard_stats_persistence():
    """T4-14: Authoritative queued and executed run stats are persisted and exposed for the UI."""
    user = await db_client.get_user_by_email("admin@spxvoice.com")
    org_id = user.selected_organization_id
    workflows = await db_client.get_all_workflows_for_listing(organization_id=org_id)
    wf_id = workflows[0].id

    campaign = await db_client.create_campaign(
        name="TEST-DASHBOARD-STATS-001",
        workflow_id=wf_id,
        source_type="csv",
        source_id="dummy",
        user_id=user.id,
        organization_id=org_id,
    )

    # 1 queued, 1 processed
    await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="dash_run_1",
        context_variables={"phone_number": "+919876500001"},
        state="queued",
    )
    await db_client.create_queued_run(
        campaign_id=campaign.id,
        source_uuid="dash_run_2",
        context_variables={"phone_number": "+919876500002"},
        state="processed",
    )

    stats_map = await db_client.get_queued_runs_stats_for_campaigns([campaign.id])
    s = stats_map.get(campaign.id, {})
    assert s.get("total") == 2
    assert s.get("executed") == 1


# ==============================================================================
# T4-15: Financial Budget Admission and Spending Caps
# ==============================================================================
def test_t4_15_financial_budget_admission_and_emergency_stop():
    """T4-15: Verifies call admission blocks when spend caps or emergency stop are breached."""
    budget = BudgetConfig(
        daily_cap_inr=100.0,
        monthly_cap_inr=500.0,
        per_call_cap_inr=100.0,
        emergency_stop_active=False,
    )

    # 1. Within budget
    admitted_1, reason_1 = check_financial_admission(
        budget=budget,
        current_day_spend_inr=50.0,
        current_month_spend_inr=450.0,
        estimated_call_cost_inr=10.0,
    )
    assert admitted_1 is True
    assert reason_1 == "Call admitted."

    # 2. Exceeds daily cap (50 + 60 = 110 > 100)
    admitted_2, reason_2 = check_financial_admission(
        budget=budget,
        current_day_spend_inr=50.0,
        current_month_spend_inr=100.0,
        estimated_call_cost_inr=60.0,
    )
    assert admitted_2 is False
    assert "Daily spend cap" in reason_2

    # 3. Emergency stop active
    budget_stopped = BudgetConfig(emergency_stop_active=True)
    admitted_3, reason_3 = check_financial_admission(
        budget=budget_stopped,
        current_day_spend_inr=0.0,
        current_month_spend_inr=0.0,
        estimated_call_cost_inr=1.0,
    )
    assert admitted_3 is False
    assert "Emergency stop active" in reason_3


# ==============================================================================
# T4-16: Campaign Objection Outcomes and Analytics Integrity
# ==============================================================================
@pytest.mark.asyncio
async def test_t4_16_campaign_objection_outcomes_and_analytics():
    """T4-16: Genuinely captures and aggregates objection categories and qualification."""
    transcript = (
        "Agent: Hello, I am calling from SPX Voice with a premium offering.\n"
        "Caller: Your price is way too expensive, our budget is completely full right now.\n"
        "Agent: I understand budget is tight. What rate would fit your plan?"
    )

    lead_intel = analyze_lead_from_transcript(transcript)
    assert "PRICE" in lead_intel.get("objection_categories", [])
    assert len(lead_intel.get("objections", [])) > 0
    assert lead_intel.get("lead_classification") in ["Cold", "Warm", "Hot"]
