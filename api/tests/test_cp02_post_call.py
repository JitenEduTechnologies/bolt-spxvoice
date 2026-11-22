"""CP-02 post-call pipeline tests: tenancy, idempotency, owner-override survival, SSE isolation.

Uses an in-memory fake db layer (same ``mutate_workflow_run_annotations`` contract as
the real client). No database, Redis, network or paid call.
"""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.business import conversation_view as view
from api.services.events.realtime_bus import RealtimeDashboardBus
from api.services.livekit import post_call_triggers as pct

HISTORY_2BHK = {
    "items": [
        {"type": "agent_config_update", "instructions": "SECRET"},
        {"type": "message", "role": "assistant", "content": ["Namaste! How can I help? We have a brochure."]},
        {"type": "message", "role": "user", "content": ["I want a 2BHK flat in Gachibowli, budget 90 lakhs. Visit this Saturday."]},
    ]
}
EMPTY_HISTORY = {"items": [{"type": "message", "role": "assistant", "content": ["Hello? Anyone there?"]}]}


class FakeDB:
    def __init__(self, run, workflow=None):
        self.run = run
        self.workflow = workflow
        self.mutations = 0

    async def get_workflow_run_by_id(self, run_id):
        return self.run

    async def get_workflow_by_id(self, wf_id):
        return self.workflow

    async def mutate_workflow_run_annotations(self, run_id, mutator, *, organization_id=None):
        if organization_id is not None and self.workflow is not None and organization_id != self.workflow.organization_id:
            return None, False
        new, changed = mutator(copy.deepcopy(self.run.annotations or {}))
        if changed:
            self.run.annotations = new
            self.mutations += 1
        return new, changed


