"""Pure helpers shared by the Conversations / Leads / Costs surfaces (CP-02).

Everything here is side-effect free so it can be unit tested without a database:

* canonical call-duration resolution with legacy fallbacks;
* call-origin and run-status classification (never reports an unstarted run as
  completed);
* recording status derived only from persisted egress state;
* the persisted analysis record, which keeps AI evidence and owner overrides
  separate and applies analysis idempotently.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from api.services.business.lead_intelligence import (
    _SPEAKER_RE,
    ANALYZER_VERSION,
    extract_timed_turns,
    extract_transcript_from_logs,
    parse_transcript_turns,
)

# A run that has been "running" / "initialized" longer than this never finished.
STALE_RUN_AFTER = timedelta(hours=2)
# Initialized runs older than this never connected.
NOT_STARTED_AFTER = timedelta(minutes=10)

OWNER_OVERRIDABLE_FIELDS = (
    "lead_classification",
    "lead_score",
    "customer_requirements",
    "recommended_next_action",
    "title",
    "appointment_status",
)
MAX_OWNER_HISTORY = 200
MAX_ANALYSIS_ATTEMPTS = 5
MAX_SEARCH_TEXT = 20000
TERMINAL_ANALYSIS_STATUSES = ("complete", "needs_review", "no_caller_speech", "no_transcript")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and number >= 0 else None


def resolve_duration_seconds(run: Any) -> float:
    """Canonical call duration in seconds.

    The LiveKit worker writes ``usage_info.call_duration_seconds`` and
    ``cost_info.call_duration_seconds``; scripted simulations historically wrote
    ``usage_info.duration_seconds``. Preference order is canonical first.
    """
    usage = getattr(run, "usage_info", None) or {}
    cost = getattr(run, "cost_info", None) or {}
    for source, key in (
        (usage, "caller_connected_duration_seconds"),
        (usage, "call_duration_seconds"),
        (cost, "call_duration_seconds"),
        (usage, "duration_seconds"),
    ):
        value = _as_float(source.get(key)) if isinstance(source, dict) else None
        if value:
            return value
    return 0.0


def classify_call_origin(run: Any) -> str:
    """One of ``simulated`` | ``browser`` | ``pstn`` | ``unknown``.

    ``browser`` is a real AI conversation with no PSTN leg (browser / embedded
    widget): it incurs AI cost but no carrier cost.
    """
    mode = _enum_value(getattr(run, "mode", "")).lower()
    name = str(getattr(run, "name", "") or "")
    annotations = getattr(run, "annotations", None) or {}
    initial = getattr(run, "initial_context", None) or {}
    gathered = getattr(run, "gathered_context", None) or {}

    if (
        getattr(run, "is_simulation", False)
        or annotations.get("is_simulation")
        or annotations.get("is_simulated")
        or initial.get("is_simulation")
        or mode in ("test", "web", "simulated", "simulation", "browser_test")
        or "simulat" in mode
        or name.startswith(("SIM-", "TEST-"))
    ):
        return "simulated"

    participant_kind = str(initial.get("participant_kind", "")).strip().lower()
    provider = str((gathered.get("telephony_snapshot") or {}).get("provider") or "")
    if (
        mode in ("vobiz", "voicelink", "livekit_sip", "pstn")
        or participant_kind in ("3", "sip", "participant_kind_sip")
        or provider in ("Vobiz", "LiveKit SIP", "VoiceLink")
    ):
        return "pstn"
    if mode in ("smallwebrtc", "web_lead", "webrtc") or (
        # An unknown participant kind is NOT assumed to be a browser session.
        mode == "livekit" and participant_kind in ("0", "standard", "participant_kind_standard")
    ):
        return "browser"
    return "unknown"


def call_source_label(origin: str) -> str:
    """Legacy ``call_source`` values consumed by the existing Leads UI."""
    return {
        "simulated": "simulated",
        "browser": "browser_test",
        "pstn": "verified_pstn",
    }.get(origin, "unverified")


def run_age(run: Any, now: Optional[datetime] = None) -> Optional[timedelta]:
    created = getattr(run, "created_at", None)
    if not isinstance(created, datetime):
        return None
    now = now or datetime.now(UTC)
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return now - created


def resolve_run_status(run: Any, now: Optional[datetime] = None) -> Dict[str, str]:
    """Truthful lifecycle status. Never labels an unstarted run as completed."""
    annotations = getattr(run, "annotations", None) or {}
    state = _enum_value(getattr(run, "state", "")).lower()
    disposition = str(getattr(run, "call_disposition", "") or "").upper()
    finalization = (annotations.get("livekit_finalization") or {}).get("state")
    age = run_age(run, now)

    if annotations.get("livekit_startup_error"):
        return {"status": "failed", "label": "Failed to start", "detail": "The call did not start."}
    if disposition == "FAILED":
        return {"status": "failed", "label": "Failed", "detail": "The call did not complete."}
    if state == "completed" or getattr(run, "is_completed", False):
        if finalization == "failed":
            return {
                "status": "finalization_failed",
                "label": "Finalization failed",
                "detail": "The call ended but post-call processing failed; it can be retried.",
            }
        if finalization == "pending":
            return {"status": "finalizing", "label": "Finalizing", "detail": "Post-call processing is in progress."}
        return {"status": "completed", "label": "Completed", "detail": ""}
    if state == "running":
        if age is not None and age > STALE_RUN_AFTER:
            return {"status": "stale", "label": "Stale", "detail": "Never finished; no completion was recorded."}
        return {"status": "in_progress", "label": "In progress", "detail": ""}
    if state == "initialized":
        if age is not None and age > NOT_STARTED_AFTER:
            return {"status": "not_started", "label": "Not started", "detail": "The call never connected."}
        return {"status": "starting", "label": "Starting", "detail": ""}
    return {"status": "unknown", "label": "Unknown", "detail": ""}


# --------------------------------------------------------------------------- #
# Recording status
# --------------------------------------------------------------------------- #

RECORDING_STATES = ("disabled", "not_configured", "starting", "failed", "ready")


def recording_status(run: Any, *, storage_configured: bool) -> Dict[str, Any]:
    """Recording state derived only from persisted egress state.

    ``ready`` requires that the egress reported a finished output file, or that a
    legacy ``recording_url`` exists. The detail route additionally verifies the
    storage object itself before offering playback.
    """
    gathered = getattr(run, "gathered_context", None) or {}
    lk = gathered.get("livekit_recording")
    recording_url = str(getattr(run, "recording_url", "") or "")
    completed = bool(getattr(run, "is_completed", False)) or _enum_value(
        getattr(run, "state", "")
    ).lower() == "completed"

    if isinstance(lk, dict):
        status = str(lk.get("status") or "").lower()
        key = str(lk.get("recording_key") or "")
        error = lk.get("error")
        if status in ("complete", "completed", "stopped"):
            files = lk.get("file_results") or []
            sized = [f for f in files if isinstance(f, dict) and (_as_float(f.get("size")) or 0) > 0]
            if recording_url and (sized or not files):
                if files and not sized:
                    return {"status": "failed", "detail": "Egress finished but reported an empty file.", "key": key, "playable": False}
                return {"status": "ready", "detail": "", "key": key, "playable": True}
            return {
                "status": "failed",
                "detail": "Recording finished without a stored file.",
                "key": key,
                "playable": False,
            }
        if status in ("not_started", "failed", "stop_failed", "aborted", "egress_aborted", "egress_failed"):
            return {"status": "failed", "detail": str(error or status), "key": key, "playable": False}
        if completed:
            return {
                "status": "failed",
                "detail": "The call ended but the recording never reached a finished state.",
                "key": key,
                "playable": False,
            }
        return {"status": "starting", "detail": "", "key": key, "playable": False}

    if recording_url:
        # Legacy / non-LiveKit storage that already persisted a URL or key.
        return {"status": "ready", "detail": "", "key": "", "playable": True}
    if not storage_configured:
        return {
            "status": "not_configured",
            "detail": "Recording storage is not configured for this deployment.",
            "key": "",
            "playable": False,
        }
    if not completed:
        return {"status": "starting", "detail": "", "key": "", "playable": False}
    return {
        "status": "disabled",
        "detail": "Recording was not enabled for this call.",
        "key": "",
        "playable": False,
    }


# --------------------------------------------------------------------------- #
# Transcript helpers
# --------------------------------------------------------------------------- #


def transcript_text(run: Any) -> str:
    return extract_transcript_from_logs(getattr(run, "logs", None))


def transcript_hash(transcript: str) -> str:
    return hashlib.sha256((transcript or "").encode("utf-8")).hexdigest()[:16]


def transcript_turns(transcript: str) -> List[Dict[str, Any]]:
    """Speaker-separated turns. No timestamps: none are persisted, none are invented."""
    out: List[Dict[str, Any]] = []
    for idx, turn in enumerate(parse_transcript_turns(transcript)):
        speaker = turn["speaker"] if turn["speaker"] in ("caller", "agent") else "unknown"
        out.append({"index": idx, "speaker": speaker, "text": turn["text"]})
    return out


def timed_transcript_turns(logs: Any) -> List[Dict[str, Any]]:
    """Speaker-separated turns straight from the stored logs.

    ``timestamp`` is set only when the source recorded one (never invented).
    """
    out: List[Dict[str, Any]] = []
    for turn in extract_timed_turns(logs):
        label, text = turn["label"], turn["text"]
        if label is None:  # already-flattened "Caller: ..." line
            m = _SPEAKER_RE.match(text)
            speaker, text = (m.group(1).lower(), m.group(2).strip()) if m else ("unknown", text)
        else:
            speaker = "caller" if label == "Caller" else "agent"
        out.append({"index": len(out), "speaker": speaker, "text": text, "timestamp": turn["timestamp"]})
    return out


# --------------------------------------------------------------------------- #
# Analysis record (AI evidence and owner overrides kept separate)
# --------------------------------------------------------------------------- #


def effective_intelligence(ai: Dict[str, Any], overrides: Dict[str, Any]) -> Dict[str, Any]:
    """The owner-facing view: AI evidence with owner overrides layered on top."""
    eff = copy.deepcopy(ai or {})
    for key in ("lead_classification", "lead_score", "customer_requirements", "recommended_next_action", "title"):
        if key in overrides:
            eff[key] = copy.deepcopy(overrides[key])
    if "appointment_status" in overrides:
        appt = copy.deepcopy(eff.get("appointment") or {})
        appt["status"] = overrides["appointment_status"]
        if overrides["appointment_status"] in ("Confirmed", "Rescheduled", "Completed"):
            appt["requested"] = True
        eff["appointment"] = appt
    if overrides:
        eff["owner_overridden_fields"] = sorted(overrides.keys())
    return eff


def _now_iso(now: Optional[datetime]) -> str:
    return (now or datetime.now(UTC)).isoformat()


def apply_ai_analysis(
    annotations: Optional[Dict[str, Any]],
    intel: Dict[str, Any],
    *,
    transcript_hash_value: str,
    now: Optional[datetime] = None,
    force: bool = False,
    search_text: str = "",
) -> Tuple[Dict[str, Any], bool]:
    """Persist an AI analysis idempotently. Returns ``(annotations, changed)``.

    * Same transcript + same analyzer version => no change (safe retries).
    * Owner overrides and history are never touched.
    * A pre-existing legacy ``lead_intelligence`` is preserved under
      ``analysis.previous_ai`` before being replaced, so original evidence is kept.
    """
    ann = copy.deepcopy(annotations or {})
    analysis = copy.deepcopy(ann.get("analysis") or {})
    if (
        not force
        and analysis.get("status") in TERMINAL_ANALYSIS_STATUSES
        and analysis.get("transcript_hash") == transcript_hash_value
        and analysis.get("analyzer_version") == ANALYZER_VERSION
    ):
        return ann, False

    previous = list(analysis.get("previous_ai") or [])
    if analysis.get("ai"):
        previous.append(
            {"analyzer_version": analysis.get("analyzer_version"), "analyzed_at": analysis.get("analyzed_at"), "ai": analysis["ai"]}
        )
    elif ann.get("lead_intelligence") and not analysis:
        previous.append({"analyzer_version": "legacy", "analyzed_at": ann.get("post_call_processed_at"), "ai": ann["lead_intelligence"]})

    overrides = analysis.get("owner_overrides") or {}
    status = intel.get("analysis_status") or "complete"
    analysis.update(
        {
            "version": 2,
            "analyzer_version": ANALYZER_VERSION,
            "status": status,
            "error": None,
            "retryable": None,
            "attempts": 0,
            "transcript_hash": transcript_hash_value,
            "analyzed_at": _now_iso(now),
            "ai": copy.deepcopy(intel),
            "owner_overrides": overrides,
            "owner_history": list(analysis.get("owner_history") or []),
            "previous_ai": previous[-5:],
            # Lower-cased, bounded text so list search never scans raw JSON logs.
            "search_text": (search_text or "").lower()[:MAX_SEARCH_TEXT],
        }
    )
    ann["analysis"] = analysis
    ann["lead_intelligence"] = effective_intelligence(intel, overrides)
    return ann, True


def apply_analysis_failure(
    annotations: Optional[Dict[str, Any]],
    error_code: str,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Record a failed analysis attempt without disturbing AI evidence or overrides."""
    ann = copy.deepcopy(annotations or {})
    analysis = copy.deepcopy(ann.get("analysis") or {})
    analysis["status"] = "failed"
    analysis["error"] = str(error_code)[:120]
    analysis["attempts"] = int(analysis.get("attempts") or 0) + 1
    # Bounded retries: after MAX_ANALYSIS_ATTEMPTS the owner must act (re-run or edit by hand).
    analysis["retryable"] = analysis["attempts"] < MAX_ANALYSIS_ATTEMPTS
    analysis["failed_at"] = _now_iso(now)
    ann["analysis"] = analysis
    return ann


