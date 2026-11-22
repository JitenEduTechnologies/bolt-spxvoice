"""Server-side conversation queries for the owner dashboard (CP-02).

Statement builders are pure (they return SQLAlchemy ``Select`` objects) so they
can be unit tested without a running PostgreSQL. Every statement is scoped to the
caller's organization through the ``workflows`` join, never loads the heavy
``logs`` column for list rows, is bounded, and sorts stably (``created_at`` /
metric, then ``id``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import Float, and_, cast, func, or_, select
from sqlalchemy.orm import defer
from sqlalchemy.sql import Select

from api.db.models import WorkflowModel, WorkflowRunModel
from api.services.business.conversation_view import (
    NOT_STARTED_AFTER,
    STALE_RUN_AFTER,
    call_source_label,
    classify_call_origin,
    effective_intelligence,
    is_archived,
    is_trashed,
    lifecycle_state,
    mask_contact,
    recording_status,
    resolve_duration_seconds,
    resolve_run_status,
    run_cost_summary,
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25
MAX_BULK_SELECTION = 1000
MAX_EXPORT_ROWS = 5000
MIN_SEARCH_LENGTH = 2

SORT_KEYS = ("created_at", "duration", "lead_score")
SOURCE_VALUES = ("all", "real", "pstn", "browser", "simulated", "test")
STATUS_VALUES = ("all", "completed", "failed", "not_started", "in_progress", "stale")
ARCHIVE_VALUES = ("active", "archived", "trashed", "all")

_SIMULATED_MODES = ("test", "web", "simulated", "simulation", "browser_test")
_PSTN_MODES = ("vobiz", "voicelink", "livekit_sip", "pstn")
_BROWSER_MODES = ("smallwebrtc", "web_lead", "webrtc")
_STANDARD_KINDS = ("0", "standard", "participant_kind_standard")
_SIP_KINDS = ("3", "sip", "participant_kind_sip")


MAX_ZIP_ROWS = 200
MAX_ZIP_RECORDINGS = 10
MAX_RECORDING_BYTES = 20 * 1024 * 1024
MAX_TOTAL_RECORDING_BYTES = 60 * 1024 * 1024
MAX_JSON_TRANSCRIPT_ROWS = 500


class ConversationQueryError(ValueError):
    """Invalid filter/selection input (mapped to HTTP 400 by the routes)."""


class SelectionChangedError(ConversationQueryError):
    """A "select all matching" scope no longer matches what the caller reviewed (HTTP 409)."""


@dataclass
class ConversationFilters:
    search: Optional[str] = None
    classification: Optional[str] = None
    call_type: Optional[str] = None
    source: str = "all"
    status: str = "all"
    archived: str = "active"
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    run_ids: Optional[Sequence[int]] = None
    analysis_status: Optional[str] = None

    def validate(self) -> "ConversationFilters":
        if self.source not in SOURCE_VALUES:
            raise ConversationQueryError(f"source must be one of {SOURCE_VALUES}")
        if self.status not in STATUS_VALUES:
            raise ConversationQueryError(f"status must be one of {STATUS_VALUES}")
        if self.archived not in ARCHIVE_VALUES:
            raise ConversationQueryError(f"archived must be one of {ARCHIVE_VALUES}")
        if self.call_type and self.call_type.lower() not in ("inbound", "outbound"):
            raise ConversationQueryError("call_type must be inbound or outbound")
        if self.search is not None and len(self.search.strip()) and len(self.search.strip()) < MIN_SEARCH_LENGTH:
            raise ConversationQueryError(f"search must be at least {MIN_SEARCH_LENGTH} characters")
        return self

    def as_dict(self) -> Dict[str, Any]:
        return {
            "search": self.search,
            "classification": self.classification,
            "call_type": self.call_type,
            "source": self.source,
            "status": self.status,
            "archived": self.archived,
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "analysis_status": self.analysis_status,
        }


def parse_date_bound(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse ``YYYY-MM-DD`` or ISO-8601; a bare date end bound covers the whole day."""
    if not value:
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            day = date.fromisoformat(text)
            base = datetime(day.year, day.month, day.day, tzinfo=UTC)
            return base + timedelta(days=1) - timedelta(microseconds=1) if end_of_day else base
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError as exc:
        raise ConversationQueryError(f"invalid date value: {value!r}") from exc


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards using ``!`` (an explicit, dialect-independent escape char)."""
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _ann(*path: str):
    return WorkflowRunModel.annotations[tuple(path)]


def _participant_kind():
    return func.lower(func.coalesce(WorkflowRunModel.initial_context[("participant_kind",)].as_string(), ""))


def _simulated_condition():
    mode = func.lower(func.coalesce(WorkflowRunModel.mode, ""))
    return or_(
        WorkflowRunModel.is_simulation.is_(True),
        mode.in_(_SIMULATED_MODES),
        mode.like("%simulat%"),
        WorkflowRunModel.name.like("SIM-%"),
        WorkflowRunModel.name.like("TEST-%"),
    )


def _pstn_condition():
    mode = func.lower(func.coalesce(WorkflowRunModel.mode, ""))
    return and_(
        ~_simulated_condition(),
        or_(mode.in_(_PSTN_MODES), _participant_kind().in_(_SIP_KINDS)),
    )


def _browser_condition():
    mode = func.lower(func.coalesce(WorkflowRunModel.mode, ""))
    return and_(
        ~_simulated_condition(),
        ~_pstn_condition(),
        or_(
            mode.in_(_BROWSER_MODES),
            and_(mode == "livekit", _participant_kind().in_(_STANDARD_KINDS)),
        ),
    )


def _failed_condition():
    return or_(
        _ann("livekit_startup_error").as_string().isnot(None),
        WorkflowRunModel.call_disposition == "FAILED",
    )


def _classification_expr():
    return _ann("lead_intelligence", "lead_classification").as_string()


def _apply_filters(stmt: Select, filters: ConversationFilters, *, skip_classification: bool = False) -> Select:
    now = datetime.now(UTC)
    conds: List[Any] = []

    if filters.run_ids is not None:
        conds.append(WorkflowRunModel.id.in_(list(filters.run_ids)))

    archived_at = _ann("archive", "archived_at").as_string()
    trashed_at = _ann("trash", "trashed_at").as_string()
    if filters.archived == "active":
        conds.append(and_(archived_at.is_(None), trashed_at.is_(None)))
    elif filters.archived == "archived":
        conds.append(and_(archived_at.isnot(None), trashed_at.is_(None)))
    elif filters.archived == "trashed":
        conds.append(trashed_at.isnot(None))

    if filters.call_type:
        conds.append(WorkflowRunModel.call_type == filters.call_type.lower())

    if filters.date_from:
        conds.append(WorkflowRunModel.created_at >= filters.date_from)
    if filters.date_to:
        conds.append(WorkflowRunModel.created_at <= filters.date_to)

    if filters.source == "simulated":
        conds.append(_simulated_condition())
    elif filters.source in ("real", "pstn"):
        conds.append(_pstn_condition())
    elif filters.source == "browser":
        conds.append(_browser_condition())
    elif filters.source == "test":
        conds.append(or_(_simulated_condition(), _browser_condition()))

    if filters.status == "failed":
        conds.append(_failed_condition())
    elif filters.status == "completed":
        conds.append(and_(WorkflowRunModel.state == "completed", ~_failed_condition()))
    elif filters.status == "not_started":
        conds.append(
            and_(
                WorkflowRunModel.state == "initialized",
                WorkflowRunModel.created_at < now - NOT_STARTED_AFTER,
                ~_failed_condition(),
            )
        )
    elif filters.status == "in_progress":
        conds.append(
            and_(
                WorkflowRunModel.state == "running",
                WorkflowRunModel.created_at >= now - STALE_RUN_AFTER,
                ~_failed_condition(),
            )
        )
    elif filters.status == "stale":
        conds.append(
            and_(
                WorkflowRunModel.state == "running",
                WorkflowRunModel.created_at < now - STALE_RUN_AFTER,
                ~_failed_condition(),
            )
        )

    if filters.classification and not skip_classification:
        wanted = filters.classification.strip().lower()
        if wanted == "unclassified":
            conds.append(_classification_expr().is_(None))
        else:
            conds.append(func.lower(_classification_expr()) == wanted)

    if filters.analysis_status:
        conds.append(_ann("analysis", "status").as_string() == filters.analysis_status)

    if filters.search and filters.search.strip():
        needle = f"%{_escape_like(filters.search.strip().lower())}%"
        conds.append(
            or_(
                func.lower(WorkflowRunModel.name).like(needle, escape="!"),
                func.lower(
                    func.coalesce(WorkflowRunModel.initial_context[("phone_number",)].as_string(), "")
                ).like(needle, escape="!"),
                func.lower(
                    func.coalesce(WorkflowRunModel.initial_context[("caller_number",)].as_string(), "")
                ).like(needle, escape="!"),
                func.lower(
                    func.coalesce(WorkflowRunModel.gathered_context[("customer_phone",)].as_string(), "")
                ).like(needle, escape="!"),
                func.lower(
                    func.coalesce(WorkflowRunModel.gathered_context[("customer_name",)].as_string(), "")
                ).like(needle, escape="!"),
                func.lower(
                    func.coalesce(_ann("analysis", "search_text").as_string(), "")
                ).like(needle, escape="!"),
            )
        )

    if conds:
        stmt = stmt.where(and_(*conds))
    return stmt


def _scoped(stmt: Select, organization_id: int) -> Select:
    return stmt.join(WorkflowModel, WorkflowModel.id == WorkflowRunModel.workflow_id).where(
        WorkflowModel.organization_id == organization_id
    )


def _order(sort: str, order: str):
    if sort not in SORT_KEYS:
        raise ConversationQueryError(f"sort must be one of {SORT_KEYS}")
    direction = "asc" if str(order).lower() == "asc" else "desc"
    if sort == "duration":
        column = cast(WorkflowRunModel.cost_info[("call_duration_seconds",)].as_string(), Float)
    elif sort == "lead_score":
        column = cast(_ann("lead_intelligence", "lead_score").as_string(), Float)
    else:
        column = WorkflowRunModel.created_at
    primary = column.asc() if direction == "asc" else column.desc()
    tiebreak = WorkflowRunModel.id.asc() if direction == "asc" else WorkflowRunModel.id.desc()
    return primary.nulls_last(), tiebreak


def build_list_stmt(
    organization_id: int,
    filters: ConversationFilters,
    *,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    max_limit: int = MAX_PAGE_SIZE,
) -> Select:
    limit = max(1, min(int(limit), max_limit))
    offset = max(0, int(offset))
    stmt = select(WorkflowRunModel, WorkflowModel.name.label("workflow_name")).options(
        defer(WorkflowRunModel.logs)
    )
    stmt = _scoped(stmt, organization_id)
    stmt = _apply_filters(stmt, filters.validate())
    return stmt.order_by(*_order(sort, order)).limit(limit).offset(offset)


def build_count_stmt(organization_id: int, filters: ConversationFilters) -> Select:
    stmt = select(func.count(WorkflowRunModel.id))
    stmt = _scoped(stmt, organization_id)
    return _apply_filters(stmt, filters.validate())


def build_facet_stmt(organization_id: int, filters: ConversationFilters) -> Select:
    """Classification counts across the *whole* filtered set (not just one page)."""
    # Group by the *labelled* column: the JSON path is a bind parameter, so repeating the
    # expression in GROUP BY would be a different parameter and PostgreSQL (asyncpg) rejects
    # it with a GroupingError.
    classification = _classification_expr().label("classification")
    stmt = select(classification, func.count(WorkflowRunModel.id))
    stmt = _scoped(stmt, organization_id)
    stmt = _apply_filters(stmt, filters.validate(), skip_classification=True)
    return stmt.group_by(classification)


def build_ids_stmt(organization_id: int, filters: ConversationFilters, *, limit: int) -> Select:
    stmt = select(WorkflowRunModel.id)
    stmt = _scoped(stmt, organization_id)
    stmt = _apply_filters(stmt, filters.validate())
    return stmt.order_by(*_order("created_at", "desc")).limit(limit)


def build_get_stmt(organization_id: int, run_id: int) -> Select:
    stmt = select(WorkflowRunModel, WorkflowModel.name.label("workflow_name"))
    stmt = _scoped(stmt, organization_id)
    return stmt.where(WorkflowRunModel.id == run_id)


def build_ids_in_org_stmt(organization_id: int, run_ids: Sequence[int]) -> Select:
    stmt = select(WorkflowRunModel.id)
    stmt = _scoped(stmt, organization_id)
    return stmt.where(WorkflowRunModel.id.in_(list(run_ids)))


# --------------------------------------------------------------------------- #
# Item construction
# --------------------------------------------------------------------------- #


def _contact(run: Any) -> str:
    return (
        (run.initial_context or {}).get("phone_number")
        or (run.initial_context or {}).get("caller_number")
        or (run.gathered_context or {}).get("customer_phone")
        or (run.gathered_context or {}).get("from_number")
        or run.name
    )


def _telephony_provider(run: Any, origin: str) -> str:
    snapshot = ((run.gathered_context or {}).get("telephony_snapshot") or {}).get("provider")
    if snapshot:
        return snapshot
    mode = str(getattr(run, "mode", "") or "").lower()
    if "voicelink" in mode:
        return "VoiceLink"
    if "vobiz" in mode:
        return "Vobiz"
    return "No PSTN (browser / simulation)" if origin in ("browser", "simulated") else "Unknown"


def _voice_engine(run: Any) -> str:
    mode = str(getattr(run, "mode", "") or "").lower()
    return (run.gathered_context or {}).get("voice_engine") or (
        "DeepSeek + Cartesia" if "cartesia" in mode else "Gemini Live Native Speech"
    )


def build_conversation_item(
    run: Any,
    workflow_name: Optional[str],
    *,
    storage_configured: bool,
    now: Optional[datetime] = None,
    mask_contact_value: bool = True,
) -> Dict[str, Any]:
    """Lightweight list item. Reads persisted analysis only; never recomputes it."""
    annotations = run.annotations or {}
    analysis = annotations.get("analysis") or {}
    intel = annotations.get("lead_intelligence") or {}
    if analysis.get("ai"):
        intel = effective_intelligence(analysis["ai"], analysis.get("owner_overrides") or {})
    origin = classify_call_origin(run)
    run_status = resolve_run_status(run, now)
    rec = recording_status(run, storage_configured=storage_configured)
    customer_name = (run.gathered_context or {}).get("customer_name") or (run.initial_context or {}).get("customer_name")
    analysis_status = analysis.get("status") or ("legacy" if intel else "pending")
    classification = intel.get("lead_classification") or "Unclassified"
    if run_status["status"] == "failed":
        summary = (
            "FAILED — call did not start."
            if annotations.get("livekit_startup_error")
            else "FAILED — call did not complete."
        )
    elif run_status["status"] in ("not_started", "stale", "starting", "in_progress", "finalization_failed"):
        summary = run_status["detail"] or run_status["label"]
    else:
        summary = (
            intel.get("summary")
            or annotations.get("summary")
            or intel.get("customer_intent")
            or "Call completed."
        )
    if intel.get("title"):
        title = intel["title"]
    elif analysis_status == "legacy":
        title = "Earlier analysis (re-analyze for a title)"
    elif run_status["status"] in ("completed", "finalizing"):
        title = "Analysis pending"
    else:
        title = run_status["label"]
    created = run.created_at
    return {
        "id": run.id,
        "name": run.name,
        "title": title,
        "contact": mask_contact(_contact(run)) if mask_contact_value else _contact(run),
        "contact_masked": bool(mask_contact_value),
        "customer_name": customer_name or "",
        "has_customer_name": bool(customer_name),
        "call_type": run.call_type.value if hasattr(run.call_type, "value") else str(run.call_type),
        "origin": origin,
        "is_test": origin in ("simulated", "browser"),
        "call_source": call_source_label(origin),
        "mode": run.mode,
        "state": run.state.value if hasattr(run.state, "value") else str(run.state),
        "run_status": run_status,
        "date": created.isoformat() if created else None,
        "duration_seconds": resolve_duration_seconds(run),
        "workflow_id": run.workflow_id,
        "agent_name": workflow_name or "AI Assistant",
        "voice_engine": _voice_engine(run),
        "telephony_provider": _telephony_provider(run, origin),
        "summary": summary,
        "outcome": intel.get("outcome"),
        "analysis_status": analysis_status,
        "analysis_error": analysis.get("error"),
        "customer_requirements": intel.get("customer_requirements", {}),
        "confirmed_information": intel.get("confirmed_information", []),
        "unconfirmed_information": intel.get("unconfirmed_information", []),
        "budget": intel.get("budget", "Not specified"),
        "timeline": intel.get("timeline", "Not specified"),
        "objections": intel.get("objections", []),
        "buying_signals": intel.get("buying_signals", []),
        "lead_score": intel.get("lead_score", 0),
        "lead_classification": classification,
        "transcript_evidence": intel.get("transcript_evidence", ""),
        "appointment": intel.get("appointment", {}),
        "human_escalation": intel.get("human_escalation", {}),
        "callback": intel.get("callback", {}),
        "recommended_next_action": intel.get("recommended_next_action", "Review conversation."),
        "owner_overridden_fields": intel.get("owner_overridden_fields", []),
        "owner_notes": annotations.get("owner_notes", ""),
        "recording": {k: rec[k] for k in ("status", "detail", "playable")},
        "lifecycle": lifecycle_state(annotations),
        "archived": lifecycle_state(annotations) == "archived",
        "trashed": is_trashed(annotations),
        "archived_at": (annotations.get("archive") or {}).get("archived_at"),
        "trashed_at": (annotations.get("trash") or {}).get("trashed_at"),
        "cost": run_cost_summary(run),
    }


# --------------------------------------------------------------------------- #
# Async execution helpers
# --------------------------------------------------------------------------- #


async def list_conversation_items(
    session: Any,
    organization_id: int,
    filters: ConversationFilters,
    *,
    sort: str = "created_at",
    order: str = "desc",
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
    max_limit: int = MAX_PAGE_SIZE,
    storage_configured: bool,
    include_facets: bool = True,
    mask_contacts: bool = True,
) -> Dict[str, Any]:
    limit = max(1, min(int(limit), max_limit))
    offset = max(0, int(offset))
    rows = (
        await session.execute(
            build_list_stmt(
                organization_id, filters, sort=sort, order=order, limit=limit, offset=offset, max_limit=max_limit
            )
        )
    ).all()
    total = (await session.execute(build_count_stmt(organization_id, filters))).scalar() or 0
    now = datetime.now(UTC)
    items = [
        build_conversation_item(
            run, wf_name, storage_configured=storage_configured, now=now, mask_contact_value=mask_contacts
        )
        for run, wf_name in rows
    ]
    result: Dict[str, Any] = {
        "items": items,
        "total": int(total),
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "order": "asc" if str(order).lower() == "asc" else "desc",
        "has_more": offset + len(items) < int(total),
    }
    if include_facets:
        facet_rows = (await session.execute(build_facet_stmt(organization_id, filters))).all()
        facets: Dict[str, int] = {}
        for classification, count in facet_rows:
            facets[classification or "Unclassified"] = int(count)
        result["facets"] = {"classification": facets}
    return result


async def resolve_selection(
    session: Any,
    organization_id: int,
    *,
    ids: Optional[Sequence[int]] = None,
    filters: Optional[ConversationFilters] = None,
    expected_count: Optional[int] = None,
    cap: Optional[int] = None,
) -> Dict[str, Any]:
    """Resolve a bulk selection to organization-owned run ids.

    * ``ids`` mode: each id is verified against the organization; ids the caller
      cannot access are reported only as a count (no existence disclosure).
    * ``filters`` mode ("select all matching"): the filter is re-evaluated
      server-side and the caller's ``expected_count`` must match the current
      count, otherwise the selection is rejected (scope drifted).
    """
    cap = int(cap or MAX_BULK_SELECTION)
    if (ids is None) == (filters is None):
        raise ConversationQueryError("Provide exactly one of ids or filters")
    if ids is not None:
        unique = list(dict.fromkeys(int(i) for i in ids))
        if not unique:
            raise ConversationQueryError("No conversations selected")
        if len(unique) > cap:
            raise ConversationQueryError(f"At most {cap} conversations can be selected at once")
        owned = [r for (r,) in (await session.execute(build_ids_in_org_stmt(organization_id, unique))).all()]
        owned_set = set(owned)
        return {"ids": [i for i in unique if i in owned_set], "skipped": len(unique) - len(owned_set), "mode": "ids"}
    assert filters is not None
    total = (await session.execute(build_count_stmt(organization_id, filters))).scalar() or 0
    if total > cap:
        raise ConversationQueryError(
            f"{total} conversations match; narrow the filters to at most {cap} before applying a bulk action"
        )
    if expected_count is None or int(expected_count) != int(total):
        raise SelectionChangedError(
            f"Selection changed: {total} conversations match now (expected {expected_count}). Review and retry."
        )
    selected = [r for (r,) in (await session.execute(build_ids_stmt(organization_id, filters, limit=cap))).all()]
    return {"ids": selected, "skipped": 0, "mode": "filters"}
