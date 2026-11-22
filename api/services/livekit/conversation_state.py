from __future__ import annotations

import time
from enum import Enum
from typing import Any, Callable
from loguru import logger


class ConversationState(str, Enum):
    """Explicit states for real-time conversational turns (10 canonical states with aliases)."""
    IDLE = "IDLE"
    WAITING_FOR_CALLER = "WAITING_FOR_CALLER"
    LISTENING = "LISTENING"  # Alias for WAITING_FOR_CALLER
    CALLER_SPEAKING = "CALLER_SPEAKING"
    CUSTOMER_SPEAKING = "CUSTOMER_SPEAKING"  # Alias for CALLER_SPEAKING
    POSSIBLE_END_OF_TURN = "POSSIBLE_END_OF_TURN"
    AI_PROCESSING = "AI_PROCESSING"
    THINKING = "THINKING"  # Alias for AI_PROCESSING
    TOOL_EXECUTING = "TOOL_EXECUTING"
    AI_SPEAKING = "AI_SPEAKING"
    AGENT_SPEAKING = "AGENT_SPEAKING"  # Alias for AI_SPEAKING
    CALLER_INTERRUPTING = "CALLER_INTERRUPTING"
    INTERRUPTED = "INTERRUPTED"  # Alias for CALLER_INTERRUPTING
    SILENCE_RECOVERY = "SILENCE_RECOVERY"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    ENDING = "ENDING"
    WRAPUP = "WRAPUP"  # Alias for ENDING


