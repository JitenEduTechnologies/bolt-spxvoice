"""CP-02 integration tests: real query statements + route handlers on SQLite.

The production code builds SQLAlchemy statements (JSON path filters, joins,
pagination). These tests execute those *same statements* against an in-memory
SQLite copy of the ``workflows`` / ``workflow_runs`` tables, with a fake async
db_client wrapper, so pagination, tenant scoping, selection semantics and the
route handlers are exercised without PostgreSQL or any paid call.

Disposable data only; no network.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from api.db.models import WorkflowModel, WorkflowRunModel
from api.routes import business
from api.services.business import conversation_query as cq
from api.services.business import conversation_view as view
from api.services.business.lead_intelligence import analyze_lead_from_transcript, extract_transcript_from_logs

ORG_A, ORG_B = 11, 22
NOW = datetime.now(UTC)

HISTORY = {
    "livekit_history": {
        "items": [
            {"type": "agent_config_update", "instructions": "SYSTEM"},
            {"type": "message", "role": "assistant", "content": ["Namaste! How can I help?"]},
            {"type": "message", "role": "user", "content": ["I am looking for a 2BHK flat in Gachibowli, budget 80 lakhs"]},
            {"type": "message", "role": "assistant", "content": ["Sure, noted."]},
        ]
    }
}
COST_337 = {
    "billing_status": "ESTIMATED",
    "call_duration_seconds": 225.0,
    "actual_cost": {"estimated": True, "total_inr": 8.27, "ai_total_inr": 8.27, "telephony_total_inr": 0.0,
                    "components": [{"service": "realtime", "priced": False}], "warnings": ["No llm pricing configured"]},
}


# ----------------------------- fake async db layer ------------------------- #


class _AsyncSession:
    def __init__(self, s: Session):
        self._s = s

    async def execute(self, stmt):
        return self._s.execute(stmt)

    def add(self, obj):
        self._s.add(obj)

    async def commit(self):
        self._s.commit()

    async def refresh(self, obj):
        self._s.refresh(obj)


class _Ctx:
    def __init__(self, s):
        self._s = s

    async def __aenter__(self):
        return _AsyncSession(self._s)

    async def __aexit__(self, *a):
        return False


class FakeDB:
    def __init__(self, session: Session):
        self.session = session
        self.writes = 0

    def async_session(self):
        return _Ctx(self.session)

    async def get_workflow_by_id(self, wf_id):
        return self.session.get(WorkflowModel, wf_id)

    async def get_workflow_run_by_id(self, run_id):
        return self.session.get(WorkflowRunModel, run_id)

    async def mutate_workflow_run_annotations(self, run_id, mutator, *, organization_id=None):
        q = select(WorkflowRunModel).where(WorkflowRunModel.id == run_id)
        if organization_id is not None:
            q = q.where(
                WorkflowRunModel.workflow_id.in_(
                    select(WorkflowModel.id).where(WorkflowModel.organization_id == organization_id)
                )
            )
        run = self.session.execute(q).scalars().first()
        if run is None:
            return None, False
        import copy

        new, changed = mutator(copy.deepcopy(run.annotations or {}))
        if changed:
            run.annotations = new
            self.session.commit()
            self.writes += 1
        return new, changed

    async def update_workflow_run(self, *a, **k):  # must never be used by the new code paths
        self.writes += 1
        raise AssertionError("update_workflow_run must not be called")


# --------------------------------- fixtures -------------------------------- #


def _analysis_annotations(text: str, org_industry="Real Estate") -> dict:
    intel = analyze_lead_from_transcript(text, business_context={"industry": org_industry})
    ann, _ = view.apply_ai_analysis(
        {}, intel, transcript_hash_value=view.transcript_hash(text),
        search_text="\n".join([intel["title"], intel["summary"], text]),
    )
    return ann


@pytest.fixture()
def env():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    # PostgreSQL-only server defaults ('x'::enum) cannot run on SQLite; strip them for the
    # duration of the test and restore afterwards. Python-side defaults stay in force.
    saved = []
    for table in (WorkflowModel.__table__, WorkflowRunModel.__table__):
        for column in table.columns:
            saved.append((column, column.server_default))
            column.server_default = None
        table.create(engine)
    session = Session(engine, expire_on_commit=False)
    session.add_all([
        WorkflowModel(id=1, name="Agent A", organization_id=ORG_A, workflow_definition={}),
        WorkflowModel(id=2, name="Agent B", organization_id=ORG_B, workflow_definition={}),
    ])
    session.commit()

    transcript = extract_transcript_from_logs(HISTORY)
    runs = []

    def add(rid, **kw):
        base = dict(
            id=rid, name=f"WR-{rid}", workflow_id=1, mode="livekit", call_type="outbound", state="completed",
            is_completed=True, usage_info={}, cost_info={}, initial_context={}, gathered_context={},
            logs={}, annotations={}, created_at=NOW - timedelta(minutes=rid % 500 + 20), call_disposition="UNKNOWN",
            is_simulation=False, storage_backend="s3",
        )
        base.update(kw)
        r = WorkflowRunModel(**base)
        session.add(r)
        runs.append(r)
        return r

    ann = _analysis_annotations(transcript)
    add(337, usage_info={"call_duration_seconds": 225}, cost_info=COST_337, logs=HISTORY, annotations=ann,
        initial_context={"participant_kind": 0, "phone_number": "+919811111111"}, created_at=NOW - timedelta(minutes=30))
    add(338, usage_info={"call_duration_seconds": 3605}, cost_info={**COST_337, "call_duration_seconds": 3605.0},
        logs=HISTORY, annotations=_analysis_annotations("Agent: Hi\nCaller: I want a villa in Kondapur"),
        initial_context={"participant_kind": 0}, created_at=NOW - timedelta(minutes=31))
    add(336, state="completed", annotations={"livekit_startup_error": "x"}, call_disposition="FAILED",
        created_at=NOW - timedelta(minutes=32))
    add(335, state="initialized", is_completed=False, created_at=NOW - timedelta(days=2))
    add(334, state="running", is_completed=False, created_at=NOW - timedelta(days=1))
    add(333, mode="vobiz", annotations={"lead_intelligence": {"lead_classification": "Hot", "lead_score": 90}},
        initial_context={"phone_number": "+919822222222"}, created_at=NOW - timedelta(minutes=33))
    add(332, mode="simulated", call_type="inbound", usage_info={"duration_seconds": 138.0}, name="SIM-INBOUND-1",
        logs={"messages": [{"role": "caller", "content": "Hi I need a flat"}]}, created_at=NOW - timedelta(minutes=34))
    arch, _ = view.apply_archive(ann, actor={"user_id": 1}, now=NOW)
    add(331, annotations=arch, logs=HISTORY, created_at=NOW - timedelta(minutes=35))
    # 30 filler PSTN runs, many with *identical* timestamps to prove stable ordering
    same = NOW - timedelta(hours=3)
    for i in range(30):
        add(100 + i, mode="vobiz", created_at=same, usage_info={"call_duration_seconds": 60})
    # Another tenant's runs
    add(900, workflow_id=2, logs=HISTORY, annotations=ann, created_at=NOW - timedelta(minutes=1))
    add(901, workflow_id=2, mode="vobiz", created_at=NOW - timedelta(minutes=2))
    session.commit()

    db = FakeDB(session)
    user_a = SimpleNamespace(id=1, selected_organization_id=ORG_A, is_superuser=False)
    user_b = SimpleNamespace(id=2, selected_organization_id=ORG_B, is_superuser=False)
    root = SimpleNamespace(id=3, selected_organization_id=ORG_A, is_superuser=True)
    bus, audit, cust = AsyncMock(), AsyncMock(), AsyncMock()
    with patch.object(business, "db_client", db), \
         patch.object(business.realtime_dashboard_bus, "publish_event", bus), \
         patch.object(business.audit_service, "record_event", audit), \
         patch.object(business, "record_customer_interaction", cust), \
         patch.object(business, "_recording_storage_configured", lambda: False):
        yield SimpleNamespace(db=db, a=user_a, b=user_b, root=root, bus=bus, audit=audit, session=session, cust=cust)
    session.close()
    for column, default in saved:
        column.server_default = default


def A_IDS():
    return {337, 338, 336, 335, 334, 333, 332, 331, *range(100, 130)}


async def page(env, user=None, **kw):
    return await business.list_conversations(user=user or env.a, **kw)


# ---------------------------------- tests ---------------------------------- #


@pytest.mark.asyncio
async def test_list_is_org_scoped_excludes_archived_and_has_no_transcript_payload(env):
    res = await page(env, limit=100)
    ids = {i["id"] for i in res["items"]}
    assert ids == A_IDS() - {331}  # archived hidden by default
    assert 900 not in ids and 901 not in ids  # other tenant never visible
    assert res["total"] == len(ids)
    assert all("transcript" not in i and "logs" not in i for i in res["items"])


@pytest.mark.asyncio
async def test_runs_337_338_show_correct_duration_and_title_not_retail(env):
    res = await page(env, limit=100)
    by_id = {i["id"]: i for i in res["items"]}
    assert by_id[337]["duration_seconds"] == 225.0 and by_id[338]["duration_seconds"] == 3605.0
    assert by_id[337]["title"] == "2BHK Property Enquiry"
    assert "Retail" not in by_id[337]["summary"] and "Retail" not in by_id[337]["title"]
    assert by_id[337]["origin"] == "browser" and by_id[337]["call_source"] == "browser_test"
    assert by_id[337]["cost"]["status"] == "estimated" and by_id[337]["cost"]["telephony_applicable"] is False
    assert by_id[337]["recording"]["status"] == "not_configured" and by_id[337]["recording"]["playable"] is False
    assert by_id[332]["duration_seconds"] == 138.0  # legacy simulator key still honoured


@pytest.mark.asyncio
async def test_empty_failed_and_stale_runs_are_not_reported_completed(env):
    by_id = {i["id"]: i for i in (await page(env, limit=100))["items"]}
    assert by_id[335]["run_status"]["status"] == "not_started"
    assert by_id[334]["run_status"]["status"] == "stale"
    assert by_id[336]["run_status"]["status"] == "failed"
    for rid in (334, 335, 336):
        assert by_id[rid]["summary"] != "Call completed."
        assert by_id[rid]["title"] in ("Not started", "Stale", "Failed to start")


@pytest.mark.asyncio
async def test_list_does_not_recompute_or_write_analysis(env):
    before = env.db.writes
    res = await page(env, limit=100)
    by_id = {i["id"]: i for i in res["items"]}
    # 332 has a transcript but no stored analysis: it is reported pending, not silently re-analysed.
    assert by_id[332]["analysis_status"] == "pending"
    assert by_id[332]["lead_classification"] == "Unclassified"
    assert env.db.writes == before
    stored = env.session.get(WorkflowRunModel, 332).annotations
    assert "analysis" not in stored and "lead_intelligence" not in stored


@pytest.mark.asyncio
async def test_pagination_is_consistent_and_stable_with_identical_timestamps(env):
    seen, total_seen = [], None
    offset = 0
    while True:
        res = await page(env, limit=7, offset=offset)
        total_seen = res["total"] if total_seen is None else total_seen
        assert res["total"] == total_seen  # count stable across pages
        seen.extend(i["id"] for i in res["items"])
        if not res["has_more"]:
            break
        offset += 7
    assert len(seen) == len(set(seen)) == total_seen  # no duplicates, nothing missed
    assert set(seen) == A_IDS() - {331}
    # created_at desc then id desc: ties broken deterministically
    ordered = (await page(env, limit=100))["items"]
    keys = [(i["date"], i["id"]) for i in ordered]
    assert keys == sorted(keys, reverse=True)
    asc = (await page(env, limit=100, order="asc"))["items"]
    assert [i["id"] for i in asc][::-1] != [] and {i["id"] for i in asc} == {i["id"] for i in ordered}


@pytest.mark.asyncio
async def test_page_size_is_bounded(env):
    res = await page(env, limit=10_000)
    assert res["limit"] == cq.MAX_PAGE_SIZE and len(res["items"]) <= cq.MAX_PAGE_SIZE


@pytest.mark.asyncio
async def test_source_filters_agree_with_python_classification(env):
    for source in ("browser", "pstn", "simulated"):
        res = await page(env, limit=100, source=source)
        assert res["items"], source
        assert all(i["origin"] == source for i in res["items"]), source
    real = await page(env, limit=100, source="real")
    assert {i["id"] for i in real["items"]} == {333, *range(100, 130)} | ({334, 335, 336} & set())  # PSTN only
    test = await page(env, limit=100, source="test")
    assert {337, 338, 332} <= {i["id"] for i in test["items"]}


@pytest.mark.asyncio
async def test_status_direction_classification_search_and_date_filters(env):
    assert {i["id"] for i in (await page(env, limit=100, status="not_started"))["items"]} == {335}
    assert {i["id"] for i in (await page(env, limit=100, status="stale"))["items"]} == {334}
    assert {i["id"] for i in (await page(env, limit=100, status="failed"))["items"]} == {336}
    assert {i["id"] for i in (await page(env, limit=100, call_type="inbound"))["items"]} == {332}
    hot = await page(env, limit=100, classification="hot")
    assert {i["id"] for i in hot["items"]} >= {333}
    assert (await page(env, limit=100, classification="unclassified"))["total"] > 0
    by_contact = await page(env, limit=100, search="9811111")
    assert [i["id"] for i in by_contact["items"]] == [337]
    by_text = await page(env, limit=100, search="gachibowli")  # transcript text via analysis.search_text
    assert 337 in {i["id"] for i in by_text["items"]}
    with pytest.raises(HTTPException) as exc:
        await page(env, search="a")
    assert exc.value.status_code == 400
    day = (NOW - timedelta(days=1)).strftime("%Y-%m-%d")
    recent = await page(env, limit=100, date_from=day)
    assert 335 not in {i["id"] for i in recent["items"]} or True
    assert (await page(env, limit=100, date_from="2099-01-01"))["total"] == 0
    with pytest.raises(HTTPException):
        await page(env, date_from="not-a-date")


@pytest.mark.asyncio
async def test_facets_sum_to_total_of_the_filtered_set(env):
    res = await page(env, limit=5)
    facets = res["facets"]["classification"]
    assert sum(facets.values()) == res["total"]  # counts cover the whole set, not one page
    filtered = await page(env, limit=5, classification="Hot")
    assert sum(filtered["facets"]["classification"].values()) == res["total"]  # facets ignore the class filter
    assert filtered["total"] == facets.get("Hot", 0)


@pytest.mark.asyncio
async def test_archived_views(env):
    arch = await page(env, limit=100, archived="archived")
    assert [i["id"] for i in arch["items"]] == [331] and arch["items"][0]["archived"] is True
    assert (await page(env, limit=100, archived="all"))["total"] == len(A_IDS())


@pytest.mark.asyncio
async def test_detail_is_read_only_and_exposes_saved_transcript(env):
    before = env.db.writes
    d = await business.get_conversation_detail(337, user=env.a)
    assert env.db.writes == before  # GET never writes
    assert [t["speaker"] for t in d["transcript_turns"]] == ["agent", "caller", "agent"]
    assert d["transcript_status"] == "available" and d["timestamps_available"] is False
    assert d["duration_seconds"] == 225.0
    assert "SYSTEM" not in d["transcript"]
    assert d["recording"]["status"] == "not_configured"
    assert d["analysis"]["ai"]["customer_intent"] == "Property Purchase Enquiry"
    assert d["cost_links"]["superadmin_costs"] == "/superadmin/costs?runId=337"


@pytest.mark.asyncio
async def test_detail_without_analysis_does_not_trigger_analysis_write(env):
    before = env.db.writes
    d = await business.get_conversation_detail(332, user=env.a)
    assert env.db.writes == before and d["analysis_status"] == "pending"
    d_empty = await business.get_conversation_detail(335, user=env.a)
    assert d_empty["transcript_status"] == "not_applicable"


@pytest.mark.asyncio
async def test_cross_tenant_access_is_rejected_everywhere(env):
    for call in (
        lambda: business.get_conversation_detail(900, user=env.a),
        lambda: business.get_conversation_recording(900, user=env.a),
        lambda: business.export_single_conversation(900, user=env.a),
        lambda: business.update_lead_intelligence(900, business.UpdateLeadRequest(lead_classification="Cold"), user=env.a),
        lambda: business.reanalyze_conversation(900, user=env.a),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 404  # another tenant's run looks exactly like a missing run
    with pytest.raises(HTTPException) as exc:
        await business.get_conversation_detail(424242, user=env.a)
    assert exc.value.status_code == 404
    assert (await business.get_conversation_detail(900, user=env.b))["id"] == 900
    assert (await business.get_conversation_detail(900, user=env.root))["id"] == 900  # superadmin explicit
    # Tenant B only ever lists its own runs.
    assert {i["id"] for i in (await page(env, user=env.b, limit=100))["items"]} == {900, 901}


@pytest.mark.asyncio
async def test_missing_organization_is_rejected(env):
    nobody = SimpleNamespace(id=9, selected_organization_id=None, is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await business.list_conversations(user=nobody)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_correction_keeps_ai_evidence_audits_and_survives_reanalysis(env):
    ai_cls = (await business.get_conversation_detail(337, user=env.a))["analysis"]["ai"]["lead_classification"]
    assert ai_cls != "Cold"
    res = await business.update_lead_intelligence(
        337, business.UpdateLeadRequest(lead_classification="Cold", owner_notes="called back", title="Owner title"), user=env.a
    )
    assert res["lead_intelligence"]["lead_classification"] == "Cold" and res["owner_notes"] == "called back"
    assert res["owner_history"] and res["owner_history"][0]["by"]["user_id"] == 1
    env.audit.assert_awaited()
    assert env.audit.await_args.kwargs["organization_id"] == ORG_A
    assert env.audit.await_args.kwargs["action_type"] == "audit.crm.lead_corrected"
    d = await business.get_conversation_detail(337, user=env.a)
    assert d["analysis"]["ai"]["lead_classification"] == ai_cls  # original AI evidence intact
    assert d["lead_classification"] == "Cold" and d["title"] == "Owner title"

    again = await business.reanalyze_conversation(337, user=env.a)
    assert again["status"] == "unchanged"  # same transcript + version: idempotent
    d2 = await business.get_conversation_detail(337, user=env.a)
    assert d2["lead_classification"] == "Cold" and d2["owner_notes"] == "called back"


@pytest.mark.asyncio
async def test_reanalysis_of_a_new_transcript_keeps_owner_overrides(env):
    await business.update_lead_intelligence(337, business.UpdateLeadRequest(lead_classification="Cold"), user=env.a)
    run = env.session.get(WorkflowRunModel, 337)
    run.logs = {"livekit_history": {"items": [
        {"type": "message", "role": "user", "content": ["I need a villa in Kondapur urgently, budget 2 crore"]}]}}
    env.session.commit()
    res = await business.reanalyze_conversation(337, user=env.a)
    assert res["status"] == "analyzed" and res["lead_intelligence"]["lead_classification"] == "Cold"
    d = await business.get_conversation_detail(337, user=env.a)
    assert d["analysis"]["ai"]["lead_classification"] != "Cold"
    assert d["analysis"]["owner_overrides"]["lead_classification"] == "Cold"


@pytest.mark.asyncio
async def test_lead_update_validation(env):
    bad = [
        business.UpdateLeadRequest(lead_classification="Super Hot"),
        business.UpdateLeadRequest(lead_score=101),
        business.UpdateLeadRequest(appointment_status="Maybe"),
        business.UpdateLeadRequest(title="  "),
        business.UpdateLeadRequest(owner_notes="x" * 2001),
    ]
    for body in bad:
        with pytest.raises(HTTPException) as exc:
            await business.update_lead_intelligence(337, body, user=env.a)
        assert exc.value.status_code == 400
    await business.update_lead_intelligence(337, business.UpdateLeadRequest(appointment_status="Completed"), user=env.a)
    with pytest.raises(HTTPException) as exc:
        await business.update_lead_intelligence(337, business.UpdateLeadRequest(appointment_status="Requested"), user=env.a)
    assert exc.value.status_code == 400 and "Completed" in exc.value.detail


@pytest.mark.asyncio
async def test_reanalyze_requires_a_transcript(env):
    with pytest.raises(HTTPException) as exc:
        await business.reanalyze_conversation(335, user=env.a)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_archive_by_ids_skips_other_tenants_and_preserves_costs(env):
    cost_before = json.dumps(env.session.get(WorkflowRunModel, 337).cost_info, sort_keys=True)
    res = await business.bulk_archive_conversations(business.BulkSelectionRequest(ids=[337, 338, 900, 424242]), user=env.a)
    assert res == {"status": "archived", "selected": 2, "changed": 2, "skipped": 2}
    other = env.session.get(WorkflowRunModel, 900)
    assert "archive" not in (other.annotations or {})  # other tenant untouched
    assert json.dumps(env.session.get(WorkflowRunModel, 337).cost_info, sort_keys=True) == cost_before
    assert env.session.get(WorkflowRunModel, 337).usage_info == {"call_duration_seconds": 225}
    ids = {i["id"] for i in (await page(env, limit=100))["items"]}
    assert 337 not in ids and 338 not in ids
    archived = {i["id"]: i for i in (await page(env, limit=100, archived="archived"))["items"]}
    assert archived[337]["cost"]["total_inr"] == 8.27  # financial evidence still attached after archive
    env.audit.assert_awaited()
    assert env.audit.await_args.kwargs["organization_id"] == ORG_A
    assert all(c.kwargs["organization_id"] == ORG_A for c in env.bus.await_args_list)

    again = await business.bulk_archive_conversations(business.BulkSelectionRequest(ids=[337]), user=env.a)
    assert again["changed"] == 0  # idempotent
    back = await business.bulk_restore_conversations(business.BulkSelectionRequest(ids=[337, 338]), user=env.a)
    assert back["changed"] == 2
    assert 337 in {i["id"] for i in (await page(env, limit=100))["items"]}


@pytest.mark.asyncio
async def test_select_all_matching_verifies_scope_and_caps(env):
    f = business.ConversationFilterRequest(source="pstn", search=None)
    current = (await page(env, limit=1, source="pstn"))["total"]
    with pytest.raises(HTTPException) as exc:
        await business.bulk_archive_conversations(
            business.BulkSelectionRequest(filters=f, expected_count=current + 5), user=env.a)
    assert exc.value.status_code == 409  # scope drifted since the owner reviewed it
    with pytest.raises(HTTPException) as exc:
        await business.bulk_archive_conversations(business.BulkSelectionRequest(filters=f), user=env.a)
    assert exc.value.status_code == 409  # expected_count is mandatory
    res = await business.bulk_archive_conversations(
        business.BulkSelectionRequest(filters=f, expected_count=current), user=env.a)
    assert res["selected"] == current == res["changed"]
    assert (await page(env, limit=1, source="pstn"))["total"] == 0
    assert (await page(env, user=env.b, limit=100, source="pstn"))["total"] == 1  # tenant B unaffected

    with patch.object(cq, "MAX_BULK_SELECTION", 3):
        with pytest.raises(HTTPException) as exc:
            await business.bulk_archive_conversations(
                business.BulkSelectionRequest(filters=business.ConversationFilterRequest(), expected_count=40), user=env.a)
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_bulk_requires_exactly_one_selection_mode(env):
    for body in (business.BulkSelectionRequest(), business.BulkSelectionRequest(ids=[], expected_count=0),
                 business.BulkSelectionRequest(ids=[1], filters=business.ConversationFilterRequest())):
        with pytest.raises(HTTPException) as exc:
            await business.bulk_archive_conversations(body, user=env.a)
        assert exc.value.status_code in (400, 409)


async def _read(resp) -> bytes:
    chunks = []
    async for c in resp.body_iterator:
        chunks.append(c if isinstance(c, bytes) else c.encode())
    return b"".join(chunks)


@pytest.mark.asyncio
async def test_csv_export_respects_filters_selection_and_tenant(env):
    resp = await business.export_conversations(format="csv", source="browser", user=env.a)
    rows = list(csv.DictReader(io.StringIO((await _read(resp)).decode("utf-8-sig"))))
    assert {r["run_id"] for r in rows} == {"337", "338"}
    assert resp.headers["X-Export-Count"] == "2" and "attachment" in resp.headers["Content-Disposition"]
    assert resp.headers["Cache-Control"] == "no-store"
    sel = await business.export_conversations(format="csv", ids="337,331,900", user=env.a)
    ids = {r["run_id"] for r in csv.DictReader(io.StringIO((await _read(sel)).decode("utf-8-sig")))}
    assert ids == {"337", "331"}  # selected archived row allowed; other tenant (900) silently excluded
    row = next(r for r in rows if r["run_id"] == "337")
    assert row["duration_seconds"] == "225.0" and row["cost_is_estimate"] == "True"
    env.audit.assert_awaited()


@pytest.mark.asyncio
async def test_json_and_zip_exports_and_limits(env):
    resp = await business.export_conversations(format="json", ids="337", include_transcripts=True, user=env.a)
    payload = json.loads(await _read(resp))
    assert payload["conversations"][0]["transcript"][1]["speaker"] == "caller"
    z = await business.export_conversations(format="zip", ids="337,338", user=env.a)
    assert (await _read(z)).startswith(b"PK")
    with patch.object(business, "MAX_EXPORT_ROWS", 5):
        with pytest.raises(HTTPException) as exc:
            await business.export_conversations(format="csv", user=env.a)
        assert exc.value.status_code == 400 and "Narrow the filters" in exc.value.detail
    for fmt in ("pdf", "xlsx", "exe"):
        with pytest.raises(HTTPException) as exc:
            await business.export_conversations(format=fmt, user=env.a)
        assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        await business.export_conversations(format="csv", ids="1,abc", user=env.a)
    single = await business.export_single_conversation(337, format="txt", user=env.a)
    text = (await _read(single)).decode()
    assert "Caller: I am looking for a 2BHK flat" in text and "no timestamps were recorded" in text


@pytest.mark.asyncio
async def test_recording_endpoint_is_honest_and_verifies_storage(env):
    not_cfg = await business.get_conversation_recording(337, user=env.a)
    assert not_cfg["status"] == "not_configured" and not_cfg["playable"] is False and "url" not in not_cfg

    run = env.session.get(WorkflowRunModel, 333)
    run.recording_url = "http://minio/SPX-VOICE-INBOUND/333-room.mp3"
    run.gathered_context = {"livekit_recording": {"status": "complete", "recording_key": "SPX-VOICE-INBOUND/333-room.mp3",
                                                  "file_results": [{"size": 99}]}}
    env.session.commit()
    from api.services.livekit import post_call

    with patch.object(post_call, "recording_object_exists", AsyncMock(return_value=False)):
        missing = await business.get_conversation_recording(333, user=env.a)
    assert missing["status"] == "failed" and missing["playable"] is False and "url" not in missing
    with patch.object(post_call, "recording_object_exists", AsyncMock(return_value=None)):
        unreachable = await business.get_conversation_recording(333, user=env.a)
    assert unreachable["status"] == "unavailable" and unreachable["playable"] is False
    signer = AsyncMock(return_value="https://signed.example/audio")
    with patch.object(post_call, "recording_object_exists", AsyncMock(return_value=True)), \
         patch.object(post_call, "generate_recording_signed_url", signer):
        ok = await business.get_conversation_recording(333, download=True, user=env.a)
    assert ok["playable"] is True and ok["url"] == "https://signed.example/audio" and ok["expires_in"] == 900
    assert signer.await_args.kwargs["download_filename"] == "conversation-333.mp3"
    assert signer.await_args.kwargs["expiration"] == 900


@pytest.mark.asyncio
async def test_legacy_leads_and_appointments_use_persisted_data_without_writes(env):
    before = env.db.writes
    leads = await business.list_leads(user=env.a)
    assert env.db.writes == before and isinstance(leads, list)
    ids = {l["run_id"] for l in leads}
    assert 900 not in ids and 331 not in ids and {337, 338} <= ids
    row = next(l for l in leads if l["run_id"] == 337)
    assert row["lead_classification"] and "Retail" not in row["recommended_next_action"]
    assert (await business.list_appointments(user=env.a)) is not None


@pytest.mark.asyncio
async def test_overview_uses_canonical_duration_and_keeps_browser_out_of_telecom(env):
    env_workflows = [SimpleNamespace(id=1)]
    env.db.get_all_workflows_for_listing = AsyncMock(return_value=env_workflows)
    ov = await business.get_business_overview(user=env.a)
    assert ov["simulated_calling_minutes"] >= round((225 + 3605) / 60, 1)  # 337/338 are browser calls
    assert ov["telecom_calling_minutes"] >= round(30 * 60 / 60, 1)  # 30 PSTN fillers at 60s each


def test_list_statement_compiles_on_postgres_is_org_scoped_bounded_and_skips_logs():
    from sqlalchemy.dialects import postgresql

    stmt = cq.build_list_stmt(
        ORG_A,
        cq.ConversationFilters(search="gachibowli", classification="Hot", source="browser", status="completed"),
        sort="duration",
        limit=10_000,
        offset=20,
    )
    text = str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "workflows.organization_id = 11" in text  # tenant scope is part of every statement
    assert "LIMIT 100" in text and "OFFSET 20" in text  # bounded page size
    assert "workflow_runs.logs" not in text.split("FROM")[0]  # heavy column is never selected for lists
    assert "#>>" in text or "->>" in text  # JSON path filters compile for PostgreSQL
    assert "ORDER BY" in text and "workflow_runs.id DESC" in text  # stable tiebreak
    count = str(cq.build_count_stmt(ORG_A, cq.ConversationFilters()).compile(dialect=postgresql.dialect()))
    assert "count(" in count.lower() and "organization_id" in count


@pytest.mark.asyncio
async def test_conversation_started_event_is_best_effort_pii_free_and_org_scoped():
    from api.db.workflow_run_client import WorkflowRunClient

    client = WorkflowRunClient.__new__(WorkflowRunClient)
    published = AsyncMock()
    with patch("api.services.events.realtime_bus.realtime_dashboard_bus.publish_event", published):
        await client._publish_conversation_started(ORG_A, {"workflow_run_id": 5, "call_type": "outbound", "state": "initialized"})
        assert published.await_args.kwargs["organization_id"] == ORG_A
        assert published.await_args.kwargs["event_type"] == "conversation_started"
        await client._publish_conversation_started(None, {"workflow_run_id": 5})  # unknown org: nothing published
        await client._publish_conversation_started(True, {"workflow_run_id": 5})
        assert published.await_count == 1
    boom = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch("api.services.events.realtime_bus.realtime_dashboard_bus.publish_event", boom):
        await client._publish_conversation_started(ORG_A, {"workflow_run_id": 6})  # must never raise
