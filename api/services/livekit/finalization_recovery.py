"""Periodic, leased recovery of atomic LiveKit finalization snapshots."""
from __future__ import annotations
import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from loguru import logger
from sqlalchemy import or_, select
from api.db import db_client
from api.db.models import WorkflowRunModel
from api.services.pricing.workflow_run_cost import calculate_workflow_run_cost

RECOVERY_VERSION = 1
RECOVERY_GRACE_SECONDS = 300
INCOMPLETE_RECOVERY_GRACE_SECONDS = 120
RECOVERY_BATCH_LIMIT = 20
RECOVERY_POST_CALL_TIMEOUT_SECONDS = 30.0
RECOVERY_ATTEMPT_TIMEOUT_SECONDS = 60.0
RECOVERY_LEASE_SECONDS = 120
RECOVERY_INTERVAL_SECONDS = 30
RECOVERY_STATES = ("pending", "failed", "recovering")
_recovery_task: asyncio.Task | None = None

def _finalization_marker(run) -> dict[str, Any]:
    marker = (getattr(run, "annotations", None) or {}).get("livekit_finalization")
    return marker if isinstance(marker, dict) else {}

def _timestamp(value, fallback=None):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    return fallback

def is_recovery_eligible(run, *, now=None, grace_seconds=RECOVERY_GRACE_SECONDS):
    marker = _finalization_marker(run)
    if marker.get("recovery_version") != RECOVERY_VERSION or marker.get("state") not in RECOVERY_STATES:
        return False
    if not getattr(run, "is_completed", False) or not getattr(run, "usage_info", None):
        return False
    current = now or datetime.now(UTC)
    lease = _timestamp(marker.get("lease_until"))
    if lease and lease > current:
        return False
    updated = _timestamp(marker.get("updated_at"), getattr(run, "created_at", None))
    return bool(updated and updated <= current - timedelta(seconds=grace_seconds))

def is_incomplete_recovery_eligible(run, *, now=None, grace_seconds=INCOMPLETE_RECOVERY_GRACE_SECONDS):
    """Eligible if the worker crashed before setting is_completed=True."""
    marker = _finalization_marker(run)
    if marker.get("recovery_version") != RECOVERY_VERSION or marker.get("state") not in RECOVERY_STATES:
        return False
    if getattr(run, "is_completed", True):
        return False
    current = now or datetime.now(UTC)
    lease = _timestamp(marker.get("lease_until"))
    if lease and lease > current:
        return False
    updated = _timestamp(marker.get("updated_at"), getattr(run, "created_at", None))
    return bool(updated and updated <= current - timedelta(seconds=grace_seconds))

def _marker_text(field):
    # annotations is JSON (not JSONB): there is no `.astext`, so every sweep raised AttributeError on
    # PostgreSQL and nothing was ever recovered. A JSON path + as_string() renders `#>>` (text).
    return WorkflowRunModel.annotations[("livekit_finalization", field)].as_string()

def _candidate_query(*, completed, grace_seconds, limit):
    cutoff = (datetime.now(UTC) - timedelta(seconds=grace_seconds)).isoformat()
    updated_at = _marker_text("updated_at")
    return (
        select(WorkflowRunModel)
        .where(WorkflowRunModel.is_completed.is_(completed))
        .where(WorkflowRunModel.mode == "livekit")
        .where(_marker_text("state").in_(RECOVERY_STATES))
        .where(_marker_text("recovery_version") == str(RECOVERY_VERSION))
        .where(or_(updated_at.is_(None), updated_at <= cutoff))
        .order_by(updated_at.asc().nullsfirst(), WorkflowRunModel.id.asc())
        .limit(limit)
    )

async def _load_candidates(limit):
    async with db_client.async_session() as session:
        result = await session.execute(
            _candidate_query(completed=True, grace_seconds=RECOVERY_GRACE_SECONDS, limit=limit)
        )
        return list(result.scalars().all())