def apply_owner_override(
    annotations: Optional[Dict[str, Any]],
    patch: Dict[str, Any],
    *,
    actor: Dict[str, Any],
    now: Optional[datetime] = None,
    reset_fields: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Layer owner corrections over the AI evidence and append an audit trail."""
    ann = copy.deepcopy(annotations or {})
    analysis = copy.deepcopy(ann.get("analysis") or {})
    if not analysis.get("ai"):
        # First owner edit on a historical run: snapshot the existing AI output.
        legacy = copy.deepcopy(ann.get("lead_intelligence") or {})
        analysis.setdefault("version", 2)
        analysis.setdefault("analyzer_version", "legacy")
        analysis.setdefault("status", "complete" if legacy else "no_transcript")
        analysis["ai"] = legacy
    overrides = dict(analysis.get("owner_overrides") or {})
    history = list(analysis.get("owner_history") or [])
    current = effective_intelligence(analysis["ai"], overrides)

    def _before(field: str) -> Any:
        if field == "appointment_status":
            return (current.get("appointment") or {}).get("status")
        return current.get(field)

    stamp = _now_iso(now)
    for field, value in patch.items():
        if field not in OWNER_OVERRIDABLE_FIELDS:
            continue
        before = _before(field)
        if before == value:
            continue
        overrides[field] = value
        history.append({"at": stamp, "by": actor, "field": field, "before": before, "after": value})
    for field in reset_fields or []:
        if field in overrides:
            before = _before(field)
            del overrides[field]
            history.append({"at": stamp, "by": actor, "field": field, "before": before, "after": "reset_to_ai"})

    analysis["owner_overrides"] = overrides
    analysis["owner_history"] = history[-MAX_OWNER_HISTORY:]
    ann["analysis"] = analysis
    ann["lead_intelligence"] = effective_intelligence(analysis["ai"], overrides)
    return ann


# --------------------------------------------------------------------------- #
# Archive state (stored in annotations; no schema change)
# --------------------------------------------------------------------------- #


def is_archived(annotations: Optional[Dict[str, Any]]) -> bool:
    return bool(((annotations or {}).get("archive") or {}).get("archived_at"))


def is_trashed(annotations: Optional[Dict[str, Any]]) -> bool:
    return bool(((annotations or {}).get("trash") or {}).get("trashed_at"))


def lifecycle_state(annotations: Optional[Dict[str, Any]]) -> str:
    """``trashed`` > ``archived`` > ``active``. Trash is reversible; nothing is erased."""
    if is_trashed(annotations):
        return "trashed"
    if is_archived(annotations):
        return "archived"
    return "active"


def _transition(
    annotations: Optional[Dict[str, Any]],
    key: str,
    stamp_key: str,
    action: str,
    *,
    actor: Dict[str, Any],
    reason: Optional[str],
    now: Optional[datetime],
) -> Tuple[Dict[str, Any], bool]:
    ann = copy.deepcopy(annotations or {})
    if lifecycle_state(ann) != "active":
        return ann, False  # already archived/trashed: restore first (no silent state swaps)
    block = dict(ann.get(key) or {})
    history = list(block.get("history") or [])
    stamp = _now_iso(now)
    history.append({"at": stamp, "action": action, "by": actor, "reason": (reason or "")[:200]})
    ann[key] = {
        stamp_key: stamp,
        f"{stamp_key.split('_')[0]}_by": actor,
        "reason": (reason or "")[:200],
        "history": history[-50:],
    }
    return ann, True


def apply_archive(
    annotations: Optional[Dict[str, Any]],
    *,
    actor: Dict[str, Any],
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], bool]:
    return _transition(annotations, "archive", "archived_at", "archived", actor=actor, reason=reason, now=now)


def apply_trash(
    annotations: Optional[Dict[str, Any]],
    *,
    actor: Dict[str, Any],
    reason: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Move to Trash: hidden from active views, fully restorable, financial data untouched."""
    return _transition(annotations, "trash", "trashed_at", "trashed", actor=actor, reason=reason, now=now)


def apply_restore(
    annotations: Optional[Dict[str, Any]],
    *,
    actor: Dict[str, Any],
    now: Optional[datetime] = None,
) -> Tuple[Dict[str, Any], bool]:
    """Restore from Archive or Trash to the active lists."""
    ann = copy.deepcopy(annotations or {})
    state = lifecycle_state(ann)
    if state == "active":
        return ann, False
    key, stamp_key = ("trash", "trashed_at") if state == "trashed" else ("archive", "archived_at")
    block = dict(ann.get(key) or {})
    history = list(block.get("history") or [])
    history.append({"at": _now_iso(now), "action": "restored", "by": actor, "from": state})
    ann[key] = {stamp_key: None, "history": history[-50:]}
    return ann, True


def mask_contact(value: Any) -> str:
    """Mask a phone number for list views (``+91 98••••••12``); non-phone text is unchanged."""
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) < 7 or len(digits) != len([c for c in text if c.isdigit()]):
        return text
    if any(ch.isalpha() for ch in text):
        return text  # e.g. run names such as WR-LK-IN-123
    plus = "+" if text.startswith("+") else ""
    return f"{plus}{digits[:2]}{'•' * (len(digits) - 4)}{digits[-2:]}"


