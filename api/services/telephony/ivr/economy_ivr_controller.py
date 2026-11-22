"""Asterisk ARI Economy IVR State Machine & Playback Controller.

Coordinates prerecorded greeting playback, RFC 4733 DTMF capture, action dispatch,
no-input timeouts, and automatic hang-up.
Guaranteed zero AI engine (Gemini/Cartesia/Deepgram/DeepSeek/LiveKit) initialization.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Dict, Optional
from loguru import logger

from api.db import db_client
from api.schemas.economy_ivr import (
    EconomyIVRCallContext,
    EconomyIVRConfig,
    IVRActionType,
    KeypadAction,
)
from api.services.telephony.ivr.actions import ivr_action_executor
from api.services.telephony.ivr.audio_cache import ivr_audio_cache


class EconomyIVRState:
    INITIALIZED = "initialized"
    ANSWERED = "answered"
    PLAYING_GREETING = "playing_greeting"
    WAITING_FOR_DIGIT = "waiting_for_digit"
    EXECUTING_ACTION = "executing_action"
    PLAYING_CONFIRMATION = "playing_confirmation"
    PLAYING_INVALID = "playing_invalid"
    PLAYING_TIMEOUT = "playing_timeout"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class EconomyIVRCallSession:
    """Manages the lifecycle and state transitions of a single Economy IVR call."""

    def __init__(
        self,
        channel_id: str,
        organization_id: int,
        workflow_id: int,
        workflow_run_id: int,
        config: EconomyIVRConfig,
        direction: str,
        caller_number: str,
        called_number: str,
        ari_client: Any,
        language: str = "en",
    ):
        self.channel_id = channel_id
        self.organization_id = organization_id
        self.workflow_id = workflow_id
        self.workflow_run_id = workflow_run_id
        self.config = config
        self.direction = direction
        self.caller_number = caller_number
        self.called_number = called_number
        self.ari_client = ari_client
        self.language = language or config.default_language

        self.state = EconomyIVRState.INITIALIZED
        self.active_playback_id: Optional[str] = None
        self.timeout_task: Optional[asyncio.Task] = None
        self.invalid_attempts = 0
        self.action_in_progress = False
        self.action_completed = False
        self._dtmf_lock = asyncio.Lock()

        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.entered_digits: list[str] = []

    async def start(self):
        """Answer call and begin prerecorded greeting playback."""
        logger.info(
            f"[Economy IVR] Starting call {self.channel_id} (org={self.organization_id}, "
            f"run={self.workflow_run_id}, direction={self.direction})"
        )
        self.start_time = time.time()

        # 1. Answer channel (for inbound calls; outbound calls are answered by remote party prior to Stasis)
        if self.direction == "inbound":
            try:
                await self.ari_client.answer_channel(self.channel_id)
            except Exception as e:
                logger.error(f"[Economy IVR] Failed to answer channel {self.channel_id}: {e}")
                await self.hangup()
                return

        self.state = EconomyIVRState.ANSWERED

        # 2. Resolve greeting audio for selected language
        rec_id = self.config.language_greetings.get(self.language) or self.config.greeting_recording_id
        if not rec_id:
            logger.error(f"[Economy IVR] No greeting recording configured for call {self.channel_id}")
            await self.hangup()
            return

        sound_path = await ivr_audio_cache.get_or_cache_recording(
            recording_id=rec_id,
            organization_id=self.organization_id,
        )
        if not sound_path:
            logger.error(f"[Economy IVR] Audio file for {rec_id} could not be retrieved")
            await self.hangup()
            return

        # 3. Play greeting via ARI REST API
        await self._play_sound(sound_path, next_state=EconomyIVRState.PLAYING_GREETING)

    async def on_playback_finished(self, playback_id: str):
        """Handle Asterisk PlaybackFinished event."""
        logger.debug(
            f"[Economy IVR] PlaybackFinished {playback_id} for channel {self.channel_id}, "
            f"current_state={self.state}"
        )
        if self.active_playback_id == playback_id:
            self.active_playback_id = None

        if self.state == EconomyIVRState.PLAYING_GREETING:
            # Greeting finished: check if actions configured or if announcement-only
            if not self.config.keypad_actions:
                logger.info(f"[Economy IVR] Announcement-only greeting complete on {self.channel_id}, hanging up")
                await self.hangup()
                return

            self.state = EconomyIVRState.WAITING_FOR_DIGIT
            self._start_timeout_timer()

        elif self.state == EconomyIVRState.PLAYING_CONFIRMATION:
            # Action confirmation audio finished
            self.state = EconomyIVRState.COMPLETED
            if self.config.auto_hangup_after_action:
                logger.info(f"[Economy IVR] Auto-hangup after action confirmation on {self.channel_id}")
                await self.hangup()

        elif self.state in (EconomyIVRState.PLAYING_INVALID, EconomyIVRState.PLAYING_TIMEOUT):
            # Invalid prompt or timeout prompt finished
            if self.invalid_attempts >= self.config.max_invalid_retries or self.state == EconomyIVRState.PLAYING_TIMEOUT:
                logger.info(f"[Economy IVR] Retry limit or timeout reached on {self.channel_id}, hanging up")
                await self.hangup()
            else:
                # Replay / re-wait for digit
                self.state = EconomyIVRState.WAITING_FOR_DIGIT
                self._start_timeout_timer()

    async def on_dtmf_received(self, digit: str):
        """Handle Asterisk ChannelDtmfReceived event."""
        digit = digit.strip()
        logger.info(f"[Economy IVR] DTMF digit '{digit}' received on channel {self.channel_id}")

        async with self._dtmf_lock:
            # 1. Concurrency & deduplication guard
            if self.action_in_progress or self.action_completed or self.state in (
                EconomyIVRState.COMPLETED,
                EconomyIVRState.TERMINATED,
                EconomyIVRState.PLAYING_CONFIRMATION,
            ):
                logger.debug(f"[Economy IVR] Ignoring digit '{digit}': action already in progress/completed or call terminating")
                return

            self.entered_digits.append(digit)

            # 2. Cancel timeout timer
            self._cancel_timeout_timer()

            # 3. Barge-in: if greeting is still playing and caller presses digit, stop audio
            if self.config.allow_barge_in and self.active_playback_id:
                logger.info(f"[Economy IVR] Barge-in detected on {self.channel_id}, stopping playback {self.active_playback_id}")
                try:
                    await self.ari_client.stop_playback(self.active_playback_id)
                except Exception as e:
                    logger.debug(f"[Economy IVR] Non-fatal error stopping playback: {e}")
                self.active_playback_id = None

            # 4. Check if digit matches a configured keypad action
            action: Optional[KeypadAction] = self.config.keypad_actions.get(digit)

            if action:
                # Valid action matched!
                self.action_in_progress = True
                self.state = EconomyIVRState.EXECUTING_ACTION

                result = await ivr_action_executor.execute_action(
                    action=action,
                    organization_id=self.organization_id,
                    caller_number=self.caller_number,
                    workflow_id=self.workflow_id,
                    workflow_run_id=self.workflow_run_id,
                    language=self.language,
                )

                self.action_in_progress = False
                self.action_completed = True

                # If action configured with confirmation audio, play it
                if action.confirmation_recording_id:
                    conf_sound = await ivr_audio_cache.get_or_cache_recording(
                        recording_id=action.confirmation_recording_id,
                        organization_id=self.organization_id,
                    )
                    if conf_sound:
                        await self._play_sound(conf_sound, next_state=EconomyIVRState.PLAYING_CONFIRMATION)
                        return

                # No confirmation audio: complete immediately
                self.state = EconomyIVRState.COMPLETED
                if self.config.auto_hangup_after_action:
                    await self.hangup()

            else:
                # Invalid digit handling
                self.invalid_attempts += 1
                logger.warning(
                    f"[Economy IVR] Invalid digit '{digit}' entered ({self.invalid_attempts}/"
                    f"{self.config.max_invalid_retries}) on {self.channel_id}"
                )

                if self.invalid_attempts >= self.config.max_invalid_retries:
                    # Exceeded retry limit
                    logger.info(f"[Economy IVR] Max invalid retries exceeded on {self.channel_id}")
                    await self.hangup()
                else:
                    # Play invalid prompt if configured
                    if self.config.invalid_input_recording_id:
                        inv_sound = await ivr_audio_cache.get_or_cache_recording(
                            recording_id=self.config.invalid_input_recording_id,
                            organization_id=self.organization_id,
                        )
                        if inv_sound:
                            await self._play_sound(inv_sound, next_state=EconomyIVRState.PLAYING_INVALID)
                            return

                    # Otherwise re-arm timeout
                    self.state = EconomyIVRState.WAITING_FOR_DIGIT
                    self._start_timeout_timer()

    async def on_timeout(self):
        """Called when no DTMF input is entered before timeout_seconds expires."""
        logger.warning(f"[Economy IVR] No-input timeout on channel {self.channel_id}")
        self.state = EconomyIVRState.PLAYING_TIMEOUT

        if self.config.timeout_recording_id:
            timeout_sound = await ivr_audio_cache.get_or_cache_recording(
                recording_id=self.config.timeout_recording_id,
                organization_id=self.organization_id,
            )
            if timeout_sound:
                await self._play_sound(timeout_sound, next_state=EconomyIVRState.PLAYING_TIMEOUT)
                return

        # Immediate hangup if no timeout audio
        await self.hangup()

    async def on_channel_destroyed(self, cause: int = 0, cause_txt: str = "unknown"):
        """Called when channel is destroyed or caller disconnects."""
        logger.info(
            f"[Economy IVR] Channel {self.channel_id} destroyed by Asterisk (cause={cause}, txt={cause_txt})"
        )
        self._cancel_timeout_timer()
        if self.state != EconomyIVRState.TERMINATED:
            await self.hangup(already_destroyed=True)

    async def hangup(self, already_destroyed: bool = False):
        """Terminate Asterisk channel, cancel timers, unregister session, and record call summary."""
        if self.state == EconomyIVRState.TERMINATED:
            return

        self._cancel_timeout_timer()
        self.state = EconomyIVRState.TERMINATED
        ivr_manager.remove_session(self.channel_id)

        self.end_time = time.time()
        duration = max(0.0, self.end_time - self.start_time)
        pulses = max(1, math.ceil(duration / 60.0))

        logger.info(
            f"[Economy IVR] Terminating channel {self.channel_id} (duration={duration:.1f}s, "
            f"pulses={pulses})"
        )

        # Record usage in workflow_run context for later carrier reconciliation
        try:
            await db_client.update_workflow_run(
                run_id=self.workflow_run_id,
                gathered_context={
                    "ivr_mode": "economy_ivr",
                    "duration_seconds": round(duration, 2),
                    "billed_pulses": pulses,
                    "digits_entered": self.entered_digits,
                    "invalid_attempts": self.invalid_attempts,
                    "action_completed": self.action_completed,
                    "final_state": self.state,
                },
            )
        except Exception as e:
            logger.error(f"[Economy IVR] Error recording call summary: {e}")

        # Delete channel in Asterisk if not already destroyed
        if not already_destroyed:
            try:
                await self.ari_client.hangup_channel(self.channel_id)
            except Exception as e:
                logger.debug(f"[Economy IVR] Channel {self.channel_id} already gone or error on hangup: {e}")

    async def _play_sound(self, sound_path: str, next_state: str):
        """Initiate audio playback on Asterisk channel with failure recovery."""
        self.state = next_state
        media_uri = f"sound:{sound_path}"
        try:
            playback = await self.ari_client.play_sound(self.channel_id, media_uri)
            if playback and isinstance(playback, dict) and playback.get("id"):
                self.active_playback_id = playback.get("id")
            else:
                logger.warning(f"[Economy IVR] Failed playback response on {self.channel_id}: {playback}")
                await self._on_playback_failure()
        except Exception as e:
            logger.error(f"[Economy IVR] Exception starting playback on {self.channel_id}: {e}")
            await self._on_playback_failure()

    async def _on_playback_failure(self):
        """Recover from playback failure by hanging up cleanly."""
        logger.warning(f"[Economy IVR] Playback failure encountered, hanging up channel {self.channel_id}")
        await self.hangup()

    def _start_timeout_timer(self):
        self._cancel_timeout_timer()
        self.timeout_task = asyncio.create_task(self._timeout_worker())

    def _cancel_timeout_timer(self):
        if self.timeout_task and not self.timeout_task.done():
            self.timeout_task.cancel()
            self.timeout_task = None

    async def _timeout_worker(self):
        try:
            await asyncio.sleep(self.config.timeout_seconds)
            await self.on_timeout()
        except asyncio.CancelledError:
            pass


class EconomyIVRManager:
    """Manages active Economy IVR call sessions across the application."""

    def __init__(self):
        self._sessions: Dict[str, EconomyIVRCallSession] = {}

    def register_session(self, channel_id: str, session: EconomyIVRCallSession):
        self._sessions[channel_id] = session

    def get_session(self, channel_id: str) -> Optional[EconomyIVRCallSession]:
        return self._sessions.get(channel_id)

    def remove_session(self, channel_id: str) -> Optional[EconomyIVRCallSession]:
        return self._sessions.pop(channel_id, None)


# Global IVR manager singleton
ivr_manager = EconomyIVRManager()