async def _load_incomplete_candidates(limit):
    """Load livekit runs that have a finalization marker but were never completed."""
    async with db_client.async_session() as session:
        result = await session.execute(
            _candidate_query(completed=False, grace_seconds=INCOMPLETE_RECOVERY_GRACE_SECONDS, limit=limit)
        )
        return list(result.scalars().all())

async def _claim_recovery(run_id, *, now=None, grace_seconds=RECOVERY_GRACE_SECONDS):
    current = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        result = await session.execute(
            select(WorkflowRunModel).where(WorkflowRunModel.id == run_id).with_for_update(skip_locked=True)
        )
        run = result.scalars().first()
        if run is None or not is_recovery_eligible(run, now=current, grace_seconds=grace_seconds):
            return None
        token = uuid.uuid4().hex
        marker = {
            **_finalization_marker(run), "state": "recovering", "lease_owner": token,
            "lease_until": (current + timedelta(seconds=RECOVERY_LEASE_SECONDS)).isoformat(),
            "updated_at": current.isoformat(),
            "attempts": int(_finalization_marker(run).get("attempts") or 0) + 1,
        }
        run.annotations = {**(run.annotations or {}), "livekit_finalization": marker}
        await session.commit()
        return token

async def _finish_recovery(run_id, token, state, error=None):
    async with db_client.async_session() as session:
        result = await session.execute(
            select(WorkflowRunModel).where(WorkflowRunModel.id == run_id).with_for_update()
        )
        run = result.scalars().first()
        if run is None or _finalization_marker(run).get("lease_owner") != token:
            return False
        marker = {
            **_finalization_marker(run), "state": state,
            "updated_at": datetime.now(UTC).isoformat(), "error": error,
            "lease_owner": None, "lease_until": None,
        }
        run.annotations = {**(run.annotations or {}), "livekit_finalization": marker}
        await session.commit()
        return True

async def _recover_tail(run):
    from api.services.livekit import post_call
    from api.services.livekit.post_call_triggers import process_post_call_intelligence_and_triggers
    recording = (run.gathered_context or {}).get("livekit_recording")
    recording_failed = False
    if isinstance(recording, dict) and recording.get("egress_id"):
        allowed = post_call.LiveKitRecordingState.__dataclass_fields__
        state = post_call.LiveKitRecordingState(**{k: v for k, v in recording.items() if k in allowed})
        stopped = await post_call.stop_livekit_room_recording(state)
        recording_failed = stopped is None or stopped.status in ("stop_failed", "failed")
        if stopped is not None:
            await db_client.update_workflow_run(
                run.id, recording_url=post_call.recording_available_url(stopped),
                gathered_context={"livekit_recording": stopped.to_log()},
            )
    await calculate_workflow_run_cost(run.id)
    result = await asyncio.wait_for(
        process_post_call_intelligence_and_triggers(
            workflow_run_id=run.id, livekit_history=(run.logs or {}).get("livekit_history"),
            gathered_context=run.gathered_context or {},
        ), timeout=RECOVERY_POST_CALL_TIMEOUT_SECONDS,
    )
    if not isinstance(result, dict) or result.get("status") != "success":
        raise RuntimeError("post_call_incomplete")
    if recording_failed:
        raise RuntimeError("recording_stop_incomplete")

async def _recover_one(run, *, now=None, grace_seconds=RECOVERY_GRACE_SECONDS):
    from api.services.livekit.worker import _safe_finalize_error
    token = await _claim_recovery(run.id, now=now, grace_seconds=grace_seconds)
    if token is None:
        return {"workflow_run_id": run.id, "state": "skipped"}
    try:
        await asyncio.wait_for(_recover_tail(run), timeout=RECOVERY_ATTEMPT_TIMEOUT_SECONDS)
        if not await _finish_recovery(run.id, token, "succeeded"):
            raise RuntimeError("recovery_lease_lost")
        return {"workflow_run_id": run.id, "state": "succeeded"}
    except asyncio.CancelledError:
        raise  # The committed lease expires; a later sweep can reclaim it.
    except Exception as exc:
        error = _safe_finalize_error(exc)
        await _finish_recovery(run.id, token, "failed", error)
        return {"workflow_run_id": run.id, "state": "failed", "error": error}

