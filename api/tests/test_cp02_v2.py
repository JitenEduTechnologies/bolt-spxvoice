"""CP-02 v2: guardrails (kill switches, rate limits) and owner-triggered bulk re-analysis."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from api.db.models import WorkflowRunModel
from api.routes import business
from api.services.business import guardrails
from api.tests.test_cp02_conversations_routes import HISTORY, ORG_A, env, page  # noqa: F401


# ------------------------------- guardrails -------------------------------------- #

def test_kill_switches_default_on_and_read_env_at_call_time(monkeypatch):
    assert guardrails.feature_enabled("exports") is True
    for off in ("false", "0", "OFF", "disabled", "no"):
        monkeypatch.setenv("CP02_EXPORTS_ENABLED", off)
        assert guardrails.feature_enabled("exports") is False
    monkeypatch.setenv("CP02_EXPORTS_ENABLED", "true")
    assert guardrails.feature_enabled("exports") is True
    monkeypatch.setenv("CP02_BULK_ACTIONS_ENABLED", "false")
    with pytest.raises(HTTPException) as exc:
        guardrails.require_feature("bulk_actions", "Bulk actions are unavailable.")
    assert exc.value.status_code == 503 and "switched off by an administrator" in exc.value.detail


@pytest.fixture()
def limiter(monkeypatch):
    monkeypatch.setenv("CP02_RATE_LIMIT_FORCE", "1")
    guardrails.reset_for_tests()

    def no_redis():
        raise ConnectionError("redis down")

    monkeypatch.setattr(guardrails, "_get_redis", no_redis)  # exercise the in-process fallback
    yield
    guardrails.reset_for_tests()


@pytest.mark.asyncio
async def test_rate_limit_blocks_after_the_window_quota_then_recovers(limiter):
    limit, window = guardrails.LIMITS["export"]
    t = 1_000_000.0
    for _ in range(limit):
        await guardrails.check_rate_limit("export", 7, now=t)
    with pytest.raises(HTTPException) as exc:
        await guardrails.check_rate_limit("export", 7, now=t)
    assert exc.value.status_code == 429 and int(exc.value.headers["Retry-After"]) >= 1
    await guardrails.check_rate_limit("export", 8, now=t)  # another user is unaffected
    await guardrails.check_rate_limit("bulk", 7, now=t)  # another bucket is unaffected
    await guardrails.check_rate_limit("export", 7, now=t + window)  # next window: allowed again


@pytest.mark.asyncio
async def test_rate_limit_uses_redis_when_available_and_is_inactive_in_test_env(monkeypatch):
    guardrails.reset_for_tests()
    await guardrails.check_rate_limit("export", 1)  # ENVIRONMENT=test and not forced: no-op
    monkeypatch.setenv("CP02_RATE_LIMIT_FORCE", "1")
    calls = {"n": 0, "expire": 0}

    class FakeRedis:
        async def incr(self, key):
            calls["n"] += 1
            return calls["n"]

        async def expire(self, key, ttl):
            calls["expire"] += 1

    monkeypatch.setattr(guardrails, "_get_redis", lambda: FakeRedis())
    limit, _ = guardrails.LIMITS["analyze"]
    for _ in range(limit):
        await guardrails.check_rate_limit("analyze", 3, now=5.0)
    with pytest.raises(HTTPException):
        await guardrails.check_rate_limit("analyze", 3, now=5.0)
    assert calls["expire"] == 1  # TTL set once per window
    monkeypatch.setenv("CP02_RATE_LIMIT_DISABLED", "1")
    await guardrails.check_rate_limit("analyze", 3, now=5.0)  # explicit operator override
    guardrails.reset_for_tests()


@pytest.mark.asyncio
async def test_routes_honour_kill_switches_and_rate_limits(env, monkeypatch, limiter):
    monkeypatch.setenv("CP02_EXPORTS_ENABLED", "false")
    for call in (
        lambda: business.export_conversations(format="csv", user=env.a),
        lambda: business.export_single_conversation(337, format="json", user=env.a),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 503
    monkeypatch.setenv("CP02_EXPORTS_ENABLED", "true")
    monkeypatch.setenv("CP02_RECORDING_BUNDLE_ENABLED", "false")
    with pytest.raises(HTTPException) as exc:
        await business.export_conversations(format="zip", ids="337", include_recordings=True, user=env.a)
    assert exc.value.status_code == 503 and "recordings" in exc.value.detail.lower()
    assert (await business.export_conversations(format="zip", ids="337", user=env.a)).headers["X-Export-Count"] == "1"
    monkeypatch.setenv("CP02_BULK_ACTIONS_ENABLED", "false")
    with pytest.raises(HTTPException) as exc:
        await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=[337]), user=env.a)
    assert exc.value.status_code == 503
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[337]), user=env.a)
    assert exc.value.status_code == 503
    monkeypatch.setenv("CP02_BULK_ACTIONS_ENABLED", "true")
    assert "trash" not in (env.session.get(WorkflowRunModel, 337).annotations or {})
    guardrails.reset_for_tests()
    limit, _ = guardrails.LIMITS["export"]
    for _ in range(limit):  # counters were reset above: a full window of exports is allowed
        await business.export_conversations(format="csv", ids="337", user=env.a)
    with pytest.raises(HTTPException) as exc:
        await business.export_conversations(format="csv", ids="337", user=env.a)
    assert exc.value.status_code == 429 and "Retry-After" in exc.value.headers


# ------------------------------ bulk re-analysis ---------------------------------- #

LEGACY_ANN = {"lead_intelligence": {"lead_classification": "Warm", "customer_intent": "Retail Product Price & Stock Inquiry", "lead_score": 60}}
TWO_BHK_LOGS = {"livekit_history": {"items": [
    {"type": "message", "role": "assistant", "content": ["Namaste, how can I help?"]},
    {"type": "message", "role": "user", "content": ["I am looking for a 2BHK flat in Gachibowli, budget 80 lakhs"]},
]}}


def _legacy_run(env, rid=500):
    env.session.add(WorkflowRunModel(
        id=rid, name=f"WR-{rid}", workflow_id=1, mode="livekit", call_type="outbound", state="completed", is_completed=True,
        usage_info={"call_duration_seconds": 90}, cost_info={}, initial_context={"participant_kind": 0}, gathered_context={},
        logs=TWO_BHK_LOGS, annotations=LEGACY_ANN, call_disposition="UNKNOWN", is_simulation=False, storage_backend="s3"))
    env.session.commit()


@pytest.mark.asyncio
async def test_legacy_runs_are_labelled_honestly_not_as_pending(env):
    _legacy_run(env)
    item = next(i for i in (await page(env, limit=100))["items"] if i["id"] == 500)
    assert item["analysis_status"] == "legacy" and item["title"] == "Earlier analysis (re-analyze for a title)"
    assert item["summary"] == "Retail Product Price & Stock Inquiry"  # the stored old label is shown as-is, not hidden


@pytest.mark.asyncio
async def test_dry_run_previews_without_writing_and_skips_runs_it_must_not_touch(env):
    _legacy_run(env)
    before = env.db.writes
    res = await business.bulk_reanalyze_conversations(
        business.BulkAnalyzeRequest(ids=[500, 337, 333, 335, 900], dry_run=True), user=env.a)
    assert env.db.writes == before and res["status"] == "dry_run"
    assert res["eligible"] == 1 and res["skipped"] == {"no_transcript": 2, "up_to_date": 1}  # 333/335 no logs; 337 already current
    assert res["skipped_other_tenant"] == 1 and res["max_per_request"] == 200
    ex = res["examples"][0]
    assert ex["run_id"] == 500 and ex["before"]["intent"] == "Retail Product Price & Stock Inquiry"
    assert ex["after"]["title"] == "2BHK Property Enquiry"
    assert "analysis" not in env.session.get(WorkflowRunModel, 500).annotations  # nothing written


@pytest.mark.asyncio
async def test_reanalysis_fixes_legacy_labels_keeps_old_output_and_owner_overrides_and_is_idempotent(env):
    _legacy_run(env)
    await business.update_lead_intelligence(338, business.UpdateLeadRequest(lead_classification="Cold"), user=env.a)
    res = await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[500, 338, 333]), user=env.a)
    assert res["status"] == "analyzed" and res["changed"] == 2 and res["skipped"]["no_transcript"] == 1
    d = await business.get_conversation_detail(500, user=env.a)
    assert d["title"] == "2BHK Property Enquiry" and d["analysis_status"] == "complete"
    assert "Retail" not in d["summary"] and d["customer_requirements"]["bhk"] == "2BHK"
    prev = env.session.get(WorkflowRunModel, 500).annotations["analysis"]["previous_ai"]
    assert prev[0]["ai"]["customer_intent"] == "Retail Product Price & Stock Inquiry"  # earlier output preserved
    d338 = await business.get_conversation_detail(338, user=env.a)
    assert d338["lead_classification"] == "Cold" and d338["analysis"]["owner_overrides"]["lead_classification"] == "Cold"
    assert env.session.get(WorkflowRunModel, 333).annotations["lead_intelligence"]["lead_classification"] == "Hot"  # no transcript: untouched
    again = await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[500, 338]), user=env.a)
    assert again["changed"] == 0 and again["skipped"]["up_to_date"] == 2  # safe to repeat
    started = [c for c in env.audit.await_args_list if c.kwargs.get("action_type") == "audit.crm.conversations_analyze_started"]
    assert started and started[0].kwargs["fail_closed"] is True  # audit-first
    assert all(c.kwargs["organization_id"] == ORG_A for c in env.bus.await_args_list)


@pytest.mark.asyncio
async def test_reanalysis_is_tenant_scoped_audit_first_capped_and_partial_failure_safe(env):
    _legacy_run(env)
    other = await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[900]), user=env.a)
    assert other["selected"] == 0 and other["skipped_other_tenant"] == 1 and other["changed"] == 0
    env.audit.side_effect = RuntimeError("audit down")
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[500]), user=env.a)
    assert exc.value.status_code == 503 and "analysis" not in env.session.get(WorkflowRunModel, 500).annotations
    env.audit.side_effect = None
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=list(range(1, 202))), user=env.a)
    assert exc.value.status_code == 400 and "200" in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(), user=env.a)
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(
            business.BulkAnalyzeRequest(filters=business.ConversationFilterRequest(source="browser"), expected_count=999), user=env.a)
    assert exc.value.status_code == 409
    nobody = SimpleNamespace(id=9, selected_organization_id=None, is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[500]), user=nobody)
    assert exc.value.status_code == 403

    real = env.db.mutate_workflow_run_annotations
    _legacy_run(env, 501)
    calls = {"n": 0}

    async def flaky(run_id, mutator, *, organization_id=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("db hiccup")
        return await real(run_id, mutator, organization_id=organization_id)

    env.db.mutate_workflow_run_annotations = flaky
    with pytest.raises(HTTPException) as exc:
        await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[500, 501]), user=env.a)
    assert exc.value.status_code == 500 and "safe to repeat" in exc.value.detail
    env.db.mutate_workflow_run_annotations = real
    done = await business.bulk_reanalyze_conversations(business.BulkAnalyzeRequest(ids=[500, 501]), user=env.a)
    assert done["changed"] == 1 and done["skipped"]["up_to_date"] == 1  # only the one that had not completed


@pytest.mark.asyncio
async def test_reanalysis_by_filter_scope_respects_expected_count(env):
    _legacy_run(env)
    total = (await page(env, limit=1, source="browser"))["total"]
    res = await business.bulk_reanalyze_conversations(
        business.BulkAnalyzeRequest(filters=business.ConversationFilterRequest(source="browser"), expected_count=total, dry_run=True), user=env.a)
    assert res["status"] == "dry_run" and res["selected"] == total and res["eligible"] >= 1


# ------------------- defects found only by the real-browser acceptance run ----------------------- #
@pytest.mark.asyncio
async def test_event_stream_is_not_transformable_by_proxies(env):
    """The Next.js proxy gzips responses and buffered the stream (10 bytes in 12 s), so no live event ever
    reached a browser. `no-transform` is what makes compression middleware leave the stream alone."""
    resp = await business.stream_dashboard_events(last_event_id=None, user=env.a)
    cache_control = resp.headers["cache-control"]
    assert "no-transform" in cache_control and "no-cache" in cache_control
    assert resp.headers["x-accel-buffering"] == "no" and resp.media_type == "text/event-stream"


@pytest.mark.asyncio
async def test_cross_tenant_run_lookup_is_indistinguishable_from_missing(env):
    """A foreign run id must not reveal that it exists (403 vs 404 was an enumeration oracle)."""
    with pytest.raises(HTTPException) as foreign:
        await business.get_conversation_detail(900, user=env.a)
    with pytest.raises(HTTPException) as missing:
        await business.get_conversation_detail(424242, user=env.a)
    assert foreign.value.status_code == missing.value.status_code == 404
    assert foreign.value.detail.replace("900", "N") == missing.value.detail.replace("424242", "N")
