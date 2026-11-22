import pytest
from api.services.livekit.latency_tracker import LatencyTracker, TurnMetric


def test_turn_metric_calculations():
    turn = TurnMetric(turn_id=1)
    turn.user_speech_start_at = 10.0
    turn.user_speech_end_at = 12.0  # Spoke for 2s
    turn.thinking_start_at = 12.0
    turn.thinking_end_at = 12.6    # Generation took 600ms
    turn.agent_speech_start_at = 12.7 # Turnaround took 700ms
    turn.agent_speech_end_at = 15.0

    assert turn.turnaround_ms == 700.0
    assert turn.generation_ms == 600.0
    assert turn.interruption_stop_ms is None

    turn.interrupted_at = 14.0
    turn.interruption_stopped_at = 14.15 # Stopped in 150ms
    assert turn.interruption_stop_ms == 150.0


def test_latency_tracker_p50_and_p95_percentiles():
    tracker = LatencyTracker(run_id=201)

    # Simulate 10 turns with known turnaround latencies (in seconds)
    # Latencies in ms: 500, 600, 700, 750, 800, 850, 900, 950, 1100, 1800
    latencies_sec = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.1, 1.8]

    for idx, lat in enumerate(latencies_sec, 1):
        tracker.record_user_speech_end(idx, timestamp=100.0)
        tracker.record_thinking_start(idx, timestamp=100.0)
        tracker.record_thinking_end(idx, timestamp=100.0 + (lat * 0.8))
        tracker.record_agent_speech_start(idx, timestamp=100.0 + lat)

    summary = tracker.summary()
    assert summary["total_turns"] == 10
    assert summary["measured_turns"] == 10

    # P50 should be around (800 + 850) / 2 = 825ms
    turnaround = summary["turnaround_ms"]
    assert 800 <= turnaround["p50"] <= 850
    # P95 should be around 1485ms (well within target)
    assert 1400 <= turnaround["p95"] <= 1800
    assert turnaround["target_met"] is True
    assert turnaround["min"] == 500.0
    assert turnaround["max"] == 1800.0


def test_target_met_flags_false_when_p95_exceeds_threshold():
    tracker = LatencyTracker(run_id=202)

    # Simulate high latency turn (> 2500ms)
    tracker.record_user_speech_end(1, timestamp=10.0)
    tracker.record_agent_speech_start(1, timestamp=12.8) # 2800ms

    summary = tracker.summary()
    assert summary["turnaround_ms"]["target_met"] is False


def test_interruption_stop_latency_averaging():
    tracker = LatencyTracker(run_id=203)

    turn1 = tracker.get_or_create_turn(1)
    turn1.interrupted_at = 10.0
    turn1.interruption_stopped_at = 10.12 # 120ms

    turn2 = tracker.get_or_create_turn(2)
    turn2.interrupted_at = 20.0
    turn2.interruption_stopped_at = 20.18 # 180ms

    summary = tracker.summary()
    assert summary["interruption_stop_avg_ms"] == 150.0

