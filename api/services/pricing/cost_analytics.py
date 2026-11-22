"""Server-side cost analytics for the superadmin Cost Analytics page (CP-02).

* Summaries are computed in SQL over the *entire* filtered set, never from only
  the rows loaded on one page.
* Every figure is labelled with its basis. Estimates are never presented as
  reconciled charges, and per-minute rates divide cost only by the duration of
  runs that actually have a cost.
* Missing pricing inputs (unpriced models, FX source age, telephony contract rate)
  are reported explicitly. No rate is invented and no cost target is claimed.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import Select

from api.db.filters import apply_workflow_run_filters
from api.db.models import WorkflowRunModel
from api.services.business.conversation_export import safe_cell
from api.services.business.conversation_view import (
    classify_call_origin,
    lifecycle_state,
    resolve_duration_seconds,
    run_cost_summary,
)

MAX_COST_EXPORT_ROWS = 5000
MAX_PRICING_SCAN_ROWS = 500

COST_CSV_COLUMNS = [
    "run_id",
    "date",
    "organization_id",
    "workflow",
    "origin",
    "mode",
    "duration_seconds",
    "ai_inr",
    "telephony_inr",
    "total_inr",
    "cost_status",
    "is_estimate",
    "unpriced_components",
    "billing_status",
    "conversation_state",
]


def _cost_json(*path: str):
    expr: Any = cast(WorkflowRunModel.cost_info, JSONB)
    for key in path[:-1]:
        expr = expr.op("->")(key)
    return expr.op("->>")(path[-1])


def _num(*path: str):
    return cast(_cost_json(*path), Float)


def build_summary_stmt(filters: Optional[List[Dict[str, Any]]]) -> Select:
    total = _num("actual_cost", "total_inr")
    costed = total.isnot(None)
    stmt = select(
        func.count(WorkflowRunModel.id),
        func.count(case((costed, WorkflowRunModel.id))),
        func.coalesce(func.sum(total), 0.0),
        func.coalesce(func.sum(_num("actual_cost", "ai_total_inr")), 0.0),
        func.coalesce(func.sum(_num("actual_cost", "telephony_total_inr")), 0.0),
        func.coalesce(func.sum(case((costed, _num("call_duration_seconds")), else_=0.0)), 0.0),
    ).select_from(WorkflowRunModel)
    return apply_workflow_run_filters(stmt, filters)


def build_status_stmt(filters: Optional[List[Dict[str, Any]]]) -> Select:
    status = _cost_json("billing_status")
    stmt = select(status, func.count(WorkflowRunModel.id)).select_from(WorkflowRunModel)
    return apply_workflow_run_filters(stmt, filters).group_by(status)


def build_pricing_scan_stmt(filters: Optional[List[Dict[str, Any]]]) -> Select:
    stmt = select(WorkflowRunModel.cost_info).select_from(WorkflowRunModel).where(
        _cost_json("actual_cost", "total_inr").isnot(None)
    )
    return apply_workflow_run_filters(stmt, filters).order_by(WorkflowRunModel.id.desc()).limit(
        MAX_PRICING_SCAN_ROWS
    )


def summarize_pricing_inputs(cost_infos: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Which pricing inputs are missing/stale, from the persisted cost components."""
    unpriced: Dict[str, int] = {}
    fx: Dict[str, Dict[str, Any]] = {}
    estimated_components = 0
    for info in cost_infos:
        actual = (info or {}).get("actual_cost") or {}
        for component in actual.get("components") or []:
            if not isinstance(component, dict):
                continue
            if component.get("priced") is False:
                key = f"{component.get('provider', '?')}/{component.get('model', '?')}"
                unpriced[key] = unpriced.get(key, 0) + 1
            if component.get("estimated"):
                estimated_components += 1
        for rate in actual.get("exchange_rates") or []:
            if isinstance(rate, dict):
                fx[f"{rate.get('pair')}@{rate.get('rate')}"] = {
                    "pair": rate.get("pair"),
                    "rate": rate.get("rate"),
                    "source_last_checked": rate.get("source_last_checked"),
                }
    return {
        "unpriced_models": [{"model": m, "runs": n} for m, n in sorted(unpriced.items())],
        "fx": list(fx.values()),
        "estimated_components": estimated_components,
        "notes": [
            "AI cost is a duration-based estimate unless token pricing is configured for the model.",
            "A negotiated carrier (Vobiz) rate is not stored; telephony is reconciled only from provider CDRs.",
            "No cost-per-minute target is evaluated here: it requires verified token rates and CDR-reconciled calls.",
        ],
    }