async def _claim_incomplete_recovery(run_id, *, now=None, grace_seconds=INCOMPLETE_RECOVERY_GRACE_SECONDS):
    current = now or datetime.now(UTC)
    async with db_client.async_session() as session:
        result = await session.execute(
            select(WorkflowRunModel).where(WorkflowRunModel.id == run_id).with_for_update(skip_locked=True)
        )
        run = result.scalars().first()
        if run is None or not is_incomplete_recovery_eligible(run, now=current, grace_seconds=grace_seconds):
            return None
        token = uuid.uuid4().hex
        marker = {
            **_finalization_marker(run), "state": "recovering", "lease_owner": token,
            "lease_until": (current + timedelta(seconds=RECOVERY_LEASE_SECONDS)).isoformat(),
            "updated_at": current.isoformat(),
            "attempts": int(_finalization_marker(run).get("attempts") or 0) + 1,
            "incomplete_recovery": True,
        }
        run.annotations = {**(run.annotations or {}), "livekit_finalization": marker}
        # Mark the run completed so tail recovery (cost calc, post-call) can proceed.
        run.is_completed = True
        if not getattr(run, "status", None):
            run.status = "failed"
        await session.commit()
        return token

async def _recover_incomplete_one(run_id, *, now=None, grace_seconds=INCOMPLETE_RECOVERY_GRACE_SECONDS):
    from api.services.livekit.worker import _safe_finalize_error
    token = await _claim_incomplete_recovery(run_id, now=now, grace_seconds=grace_seconds)
    if token is None:
        return {"workflow_run_id": run_id, "state": "skipped"}
    # Re-fetch after claim; is_completed was set inside _claim_incomplete_recovery.
    async with db_client.async_session() as session:
        result = await session.execute(select(WorkflowRunModel).where(WorkflowRunModel.id == run_id))
        run = result.scalars().first()
    if run is None:
        return {"workflow_run_id": run_id, "state": "skipped"}
    try:
        await asyncio.wait_for(_recover_tail(run), timeout=RECOVERY_ATTEMPT_TIMEOUT_SECONDS)
        if not await _finish_recovery(run_id, token, "succeeded"):
            raise RuntimeError("recovery_lease_lost")
        return {"workflow_run_id": run_id, "state": "succeeded", "incomplete_recovery": True}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        error = _safe_finalize_error(exc)
        await _finish_recovery(run_id, token, "failed", error)
        return {"workflow_run_id": run_id, "state": "failed", "error": error, "incomplete_recovery": True}

async def recover_pending_livekit_finalizations(*, limit=RECOVERY_BATCH_LIMIT, grace_seconds=RECOVERY_GRACE_SECONDS, now=None):
    outcomes = []
    try:
        candidates = await _load_candidates(limit)
        for run in candidates:
            if is_recovery_eligible(run, now=now, grace_seconds=grace_seconds):
                outcomes.append(await _recover_one(run, now=now, grace_seconds=grace_seconds))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("[LiveKit] recovery sweep failed; will retry")
    # Second pass: runs that crashed before is_completed was set.
    try:
        incomplete = await _load_incomplete_candidates(limit)
        for run in incomplete:
            if is_incomplete_recovery_eligible(run, now=now):
                outcomes.append(await _recover_incomplete_one(run.id, now=now))
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.error("[LiveKit] incomplete recovery sweep failed; will retry")
    return outcomes

async def _recovery_loop():
    while True:
        await recover_pending_livekit_finalizations()
        await asyncio.sleep(RECOVERY_INTERVAL_SECONDS)

def schedule_finalization_recovery():
    global _recovery_task
    if _recovery_task is None or _recovery_task.done():
        _recovery_task = asyncio.create_task(_recovery_loop(), name="livekit-finalization-recovery")
    return _recovery_task

async def stop_finalization_recovery():
    global _recovery_task
    if _recovery_task is not None:
        _recovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _recovery_task
        _recovery_task = None
