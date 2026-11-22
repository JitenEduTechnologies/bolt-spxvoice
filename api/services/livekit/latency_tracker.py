from __future__ import annotations

import statistics
import time
from typing import Any
from loguru import logger


class TurnMetric:
    """Detailed timing metrics for a single conversational turn."""

    def __init__(self, turn_id: int) -> None:
        self.turn_id = turn_id
        self.user_speech_start_at: float | None = None
        self.user_speech_end_at: float | None = None
        self.thinking_start_at: float | None = None
        self.thinking_end_at: float | None = None
        self.agent_speech_start_at: float | None = None
        self.agent_speech_end_at: float | None = None
        self.interrupted_at: float | None = None
        self.interruption_stopped_at: float | None = None

    @property
    def turnaround_ms(self) -> float | None:
        """Latency from user speech completion to first agent audio frame."""
        if self.user_speech_end_at is not None and self.agent_speech_start_at is not None:
            return round(max(0.0, (self.agent_speech_start_at - self.user_speech_end_at) * 1000), 1)
        return None

    @property
    def generation_ms(self) -> float | None:
        """Time taken by model inference / thinking."""
        if self.thinking_start_at is not None and self.thinking_end_at is not None:
            return round(max(0.0, (self.thinking_end_at - self.thinking_start_at) * 1000), 1)
        return None

    @property
    def interruption_stop_ms(self) -> float | None:
        """Time from interruption detection to audio actually silencing."""
        if self.interrupted_at is not None and self.interruption_stopped_at is not None:
            return round(max(0.0, (self.interruption_stopped_at - self.interrupted_at) * 1000), 1)
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "turnaround_ms": self.turnaround_ms,
            "generation_ms": self.generation_ms,
            "interruption_stop_ms": self.interruption_stop_ms,
        }


class LatencyTracker:
    """Collects per-turn timing data and computes call-level P50/P95 latency statistics."""

    def __init__(self, run_id: int | None = None) -> None:
        self.run_id = run_id
        self._turns: dict[int, TurnMetric] = {}
        self._current_turn_id: int = 0

    def get_or_create_turn(self, turn_id: int) -> TurnMetric:
        if turn_id not in self._turns:
            self._turns[turn_id] = TurnMetric(turn_id)
        return self._turns[turn_id]

    def record_user_speech_start(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.user_speech_start_at = timestamp or time.monotonic()

    def record_user_speech_end(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.user_speech_end_at = timestamp or time.monotonic()
        self._current_turn_id = turn_id

    def record_thinking_start(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.thinking_start_at = timestamp or time.monotonic()

    def record_thinking_end(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.thinking_end_at = timestamp or time.monotonic()

    def record_agent_speech_start(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.agent_speech_start_at = timestamp or time.monotonic()
        if turn.turnaround_ms is not None:
            logger.info(
                f"[LatencyTracker] Turn {turn_id} turnaround: {turn.turnaround_ms:.1f}ms "
                f"run_id={self.run_id}"
            )

    def record_agent_speech_end(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.agent_speech_end_at = timestamp or time.monotonic()

    def record_interruption(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.interrupted_at = timestamp or time.monotonic()

    def record_interruption_stopped(self, turn_id: int, timestamp: float | None = None) -> None:
        turn = self.get_or_create_turn(turn_id)
        turn.interruption_stopped_at = timestamp or time.monotonic()

    def calculate_percentiles(self, values: list[float]) -> tuple[float, float]:
        """Calculate P50 and P95 from a list of float measurements."""
        if not values:
            return (0.0, 0.0)
        sorted_vals = sorted(values)
        p50 = statistics.median(sorted_vals)
        if len(sorted_vals) == 1:
            p95 = sorted_vals[0]
        else:
            k = (len(sorted_vals) - 1) * 0.95
            f = int(k)
            c = min(f + 1, len(sorted_vals) - 1)
            d0 = sorted_vals[f] * (c - k)
            d1 = sorted_vals[c] * (k - f)
            p95 = d0 + d1
        return (round(p50, 1), round(p95, 1))

    def summary(self) -> dict[str, Any]:
        """Generate comprehensive summary of turn metrics and P50/P95 latencies."""
        turnarounds: list[float] = [
            turn.turnaround_ms for turn in self._turns.values() if turn.turnaround_ms is not None
        ]
        generations: list[float] = [
            turn.generation_ms for turn in self._turns.values() if turn.generation_ms is not None
        ]
        interruption_stops: list[float] = [
            turn.interruption_stop_ms
            for turn in self._turns.values()
            if turn.interruption_stop_ms is not None
        ]

        p50_turnaround, p95_turnaround = self.calculate_percentiles(turnarounds)
        p50_gen, p95_gen = self.calculate_percentiles(generations)

        # Target benchmarks: P50 < 1000ms, P95 < 2000ms
        target_met = (
            len(turnarounds) > 0
            and p50_turnaround <= 1000.0
            and p95_turnaround <= 2000.0
        )

        return {
            "total_turns": len(self._turns),
            "measured_turns": len(turnarounds),
            "turnaround_ms": {
                "p50": p50_turnaround,
                "p95": p95_turnaround,
                "min": round(min(turnarounds), 1) if turnarounds else None,
                "max": round(max(turnarounds), 1) if turnarounds else None,
                "avg": round(statistics.mean(turnarounds), 1) if turnarounds else None,
                "target_met": target_met,
            },
            "generation_ms": {
                "p50": p50_gen,
                "p95": p95_gen,
                "avg": round(statistics.mean(generations), 1) if generations else None,
            },
            "interruption_stop_avg_ms": (
                round(statistics.mean(interruption_stops), 1) if interruption_stops else None
            ),
            "turn_details": [turn.to_dict() for turn in sorted(self._turns.values(), key=lambda t: t.turn_id)],
        }