def assemble_summary(
    totals: Sequence[Any],
    status_rows: Sequence[Sequence[Any]],
    pricing_inputs: Dict[str, Any],
    pstn_runs: int,
) -> Dict[str, Any]:
    runs, costed_runs, total_inr, ai_inr, tel_inr, costed_seconds = totals
    runs, costed_runs = int(runs or 0), int(costed_runs or 0)
    breakdown: Dict[str, int] = {}
    for status, count in status_rows:
        breakdown[status or "NOT_COSTED"] = int(count)
    reconciled = breakdown.get("VERIFIED", 0)
    minutes = float(costed_seconds or 0.0) / 60.0
    if costed_runs == 0:
        basis = "no_cost_data"
    elif reconciled == costed_runs:
        basis = "reconciled"
    elif reconciled == 0:
        basis = "estimate"
    else:
        basis = "mixed"
    return {
        "scope": {"runs": runs, "costed_runs": costed_runs, "uncosted_runs": runs - costed_runs},
        "totals_inr": {
            "total": round(float(total_inr or 0.0), 4),
            "ai": round(float(ai_inr or 0.0), 4),
            "telephony": round(float(tel_inr or 0.0), 4),
        },
        "costed_minutes": round(minutes, 2),
        "per_minute_inr": round(float(total_inr or 0.0) / minutes, 4) if minutes > 0 else None,
        "per_minute_basis": "total cost / duration of costed runs only",
        "status_breakdown": breakdown,
        "reconciled_runs": reconciled,
        "estimated_runs": costed_runs - reconciled,
        "basis": basis,
        "telephony": {
            "pstn_runs": int(pstn_runs),
            "applicable": pstn_runs > 0,
            "negotiated_rate_configured": False,
        },
        "pricing_inputs": pricing_inputs,
        "target": {"per_minute_inr": None, "status": "not_evaluated"},
    }


def cost_row(run: Any, *, workflow_name: Optional[str], organization_id: Optional[int]) -> Dict[str, Any]:
    """One financial record. Unknown amounts are labelled, never shown as zero."""
    summary = run_cost_summary(run)
    annotations = getattr(run, "annotations", None) or {}
    origin = classify_call_origin(run)
    costed = summary.get("status") != "not_costed"
    if origin != "pstn":
        telephony: Any = "n/a (no PSTN)"
    elif costed and summary.get("telephony_inr") is not None:
        telephony = summary.get("telephony_inr")
    else:
        telephony = "n/a (carrier cost pending)"
    return {
        "run_id": run.id,
        "date": run.created_at.isoformat() if getattr(run, "created_at", None) else None,
        "organization_id": organization_id,
        "workflow": workflow_name,
        "origin": origin,
        "mode": run.mode,
        "duration_seconds": round(resolve_duration_seconds(run), 1),
        "ai_inr": summary.get("ai_inr") if costed else "n/a (not costed)",
        "telephony_inr": telephony,
        "total_inr": summary.get("total_inr") if costed else "n/a (not costed)",
        "cost_status": summary.get("status"),
        "is_estimate": summary.get("estimated"),
        "unpriced_components": summary.get("unpriced_components", 0),
        "billing_status": (getattr(run, "cost_info", None) or {}).get("billing_status") or "NOT_COSTED",
        # Financial history is retained whatever happens to the conversation record.
        "conversation_state": lifecycle_state(annotations),
    }


def build_cost_csv(rows: Sequence[Dict[str, Any]]) -> bytes:
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(COST_CSV_COLUMNS)
    for row in rows:
        writer.writerow([safe_cell(row.get(col)) for col in COST_CSV_COLUMNS])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")


def build_cost_json(rows: Sequence[Dict[str, Any]], *, filters: Optional[Dict[str, Any]] = None) -> bytes:
    import json
    from datetime import UTC, datetime

    return json.dumps(
        {
            "schema": "spx-voice.cost-records.v1",
            "exported_at": datetime.now(UTC).isoformat(),
            "count": len(rows),
            "filters": filters or {},
            "note": "Amounts are estimates unless cost_status is 'reconciled'. 'n/a' means unknown, not zero.",
            "records": list(rows),
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")


def build_cost_zip(rows: Sequence[Dict[str, Any]], *, filters: Optional[Dict[str, Any]] = None) -> bytes:
    import io
    import json
    import zipfile
    from datetime import UTC, datetime

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("cost-records.csv", build_cost_csv(rows))
        archive.writestr("cost-records.json", build_cost_json(rows, filters=filters))
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema": "spx-voice.cost-records.zip.v1",
                    "exported_at": datetime.now(UTC).isoformat(),
                    "count": len(rows),
                    "files": ["cost-records.csv", "cost-records.json"],
                    "estimate_notice": "Amounts are estimates unless cost_status is 'reconciled'.",
                },
                indent=2,
            ),
        )
    return buffer.getvalue()
