from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, Awaitable, Callable
from loguru import logger

from api.services.livekit.conversation_state import ConversationState
from api.services.livekit.language_packs import get_language_pack


# Localized default silence prompts across supported languages
DEFAULT_SILENCE_PROMPTS: dict[str, dict[int, str]] = {
    "en": {
        1: "Take your time, I'm listening.",
        2: "Are you still there? Please let me know if you can hear me.",
        3: "It seems we might be disconnected. Thank you for your time. Goodbye.",
    },
    "hi": {
        1: "Aap aaram se boliye, main sun raha hoon.",
        2: "Kya aap mujhe sun paa rahe hain? Kripya bataiye.",
        3: "Lagta hai humara sampark toot gaya hai. Samay dene ke liye dhanyavad. Alvida.",
    },
    "te": {
        1: "Meeru aalochinchi cheppandi, nenu vintunnanu.",
        2: "Meeru line lo unnara? Dayachesi nenu matladedi vinapadithe cheppandi.",
        3: "Mee vaipu nundi maata vinapadatam ledu. Mee samayaniki dhanyavaadaalu. Selavu.",
    },
}


class SilenceManager:
    """Manages progressive 3-stage silence recovery with state-aware gating."""

    def __init__(
        self,
        *,
        stage1_seconds: float = 4.0,
        stage2_seconds: float = 8.0,
        stage3_seconds: float = 14.0,
        language: str = "en",
        custom_prompts: dict[int, str] | None = None,
        speak_callback: Callable[[str], Any] | None = None,
        disconnect_callback: Callable[[str], Any] | None = None,
    ) -> None:
        self.stage1_seconds = stage1_seconds
        self.stage2_seconds = stage2_seconds
        self.stage3_seconds = stage3_seconds
        pack = get_language_pack(language)
        self.language = pack.code
        self.speak_callback = speak_callback
        self.disconnect_callback = disconnect_callback

        # Resolve prompts from language pack, with custom overrides
        prompts: dict[int, str] = {
            1: pack.silence_stage1,
            2: pack.silence_stage2,
            3: pack.silence_stage3,
        }
        if custom_prompts:
            prompts.update(custom_prompts)
        self.prompts = prompts

        self._current_stage: int = 0
        self._last_activity_at: float = time.monotonic()
        self._is_tool_running: bool = False
        self._disposition: str | None = None

    @property
    def current_stage(self) -> int:
        return self._current_stage

    @property
    def silence_duration(self) -> float:
        return max(0.0, time.monotonic() - self._last_activity_at)

    @property
    def disposition(self) -> str | None:
        return self._disposition

    def set_tool_running(self, running: bool) -> None:
        """Mark whether a tool or background operation is in progress."""
        self._is_tool_running = running
        if running:
            self.reset_silence(reason="tool_executing")

    def reset_silence(self, reason: str = "") -> None:
        """Reset silence timer and clear stage when user or agent is active."""
        self._last_activity_at = time.monotonic()
        prev_stage = self._current_stage
        self._current_stage = 0
        if prev_stage > 0:
            logger.debug(
                f"[SilenceManager] Silence timer reset (was stage {prev_stage}, reason='{reason}')"
            )

    def can_trigger(self, current_state: ConversationState) -> bool:
        """Determine if a silence prompt can safely trigger right now."""
        # MUST only trigger when the agent is waiting in LISTENING state
        if current_state != ConversationState.LISTENING:
            return False
        # Do not trigger if a tool or external API is running
        if self._is_tool_running:
            return False
        return True

    async def _safe_invoke(self, callback: Callable | None, *args: Any) -> None:
        if not callback:
            return
        try:
            res = callback(*args)
            if inspect.isawaitable(res):
                await res
        except Exception as exc:
            logger.error(f"[SilenceManager] Callback error: {exc}")

    async def evaluate_silence(self, current_state: ConversationState) -> int | None:
        """Evaluate current silence duration against thresholds and trigger action if eligible.

        Returns:
            The triggered stage number (1, 2, or 3), or None if no threshold met or ineligible.
        """
        if not self.can_trigger(current_state):
            # Keep updating timestamp if we are not actively listening to prevent sudden burst triggers
            if current_state in (
                ConversationState.CUSTOMER_SPEAKING,
                ConversationState.THINKING,
                ConversationState.AGENT_SPEAKING,
            ):
                self._last_activity_at = time.monotonic()
            return None

        elapsed = self.silence_duration

        # Stage 3: Polite closing & clean disconnect
        if elapsed >= self.stage3_seconds and self._current_stage < 3:
            self._current_stage = 3
            self._disposition = "no_response"
            closing_text = self.prompts.get(3, DEFAULT_SILENCE_PROMPTS["en"][3])
            logger.info(
                f"[SilenceManager] Stage 3 silence threshold reached ({elapsed:.1f}s >= {self.stage3_seconds}s). "
                f"Closing call with disposition='no_response'."
            )
            await self._safe_invoke(self.speak_callback, closing_text)
            await self._safe_invoke(self.disconnect_callback, "no_response_timeout")
            return 3

        # Stage 2: Contextual clarification
        elif elapsed >= self.stage2_seconds and self._current_stage < 2:
            self._current_stage = 2
            clarify_text = self.prompts.get(2, DEFAULT_SILENCE_PROMPTS["en"][2])
            logger.info(
                f"[SilenceManager] Stage 2 silence prompt triggered ({elapsed:.1f}s >= {self.stage2_seconds}s)"
            )
            await self._safe_invoke(self.speak_callback, clarify_text)
            return 2

        # Stage 1: Gentle patience confirmation
        elif elapsed >= self.stage1_seconds and self._current_stage < 1:
            self._current_stage = 1
            nudge_text = self.prompts.get(1, DEFAULT_SILENCE_PROMPTS["en"][1])
            logger.info(
                f"[SilenceManager] Stage 1 silence nudge triggered ({elapsed:.1f}s >= {self.stage1_seconds}s)"
            )
            await self._safe_invoke(self.speak_callback, nudge_text)
            return 1

        return None