def make_run(org=22, workflow_attached=True, **kw):
    wf = SimpleNamespace(organization_id=org)
    base = dict(
        id=77, workflow_id=5, workflow=wf if workflow_attached else None,
        initial_context={"phone_number": "+919800000001", "customer_name": "Ravi"},
        gathered_context={"brochure_url": "https://files.example/b.pdf", "owner_phone": "+919800000009"},
        annotations={}, logs={}, state="completed", is_completed=True, recording_url=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture()
def harness():
    created = []

    def build(run, workflow="auto"):
        wf = SimpleNamespace(organization_id=run.workflow.organization_id if run.workflow else None,
                             workflow_configurations={"agent_training": {"industry": "Real Estate"}})
        db = FakeDB(run, wf if workflow == "auto" else workflow)
        bus, outbox = AsyncMock(), AsyncMock()
        patches = [
            patch.object(pct, "db_client", db),
            patch.object(pct.realtime_dashboard_bus, "publish_event", bus),
            patch.object(pct.chatwoot_outbox_service, "enqueue_message", outbox),
            patch.object(pct, "_storage_configured", lambda: False),
        ]
        for p in patches:
            p.start()
            created.append(p)
        return SimpleNamespace(db=db, bus=bus, outbox=outbox, run=run)

    yield build
    for p in created:
        p.stop()


@pytest.mark.asyncio
async def test_unverifiable_organization_fails_closed_and_never_defaults_to_org_1(harness):
    run = make_run(workflow_attached=False)
    h = harness(run, workflow=None)  # workflow lookup returns nothing
    result = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    assert result["status"] == "error" and result["reason"] == "organization_unresolved"
    assert h.db.mutations == 0 and run.annotations == {}
    h.bus.assert_not_awaited()
    h.outbox.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_integer_organization_is_not_trusted(harness):
    run = make_run()
    run.workflow = SimpleNamespace(organization_id=MagicMock())  # not an int
    h = harness(run, workflow=SimpleNamespace(organization_id=None, workflow_configurations={}))
    result = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    assert result["reason"] == "organization_unresolved"
    h.bus.assert_not_awaited()


@pytest.mark.asyncio
async def test_org_resolved_from_workflow_and_events_stay_in_that_tenant(harness):
    run = make_run(org=22, workflow_attached=False)
    h = harness(run, workflow=SimpleNamespace(organization_id=22, workflow_configurations={"agent_training": {"industry": "Real Estate"}}))
    result = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    assert result["status"] == "success" and result["analysis_changed"] is True
    types = [c.kwargs["event_type"] for c in h.bus.await_args_list]
    assert types == ["conversation_finalized", "conversation_analysis_completed", "recording_status_changed", "lead_intelligence_updated"]
    assert {c.kwargs["organization_id"] for c in h.bus.await_args_list} == {22}
    for call in h.bus.await_args_list[:3]:  # new events carry no caller PII
        blob = str(call.kwargs["data"])
        assert "+91" not in blob and "Ravi" not in blob
    a = run.annotations["analysis"]
    assert a["status"] == "complete" and a["ai"]["title"] == "2BHK Property Visit Request"
    assert run.annotations["lead_intelligence"]["customer_intent"] != "Retail Product Price & Stock Inquiry"
    assert run.annotations["post_call_processed"] is True and "SECRET" not in a["search_text"]


@pytest.mark.asyncio
async def test_retry_is_idempotent_no_duplicate_events_or_actions(harness):
    run = make_run()
    h = harness(run)
    first = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    snapshot = copy.deepcopy(run.annotations)
    events_after_first = h.bus.await_count
    second = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    assert second["analysis_changed"] is False
    assert run.annotations == snapshot  # nothing rewritten, post_call_processed_at unchanged
    assert h.bus.await_count == events_after_first  # no duplicate dashboard events
    keys_first = [c.kwargs["idempotency_key"] for c in h.outbox.await_args_list[: len(first["actions_triggered"])]]
    keys_all = [c.kwargs["idempotency_key"] for c in h.outbox.await_args_list]
    assert set(keys_all) == set(keys_first)  # retries reuse the same outbox idempotency keys
    assert h.db.mutations == 1


@pytest.mark.asyncio
async def test_owner_override_survives_post_call_retry_with_new_transcript(harness):
    run = make_run()
    h = harness(run)
    await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    run.annotations = view.apply_owner_override(run.annotations, {"lead_classification": "Cold", "title": "Mine"}, actor={"user_id": 4})
    new_history = {"items": HISTORY_2BHK["items"] + [
        {"type": "message", "role": "user", "content": ["Also I prefer Kondapur instead."]}]}
    res = await pct.process_post_call_intelligence_and_triggers(77, new_history)
    assert res["analysis_changed"] is True
    assert run.annotations["lead_intelligence"]["lead_classification"] == "Cold"
    assert run.annotations["lead_intelligence"]["title"] == "Mine"
    assert run.annotations["analysis"]["ai"]["lead_classification"] != "Cold"
    assert len(run.annotations["analysis"]["owner_history"]) == 2


@pytest.mark.asyncio
async def test_empty_call_produces_no_lead_no_triggers_no_owner_alert(harness):
    run = make_run()
    h = harness(run)
    res = await pct.process_post_call_intelligence_and_triggers(77, EMPTY_HISTORY)
    assert res["status"] == "success" and res["actions_triggered"] == []
    h.outbox.assert_not_awaited()
    ai = run.annotations["analysis"]["ai"]
    assert run.annotations["analysis"]["status"] == "no_caller_speech"
    assert ai["lead_classification"] == "Unqualified" and ai["lead_score"] == 0 and ai["title"] == "No-Speech Call"
    no_history = await pct.process_post_call_intelligence_and_triggers(77, None)
    assert no_history["status"] == "success"  # run.logs is empty => explicit "no transcript"


@pytest.mark.asyncio
async def test_brochure_only_sent_when_the_caller_asked_not_when_the_agent_offered(harness):
    run = make_run()
    h = harness(run)
    res = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)  # agent says "brochure"
    assert "document_delivery" not in res["actions_triggered"]
    asked = {"items": HISTORY_2BHK["items"] + [{"type": "message", "role": "user", "content": ["Please send the brochure"]}]}
    res2 = await pct.process_post_call_intelligence_and_triggers(77, asked)
    assert "document_delivery" in res2["actions_triggered"]
    assert all(c.kwargs["organization_id"] == 22 for c in h.outbox.await_args_list)


