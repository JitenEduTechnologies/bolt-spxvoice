"""Package 1: product-mode mapping and /costs/budget guard (pure unit tests).

No database, Redis, network or provider access: any route or reporting database
interaction exercised here uses isolated mocks.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from api.services.business.agent_training import (
    SUPPORTED_PRODUCT_MODES,
    check_budget_mode_request,
    normalize_product_mode,
    resolve_product_mode_overrides,
)

# Standard/budget Gemini override. Kept under the historical name; the model is
# the current default (CP-01 Unit 1 bumped it from 3.1 to 3.8).
LEGACY_GEMINI = {
    "is_realtime": True,
    "realtime": {
        "provider": "google_realtime",
        "model": "gemini-3.8-live",
        "voice": "Kore",
        "language": "en",
    },
}

LEGACY_PREMIUM = {
    "is_realtime": False,
    "llm": {
        "provider": "deepseek",
        "model": "deepseek-chat",
        "base_url": "https://api.deepseek.com",
    },
    "tts": {
        "provider": "cartesia",
        "model": "sonic-3",
        "voice": "248be419-c632-4f23-adf1-5324ed7dbf10",
    },
    "stt": {
        "provider": "deepgram",
        "model": "nova-3-general",
        "language": "en",
    },
}


def test_standard_mapping_is_unchanged():
    assert resolve_product_mode_overrides("standard") == ("standard", LEGACY_GEMINI)


def test_premium_mapping_is_unchanged():
    assert resolve_product_mode_overrides("premium") == ("premium", LEGACY_PREMIUM)


def test_budget_keeps_legacy_explicit_gemini_overrides():
    assert resolve_product_mode_overrides("budget") == ("budget", LEGACY_GEMINI)


def test_economy_ivr_has_no_model_overrides_and_is_not_budget():
    mode, overrides = resolve_product_mode_overrides("economy_ivr")
    assert mode == "economy_ivr"
    assert overrides is None
    assert normalize_product_mode("economy_ivr") != normalize_product_mode("budget")


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_blank_mode_defaults_to_standard(raw):
    assert resolve_product_mode_overrides(raw) == ("standard", LEGACY_GEMINI)


def test_mode_is_case_and_space_insensitive():
    assert normalize_product_mode(" Premium ") == "premium"
    assert normalize_product_mode("BUDGET") == "budget"


@pytest.mark.parametrize("raw", ["foo", "gemini", "ivr", "economy", "budget_ivr", "premium2"])
def test_unknown_modes_are_rejected(raw):
    with pytest.raises(ValueError, match="Unsupported voice mode"):
        resolve_product_mode_overrides(raw)


def test_supported_modes_are_exactly_the_existing_identifiers():
    assert SUPPORTED_PRODUCT_MODES == ("standard", "premium", "budget", "economy_ivr")


def test_returned_overrides_are_independent_copies():
    _, first = resolve_product_mode_overrides("standard")
    first["realtime"]["voice"] = "mutated"
    _, second = resolve_product_mode_overrides("standard")
    assert second["realtime"]["voice"] == "Kore"


def test_budget_mode_request_check():
    check_budget_mode_request(None, "premium")
    check_budget_mode_request("", "premium")
    check_budget_mode_request("Premium", "premium")
    check_budget_mode_request("standard", None)  # absent current == standard
    with pytest.raises(ValueError):
        check_budget_mode_request("premium", "standard")
    with pytest.raises(ValueError):
        check_budget_mode_request("economy_ivr", "budget")


# --- /costs/budget route: rejection paths happen before any DB access -------


def _patch_workflow(monkeypatch, business, configs):
    async def _fake_primary_workflow(user, workflow_id=None):
        return SimpleNamespace(id=1, workflow_configurations=configs)

    monkeypatch.setattr(business, "_get_primary_workflow", _fake_primary_workflow)


def _user():
    return SimpleNamespace(
        id=1, selected_organization_id=1, is_superuser=False, provider_id="test-user"
    )


@pytest.mark.asyncio
async def test_costs_budget_rejects_mode_change_with_409(monkeypatch):
    from api.routes import business

    _patch_workflow(monkeypatch, business, {"product_mode": "standard"})
    body = business.BudgetUpdateRequest(mode_preference="premium")
    with pytest.raises(HTTPException) as exc:
        await business.update_business_budget(body, user=_user())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [0, -5, float("inf"), float("nan")])
async def test_costs_budget_rejects_invalid_caps_with_422(monkeypatch, bad):
    from api.routes import business

    _patch_workflow(monkeypatch, business, {"product_mode": "standard"})
    body = business.BudgetUpdateRequest(daily_cap_inr=bad)
    with pytest.raises(HTTPException) as exc:
        await business.update_business_budget(body, user=_user())
    assert exc.value.status_code == 422


def test_budget_request_defaults_never_request_a_mode():
    from api.routes import business

    body = business.BudgetUpdateRequest()
    assert body.mode_preference is None
    # The Costs page sends tier_preference; it must still validate.
    ui_body = business.BudgetUpdateRequest(
        daily_cap_inr=500, monthly_cap_inr=10000, per_call_cap_inr=25,
        tier_preference="premium",
    )
    assert ui_body.tier_preference == "premium"
    assert ui_body.mode_preference is None


# --- LiveKit worker guard: economy_ivr must never start an AI session --------


def _patch_worker_entrypoint(
    monkeypatch,
    product_mode,
    *,
    max_call_duration_seconds=60,
    call_type="web",
):
    """Mock everything entrypoint() touches before model resolution."""
    from unittest.mock import AsyncMock, MagicMock, Mock

    from api.services.livekit import worker

    ctx = SimpleNamespace(
        job=SimpleNamespace(id="job-1"),
        room=SimpleNamespace(name="room-1"),
        connect=AsyncMock(),
        wait_for_participant=AsyncMock(return_value=SimpleNamespace()),
        delete_room=AsyncMock(),
        shutdown=Mock(),
    )
    definition = SimpleNamespace(
        workflow_configurations={
            "product_mode": product_mode,
            "max_call_duration_seconds": max_call_duration_seconds,
        },
        workflow_json={},
    )
    db = SimpleNamespace(
        update_workflow_run=AsyncMock(),
        get_workflow_run=AsyncMock(return_value=SimpleNamespace(definition=definition)),
        get_workflow=AsyncMock(return_value=SimpleNamespace(id=1)),
        get_user_configurations=AsyncMock(return_value=SimpleNamespace()),
        has_active_recordings=AsyncMock(return_value=False),
    )
    past_guard = RuntimeError("reached model resolution")
    resolve = Mock(side_effect=past_guard)
    create_session = Mock()
    record_failure = AsyncMock()

    monkeypatch.setattr(worker, "db_client", db)
    monkeypatch.setattr(
        worker, "_metadata_from_job", lambda c: {"call_type": call_type}
    )
    monkeypatch.setattr(worker, "_metadata_from_room", lambda c: {})
    monkeypatch.setattr(worker, "_participant_context", lambda p: {})
    monkeypatch.setattr(
        worker, "_resolve_or_create_workflow_run", AsyncMock(return_value=(1, 2, 3, 4, {}))
    )
    monkeypatch.setattr(worker, "resolve_effective_config", resolve)
    monkeypatch.setattr(worker, "_create_session", create_session)
    monkeypatch.setattr(worker, "_record_livekit_startup_failure", record_failure)
    return worker, ctx, resolve, create_session, record_failure


@pytest.mark.asyncio
async def test_worker_blocks_economy_ivr_before_any_model_resolution(monkeypatch):
    worker, ctx, resolve, create_session, record_failure = _patch_worker_entrypoint(
        monkeypatch, " Economy_IVR "
    )
    # entrypoint() now raises a sanitized LiveKitStartupError in place of the
    # original ValueError -- see LiveKitStartupError / the SDK re-raise fix.
    # The economy_ivr text itself no longer escapes; only the stable code does.
    with pytest.raises(worker.LiveKitStartupError, match="INVALID_CONFIGURATION"):
        await worker.entrypoint(ctx)
    resolve.assert_not_called()  # no global model fallback
    create_session.assert_not_called()  # no AI session
    record_failure.assert_awaited_once()  # failure is visible on the run


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["standard", "premium", "budget", None])
async def test_worker_guard_does_not_affect_other_modes(monkeypatch, mode):
    worker, ctx, resolve, create_session, record_failure = _patch_worker_entrypoint(
        monkeypatch, mode
    )
    # Sanitized exception escapes now, not the original RuntimeError -- the
    # guard-marker message itself is exactly what must not survive.
    with pytest.raises(worker.LiveKitStartupError, match="STARTUP_FAILED"):
        await worker.entrypoint(ctx)
    resolve.assert_called_once()


# --- G-48: pinned definitions need a safe duration before paid AI startup ---


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["web", "inbound"])
@pytest.mark.parametrize(
    "invalid_duration",
    [None, "", " ", True, False, 0, -1, 29, 3601, 60.5, "60.0", "invalid", {}, []],
)
async def test_worker_rejects_invalid_pinned_duration_before_ai_session(
    monkeypatch, call_type, invalid_duration
):
    worker, ctx, resolve, create_session, record_failure = _patch_worker_entrypoint(
        monkeypatch,
        "standard",
        max_call_duration_seconds=invalid_duration,
        call_type=call_type,
    )

    # The original InvalidCallDurationError (with its "30 to 3600" text) no
    # longer escapes entrypoint() -- only the sanitized LiveKitStartupError
    # does. _record_livekit_startup_failure still receives the real
    # exception object (asserted below via the mock's recorded argument),
    # which is what the DB-persisted annotation is built from -- never from
    # whatever propagates out of entrypoint() itself.
    with pytest.raises(worker.LiveKitStartupError, match="INVALID_CALL_DURATION"):
        await worker.entrypoint(ctx)

    resolve.assert_not_called()
    create_session.assert_not_called()
    record_failure.assert_awaited_once()
    recorded_error = record_failure.await_args.args[1]
    assert isinstance(recorded_error, worker.InvalidCallDurationError)
    assert recorded_error.__suppress_context__ is True
    assert str(recorded_error) == (
        "Pinned workflow definition has an invalid maximum call duration. "
        "Set a whole number from 30 to 3600 seconds (max_call_duration_seconds) and republish the workflow."
    )
    ctx.delete_room.assert_awaited_once_with()
    ctx.shutdown.assert_called_once_with("invalid_call_duration")


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["web", "inbound"])
@pytest.mark.parametrize("valid_duration", [30, 60, 3600, "60", 60.0])
async def test_worker_accepts_valid_pinned_duration_before_model_resolution(
    monkeypatch, call_type, valid_duration
):
    worker, ctx, resolve, create_session, record_failure = _patch_worker_entrypoint(
        monkeypatch,
        "standard",
        max_call_duration_seconds=valid_duration,
        call_type=call_type,
    )

    with pytest.raises(worker.LiveKitStartupError, match="STARTUP_FAILED"):
        await worker.entrypoint(ctx)

    resolve.assert_called_once()
    create_session.assert_not_called()
    ctx.delete_room.assert_not_awaited()
    ctx.shutdown.assert_not_called()


@pytest.mark.asyncio
async def test_duration_rejection_records_failed_terminal_run(monkeypatch):
    from unittest.mock import AsyncMock

    from api.enums import WorkflowRunState
    from api.services.livekit import worker

    append_event = AsyncMock()
    update_run = AsyncMock()
    monkeypatch.setattr(worker, "_append_livekit_run_event", append_event)
    monkeypatch.setattr(
        worker.db_client, "update_workflow_run", update_run
    )
    error = worker.InvalidCallDurationError(
        "Pinned workflow definition has an invalid maximum call duration. "
        "Set a whole number from 30 to 3600 seconds (max_call_duration_seconds) and republish the workflow."
    )

    await worker._record_livekit_startup_failure(17, error)

    # Category-only: exception class + stable error code + stage, never the
    # exception's own message (which may carry provider/credential detail
    # for other exception types, even though this specific message is
    # static and secret-free). See
    # test_livekit_startup_redaction.py for the adversarial cases.
    safe_message = "InvalidCallDurationError: INVALID_CALL_DURATION (stage=startup)"
    event = append_event.await_args.args[1]
    assert event["type"] == "startup_failed"
    assert event["message"] == safe_message
    assert event["error_class"] == "InvalidCallDurationError"
    assert event["error_code"] == "INVALID_CALL_DURATION"
    assert event["stage"] == "startup"
    assert str(error) not in event["message"]
    update_run.assert_awaited_once_with(
        17,
        is_completed=True,
        state=WorkflowRunState.COMPLETED.value,
        annotations={"livekit_startup_error": safe_message},
        call_disposition="FAILED",
    )


@pytest.mark.asyncio
async def test_duration_rejection_cancels_and_awaits_pending_startup_tasks(monkeypatch):
    import asyncio

    worker, ctx, resolve, create_session, record_failure = _patch_worker_entrypoint(
        monkeypatch,
        "standard",
        max_call_duration_seconds=None,
    )
    started = set()
    cancelled = set()
    never = asyncio.Event()

    async def pending(name):
        started.add(name)
        try:
            await never.wait()
        except asyncio.CancelledError:
            cancelled.add(name)
            raise

    worker.db_client.get_user_configurations = lambda _user_id: pending("config")
    worker.db_client.has_active_recordings = lambda _org_id: pending("recordings")

    with pytest.raises(worker.LiveKitStartupError, match="INVALID_CALL_DURATION"):
        await worker.entrypoint(ctx)

    assert started == {"config", "recordings"}
    assert cancelled == started
    resolve.assert_not_called()
    create_session.assert_not_called()
    record_failure.assert_awaited_once()


@pytest.mark.asyncio
async def test_duration_rejection_is_failed_not_answered_or_billed(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock, Mock

    from api.routes import business

    rejected_run = SimpleNamespace(
        annotations={"livekit_startup_error": "safe error"},
        mode="livekit",
        name="WR-LK-IN-1",
        call_type="inbound",
        call_disposition="FAILED",
        state="completed",
        usage_info={},
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [rejected_run]
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(
        business.db_client,
        "get_all_workflows_for_listing",
        AsyncMock(return_value=[SimpleNamespace(id=1)]),
    )
    monkeypatch.setattr(
        business.db_client,
        "async_session",
        Mock(return_value=session_context),
    )

    result = await business.get_business_overview(
        user=SimpleNamespace(selected_organization_id=4)
    )

    assert result["total_calls"] == 1
    assert result["answered_calls"] == 0
    assert result["failed_calls"] == 1
    assert result["total_calling_minutes"] == 0.0
    assert result["estimated_cost_inr"] == 0.0


@pytest.mark.asyncio
async def test_duration_rejection_observability_reports_no_ai_session(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, Mock

    from api.services.health import observability_service as observability_module

    rejected_run = SimpleNamespace(
        id=17,
        name="WR-LK-IN-1",
        workflow_id=1,
        definition_id=250,
        created_at=datetime.now(UTC),
        initial_context={},
        gathered_context={"livekit_room": "room-1"},
        usage_info={},
        cost_info={},
        logs={"livekit_events": [{"type": "startup_failed"}]},
        annotations={"livekit_startup_error": "safe error"},
        call_type="inbound",
        mode="livekit",
        recording_url=None,
        transcript_url=None,
        storage_backend="minio",
        state="completed",
        is_completed=True,
        call_disposition="FAILED",
    )
    workflow = SimpleNamespace(
        id=1,
        name="Default Voice Assistant",
        organization_id=4,
        model_overrides={},
    )
    session = AsyncMock()
    session.execute.side_effect = [
        MagicMock(scalar_one_or_none=Mock(return_value=rejected_run)),
        MagicMock(scalar_one_or_none=Mock(return_value=workflow)),
    ]
    session_context = AsyncMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(
        observability_module.db_client,
        "async_session",
        Mock(return_value=session_context),
    )

    result = await observability_module.ObservabilityService().get_call_timeline(
        "17",
        SimpleNamespace(is_superuser=False, selected_organization_id=4),
    )
    milestones = {item["milestone"]: item for item in result["timeline"]}

    assert result["overall_status"] == "FAILED"
    assert result["billing_reconciliation_status"] == "NO_AI_SESSION"
    assert milestones["livekit_worker_lifecycle"]["status"] == "FAILED"
    assert milestones["voice_engine_runtime"]["status"] == "NOT_STARTED"
    assert milestones["crm_business_context"]["status"] == "NOT_STARTED"
    assert milestones["call_disconnect_outcome"]["status"] == "FAILED"
    assert milestones["carrier_sip_session"]["status"] == "UNVERIFIED"
    assert milestones["carrier_sip_session"]["details"]["carrier_response_code"] == "UNVERIFIED"
    billing = milestones["billing_and_reconciliation"]
    assert billing["status"] == "NO_AI_SESSION"
    assert billing["details"]["telephony_pulses"] == 0
    assert billing["details"]["ai_runtime_cost_inr"] == 0.0
    assert billing["details"]["total_cost_inr"] == 0.0

    # A failed AI startup can still incur a real carrier charge; preserve
    # recorded carrier evidence without inventing AI usage.
    rejected_run.cost_info = {"carrier_cost": 0.4, "total_cost": 0.4,
                              "carrier_cdr_reconciled": True}
    rejected_run.logs = {"sip_status": "CONNECTED", "sip_response_code": "200_OK"}
    session.execute.side_effect = [
        MagicMock(scalar_one_or_none=Mock(return_value=rejected_run)),
        MagicMock(scalar_one_or_none=Mock(return_value=workflow)),
    ]
    with_carrier = await observability_module.ObservabilityService().get_call_timeline(
        "17", SimpleNamespace(is_superuser=False, selected_organization_id=4)
    )
    carrier_milestones = {item["milestone"]: item for item in with_carrier["timeline"]}
    assert carrier_milestones["carrier_sip_session"]["status"] == "CONNECTED"
    assert carrier_milestones["carrier_sip_session"]["details"]["carrier_response_code"] == "200_OK"
    assert carrier_milestones["billing_and_reconciliation"]["details"]["telephony_cost_inr"] == 0.4
    assert carrier_milestones["billing_and_reconciliation"]["details"]["ai_runtime_cost_inr"] == 0.0


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["web", "inbound"])
@pytest.mark.parametrize("failed", [True, False])
async def test_conversation_reporting_distinguishes_failed_startup(monkeypatch, call_type, failed):
    from unittest.mock import AsyncMock, MagicMock, Mock
    from api.routes import business

    run = SimpleNamespace(
        id=17, name="WR-LK-IN-17", workflow_id=1, call_type=call_type,
        mode="livekit", state="completed", created_at=None,
        is_simulation=False, initial_context={}, gathered_context={}, logs={},
        annotations={"livekit_startup_error": "safe error"} if failed else {},
        usage_info={}, cost_info={}, call_disposition="FAILED" if failed else "ANSWERED",
        recording_url=None,
    )
    workflow = SimpleNamespace(id=1, name="Agent", organization_id=4,
                               workflow_configurations={})
    # The list route is now a bounded server-side query: it executes the page
    # statement, the count statement and the facet statement, in that order.
    page_result = MagicMock()
    page_result.all.return_value = [(run, "Agent")]
    count_result = MagicMock()
    count_result.scalar.return_value = 1
    facet_result = MagicMock()
    facet_result.all.return_value = []
    detail_result = MagicMock()
    detail_result.first.return_value = (run, "Agent", 4)
    session = MagicMock()
    session.execute = AsyncMock(
        side_effect=[page_result, count_result, facet_result, detail_result]
    )
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(business.db_client, "async_session", Mock(return_value=context))
    monkeypatch.setattr(business, "_recording_storage_configured", lambda: False)
    user = SimpleNamespace(selected_organization_id=4, is_superuser=False)

    listed = await business.list_conversations(user=user)
    detail = await business.get_conversation_detail(17, user=user)
    expected = "FAILED — call did not start." if failed else "Call completed."
    assert listed["items"][0]["summary"] == expected
    assert detail["summary"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("call_type", ["web", "inbound"])
@pytest.mark.parametrize("failed", [True, False])
async def test_observability_run_event_uses_call_outcome(monkeypatch, failed, call_type):
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, Mock
    from api.db.models import WorkflowRunModel, WorkflowModel
    from api.services.health import observability_service as module

    run = WorkflowRunModel(
        id=17, workflow_id=1, name="WR-LK-IN-17", mode="livekit",
        call_type=call_type, state="completed", is_completed=True,
        call_disposition="FAILED" if failed else "ANSWERED",
        annotations={"livekit_startup_error": "safe error"} if failed else {},
        initial_context={}, gathered_context={}, logs={}, cost_info={}, usage_info={},
        created_at=datetime.now(UTC),
    )
    workflow = WorkflowModel(id=1, name="Agent", organization_id=4)
    empty = MagicMock()
    empty.scalars.return_value.all.return_value = []
    session = AsyncMock()
    session.execute.side_effect = [
        empty, MagicMock(scalar_one_or_none=Mock(return_value=run)),
        MagicMock(scalar_one_or_none=Mock(return_value=workflow)),
    ]
    context = AsyncMock()
    context.__aenter__.return_value = session
    monkeypatch.setattr(module.db_client, "async_session", Mock(return_value=context))
    monkeypatch.setattr(module.durable_incident_store, "query_incidents",
                        AsyncMock(return_value=([], 0)))
    events = (await module.ObservabilityService().search_logs(
        user=SimpleNamespace(is_superuser=False, selected_organization_id=4),
        run_id="17",
    ))["events"]
    outcome = next(event for event in events if event["id"] == "run_17_end")
    assert outcome["event_type"] == ("workflow.run.failed" if failed else "workflow.run.completed")
    assert outcome["severity"] == ("ERROR" if failed else "INFO")


# --- Pre-publish provider readiness (configuration presence only) ------------


def _gemini_config(api_key="fake-test-key-not-real", is_realtime=True):
    from api.schemas.user_configuration import UserConfiguration
    from api.services.configuration.registry import GoogleRealtimeLLMConfiguration

    return UserConfiguration(
        is_realtime=is_realtime,
        realtime=GoogleRealtimeLLMConfiguration(
            provider="google_realtime",
            api_key=api_key,
            model="gemini-3.1-flash-live-preview",
            voice="Kore",
            language="en",
        ),
    )


def _readiness(mode, user_config):
    from api.services.business.agent_training import find_missing_provider_config

    _, overrides = resolve_product_mode_overrides(mode)
    return find_missing_provider_config(mode, overrides, user_config)


def test_standard_ready_with_gemini_key_even_if_global_mode_is_pipeline():
    assert _readiness("standard", _gemini_config(is_realtime=False)) == []
    assert _readiness("standard", _gemini_config()) == []


def test_standard_not_ready_without_gemini_section_or_key():
    from api.schemas.user_configuration import UserConfiguration

    assert len(_readiness("standard", UserConfiguration())) == 1
    assert len(_readiness("standard", None)) == 1
    assert "Gemini Live" in _readiness("standard", None)[0]
    assert len(_readiness("standard", _gemini_config(api_key="   "))) == 1


def test_ivr_modes_and_budget_publish_do_not_require_ai_providers():
    from api.schemas.user_configuration import UserConfiguration

    for mode in ("economy_ivr", "budget"):
        assert _readiness(mode, UserConfiguration()) == []


# --- /agent/publish route: readiness gate (mocked DB, isolated test data) ----


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))


def _patch_publish(monkeypatch, user_config):
    from unittest.mock import AsyncMock

    from api.routes import business
    from api.services.business.agent_training import get_preset_by_id
    from api.services.workflow.defaults import build_default_workflow_definition

    workflow = SimpleNamespace(
        id=1,
        user_id=5,
        workflow_definition=build_default_workflow_definition(),
        workflow_configurations={},
        template_context_variables={},
    )

    async def _primary(user, workflow_id=None):
        return workflow

    db = SimpleNamespace(
        get_user_configurations=AsyncMock(return_value=user_config),
        save_workflow_draft=AsyncMock(),
        publish_workflow_draft=AsyncMock(
            return_value=SimpleNamespace(id=9, version_number=3)
        ),
        async_session=lambda: _FakeSession(),
    )
    monkeypatch.setattr(business, "_get_primary_workflow", _primary)
    monkeypatch.setattr(business, "db_client", db)
    training = get_preset_by_id("real_estate")
    return business, db, training


@pytest.mark.asyncio
async def test_publish_standard_without_gemini_config_returns_422_and_saves_nothing(
    monkeypatch,
):
    from api.schemas.user_configuration import UserConfiguration

    business, db, training = _patch_publish(monkeypatch, UserConfiguration())
    training["product_mode"] = "standard"
    with pytest.raises(HTTPException) as exc:
        await business.publish_agent_training(
            business.AgentTrainingDraftRequest(training_data=training), user=_user()
        )
    assert exc.value.status_code == 422
    assert "Gemini Live" in exc.value.detail
    db.save_workflow_draft.assert_not_called()
    db.publish_workflow_draft.assert_not_called()


@pytest.mark.asyncio
async def test_publish_standard_with_gemini_config_succeeds(monkeypatch):
    business, db, training = _patch_publish(monkeypatch, _gemini_config())
    training["product_mode"] = "standard"
    result = await business.publish_agent_training(
        business.AgentTrainingDraftRequest(training_data=training), user=_user()
    )
    assert result["status"] == "published"
    saved = db.save_workflow_draft.await_args.kwargs["workflow_configurations"]
    assert saved["product_mode"] == "standard"
    assert saved["model_overrides"] == LEGACY_GEMINI


@pytest.mark.asyncio
async def test_publish_economy_ivr_needs_no_ai_config_and_writes_no_overrides(
    monkeypatch,
):
    from api.schemas.user_configuration import UserConfiguration

    business, db, training = _patch_publish(monkeypatch, UserConfiguration())
    training["product_mode"] = "economy_ivr"
    result = await business.publish_agent_training(
        business.AgentTrainingDraftRequest(training_data=training), user=_user()
    )
    assert result["status"] == "published"
    saved = db.save_workflow_draft.await_args.kwargs["workflow_configurations"]
    assert saved["product_mode"] == "economy_ivr"
    assert "model_overrides" not in saved


@pytest.mark.asyncio
async def test_publish_unknown_mode_returns_422_before_any_save(monkeypatch):
    business, db, training = _patch_publish(monkeypatch, _gemini_config())
    training["product_mode"] = "gemini-pro-max"
    with pytest.raises(HTTPException) as exc:
        await business.publish_agent_training(
            business.AgentTrainingDraftRequest(training_data=training), user=_user()
        )
    assert exc.value.status_code == 422
    db.save_workflow_draft.assert_not_called()


def test_premium_not_ready_when_providers_missing_or_unsupported(monkeypatch):
    for env in ("DEEPGRAM_API_KEY", "DEEPSEEK_API_KEY", "CARTESIA_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    # Even with a Gemini key present, Premium has no usable providers: this
    # build has no registered DeepSeek provider, so it must never report ready.
    problems = _readiness("premium", _gemini_config())
    assert problems
    assert all("Premium mode cannot be published" in p for p in problems)


# --- Model configuration save route (PUT /user/configurations/user) ----------
# Mocked DB and validator: no network, no real credentials, no shared state.

_FAKE_SECRET = "fake-gemini-secret-for-tests-0000"


def _save_route_env(monkeypatch, existing_config):
    from unittest.mock import AsyncMock

    from api.routes import user as user_route

    persisted = {}

    async def _update(user_id, cfg):
        persisted["cfg"] = cfg
        return cfg

    db = SimpleNamespace(
        get_user_configurations=AsyncMock(return_value=existing_config),
        update_user_configuration=AsyncMock(side_effect=_update),
    )
    validate = AsyncMock()
    monkeypatch.setattr(user_route, "db_client", db)
    monkeypatch.setattr(user_route.UserConfigurationValidator, "validate", validate)
    return user_route, db, validate, persisted


def _incoming(user_route, payload):
    return user_route.UserConfigurationRequestResponseSchema(**payload)


def _first_key(cfg):
    key = cfg.realtime.api_key
    return key[0] if isinstance(key, list) else key


@pytest.mark.asyncio
async def test_save_new_gemini_config_persists_and_response_is_masked(monkeypatch):
    from api.schemas.user_configuration import UserConfiguration

    user_route, db, validate, persisted = _save_route_env(
        monkeypatch, UserConfiguration()
    )
    request = _incoming(
        user_route,
        {
            "is_realtime": True,
            "realtime": {
                "provider": "google_realtime",
                "api_key": _FAKE_SECRET,
                "model": "gemini-3.1-flash-live-preview",
                "voice": "Kore",
                "language": "en",
            },
        },
    )
    response = await user_route.update_user_configurations(request, user=_user())
    validate.assert_awaited_once()
    assert _first_key(persisted["cfg"]) == _FAKE_SECRET  # real key stored
    assert _FAKE_SECRET not in str(response)  # never echoed back


@pytest.mark.asyncio
async def test_resaving_masked_placeholder_keeps_stored_key(monkeypatch):
    from api.services.configuration.masking import mask_user_config

    existing = _gemini_config(api_key=_FAKE_SECRET)
    user_route, db, validate, persisted = _save_route_env(monkeypatch, existing)
    masked_payload = mask_user_config(existing)
    request = _incoming(user_route, {k: v for k, v in masked_payload.items() if v is not None})
    assert _FAKE_SECRET not in str(masked_payload)
    await user_route.update_user_configurations(request, user=_user())
    assert _first_key(persisted["cfg"]) == _FAKE_SECRET  # not overwritten by mask


@pytest.mark.asyncio
async def test_masked_placeholder_without_stored_key_is_rejected_400(monkeypatch):
    from api.schemas.user_configuration import UserConfiguration

    user_route, db, validate, persisted = _save_route_env(
        monkeypatch, UserConfiguration()
    )
    request = _incoming(
        user_route,
        {
            "is_realtime": True,
            "realtime": {
                "provider": "google_realtime",
                "api_key": "****************",
                "model": "gemini-3.1-flash-live-preview",
            },
        },
    )
    with pytest.raises(HTTPException) as exc:
        await user_route.update_user_configurations(request, user=_user())
    assert exc.value.status_code == 400
    assert "cfg" not in persisted  # nothing saved


@pytest.mark.asyncio
async def test_provider_validation_failure_returns_422_and_saves_nothing(monkeypatch):
    from unittest.mock import AsyncMock

    from api.schemas.user_configuration import UserConfiguration

    user_route, db, validate, persisted = _save_route_env(
        monkeypatch, UserConfiguration()
    )
    validate.side_effect = ValueError("Google/Gemini realtime key was rejected")
    request = _incoming(
        user_route,
        {
            "is_realtime": True,
            "realtime": {
                "provider": "google_realtime",
                "api_key": _FAKE_SECRET,
                "model": "gemini-3.1-flash-live-preview",
            },
        },
    )
    with pytest.raises(HTTPException) as exc:
        await user_route.update_user_configurations(request, user=_user())
    assert exc.value.status_code == 422
    assert _FAKE_SECRET not in str(exc.value.detail)
    assert "cfg" not in persisted


@pytest.mark.asyncio
async def test_storage_failure_is_not_reported_as_success(monkeypatch):
    from unittest.mock import AsyncMock

    from api.schemas.user_configuration import UserConfiguration

    user_route, db, validate, persisted = _save_route_env(
        monkeypatch, UserConfiguration()
    )
    db.update_user_configuration = AsyncMock(side_effect=RuntimeError("db down"))
    request = _incoming(
        user_route,
        {
            "is_realtime": True,
            "realtime": {
                "provider": "google_realtime",
                "api_key": _FAKE_SECRET,
                "model": "gemini-3.1-flash-live-preview",
            },
        },
    )
    with pytest.raises(RuntimeError):
        await user_route.update_user_configurations(request, user=_user())


@pytest.mark.asyncio
async def test_unauthenticated_save_is_rejected_401():
    from api.services.auth.depends import get_user

    with pytest.raises(HTTPException) as exc:
        await get_user(authorization=None, x_api_key=None)
    assert exc.value.status_code == 401


# --- Explicit workflow selection: Pilot must never touch Workflow 1 / other orgs


class _RecordingSession:
    """Fake DB session that records every statement it is asked to run."""

    def __init__(self, store):
        self.store = store
        self.live = SimpleNamespace(workflow_configurations={}, workflow_definition={})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.store["stmts"].append(
            str(stmt.compile(compile_kwargs={"literal_binds": True}))
        )
        live = self.live
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: live, all=lambda: [])
        )

    async def commit(self):
        self.store["commits"] += 1


def _multi_workflow_env(monkeypatch):
    from unittest.mock import AsyncMock

    from api.routes import business
    from api.services.workflow.defaults import build_default_workflow_definition

    def _wf(wf_id, name, org_id):
        return SimpleNamespace(
            id=wf_id,
            name=name,
            organization_id=org_id,
            user_id=5,
            status="active",
            released_definition_id=wf_id * 10,
            workflow_definition=build_default_workflow_definition(),
            workflow_configurations={"product_mode": "standard", "marker": name},
            template_context_variables={},
        )

    wf1 = _wf(1, "Default Voice Assistant", 1)
    pilot = _wf(161, "Pilot Receptionist", 1)
    other_org = _wf(900, "Other Org Workflow", 2)
    by_id = {1: wf1, 161: pilot, 900: other_org}
    store = {"stmts": [], "commits": 0}
    db = SimpleNamespace(
        get_workflow_by_id=AsyncMock(side_effect=lambda i: by_id.get(i)),
        get_all_workflows_for_listing=AsyncMock(return_value=[wf1, pilot]),
        get_user_configurations=AsyncMock(return_value=_gemini_config()),
        save_workflow_draft=AsyncMock(
            return_value=SimpleNamespace(version_number=2)
        ),
        publish_workflow_draft=AsyncMock(
            return_value=SimpleNamespace(id=9, version_number=3)
        ),
        discard_workflow_draft=AsyncMock(),
        revert_to_version=AsyncMock(),
        async_session=lambda: _RecordingSession(store),
    )
    audit = SimpleNamespace(record_event=AsyncMock())
    monkeypatch.setattr(business, "db_client", db)
    monkeypatch.setattr(business, "audit_service", audit)
    return business, db, audit, store, by_id


def _training(product_mode="standard"):
    from api.services.business.agent_training import get_preset_by_id

    data = get_preset_by_id("real_estate")
    data["product_mode"] = product_mode
    return data


@pytest.mark.asyncio
async def test_publish_to_pilot_touches_only_pilot_and_is_audited(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    wf1_before = dict(by_id[1].workflow_configurations)
    result = await business.publish_agent_training(
        business.AgentTrainingDraftRequest(training_data=_training(), workflow_id=161),
        user=_user(),
    )
    assert result["workflow_id"] == 161
    assert db.save_workflow_draft.await_args.kwargs["workflow_id"] == 161
    assert db.publish_workflow_draft.await_args.kwargs["workflow_id"] == 161
    assert store["stmts"] and all("workflows.id = 161" in s for s in store["stmts"])
    assert by_id[1].workflow_configurations == wf1_before  # Workflow 1 untouched
    event = audit.record_event.await_args.kwargs
    assert event["action_type"] == "audit.config.agent_published"
    assert event["target_id"] == "161"
    assert event["changed_fields"]["workflow_name"] == "Pilot Receptionist"
    assert event["organization_id"] == 1


@pytest.mark.asyncio
async def test_save_draft_to_pilot_does_not_write_workflow_1(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    await business.save_agent_draft(
        business.AgentTrainingDraftRequest(training_data=_training(), workflow_id=161),
        user=_user(),
    )
    assert db.save_workflow_draft.await_args.kwargs["workflow_id"] == 161
    db.publish_workflow_draft.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("target,status", [(900, 403), (999999, 404)])
async def test_cross_org_or_unknown_workflow_is_rejected_without_fallback(
    monkeypatch, target, status
):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    for call in (
        lambda: business.publish_agent_training(
            business.AgentTrainingDraftRequest(
                training_data=_training(), workflow_id=target
            ),
            user=_user(),
        ),
        lambda: business.save_agent_draft(
            business.AgentTrainingDraftRequest(
                training_data=_training(), workflow_id=target
            ),
            user=_user(),
        ),
        lambda: business.get_agent_training(workflow_id=target, user=_user()),
        lambda: business.restore_agent_version(5, workflow_id=target, user=_user()),
        lambda: business.save_business_ivr(
            business.BusinessIVRConfig(), workflow_id=target, user=_user()
        ),
        lambda: business.get_business_ivr(workflow_id=target, user=_user()),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == status
    db.save_workflow_draft.assert_not_called()
    db.publish_workflow_draft.assert_not_called()
    db.discard_workflow_draft.assert_not_called()
    db.revert_to_version.assert_not_called()
    audit.record_event.assert_not_called()
    assert store["stmts"] == []  # no DB statement ran against anything


@pytest.mark.asyncio
async def test_pilot_ivr_save_and_agent_load_target_pilot_only(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    await business.save_business_ivr(
        business.BusinessIVRConfig(), workflow_id=161, user=_user()
    )
    assert store["stmts"] and all("workflows.id = 161" in s for s in store["stmts"])
    loaded = await business.get_agent_training(workflow_id=161, user=_user())
    assert loaded["workflow_id"] == 161
    assert loaded["workflow_name"] == "Pilot Receptionist"


@pytest.mark.asyncio
async def test_restore_on_pilot_reverts_only_pilot(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    await business.restore_agent_version(7, workflow_id=161, user=_user())
    assert db.revert_to_version.await_args.kwargs["workflow_id"] == 161
    assert db.publish_workflow_draft.await_args.kwargs["workflow_id"] == 161
    db.discard_workflow_draft.assert_awaited_once_with(161)


@pytest.mark.asyncio
async def test_omitting_workflow_id_keeps_default_behaviour(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    await business.publish_agent_training(
        business.AgentTrainingDraftRequest(training_data=_training()),
        user=_user(),
    )
    # Unchanged legacy behaviour: the named default workflow (#1) is targeted.
    assert db.publish_workflow_draft.await_args.kwargs["workflow_id"] == 1


@pytest.mark.asyncio
async def test_workflow_without_saved_training_is_flagged_and_never_shows_workflow_1_data(
    monkeypatch,
):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    by_id[1].workflow_configurations["agent_training"] = {
        "business_name": "WORKFLOW-1-PRIVATE-NAME"
    }
    loaded = await business.get_agent_training(workflow_id=161, user=_user())
    assert loaded["workflow_id"] == 161
    assert loaded["training_source"] == "starter_template"
    assert "WORKFLOW-1-PRIVATE-NAME" not in str(loaded)


@pytest.mark.asyncio
async def test_workflow_with_saved_training_reports_saved_source(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    by_id[161].workflow_configurations["agent_training"] = {"business_name": "Pilot Co"}
    loaded = await business.get_agent_training(workflow_id=161, user=_user())
    assert loaded["training_source"] == "saved"
    assert loaded["training_data"]["business_name"] == "Pilot Co"


@pytest.mark.asyncio
async def test_restore_is_audited_for_the_selected_workflow_only(monkeypatch):
    business, db, audit, store, by_id = _multi_workflow_env(monkeypatch)
    db.revert_to_version = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(
        return_value=SimpleNamespace(version_number=4)
    )
    await business.restore_agent_version(7, workflow_id=161, user=_user())
    event = audit.record_event.await_args.kwargs
    assert event["action_type"] == "audit.config.agent_restored"
    assert event["target_id"] == "161"
    assert event["changed_fields"]["restored_from_definition_id"] == 7


# --- G-45: the owner's duration limit must reach the key the worker enforces --


def _duration(data):
    from api.services.business.agent_training import resolve_max_call_duration_seconds

    return resolve_max_call_duration_seconds(data)


def test_duration_helper_accepts_whole_seconds_in_range():
    assert _duration({"max_call_duration_seconds": 60}) == 60
    assert _duration({"max_call_duration_seconds": "60"}) == 60
    assert _duration({"max_call_duration_seconds": 60.0}) == 60
    assert _duration({"max_call_duration_seconds": 30}) == 30
    assert _duration({"max_call_duration_seconds": 3600}) == 3600


@pytest.mark.parametrize(
    "raw",
    [None, "", "  ", True, False, 0, -5, 29, 3601, 60.5, "abc", "60s", [60], {"v": 60}],
)
def test_duration_helper_rejects_missing_invalid_or_out_of_range(raw):
    with pytest.raises(ValueError):
        _duration({"max_call_duration_seconds": raw})


def test_duration_helper_rejects_absent_key_and_non_dict():
    with pytest.raises(ValueError, match="required"):
        _duration({})
    with pytest.raises(ValueError, match="required"):
        _duration(None)


def test_all_industry_presets_carry_a_valid_duration():
    from api.services.business.agent_training import UNIVERSAL_INDUSTRY_PRESETS

    for preset in UNIVERSAL_INDUSTRY_PRESETS:
        assert _duration(preset) == preset["max_call_duration_seconds"]


def _saved_configs(db):
    return db.save_workflow_draft.await_args.kwargs["workflow_configurations"]


@pytest.mark.asyncio
async def test_publish_copies_duration_to_the_worker_key(monkeypatch):
    business, db, training = _patch_publish(monkeypatch, _gemini_config())
    training["max_call_duration_seconds"] = 60
    await business.publish_agent_training(
        business.AgentTrainingDraftRequest(training_data=training), user=_user()
    )
    configs = _saved_configs(db)
    assert configs["max_call_duration_seconds"] == 60  # read by worker.py
    assert configs["agent_training"]["max_call_duration_seconds"] == 60  # read by UI
    db.publish_workflow_draft.assert_awaited_once()


@pytest.mark.asyncio
async def test_draft_copies_duration_to_the_worker_key(monkeypatch):
    from unittest.mock import AsyncMock

    business, db, training = _patch_publish(monkeypatch, _gemini_config())
    monkeypatch.setattr(
        business, "audit_service", SimpleNamespace(record_event=AsyncMock())
    )
    training["max_call_duration_seconds"] = "45"
    await business.save_agent_draft(
        business.AgentTrainingDraftRequest(training_data=training), user=_user()
    )
    assert _saved_configs(db)["max_call_duration_seconds"] == 45


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["MISSING", 0, 5, 7200, "ten", True, None])
async def test_publish_rejects_bad_duration_with_422_and_saves_nothing(
    monkeypatch, bad
):
    business, db, training = _patch_publish(monkeypatch, _gemini_config())
    if bad == "MISSING":
        training.pop("max_call_duration_seconds", None)
    else:
        training["max_call_duration_seconds"] = bad
    with pytest.raises(HTTPException) as exc:
        await business.publish_agent_training(
            business.AgentTrainingDraftRequest(training_data=training), user=_user()
        )
    assert exc.value.status_code == 422
    assert "duration" in str(exc.value.detail).lower()
    db.save_workflow_draft.assert_not_awaited()
    db.publish_workflow_draft.assert_not_awaited()


@pytest.mark.asyncio
async def test_draft_rejects_bad_duration_with_422_and_saves_nothing(monkeypatch):
    business, db, training = _patch_publish(monkeypatch, _gemini_config())
    training["max_call_duration_seconds"] = 10
    with pytest.raises(HTTPException) as exc:
        await business.save_agent_draft(
            business.AgentTrainingDraftRequest(training_data=training), user=_user()
        )
    assert exc.value.status_code == 422
    db.save_workflow_draft.assert_not_awaited()


def test_worker_reads_the_same_top_level_key_the_routes_write():
    """Contract test: the key written by the routes is the one worker.py reads."""
    import inspect

    from api.services.livekit import worker

    # The real body lives in _run_entrypoint; entrypoint() is a thin
    # sanitizing wrapper around it (see LiveKitStartupError).
    entrypoint_source = inspect.getsource(worker._run_entrypoint)
    validator_source = inspect.getsource(worker._validate_pinned_call_duration)
    assert "_validate_pinned_call_duration(run_configs)" in entrypoint_source
    assert 'run_configs.get("max_call_duration_seconds")' in validator_source
    # The validated value is handed to the agent's enforced duration limit.
    assert "max_duration_seconds=max_duration_seconds" in entrypoint_source