# Allowed state transitions: from_state -> set of valid to_states
VALID_TRANSITIONS: dict[ConversationState, set[ConversationState]] = {
    ConversationState.IDLE: {
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.WAITING_FOR_CALLER: {
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.SILENCE_RECOVERY,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.LISTENING: {
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.SILENCE_RECOVERY,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.CALLER_SPEAKING: {
        ConversationState.POSSIBLE_END_OF_TURN,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.CUSTOMER_SPEAKING: {
        ConversationState.POSSIBLE_END_OF_TURN,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.POSSIBLE_END_OF_TURN: {
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
    },
    ConversationState.AI_PROCESSING: {
        ConversationState.TOOL_EXECUTING,
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.THINKING: {
        ConversationState.TOOL_EXECUTING,
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.TOOL_EXECUTING: {
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.CALLER_INTERRUPTING,
        ConversationState.INTERRUPTED,
        ConversationState.PROVIDER_ERROR,
    },
    ConversationState.AI_SPEAKING: {
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.CALLER_INTERRUPTING,
        ConversationState.INTERRUPTED,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.TOOL_EXECUTING,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.AGENT_SPEAKING: {
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.CALLER_INTERRUPTING,
        ConversationState.INTERRUPTED,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.TOOL_EXECUTING,
        ConversationState.PROVIDER_ERROR,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.CALLER_INTERRUPTING: {
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.INTERRUPTED: {
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.SILENCE_RECOVERY: {
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.CALLER_SPEAKING,
        ConversationState.CUSTOMER_SPEAKING,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.PROVIDER_ERROR: {
        ConversationState.WAITING_FOR_CALLER,
        ConversationState.LISTENING,
        ConversationState.AI_PROCESSING,
        ConversationState.THINKING,
        ConversationState.AI_SPEAKING,
        ConversationState.AGENT_SPEAKING,
        ConversationState.ENDING,
        ConversationState.WRAPUP,
    },
    ConversationState.ENDING: {
        ConversationState.IDLE,
    },
    ConversationState.WRAPUP: {
        ConversationState.IDLE,
    },
}
# Maximum duration in seconds allowed before a watchdog triggers recovery
DEFAULT_STATE_TIMEOUTS: dict[ConversationState, float] = {
    ConversationState.THINKING: 15.0,        # Recovery if model takes > 15s without output
    ConversationState.INTERRUPTED: 5.0,      # Should transition to speaking or listening
    ConversationState.CUSTOMER_SPEAKING: 30.0, # Safeguard against stuck VAD
}


class ConversationStateMachine:
    """Manages real-time conversational turn states with safety guards and history tracking."""

    def __init__(
        self,
        run_id: int | None = None,
        *,
        on_state_changed: Callable[[ConversationState, ConversationState, str], Any] | None = None,
        timeouts: dict[ConversationState, float] | None = None,
    ) -> None:
        self.run_id = run_id
        self._state: ConversationState = ConversationState.IDLE
        self._state_entered_at: float = time.monotonic()
        self._turn_id: int = 0
        self._on_state_changed = on_state_changed
        self._timeouts = {**DEFAULT_STATE_TIMEOUTS, **(timeouts or {})}
        self._history: list[dict[str, Any]] = [
            {
                "state": ConversationState.IDLE.value,
                "entered_at": self._state_entered_at,
                "duration_seconds": 0.0,
                "reason": "initialization",
                "turn_id": 0,
            }
        ]

    @property
    def state(self) -> ConversationState:
        return self._state

    @property
    def turn_id(self) -> int:
        return self._turn_id

    @property
    def time_in_current_state(self) -> float:
        return max(0.0, time.monotonic() - self._state_entered_at)

    def transition_to(
        self,
        new_state: ConversationState,
        reason: str = "",
        *,
        force: bool = False,
    ) -> bool:
        """Attempt to transition to a new conversation state.

        Args:
            new_state: The target ConversationState.
            reason: Human-readable rationale for the transition.
            force: If True, bypass validity matrix (for emergency error recovery).

        Returns:
            True if transition succeeded, False if rejected.
        """
        if self._state == new_state:
            return True

        valid_targets = VALID_TRANSITIONS.get(self._state, set())
        if not force and new_state not in valid_targets:
            logger.warning(
                f"[ConversationStateMachine] Invalid transition rejected "
                f"run_id={self.run_id} from {self._state.value} to {new_state.value} "
                f"(reason: {reason})"
            )
            return False

        now = time.monotonic()
        prev_state = self._state
        prev_duration = max(0.0, now - self._state_entered_at)

        # Update last history item duration
        if self._history:
            self._history[-1]["duration_seconds"] = round(prev_duration, 3)

        # Advance turn ID when customer begins a new utterance
        if new_state == ConversationState.CUSTOMER_SPEAKING and prev_state != ConversationState.INTERRUPTED:
            self._turn_id += 1

        self._state = new_state
        self._state_entered_at = now

        entry = {
            "state": new_state.value,
            "entered_at": now,
            "duration_seconds": 0.0,
            "reason": reason,
            "turn_id": self._turn_id,
        }
        self._history.append(entry)

        logger.info(
            f"[ConversationStateMachine] State changed run_id={self.run_id} "
            f"{prev_state.value} -> {new_state.value} "
            f"(prev_duration={prev_duration:.2f}s, turn={self._turn_id}, reason='{reason}')"
        )

        if self._on_state_changed:
            try:
                self._on_state_changed(prev_state, new_state, reason)
            except Exception as exc:
                logger.error(f"[ConversationStateMachine] callback error: {exc}")

        return True

    def check_timeouts(self) -> ConversationState | None:
        """Check if current state has exceeded its safety timeout and auto-recover."""
        timeout = self._timeouts.get(self._state)
        if timeout is None:
            return None

        elapsed = self.time_in_current_state
        if elapsed > timeout:
            recovery_target = ConversationState.LISTENING
            logger.warning(
                f"[ConversationStateMachine] State {self._state.value} timed out "
                f"({elapsed:.1f}s > {timeout:.1f}s) for run_id={self.run_id}; "
                f"recovering to {recovery_target.value}"
            )
            self.transition_to(
                recovery_target,
                reason=f"timeout_recovery_{self._state.value.lower()}",
                force=True,
            )
            return recovery_target

        return None

    def history(self) -> list[dict[str, Any]]:
        """Return full transition history with updated current state duration."""
        hist = [dict(item) for item in self._history]
        if hist:
            hist[-1]["duration_seconds"] = round(self.time_in_current_state, 3)
        return hist

