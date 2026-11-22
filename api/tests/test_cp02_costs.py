"""CP-02 cost analytics tests: truthful labels, full-set summaries, filters, export.

Cost statements use PostgreSQL JSONB operators, so they are verified by compiling
against the PostgreSQL dialect (no database needed) plus pure-logic assertions.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from api.db.filters import apply_workflow_run_filters, get_workflow_run_order_clause
from api.routes import superuser
from api.services.pricing import cost_analytics as ca

PG = postgresql.dialect()
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)

COST_337 = {
    "billing_status": "ESTIMATED",
    "call_duration_seconds": 225.0,
    "actual_cost": {
        "estimated": True,
        "total_inr": 8.2709,
        "ai_total_inr": 8.2709,
        "telephony_total_inr": 0.0,
        "components": [
            {"service": "realtime", "provider": "google", "model": "gemini-3.1-flash-live-preview", "priced": False},
            {"service": "realtime", "provider": "google_realtime", "estimated": True, "cost_inr": 8.2709},
        ],
        "exchange_rates": [{"pair": "USD_INR", "rate": 95.8945, "source_last_checked": "2026-05-22"}],
        "warnings": ["No llm pricing configured"],
    },
}


def sql(stmt) -> str:
    return str(stmt.compile(dialect=PG, compile_kwargs={"literal_binds": True}))


def run(**kw):
    base = dict(
        id=1, name="WR-1", mode="livekit", state="completed", is_completed=True, is_simulation=False,
        usage_info={}, cost_info={}, initial_context={}, gathered_context={}, annotations={}, logs={},
        created_at=NOW, recording_url=None, call_disposition="UNKNOWN",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ------------------------------- statements -------------------------------- #


def test_filters_compile_on_postgres_and_apply_expected_predicates():
    filters = [
        {"attribute": "costRange", "type": "numberRange", "value": {"min": 1, "max": 50}},
        {"attribute": "billingStatus", "type": "multiSelect", "value": {"codes": ["ESTIMATED", "NOT_COSTED"]}},
        {"attribute": "callOrigin", "type": "multiSelect", "value": {"codes": ["browser", "pstn"]}},
        {"attribute": "direction", "type": "radio", "value": {"status": "outbound"}},
        {"attribute": "engine", "type": "multiSelect", "value": {"codes": ["livekit"]}},
        {"attribute": "organizationId", "type": "number", "value": {"value": 4}},
        {"attribute": "search", "type": "text", "value": {"value": "WR-80"}},
        {"attribute": "dateRange", "type": "dateRange", "value": {"from": "2026-11-01T00:00:00+00:00"}},
        {"attribute": "totallyUnknown", "type": "text", "value": {"value": "ignored"}},
    ]
    text = sql(ca.build_summary_stmt(filters))
    for fragment in ("total_inr", "billing_status", "organization_id", "call_type", "workflow_runs.mode IN ('livekit')"):
        assert fragment in text, fragment
    assert "totallyUnknown" not in text and "ignored" not in text


def test_search_filter_escapes_like_wildcards():
    text = sql(ca.build_summary_stmt([{"attribute": "search", "type": "text", "value": {"value": "50%_off"}}]))
    # '%' and '_' typed by the user are escaped with '!' so they match literally, not as wildcards.
    assert "50!%" in text and "!_off" in text and "ESCAPE '!'" in text


def test_cost_sort_and_stable_ordering_clause_compile():
    clause = get_workflow_run_order_clause("cost", "desc")
    assert "total_inr" in sql(clause) and "DESC" in sql(clause)


# ------------------------------- aggregation ------------------------------- #


def test_summary_uses_costed_minutes_only_and_labels_estimates():
    # 13 runs costed (all estimates), 241 uncosted; durations of uncosted runs must not dilute the rate.
    totals = (254, 13, 181.08, 181.08, 0.0, 4925.0)
    summary = ca.assemble_summary(totals, [("ESTIMATED", 13), (None, 241)], {"unpriced_models": []}, 0)
    assert summary["basis"] == "estimate"
    assert summary["scope"] == {"runs": 254, "costed_runs": 13, "uncosted_runs": 241}
    assert summary["reconciled_runs"] == 0 and summary["estimated_runs"] == 13
    assert summary["status_breakdown"] == {"ESTIMATED": 13, "NOT_COSTED": 241}
    assert summary["costed_minutes"] == round(4925 / 60, 2)
    assert summary["per_minute_inr"] == round(181.08 / (4925 / 60), 4)
    assert summary["telephony"]["applicable"] is False and summary["telephony"]["negotiated_rate_configured"] is False
    assert summary["target"] == {"per_minute_inr": None, "status": "not_evaluated"}  # no target is claimed


def test_summary_basis_reconciled_mixed_and_empty():
    assert ca.assemble_summary((2, 2, 4.0, 3.0, 1.0, 120.0), [("VERIFIED", 2)], {}, 2)["basis"] == "reconciled"
    assert ca.assemble_summary((3, 3, 4.0, 3.0, 1.0, 120.0), [("VERIFIED", 1), ("ESTIMATED", 2)], {}, 1)["basis"] == "mixed"
    empty = ca.assemble_summary((0, 0, 0, 0, 0, 0), [], {}, 0)
    assert empty["basis"] == "no_cost_data" and empty["per_minute_inr"] is None


def test_pricing_inputs_report_unpriced_models_fx_age_and_no_invented_rates():
    info = ca.summarize_pricing_inputs([COST_337, COST_337])
    assert info["unpriced_models"] == [{"model": "google/gemini-3.1-flash-live-preview", "runs": 2}]
    assert info["fx"] == [{"pair": "USD_INR", "rate": 95.8945, "source_last_checked": "2026-05-22"}]
    assert any("negotiated carrier" in n for n in info["notes"])
    assert not any("1.90" in n for n in info["notes"])


# --------------------------------- rows/CSV -------------------------------- #


def test_cost_row_labels_browser_no_pstn_estimate_and_archive_state():
    r = run(id=337, initial_context={"participant_kind": 0}, cost_info=COST_337, usage_info={"call_duration_seconds": 225},
            annotations={"archive": {"archived_at": "2026-11-16T10:00:00+00:00"}})
    row = ca.cost_row(r, workflow_name="Default Voice Assistant", organization_id=1)
    assert row["origin"] == "browser" and row["telephony_inr"] == "n/a (no PSTN)"
    assert row["cost_status"] == "estimated" and row["is_estimate"] is True and row["unpriced_components"] == 1
    assert row["duration_seconds"] == 225.0 and row["conversation_state"] == "archived"  # cost evidence survives archive
    assert row["total_inr"] == 8.2709


def test_cost_csv_is_safe_and_unicode():
    r = run(id=5, cost_info=COST_337)
    row = ca.cost_row(r, workflow_name="=cmd|' /C calc'!A0 తెలుగు", organization_id=2)
    data = ca.build_cost_csv([row])
    assert data.startswith("﻿".encode())
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0] == ca.COST_CSV_COLUMNS
    cell = dict(zip(rows[0], rows[1]))["workflow"]
    assert cell.startswith("'=") and "తెలుగు" in cell


# --------------------------------- routes ---------------------------------- #


class FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    async def execute(self, stmt):
        self.executed.append(stmt)
        return self.results.pop(0)


class Ctx:
    def __init__(self, session):
        self.s = session

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *a):
        return False


def _res(**kw):
    m = MagicMock()
    for k, v in kw.items():
        getattr(m, k).return_value = v
    return m


@pytest.mark.asyncio
async def test_cost_summary_route_returns_full_set_summary_and_rejects_bad_filters():
    session = FakeSession([
        _res(one=(254, 13, 181.08, 181.08, 0.0, 4925.0)),
        _res(all=[("ESTIMATED", 13), (None, 241)]),
        _res(all=[(COST_337,)]),
        _res(scalar=0),
    ])
    db = SimpleNamespace(async_session=lambda: Ctx(session))
    root = SimpleNamespace(id=1, selected_organization_id=1)
    with patch.object(superuser, "db_client", db):
        out = await superuser.get_cost_summary(filters=None, user=root)
        assert out["basis"] == "estimate" and out["scope"]["runs"] == 254
        assert out["pricing_inputs"]["unpriced_models"][0]["model"].startswith("google/")
        for bad in ("{not json", json.dumps({"a": 1}), json.dumps(["x"])):
            with pytest.raises(HTTPException) as exc:
                await superuser.get_cost_summary(filters=bad, user=root)
            assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_cost_export_caps_rejects_non_csv_and_audits_with_real_org_only():
    root = SimpleNamespace(id=1, selected_organization_id=7)
    r = run(id=9, cost_info=COST_337, usage_info={"call_duration_seconds": 225}, initial_context={"participant_kind": 0})
    audit = AsyncMock()
    with patch.object(superuser.audit_service, "record_event", audit):
        ok = FakeSession([_res(scalar=1), _res(all=[(r, "Agent", 7)])])
        with patch.object(superuser, "db_client", SimpleNamespace(async_session=lambda: Ctx(ok))):
            resp = await superuser.export_cost_records(format="csv", ids="9", filters=None, user=root)
            body = b"".join([c async for c in resp.body_iterator]).decode("utf-8-sig")
        assert resp.headers["X-Export-Count"] == "1" and "n/a (no PSTN)" in body and "estimated" in body
        assert audit.await_args.kwargs["organization_id"] == 7
        audit.reset_mock()

        too_big = FakeSession([_res(scalar=ca.MAX_COST_EXPORT_ROWS + 1)])
        with patch.object(superuser, "db_client", SimpleNamespace(async_session=lambda: Ctx(too_big))):
            with pytest.raises(HTTPException) as exc:
                await superuser.export_cost_records(format="csv", ids=None, filters=None, user=root)
        assert exc.value.status_code == 400 and "Narrow the filters" in exc.value.detail

        with pytest.raises(HTTPException):
            await superuser.export_cost_records(format="xlsx", ids=None, filters=None, user=root)
        with pytest.raises(HTTPException):
            await superuser.export_cost_records(format="csv", ids="1,x", filters=None, user=root)

        # No verified organization => no audit row is written under a made-up org id.
        nobody = SimpleNamespace(id=2, selected_organization_id=None)
        empty = FakeSession([_res(scalar=0), _res(all=[])])
        with patch.object(superuser, "db_client", SimpleNamespace(async_session=lambda: Ctx(empty))):
            await superuser.export_cost_records(format="csv", ids=None, filters=None, user=nobody)
        audit.assert_not_awaited()


def test_superuser_run_response_accepts_new_labels():
    item = superuser.SuperuserWorkflowRunResponse(
        id=1, name="WR-1", workflow_id=1, workflow_name="A", user_id=1, organization_id=1, organization_name="o",
        mode="livekit", is_completed=True, recording_url=None, transcript_url=None, usage_info={}, cost_info={},
        initial_context={}, gathered_context={}, created_at=NOW, call_origin="browser",
        cost_summary={"status": "estimated"}, duration_seconds=225.0, conversation_state="active",
    )
    assert item.call_origin == "browser" and item.cost_summary["status"] == "estimated"