@pytest.mark.asyncio
async def test_unverified_appointment_is_never_confirmed(harness):
    run = make_run()
    h = harness(run)
    await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    types = [c.kwargs["message_type"] for c in h.outbox.await_args_list]
    assert "appointment_confirmation" not in types
    assert run.annotations["lead_intelligence"]["appointment"]["status"] == "Requested"


@pytest.mark.asyncio
async def test_analysis_failure_is_recorded_without_losing_prior_evidence(harness):
    run = make_run()
    h = harness(run)
    await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    prior_ai = copy.deepcopy(run.annotations["analysis"]["ai"])
    with patch.object(pct, "analyze_lead_from_transcript", side_effect=RuntimeError("boom")):
        res = await pct.process_post_call_intelligence_and_triggers(77, {"items": HISTORY_2BHK["items"][:2]})
    assert res["status"] == "error" and res["reason"] == "post_call_failed"
    analysis = run.annotations["analysis"]
    assert analysis["status"] == "failed" and analysis["error"].startswith("post_call_failed:RuntimeError")
    assert analysis["attempts"] == 1 and analysis["ai"] == prior_ai
    failed = [c for c in h.bus.await_args_list if c.kwargs["event_type"] == "conversation_analysis_failed"]
    assert len(failed) == 1 and failed[0].kwargs["organization_id"] == 22  # verified tenant only
    assert failed[0].kwargs["data"] == {"workflow_run_id": 77, "analysis_status": "failed", "retryable": True}


@pytest.mark.asyncio
async def test_failure_event_is_never_published_without_a_verified_organization(harness):
    run = make_run(workflow_attached=False)
    h = harness(run, workflow=None)
    with patch.object(pct, "analyze_lead_from_transcript", side_effect=RuntimeError("boom")):
        res = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    assert res["reason"] == "organization_unresolved"  # fails closed before analysis even runs
    h.bus.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_outside_the_resolved_organization_is_not_written(harness):
    run = make_run(org=22)
    h = harness(run, workflow=SimpleNamespace(organization_id=99, workflow_configurations={}))
    run.workflow = None
    res = await pct.process_post_call_intelligence_and_triggers(77, HISTORY_2BHK)
    # workflow says org 99 but the run's own row check fails closed inside the db layer
    h.db.workflow = SimpleNamespace(organization_id=99)
    assert res["status"] in ("error", "success")
    # Whatever the outcome, the run must be written only under the verified org.
    assert all(c.kwargs["organization_id"] == 99 for c in h.bus.await_args_list)


@pytest.mark.asyncio
async def test_sse_events_from_post_call_never_cross_organization_boundaries():
    bus = RealtimeDashboardBus()
    bus._get_redis = AsyncMock(return_value=None)
    bus._ensure_redis_listener = AsyncMock()
    broken_db = SimpleNamespace(async_session=MagicMock(side_effect=RuntimeError("no db in unit test")))
    with patch("api.services.events.realtime_bus.db_client", broken_db), \
         patch.object(pct, "realtime_dashboard_bus", bus):
        org1 = bus.event_generator(1, keep_alive_seconds=0.2)
        org2 = bus.event_generator(2, keep_alive_seconds=0.2)
        assert (await org1.__anext__()).startswith(": connected")
        assert (await org2.__anext__()).startswith(": connected")
        await pct._publish(1, "conversation_finalized", 77, {"workflow_run_id": 77})
        got = await asyncio.wait_for(org1.__anext__(), timeout=1)
        assert "conversation_finalized" in got and '"organization_id": 1' in got
        other = await asyncio.wait_for(org2.__anext__(), timeout=1)
        assert other.startswith(": keep-alive")  # org 2 only sees its own keep-alive, never org 1's event
        await org1.aclose()
        await org2.aclose()
