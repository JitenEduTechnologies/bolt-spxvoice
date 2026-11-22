"""Focused tests: durable, deterministic LiveKit finalization recovery."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from api.services.livekit import finalization_recovery as recovery


def _run(
    *,
    marker,
    completed=True,
    usage=None,
    age_seconds=600,
    run_id=1,
):
    return SimpleNamespace(
        id=run_id,
        is_completed=completed,
        usage_info=usage if usage is not None else {"call_duration_seconds": 10},
        annotations={"livekit_finalization": marker},
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        logs={"livekit_history": {"items": []}},
        gathered_context={"livekit_room": "room-1"},
    )


def test_pending_marker_is_eligible():
    assert (
        recovery.is_recovery_eligible(
            _run(marker={"state": "pending", "recovery_version": 1})
        )
        is True
    )


def test_failed_marker_is_eligible():
    assert (
        recovery.is_recovery_eligible(
            _run(marker={"state": "failed", "recovery_version": 1})
        )
        is True
    )


def test_historical_runs_without_marker_are_not_eligible():
    legacy = SimpleNamespace(
        id=2,
        is_completed=True,
        usage_info={"call_duration_seconds": 10},
        annotations={},
        created_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert recovery.is_recovery_eligible(legacy) is False


@pytest.mark.parametrize("state", ["succeeded", "running", "weird"])
def test_only_pending_or_failed_states_are_eligible(state):
    assert (
        recovery.is_recovery_eligible(
            _run(marker={"state": state, "recovery_version": 1})
        )
        is False
    )


def test_fresh_runs_are_not_eligible():
    assert (
        recovery.is_recovery_eligible(
            _run(
                marker={"state": "pending", "recovery_version": 1},
                age_seconds=5,
            )
        )
        is False
    )


def test_runs_without_usage_are_not_eligible():
    assert (
        recovery.is_recovery_eligible(
            _run(marker={"state": "pending", "recovery_version": 1}, usage={})
        )
        is False
    )


def test_incomplete_runs_are_not_eligible():
    assert (
        recovery.is_recovery_eligible(
            _run(
                marker={"state": "pending", "recovery_version": 1},
                completed=False,
            )
        )
        is False
    )


def test_unknown_recovery_version_is_not_eligible():
    assert (
        recovery.is_recovery_eligible(
            _run(marker={"state": "pending", "recovery_version": 99})
        )
        is False
    )


@pytest.mark.asyncio
async def test_recovery_processes_only_eligible_candidates(monkeypatch):
    eligible = _run(marker={"state": "pending", "recovery_version": 1}, run_id=1)
    fresh = _run(
        marker={"state": "pending", "recovery_version": 1},
        run_id=2,
        age_seconds=5,
    )
    historical = _run(marker={}, run_id=3)

    monkeypatch.setattr(
        recovery,
        "_load_candidates",
        AsyncMock(return_value=[eligible, fresh, historical]),
    )
    recover_one = AsyncMock(
        return_value={"workflow_run_id": 1, "state": "succeeded"}
    )
    monkeypatch.setattr(recovery, "_recover_one", recover_one)

    outcomes = await recovery.recover_pending_livekit_finalizations(limit=10)

    assert outcomes == [{"workflow_run_id": 1, "state": "succeeded"}]
    recover_one.assert_awaited_once_with(
        eligible,
        now=None,
        grace_seconds=recovery.RECOVERY_GRACE_SECONDS,
    )


@pytest.mark.asyncio
async def test_recovery_respects_the_batch_limit(monkeypatch):
    candidates = [
        _run(marker={"state": "pending", "recovery_version": 1}, run_id=i)
        for i in range(1, 6)
    ]
    monkeypatch.setattr(
        recovery, "_load_candidates", AsyncMock(return_value=candidates[:2])
    )
    recover_one = AsyncMock(
        side_effect=lambda run, **_kwargs: {
            "workflow_run_id": run.id,
            "state": "succeeded",
        }
    )
    monkeypatch.setattr(recovery, "_recover_one", recover_one)

    outcomes = await recovery.recover_pending_livekit_finalizations(limit=2)

    assert len(outcomes) == 2
    assert recover_one.await_count == 2
    assert [outcome["workflow_run_id"] for outcome in outcomes] == [1, 2]


@pytest.mark.asyncio
async def test_recovery_scan_failure_is_contained(monkeypatch):
    monkeypatch.setattr(
        recovery,
        "_load_candidates",
        AsyncMock(side_effect=RuntimeError("db down")),
    )
    assert await recovery.recover_pending_livekit_finalizations() == []


# ---------------------------------------------------------------------------
# P1-A: is_incomplete_recovery_eligible unit tests
# ---------------------------------------------------------------------------

def _incomplete_run(*, state="pending", age_seconds=300, run_id=20, lease_until=None):
    marker = {"state": state, "recovery_version": 1}
    if lease_until is not None:
        marker["lease_until"] = lease_until
    return SimpleNamespace(
        id=run_id,
        is_completed=False,
        usage_info=None,  # not yet completed — no usage
        annotations={"livekit_finalization": marker},
        created_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
        mode="livekit",
    )


def test_incomplete_eligible_pending_past_grace():
    run = _incomplete_run(state="pending", age_seconds=300)
    assert recovery.is_incomplete_recovery_eligible(run) is True


def test_incomplete_eligible_failed_state():
    run = _incomplete_run(state="failed", age_seconds=300)
    assert recovery.is_incomplete_recovery_eligible(run) is True


def test_incomplete_eligible_recovering_state():
    run = _incomplete_run(state="recovering", age_seconds=300)
    # Still eligible by state; lease is not set so it isn't blocked
    assert recovery.is_incomplete_recovery_eligible(run) is True


def test_incomplete_already_completed_is_not_eligible():
    """Completed runs must not be re-processed by the incomplete path."""
    run = SimpleNamespace(
        id=21,
        is_completed=True,
        usage_info={"call_duration_seconds": 10},
        annotations={"livekit_finalization": {"state": "pending", "recovery_version": 1}},
        created_at=datetime.now(UTC) - timedelta(seconds=300),
    )
    assert recovery.is_incomplete_recovery_eligible(run) is False


def test_incomplete_fresh_run_not_eligible():
    run = _incomplete_run(state="pending", age_seconds=30)  # within 120s grace
    assert recovery.is_incomplete_recovery_eligible(run) is False


def test_incomplete_active_lease_excluded():
    from datetime import UTC, datetime, timedelta
    future = (datetime.now(UTC) + timedelta(seconds=60)).isoformat()
    run = _incomplete_run(state="recovering", age_seconds=300, lease_until=future)
    assert recovery.is_incomplete_recovery_eligible(run) is False


def test_incomplete_missing_marker_not_eligible():
    run = SimpleNamespace(
        id=22, is_completed=False, usage_info=None,
        annotations={},
        created_at=datetime.now(UTC) - timedelta(seconds=300),
    )
    assert recovery.is_incomplete_recovery_eligible(run) is False


def test_incomplete_unknown_version_not_eligible():
    run = _incomplete_run(state="pending", age_seconds=300)
    run.annotations["livekit_finalization"]["recovery_version"] = 99
    assert recovery.is_incomplete_recovery_eligible(run) is False


def test_incomplete_succeeded_state_not_eligible():
    run = _incomplete_run(state="succeeded", age_seconds=300)
    assert recovery.is_incomplete_recovery_eligible(run) is False
