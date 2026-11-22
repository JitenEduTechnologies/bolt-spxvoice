from __future__ import annotations

import re
import time
from enum import Enum
from typing import Any
from loguru import logger


class InterruptionDecision(str, Enum):
    """Decision produced when evaluating user speech during agent output."""
    BACKCHANNEL = "BACKCHANNEL"      # Acoustic nod / short acknowledgment: keep agent speaking
    GENUINE_BARGE_IN = "GENUINE_BARGE_IN"  # Genuine interruption: stop agent immediately
    NOISE = "NOISE"                  # Transient burst / cough / click: ignore


# Common acoustic backchannel words across supported languages (lower-case, punctuation stripped)
DEFAULT_BACKCHANNEL_VOCABULARY: set[str] = {
    # English
    "yeah", "yes", "yep", "uh", "huh", "uh huh", "uh-huh", "uhhuh",
    "mhm", "mm", "hm", "hmm", "mm-hmm", "mmhmm",
    "okay", "ok", "right", "sure", "got it", "i see", "ah",
    # Hindi / Romanized Hindi
    "ha", "haa", "haan", "theek", "hai", "theek hai", "theekhai", "achha", "accha",
    "achaa", "sahi", "sahi hai", "ji", "ji haan", "jihaan", "bolo",
    # Telugu / Romanized Telugu
    "avunu", "sare", "avuna", "alage", "alaage", "anthey", "kadu",
    "cheppandi", "vinapadutundi", "haa",
}


class InterruptionFilter:
    """Intelligently classifies incoming customer speech during active agent playback."""

    def __init__(
        self,
        *,
        min_duration_seconds: float = 0.45,
        min_words_for_barge_in: int = 2,
        backchannel_filter_enabled: bool = True,
        custom_backchannel_words: set[str] | None = None,
    ) -> None:
        self.min_duration_seconds = min_duration_seconds
        self.min_words_for_barge_in = min_words_for_barge_in
        self.backchannel_filter_enabled = backchannel_filter_enabled
        self.backchannel_vocab = set(DEFAULT_BACKCHANNEL_VOCABULARY)
        if custom_backchannel_words:
            self.backchannel_vocab.update(w.strip().lower() for w in custom_backchannel_words)

        self._interruption_count: int = 0
        self._backchannel_count: int = 0
        self._noise_count: int = 0
        self._last_barge_in_at: float | None = None

    @property
    def stats(self) -> dict[str, int]:
        return {
            "interruptions": self._interruption_count,
            "backchannels": self._backchannel_count,
            "noise_filtered": self._noise_count,
        }

    def evaluate(
        self,
        duration_seconds: float,
        transcript: str | None = None,
    ) -> InterruptionDecision:
        """Evaluate incoming user speech during agent speaking.

        Args:
            duration_seconds: Length of the user speech segment detected by VAD.
            transcript: Transcribed speech if already available from STT.

        Returns:
            InterruptionDecision (BACKCHANNEL, GENUINE_BARGE_IN, or NOISE).
        """
        # 1. Transient noise filter (< 0.20s with no meaningful text)
        if duration_seconds < 0.20 and (not transcript or len(transcript.strip()) == 0):
            self._noise_count += 1
            logger.debug(
                f"[InterruptionFilter] Classified as NOISE "
                f"(duration={duration_seconds:.2f}s, text={transcript!r})"
            )
            return InterruptionDecision.NOISE

        normalized_text = self._normalize_text(transcript) if transcript else ""
        words = normalized_text.split() if normalized_text else []
        word_count = len(words)

        # 2. Check for explicit backchannel words if filter is enabled
        if self.backchannel_filter_enabled and word_count > 0:
            if self._is_backchannel_phrase(normalized_text, words):
                self._backchannel_count += 1
                logger.info(
                    f"[InterruptionFilter] Filtered BACKCHANNEL acknowledgment "
                    f"duration={duration_seconds:.2f}s, text={transcript!r}"
                )
                return InterruptionDecision.BACKCHANNEL

        # 3. Acoustic duration check:
        # If speech is very short (< min_duration_seconds) and has 0 or 1 word:
        if duration_seconds < self.min_duration_seconds and word_count <= 1:
            if word_count == 0:
                self._noise_count += 1
                return InterruptionDecision.NOISE
            if self.backchannel_filter_enabled:
                self._backchannel_count += 1
                return InterruptionDecision.BACKCHANNEL

        # 4. Otherwise: Genuine barge-in
        self._interruption_count += 1
        self._last_barge_in_at = time.monotonic()
        logger.info(
            f"[InterruptionFilter] Confirmed GENUINE_BARGE_IN "
            f"duration={duration_seconds:.2f}s, words={word_count}, text={transcript!r}"
        )
        return InterruptionDecision.GENUINE_BARGE_IN

    def _normalize_text(self, text: str) -> str:
        # Lowercase, replace hyphens and punctuation with spaces
        cleaned = re.sub(r"[^\w\s]", " ", text.lower())
        return " ".join(cleaned.split())

    def _is_backchannel_phrase(self, normalized_text: str, words: list[str]) -> bool:
        if not normalized_text:
            return False

        # Single word match: e.g. "yeah", "theek", "avunu", "okay"
        if len(words) == 1 and words[0] in self.backchannel_vocab:
            return True

        # Exact multi-word match: e.g. "theek hai", "ji haan", "got it", "uh huh"
        if normalized_text in self.backchannel_vocab:
            return True

        # All words in short utterance are backchannel particles (e.g. "yeah theek hai", "okay avunu")
        if len(words) <= 3 and all(w in self.backchannel_vocab for w in words):
            return True

        return False