# --------------------------------------------------------------------------- #
# Cost summary for a single run (truthful labels only)
# --------------------------------------------------------------------------- #


def run_cost_summary(run: Any) -> Dict[str, Any]:
    """Cost labels for one run. Estimates are never presented as reconciled."""
    cost = getattr(run, "cost_info", None) or {}
    actual = cost.get("actual_cost") if isinstance(cost.get("actual_cost"), dict) else None
    origin = classify_call_origin(run)
    billing_status = str(cost.get("billing_status") or "")
    if not cost or actual is None:
        return {
            "status": "not_costed",
            "total_inr": None,
            "ai_inr": None,
            "telephony_inr": None,
            "estimated": None,
            "pstn": origin == "pstn",
            "warnings": [],
        }
    components = actual.get("components") or []
    unpriced = [c for c in components if isinstance(c, dict) and c.get("priced") is False]
    estimated = bool(actual.get("estimated")) or billing_status in ("ESTIMATED", "PENDING_RECONCILIATION")
    if billing_status == "VERIFIED" and not actual.get("estimated"):
        status = "reconciled"
    elif billing_status == "RECONCILIATION_FAILED":
        status = "reconciliation_failed"
    elif estimated:
        status = "estimated"
    else:
        status = "unverified"
    return {
        "status": status,
        "total_inr": actual.get("total_inr"),
        "ai_inr": actual.get("ai_total_inr"),
        "telephony_inr": actual.get("telephony_total_inr"),
        "estimated": estimated,
        "pstn": origin == "pstn",
        "telephony_applicable": origin == "pstn",
        "unpriced_components": len(unpriced),
        "warnings": [str(w) for w in (actual.get("warnings") or [])][:5],
    }
