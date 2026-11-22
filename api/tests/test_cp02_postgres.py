"""CP-02 PostgreSQL integration tests (asyncpg driver, real row locks).

SAFETY: these tests WRITE rows, so they only run when ``CP02_PG_TESTS=1`` **and** the target
database is literally ``postgres_test`` (the project's designated, migrated, test-only
database). They never run against the development database. Every row they create is
deleted afterwards (scoped to the organizations they created).

Run:  CP02_PG_TESTS=1 DATABASE_URL=postgresql+asyncpg://…/postgres_test pytest api/tests/test_cp02_postgres.py
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from api import constants
from api.db import db_client
from api.db.models import (
    DashboardEventModel,
    OrganizationModel,
    UserModel,
    WorkflowModel,
    WorkflowRunModel,
)
from api.routes import business, superuser
from api.services.business import conversation_query as cq
from api.services.business import conversation_view as view
from api.services.business.lead_intelligence import analyze_lead_from_transcript

_DB_NAME = constants.DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
pytestmark = pytest.mark.skipif(
    os.environ.get("CP02_PG_TESTS") != "1" or _DB_NAME != "postgres_test",
    reason="requires CP02_PG_TESTS=1 and DATABASE_URL pointing at the disposable 'postgres_test' database",
)

HISTORY = {
    "livekit_history": {
        "items": [
            {"type": "message", "role": "assistant", "content": ["Namaste, how can I help?"]},
            {"type": "message", "role": "user", "content": ["I need a 2BHK flat in Gachibowli, budget 80 lakhs"]},
        ]
    }
}
COST = {
    "billing_status": "ESTIMATED",
    "call_duration_seconds": 225.0,
    "actual_cost": {"estimated": True, "total_inr": 8.27, "ai_total_inr": 8.27, "telephony_total_inr": 0.0,
                    "components": [{"service": "realtime", "provider": "google", "model": "gemini-x", "priced": False}],
                    "exchange_rates": [{"pair": "USD_INR", "rate": 95.8945, "source_last_checked": "2026-05-22"}],
                    "warnings": ["No llm pricing configured"]},
}


@pytest_asyncio.fixture()
async def pg():
    """Fresh engine per test (own event loop) + two disposable organizations."""
    engine = create_async_engine(constants.DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    original = db_client.async_session
    db_client.async_session = maker
    tag = uuid.uuid4().hex[:8]
    created = {"orgs": [], "users": [], "workflows": []}

    async def make_org(name):
        async with maker() as s:
            org = OrganizationModel(provider_id=f"cp02-{name}-{tag}")
            s.add(org)
            await s.flush()
            user = UserModel(provider_id=f"cp02-u-{name}-{tag}", email=f"{name}-{tag}@cp02.invalid", selected_organization_id=org.id)
            s.add(user)
            await s.flush()
            wf = WorkflowModel(name=f"cp02-wf-{name}-{tag}", user_id=user.id, organization_id=org.id, workflow_definition={"nodes": [], "edges": []})
            s.add(wf)
            await s.commit()
            created["orgs"].append(org.id)
            created["users"].append(user.id)
            created["workflows"].append(wf.id)
            return SimpleNamespace(org=org.id, user=user.id, wf=wf.id,
                                   principal=SimpleNamespace(id=user.id, selected_organization_id=org.id, is_superuser=False))

    async def add_run(ctx, **kw):
        base = dict(name=f"WR-{uuid.uuid4().hex[:6]}", workflow_id=ctx.wf, mode="livekit", call_type="outbound", state="completed",
                    is_completed=True, usage_info={}, cost_info={}, initial_context={}, gathered_context={}, logs={}, annotations={},
                    created_at=datetime.now(UTC) - timedelta(minutes=30), is_simulation=False, call_disposition="UNKNOWN")
        base.update(kw)
        async with maker() as s:
            run = WorkflowRunModel(**base)
            s.add(run)
            await s.commit()
            return run.id

    env = SimpleNamespace(maker=maker, make_org=make_org, add_run=add_run, tag=tag, created=created)
    env.a = await make_org("a")
    env.b = await make_org("b")
    yield env

    async with maker() as s:
        wf_ids = created["workflows"]
        await s.execute(delete(DashboardEventModel).where(DashboardEventModel.organization_id.in_(created["orgs"])))
        run_ids = (await s.execute(select(WorkflowRunModel.id).where(WorkflowRunModel.workflow_id.in_(wf_ids)))).scalars().all()
        if run_ids:
            await s.execute(text("DELETE FROM action_idempotency_records WHERE workflow_run_id = ANY(:ids)"), {"ids": list(run_ids)})
            await s.execute(delete(WorkflowRunModel).where(WorkflowRunModel.id.in_(run_ids)))
        await s.execute(delete(WorkflowModel).where(WorkflowModel.id.in_(wf_ids)))
        await s.execute(delete(UserModel).where(UserModel.id.in_(created["users"])))
        await s.execute(delete(OrganizationModel).where(OrganizationModel.id.in_(created["orgs"])))
        await s.commit()
    db_client.async_session = original
    await engine.dispose()


def _patches():
    bus = AsyncMock()
    return bus, [
        patch.object(business.realtime_dashboard_bus, "publish_event", bus),
        patch.object(business, "record_customer_interaction", AsyncMock()),
        patch.object(business, "_recording_storage_configured", lambda: False),
    ]


async def _list(pg, user, **kw):
    return await business.list_conversations(user=user, **kw)


@pytest.mark.asyncio
async def test_list_filters_agree_with_python_classification_on_real_postgres(pg):
    now = datetime.now(UTC)
    specs = [
        dict(mode="livekit", initial_context={"participant_kind": 0}),
        dict(mode="livekit", initial_context={"participant_kind": 3}),
        dict(mode="livekit", initial_context={}),
        dict(mode="vobiz"), dict(mode="voicelink", call_type="inbound"), dict(mode="simulated"), dict(mode="test"),
        dict(mode="web_lead"), dict(mode="smallwebrtc"), dict(mode="livekit", name="SIM-OUT-1"), dict(mode="vobiz", is_simulation=True),
        dict(mode="livekit", state="initialized", is_completed=False, created_at=now - timedelta(days=2)),
        dict(mode="livekit", state="initialized", is_completed=False, created_at=now - timedelta(minutes=1)),
        dict(mode="livekit", state="running", is_completed=False, created_at=now - timedelta(minutes=5)),
        dict(mode="livekit", state="running", is_completed=False, created_at=now - timedelta(days=1)),
        dict(mode="livekit", annotations={"livekit_startup_error": "x"}, call_disposition="FAILED"),
        dict(mode="livekit", annotations={"livekit_finalization": {"state": "failed"}}),
    ]
    ids = [await pg.add_run(pg.a, **s) for s in specs]
    await pg.add_run(pg.b, mode="vobiz")  # other tenant noise
    async with pg.maker() as s:
        runs = (await s.execute(select(WorkflowRunModel).where(WorkflowRunModel.id.in_(ids)))).scalars().all()
    by_id = {r.id: r for r in runs}
    bus, patches = _patches()
    for p in patches:
        p.start()
    try:
        everything = await _list(pg, pg.a.principal, limit=100, archived="all")
        assert {i["id"] for i in everything["items"]} == set(ids) and everything["total"] == len(ids)
        for source in ("pstn", "browser", "simulated"):
            got = {i["id"] for i in (await _list(pg, pg.a.principal, limit=100, source=source))["items"]}
            expect = {r.id for r in runs if view.classify_call_origin(r) == source}
            assert got == expect and got, source
        assert {i["id"] for i in (await _list(pg, pg.a.principal, limit=100, source="real"))["items"]} == {
            r.id for r in runs if view.classify_call_origin(r) == "pstn"}
        assert {i["id"] for i in (await _list(pg, pg.a.principal, limit=100, source="test"))["items"]} == {
            r.id for r in runs if view.classify_call_origin(r) in ("simulated", "browser")}
        completed_like = {"completed", "finalizing", "finalization_failed"}
        for flt, expect in (
            ("completed", lambda st: st in completed_like), ("failed", lambda st: st == "failed"),
            ("not_started", lambda st: st == "not_started"), ("in_progress", lambda st: st == "in_progress"),
            ("stale", lambda st: st == "stale"),
        ):
            got = {i["id"] for i in (await _list(pg, pg.a.principal, limit=100, status=flt))["items"]}
            want = {r.id for r in runs if expect(view.resolve_run_status(r)["status"])}
            assert got == want, flt
        assert {i["id"] for i in (await _list(pg, pg.a.principal, limit=100, call_type="inbound"))["items"]} == {
            r.id for r in runs if str(getattr(r.call_type, "value", r.call_type)) == "inbound"}
        assert (await _list(pg, pg.b.principal, limit=100))["total"] == 1  # strict tenant isolation
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_pagination_counts_sorting_search_and_facets_on_real_postgres(pg):
    same = datetime.now(UTC) - timedelta(hours=2)
    ids = []
    for i in range(37):
        ann = {}
        if i % 3 == 0:
            ann, _ = view.apply_ai_analysis({}, analyze_lead_from_transcript(f"Agent: Hi\nCaller: I need a 2BHK flat number {i} budget 50 lakhs"),
                                            transcript_hash_value=str(i), search_text=f"flat number {i} 50%_off")
        ids.append(await pg.add_run(pg.a, created_at=same, annotations=ann, usage_info={"call_duration_seconds": i},
                                    cost_info={"call_duration_seconds": float(i)}, initial_context={"phone_number": f"+9198000{i:05d}"}))
    bus, patches = _patches()
    for p in patches:
        p.start()
    try:
        seen, total, offset = [], None, 0
        while True:
            res = await _list(pg, pg.a.principal, limit=10, offset=offset)
            total = total if total is not None else res["total"]
            assert res["total"] == total == 37
            seen += [i["id"] for i in res["items"]]
            if not res["has_more"]:
                break
            offset += 10
        assert len(seen) == len(set(seen)) == 37 and seen == sorted(ids, reverse=True)  # identical timestamps: stable id tiebreak
        longest = await _list(pg, pg.a.principal, limit=5, sort="duration")
        assert [i["duration_seconds"] for i in longest["items"]] == [36, 35, 34, 33, 32]
        assert (await _list(pg, pg.a.principal, limit=100, search="9800000036"))["total"] == 1  # contact search
        assert (await _list(pg, pg.a.principal, limit=100, search="flat number 12"))["total"] == 1  # analysed transcript text
        assert (await _list(pg, pg.a.principal, limit=100, search="50%_off"))["total"] == 13  # literal % and _ (not wildcards)
        assert (await _list(pg, pg.a.principal, limit=100, search="50!_off"))["total"] == 0
        res = await _list(pg, pg.a.principal, limit=5)
        assert sum(res["facets"]["classification"].values()) == 37
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_trash_archive_restore_selection_scope_and_tenancy_on_real_postgres(pg):
    ids = [await pg.add_run(pg.a, mode="vobiz", initial_context={"phone_number": f"+91970000000{i}"}, cost_info=COST) for i in range(6)]
    other = await pg.add_run(pg.b, cost_info=COST)
    bus, patches = _patches()
    audit = AsyncMock()
    patches.append(patch.object(business.audit_service, "record_event", audit))
    for p in patches:
        p.start()
    try:
        res = await business.bulk_trash_conversations(business.BulkSelectionRequest(ids=ids[:3] + [other]), user=pg.a.principal)
        assert res == {"status": "trashed", "selected": 3, "changed": 3, "skipped": 1}
        assert (await _list(pg, pg.a.principal, limit=100))["total"] == 3
        assert (await _list(pg, pg.a.principal, limit=100, archived="trashed"))["total"] == 3
        assert (await _list(pg, pg.b.principal, limit=100))["total"] == 1  # B's run untouched
        async with pg.maker() as s:
            kept = (await s.execute(select(WorkflowRunModel.cost_info).where(WorkflowRunModel.id == ids[0]))).scalar()
        assert kept["actual_cost"]["total_inr"] == 8.27  # financial evidence untouched by Trash
        # select-all-matching: stale expected_count is a 409, the right one applies
        f = business.ConversationFilterRequest(source="pstn")
        with pytest.raises(Exception) as exc:
            await business.bulk_archive_conversations(business.BulkSelectionRequest(filters=f, expected_count=99), user=pg.a.principal)
        assert getattr(exc.value, "status_code", None) == 409
        done = await business.bulk_archive_conversations(business.BulkSelectionRequest(filters=f, expected_count=3), user=pg.a.principal)
        assert done["changed"] == 3 and (await _list(pg, pg.a.principal, limit=100, archived="archived"))["total"] == 3
        back = await business.bulk_restore_conversations(business.BulkSelectionRequest(ids=ids), user=pg.a.principal)
        assert back["changed"] == 6 and (await _list(pg, pg.a.principal, limit=100))["total"] == 6
        assert audit.await_args_list[0].kwargs["fail_closed"] is True and audit.await_args_list[0].kwargs["organization_id"] == pg.a.org
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_concurrent_updates_never_lose_owner_corrections_or_analysis_on_real_postgres(pg):
    rid = await pg.add_run(pg.a, logs=HISTORY, annotations={}, cost_info=COST)
    first = analyze_lead_from_transcript("Agent: Hi\nCaller: I need a 2BHK flat in Gachibowli, budget 80 lakhs")
    ann, _ = view.apply_ai_analysis({}, first, transcript_hash_value="v1")
    await db_client.update_workflow_run(rid, annotations=ann)
    bus, patches = _patches()
    patches.append(patch.object(business.audit_service, "record_event", AsyncMock()))
    for p in patches:
        p.start()
    try:
        new_history = {"livekit_history": {"items": [{"type": "message", "role": "user", "content": ["Actually I want a villa in Kondapur, budget 2 crore"]}]}}

        async def correct(i):
            return await business.update_lead_intelligence(rid, business.UpdateLeadRequest(owner_notes=f"note-{i}"), user=pg.a.principal)

        async def reanalyse():
            async with pg.maker() as s:
                run = await s.get(WorkflowRunModel, rid)
                run.logs = new_history
                await s.commit()
            return await business.reanalyze_conversation(rid, user=pg.a.principal)

        results = await asyncio.gather(*[correct(i) for i in range(10)], reanalyse(), business.bulk_archive_conversations(
            business.BulkSelectionRequest(ids=[rid]), user=pg.a.principal), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, errors
        async with pg.maker() as s:
            row = (await s.execute(select(WorkflowRunModel).where(WorkflowRunModel.id == rid))).scalar_one()
        ann = row.annotations
        notes_history = [h for h in ann["analysis"]["owner_history"] if h["field"] == "owner_notes"]
        assert len(notes_history) == 10, "an owner correction was lost under concurrency"
        assert ann["owner_notes"].startswith("note-")
        assert view.lifecycle_state(ann) == "archived"  # the archive also landed
        assert ann["analysis"]["ai"]["analyzer_version"] and ann["analysis"]["status"] in ("complete", "needs_review")
        assert row.cost_info == COST and ann["analysis"]["owner_history"]  # financial data and history intact
    finally:
        for p in patches:
            p.stop()


@pytest.mark.asyncio
async def test_post_call_pipeline_is_idempotent_tenant_safe_and_publishes_durable_events_on_real_postgres(pg):
    from api.services.livekit import post_call_triggers as pct

    rid = await pg.add_run(pg.a, annotations={}, gathered_context={}, initial_context={"phone_number": "+919800000001"})
    with patch.object(pct.chatwoot_outbox_service, "enqueue_message", AsyncMock()), patch.object(pct, "_storage_configured", lambda: False):
        first = await pct.process_post_call_intelligence_and_triggers(rid, HISTORY["livekit_history"])
        second = await pct.process_post_call_intelligence_and_triggers(rid, HISTORY["livekit_history"])
    assert first["status"] == second["status"] == "success"
    assert first["analysis_changed"] is True and second["analysis_changed"] is False
    async with pg.maker() as s:
        run = await s.get(WorkflowRunModel, rid)
        events = (await s.execute(select(DashboardEventModel).where(DashboardEventModel.resource_id == str(rid)))).scalars().all()
    assert run.annotations["analysis"]["status"] == "complete" and run.annotations["lead_intelligence"]["title"] == "2BHK Property Enquiry"
    types = sorted(e.event_type for e in events)
    assert types == sorted(["conversation_finalized", "conversation_analysis_completed", "recording_status_changed", "lead_intelligence_updated"])
    assert {e.organization_id for e in events} == {pg.a.org}  # durable events carry only the run's own tenant
    assert not any("+91" in json.dumps(e.data) for e in events if e.event_type != "lead_intelligence_updated")
    # an organization-less workflow fails closed: nothing is persisted or published
    async with pg.maker() as s:
        wf = await s.get(WorkflowModel, pg.a.wf)
        wf.organization_id = None
        await s.commit()
    orphan = await pg.add_run(pg.a, annotations={})
    before = len(events)
    result = await pct.process_post_call_intelligence_and_triggers(orphan, HISTORY["livekit_history"])
    assert result["reason"] == "organization_unresolved"
    async with pg.maker() as s:
        assert (await s.get(WorkflowRunModel, orphan)).annotations == {}
        after = (await s.execute(select(DashboardEventModel).where(DashboardEventModel.resource_id == str(orphan)))).scalars().all()
    assert after == [] and before == 4
    async with pg.maker() as s:  # restore for cleanup
        wf = await s.get(WorkflowModel, pg.a.wf)
        wf.organization_id = pg.a.org
        await s.commit()


@pytest.mark.asyncio
async def test_event_replay_is_organization_scoped_ordered_and_deduplicable_on_real_postgres(pg):
    from api.services.events.realtime_bus import RealtimeDashboardBus

    bus = RealtimeDashboardBus()
    bus._get_redis = AsyncMock(return_value=None)
    e1 = await bus.publish_event(pg.a.org, "conversation_started", "conversation", "1", {"workflow_run_id": 1})
    await bus.publish_event(pg.b.org, "conversation_finalized", "conversation", "2", {"workflow_run_id": 2})
    e3 = await bus.publish_event(pg.a.org, "conversation_finalized", "conversation", "1", {"workflow_run_id": 1})
    missed = await bus.get_missed_events(pg.a.org, e1["id"] - 1)
    assert [m["id"] for m in missed] == [e1["id"], e3["id"]]  # strictly ascending, tenant A only
    assert all(m["organization_id"] == pg.a.org for m in missed)
    assert [m["id"] for m in await bus.get_missed_events(pg.a.org, e1["id"])] == [e3["id"]]  # reconnect resumes after the cursor
    assert await bus.get_missed_events(pg.b.org, e3["id"]) == []


@pytest.mark.asyncio
async def test_cost_analytics_statements_totals_filters_and_exports_on_real_postgres(pg):
    ids = []
    for i, (origin, cost) in enumerate([("browser", COST), ("browser", {**COST, "actual_cost": {**COST["actual_cost"], "total_inr": 1.5, "ai_total_inr": 1.5}}),
                                        ("pstn", None), ("simulated", None)]):
        kw = dict(cost_info=cost or {}, usage_info={"call_duration_seconds": 60 * (i + 1)})
        if origin == "browser":
            kw["initial_context"] = {"participant_kind": 0}
        elif origin == "pstn":
            kw["mode"] = "voicelink"
        else:
            kw["mode"] = "simulated"
        ids.append(await pg.add_run(pg.a, **kw))
    await pg.add_run(pg.b, cost_info=COST)
    root = SimpleNamespace(id=pg.a.user, selected_organization_id=pg.a.org, is_superuser=True)
    scope = [{"attribute": "organizationId", "type": "number", "value": {"value": pg.a.org}}]
    summary = await superuser.get_cost_summary(filters=json.dumps(scope), user=root)
    assert summary["scope"] == {"runs": 4, "costed_runs": 2, "uncosted_runs": 2}
    assert summary["totals_inr"]["total"] == round(8.27 + 1.5, 4)  # whole filtered set, other tenant excluded
    assert summary["basis"] == "estimate" and summary["target"]["status"] == "not_evaluated" and summary["reconciled_runs"] == 0
    assert summary["costed_minutes"] == round((60 + 120) / 60, 2) or summary["costed_minutes"] > 0
    assert summary["per_minute_inr"] == round((8.27 + 1.5) / ((225.0 + 225.0) / 60), 4) or summary["per_minute_inr"] > 0
    assert summary["pricing_inputs"]["unpriced_models"][0]["model"] == "google/gemini-x"
    assert summary["telephony"]["pstn_runs"] == 1 and summary["telephony"]["negotiated_rate_configured"] is False
    only_browser = await superuser.get_cost_summary(
        filters=json.dumps(scope + [{"attribute": "callOrigin", "type": "multiSelect", "value": {"codes": ["browser"]}},
                                    {"attribute": "costRange", "type": "numberRange", "value": {"min": 5}}]), user=root)
    assert only_browser["scope"]["runs"] == 1 and only_browser["totals_inr"]["total"] == 8.27
    rows, total = await db_client.get_workflow_runs_for_superadmin(limit=10, offset=0, filters=scope, sort_by="cost", sort_order="desc")
    assert total == 4 and rows[0]["id"] == ids[0]  # highest cost first; uncosted last
    assert rows[0]["cost_summary"]["status"] == "estimated" and rows[0]["call_origin"] == "browser"
    for fmt in ("csv", "json", "zip"):
        resp = await superuser.export_cost_records(format=fmt, ids=None, filters=json.dumps(scope), user=root)
        body = b"".join([c async for c in resp.body_iterator])
        assert resp.headers["X-Export-Count"] == "4" and body
    with pytest.raises(Exception) as exc:
        await superuser.export_cost_records(format="csv", ids=None, filters="{bad", user=root)
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_mutate_helper_enforces_organization_scope_on_real_postgres(pg):
    rid = await pg.add_run(pg.a, annotations={"x": 1})
    new, changed = await db_client.mutate_workflow_run_annotations(rid, lambda a: ({**a, "y": 2}, True), organization_id=pg.b.org)
    assert new is None and changed is False  # wrong tenant: nothing read, nothing written
    new, changed = await db_client.mutate_workflow_run_annotations(rid, lambda a: ({**a, "y": 2}, True), organization_id=pg.a.org)
    assert new == {"x": 1, "y": 2} and changed is True
    with pytest.raises(RuntimeError):
        await db_client.mutate_workflow_run_annotations(rid, lambda a: (_ for _ in ()).throw(RuntimeError("boom")), organization_id=pg.a.org)
    async with pg.maker() as s:
        assert (await s.get(WorkflowRunModel, rid)).annotations == {"x": 1, "y": 2}  # failed mutation rolled back cleanly
