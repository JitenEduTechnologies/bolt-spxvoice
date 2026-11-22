"""Gate 3C — duration-separation regression (run #345 class).

Caller-connected duration must be tracked independently of worker/session
lifetime and persisted distinctly in usage/cost/annotations so the historical
26 s caller vs 61 s displayed discrepancy cannot recur silently.
"""
from types import SimpleNamespace

from api.services.livekit import worker


def test_usage_info_includes_caller_connected_fields():
    usage = SimpleNamespace(model_usage=[])
    info = worker._livekit_usage_info(
        usage,
        61,
        caller_connected_seconds=26,
        caller_connected_at="2026-11-20T10:00:00+00:00",
    )
    assert info["call_duration_seconds"] == 61  # session total (backward compat)
    assert info["caller_connected_duration_seconds"] == 26
    assert info["caller_connected_at"] == "2026-11-20T10:00:00+00:00"


def test_usage_info_without_caller_connection_omits_fields():
    info = worker._livekit_usage_info(SimpleNamespace(model_usage=[]), 61)
    assert "caller_connected_duration_seconds" not in info
    assert "caller_connected_at" not in info


def test_caller_connected_duration_is_bounded_and_distinct():
    agent = SimpleNamespace(
        _started_at=1000.0, _caller_connected_monotonic=1035.0
    )
    assert worker._caller_connected_duration(agent, now=1061.0) == 26
    # Never negative even with clock skew.
    assert worker._caller_connected_duration(agent, now=1030.0) == 0


def test_caller_connected_duration_none_when_never_connected():
    agent = SimpleNamespace(_started_at=1000.0)
    assert worker._caller_connected_duration(agent, now=1061.0) is None


def test_agent_has_caller_connection_attributes():
    src = __import__("inspect").getsource(worker.LiveKitWorkflowAgent.__init__)
    assert "_caller_connected_monotonic" in src
    assert "_caller_connected_wall" in src
    assert "_caller_disconnected_monotonic" in src


def test_run345_scenario_connected_26_finalized_61_displays_26():
    """Caller disconnects at 26 s; worker finalizes at 61 s. The caller-connected
    duration must be bounded by the disconnect event, not finalization."""
    agent = SimpleNamespace(
        _started_at=100.0,
        _caller_connected_monotonic=100.0,
        _caller_disconnected_monotonic=126.0,
    )
    assert worker._caller_connected_duration(agent, now=161.0) == 26


def test_missing_disconnect_falls_back_to_finalization_bound():
    agent = SimpleNamespace(
        _started_at=100.0, _caller_connected_monotonic=100.0
    )
    assert worker._caller_connected_duration(agent, now=161.0) == 61


def test_never_connected_returns_none_even_with_disconnect():
    agent = SimpleNamespace(_caller_disconnected_monotonic=126.0)
    assert worker._caller_connected_duration(agent, now=161.0) is None


def test_duplicate_disconnect_records_single_authoritative_end():
    # Source contract: the disconnect timestamp is set only when cleanup fires
    # the shutdown (after the rejoin recheck); the handler's pending flag
    # prevents duplicate cleanups (covered by gate2 lifecycle tests).
    src = __import__("inspect").getsource(worker._caller_disconnect_cleanup)
    set_at = src.index('"_caller_disconnected_monotonic"')
    schedule_at = src.index("_schedule_shutdown")
    assert set_at < schedule_at


def test_display_resolver_prefers_caller_connected_duration():
    from api.services.business.conversation_view import resolve_duration_seconds

    run_new = SimpleNamespace(
        usage_info={
            "call_duration_seconds": 61,
            "caller_connected_duration_seconds": 26,
        },
        cost_info={"call_duration_seconds": 61},
    )
    assert resolve_duration_seconds(run_new) == 26.0
    # Historical records without the new field keep the old display value.
    run_legacy = SimpleNamespace(
        usage_info={"call_duration_seconds": 61}, cost_info={}
    )
    assert resolve_duration_seconds(run_legacy) == 61.0

