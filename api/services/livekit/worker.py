from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
import uuid
import wave
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types as genai_types
from loguru import logger

from api.db import db_client
from api.enums import CallType, WorkflowRunMode, WorkflowRunState
from api.services.business.agent_training import resolve_max_call_duration_seconds
from api.services.configuration.endpoint_policy import check_endpoint
from api.services.configuration.registry import ServiceProviders
from api.services.configuration.resolve import resolve_effective_config
from api.services.gen_ai import resolve_embedding_settings
from api.services.livekit import post_call
from api.services.livekit.runtime_config import (
    effective_livekit_settings,
    livekit_environment,
)
from api.services.livekit.conversation_state import (
    ConversationState,
    ConversationStateMachine,
)
from api.services.livekit.interruption_filter import InterruptionFilter
from api.services.livekit.language_packs import get_language_pack
from api.services.livekit.latency_tracker import LatencyTracker
from api.services.livekit.silence_manager import SilenceManager
from api.services.workflow.dto import ReactFlowDTO
from api.services.workflow.pipecat_engine_context_composer import (
    compose_system_prompt_for_node,
)
from api.services.workflow.tools.knowledge_base import (
    get_knowledge_base_tool,
    retrieve_from_knowledge_base,
)
from api.services.workflow.workflow_graph import Edge, Node, WorkflowGraph
from api.utils.template_renderer import render_template
from livekit import agents, rtc
from livekit.agents import Agent, AgentSession, JobContext, cli, llm
from livekit.plugins import google, openai, silero

FEEDBACK_TOPIC = "spx-voice.feedback"
DEFAULT_OPENING = "Hello, this is your SPX Voice assistant. How can I help you today?"
OPENING_AUDIO_CACHE_DIR = (
    Path(__file__).resolve().parents[2] / ".runtime" / "livekit_openings"
)
OPENING_AUDIO_LEADING_SILENCE_MS = 1000
OPENING_AUDIO_TRAILING_SILENCE_MS = 1100
OPENING_AUDIO_CACHE_FORMAT = (
    "pcm24k-wav-v4"
    f"-lead{OPENING_AUDIO_LEADING_SILENCE_MS}"
    f"-tail{OPENING_AUDIO_TRAILING_SILENCE_MS}"
)
OPENING_AUDIO_GENERATION_TIMEOUT_SECONDS = 20.0
OPENING_AUDIO_MAX_DURATION_SECONDS = 12.0
GEMINI_TTS_MODEL = "gemini-2.5-flash-preview-tts"
GEMINI_TTS_SAMPLE_RATE = 24000
GEMINI_TTS_CHANNELS = 1
GEMINI_TTS_FRAME_MS = 20
WAV_FRAME_MS = 20
FEEDBACK_FLUSH_INTERVAL_SECONDS = 1.0
GEMINI_SERVER_VAD_PREFIX_PADDING_MS = 120
GEMINI_SERVER_VAD_SILENCE_DURATION_MS = 450
FAST_GEMINI_SERVER_VAD_PREFIX_PADDING_MS = 320
FAST_GEMINI_SERVER_VAD_SILENCE_DURATION_MS = 480
FAST_ENDPOINTING_MIN_DELAY_SECONDS = 0.14
FAST_ENDPOINTING_MAX_DELAY_SECONDS = 0.32
FAST_INTERRUPTION_MIN_DURATION_SECONDS = 0.55
FAST_INTERRUPTION_MIN_WORDS = 2
FALSE_INTERRUPTION_TIMEOUT_SECONDS = 1.5
FAST_PREEMPTIVE_MAX_SPEECH_DURATION_SECONDS = 3.2
FAST_LATENCY_PROFILE_MARKERS = ("fast", "insane", "low_latency", "ultra")
# Generic, tenant-neutral low-latency guidance. Workflow-specific data
# collection (e.g. lead capture) is added separately and only when that feature
# is enabled, so this prompt must not hardcode any one deployment's fields,
# language, or honorifics.
FAST_RESPONSE_INSTRUCTIONS = """\nPROFESSIONAL VOICE ASSISTANT:
You are a professional voice assistant for the configured workflow.

Tone: courteous, neutral, concise, and official. Avoid slang, jokes, flirting, and filler.

Call flow:
- Answer the caller's latest question first in one short sentence.
- Follow the workflow's node instructions for what to ask or collect next.
- Use tool results as guidance only. Do not sound like an IVR; keep replies conversational and responsive.

Speech:
- Mirror the caller's language when possible.
- Never repeat the opening identity unless asked who is speaking.
- Do not ask for OTPs, bank details, payment details, government IDs, or other sensitive personal data unless the workflow explicitly requires it and the caller has consented.
- Do not promise messages, links, callbacks, or third-party actions unless the workflow provides that capability.
- Close only after a clear caller close intent."""
POST_OPENING_STATE_INSTRUCTIONS = """\
OPENING STATE:
- The assistant has already spoken this opening greeting exactly once: {opening}
- Do not repeat or paraphrase that greeting or the agent identity unless the caller
  asks who this is.
- If the caller only says hello, hi, or another short greeting, continue
  directly with a brief helpful prompt instead of greeting again."""
EXACT_SAY_RE = re.compile(r"say exactly(?:\s+in\s+[^:]+)?:\s*(.+)", re.IGNORECASE)
# Tenant-neutral defaults. A deployment that wants a different closing line,
# realtime voice, or language can override these via env without code changes.
DEFAULT_END_CALL_TEXT = os.getenv(
    "LIVEKIT_DEFAULT_END_CALL_TEXT", "Thank you, ending the call now."
)
DEFAULT_REALTIME_VOICE = os.getenv("LIVEKIT_DEFAULT_REALTIME_VOICE", "Kore")
DEFAULT_REALTIME_LANGUAGE = os.getenv("LIVEKIT_DEFAULT_REALTIME_LANGUAGE", "en-US")
_GEMINI_TTS_CACHE: dict[tuple[str, str, str, str], bytes] = {}
_OPENING_AUDIO_INFLIGHT: dict[Path, asyncio.Task[Path]] = {}
KNOWLEDGE_BASE_GROUNDING_INSTRUCTIONS = """\
KNOWLEDGE BASE GROUNDING:
- This node has knowledge base documents attached. Use retrieve_from_knowledge_base before answering questions about those documents, policies, procedures, or agent/business details.
- Answer only from retrieved chunks and the node instructions. If the retrieved chunks do not contain the answer, say you do not have that information.
- Do not invent facts, prices, policies, availability, names, or contact details that are not in the retrieved knowledge base content."""


class InvalidCallDurationError(ValueError):
    """The pinned workflow definition cannot safely start a paid AI session."""


def _validate_pinned_call_duration(run_configs: dict[str, Any]) -> int:
    """Validate the worker-facing duration with the same contract as G-45.

    Read-time backward compatibility: pinned published definitions written
    before ``max_call_duration_seconds`` existed may only carry the legacy
    ``max_call_duration`` key; it is normalized to the canonical contract
    (30-3600 whole seconds, fail-closed). Keep the public error generic
    because it is persisted to workflow-run logs. The raw saved value may be
    malformed and must not be echoed into logs.
    """

    canonical = run_configs.get("max_call_duration_seconds")
    if canonical is None:
        canonical = run_configs.get("max_call_duration")
    try:
        return resolve_max_call_duration_seconds(
            {"max_call_duration_seconds": canonical}
        )
    except ValueError:
        raise InvalidCallDurationError(
            "Pinned workflow definition has an invalid maximum call duration. "
            "Set a whole number from 30 to 3600 seconds "
            "(max_call_duration_seconds) and republish the workflow."
        ) from None


async def _cleanup_rejected_startup(ctx: JobContext, reason: str) -> None:
    """Close the connected room/job after a pre-session safety rejection."""

    delete_room = getattr(ctx, "delete_room", None)
    if callable(delete_room):
        try:
            result = delete_room()
            if hasattr(result, "__await__"):
                await result
        except Exception as exc:
            logger.warning(
                "[LiveKit] failed to delete rejected startup room "
                f"error_type={type(exc).__name__}"
            )
    try:
        ctx.shutdown(reason)
    except Exception as exc:
        logger.warning(
            "[LiveKit] failed to shut down rejected startup job "
            f"error_type={type(exc).__name__}"
        )


async def _settle_startup_tasks(tasks: list[asyncio.Task]) -> None:
    """Cancel and consume startup tasks without replacing the startup error."""

    for task in tasks:
        if not task.done():
            task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _is_fast_latency_profile(latency_profile: str | None) -> bool:
    value = str(latency_profile or "").lower()
    return any(marker in value for marker in FAST_LATENCY_PROFILE_MARKERS)


def _latency_response_instructions(latency_profile: str | None) -> str | None:
    if _is_fast_latency_profile(latency_profile):
        return FAST_RESPONSE_INSTRUCTIONS
    return None


def _fast_turn_handling(
    turn_detection: str | None,
    *,
    latency_profile: str | None = None,
) -> dict[str, Any]:
    fast_profile = _is_fast_latency_profile(latency_profile)
    if turn_detection == "realtime_llm":
        return {
            "endpointing": {
                "mode": "fixed",
                "min_delay": (
                    FAST_ENDPOINTING_MIN_DELAY_SECONDS if fast_profile else 0.25
                ),
                "max_delay": (
                    FAST_ENDPOINTING_MAX_DELAY_SECONDS if fast_profile else 0.75
                ),
            },
            "interruption": {
                "enabled": True,
                "mode": "vad",
                "discard_audio_if_uninterruptible": False,
                "min_duration": (
                    FAST_INTERRUPTION_MIN_DURATION_SECONDS if fast_profile else 0.7
                ),
                "min_words": FAST_INTERRUPTION_MIN_WORDS,
                "resume_false_interruption": True,
                "false_interruption_timeout": FALSE_INTERRUPTION_TIMEOUT_SECONDS,
            },
            "preemptive_generation": {"enabled": False},
            "turn_detection": turn_detection,
        }

    options: dict[str, Any] = {
        "endpointing": {
            "mode": "fixed",
            "min_delay": FAST_ENDPOINTING_MIN_DELAY_SECONDS if fast_profile else 0.25,
            "max_delay": FAST_ENDPOINTING_MAX_DELAY_SECONDS if fast_profile else 0.75,
        },
        "interruption": {
            "enabled": True,
            "mode": "vad",
            "discard_audio_if_uninterruptible": False,
            "min_duration": (
                FAST_INTERRUPTION_MIN_DURATION_SECONDS if fast_profile else 0.7
            ),
            "min_words": FAST_INTERRUPTION_MIN_WORDS,
            "resume_false_interruption": True,
            "false_interruption_timeout": FALSE_INTERRUPTION_TIMEOUT_SECONDS,
        },
        "preemptive_generation": {
            "enabled": True,
            "preemptive_tts": False,
            "max_speech_duration": (
                FAST_PREEMPTIVE_MAX_SPEECH_DURATION_SECONDS if fast_profile else 6.0
            ),
            "max_retries": 2,
        },
    }
    if turn_detection is not None:
        options["turn_detection"] = turn_detection
    return options


def _session_latency_options(
    turn_detection: str | None,
    *,
    latency_profile: str | None = None,
) -> dict[str, Any]:
    return {
        "turn_handling": _fast_turn_handling(
            turn_detection,
            latency_profile=latency_profile,
        ),
        "min_consecutive_speech_delay": 0.0,
        "aec_warmup_duration": 0.0,
        "user_away_timeout": None,
        "session_close_transcript_timeout": 0.2,
    }


def _local_vad_realtime_input_config() -> genai_types.RealtimeInputConfig:
    return genai_types.RealtimeInputConfig(
        automatic_activity_detection=genai_types.AutomaticActivityDetection(
            disabled=True
        )
    )


def _fast_server_vad_realtime_input_config(
    latency_profile: str | None = None,
) -> genai_types.RealtimeInputConfig:
    fast_profile = _is_fast_latency_profile(latency_profile)
    return genai_types.RealtimeInputConfig(
        automatic_activity_detection=genai_types.AutomaticActivityDetection(
            disabled=False,
            start_of_speech_sensitivity=genai_types.StartSensitivity.START_SENSITIVITY_LOW,
            end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
            prefix_padding_ms=(
                FAST_GEMINI_SERVER_VAD_PREFIX_PADDING_MS
                if fast_profile
                else GEMINI_SERVER_VAD_PREFIX_PADDING_MS
            ),
            silence_duration_ms=(
                FAST_GEMINI_SERVER_VAD_SILENCE_DURATION_MS
                if fast_profile
                else GEMINI_SERVER_VAD_SILENCE_DURATION_MS
            ),
        ),
        # VOICE-01: Gemini server-side VAD is the only barge-in mechanism on
        # this path (the SDK disables local-VAD interruption when
        # turn_detection="realtime_llm", and the google plugin's interrupt()
        # is a deliberate no-op under NO_INTERRUPTION). NO_INTERRUPTION told
        # Gemini to finish every response regardless of caller speech, which
        # is exactly the "AI keeps talking until its sentence ends" defect.
        # START_OF_ACTIVITY_INTERRUPTS is the provider's supported semantics:
        # Gemini emits `interrupted`, the plugin raises input_speech_started,
        # and the SDK cancels the current speech and flushes queued audio.
        # False-trigger protection stays with START_SENSITIVITY_LOW and
        # prefix_padding_ms (minimum speech before start-of-speech commits).
        activity_handling=genai_types.ActivityHandling.START_OF_ACTIVITY_INTERRUPTS,
        turn_coverage=genai_types.TurnCoverage.TURN_INCLUDES_ONLY_ACTIVITY,
    )


def _extract_exact_say_text(prompt: str | None) -> str | None:
    for line in (prompt or "").splitlines():
        match = EXACT_SAY_RE.search(line.strip())
        if not match:
            continue
        text = match.group(1).strip().strip("\"'")
        if text:
            return text
    return None


def _gemini_tts_cache_key(
    *, api_key: str, voice: str, language: str, text: str
) -> tuple[str, str, str, str]:
    return (api_key[-8:], voice, language, text)


def _extract_gemini_audio(response: Any) -> bytes | None:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline_data = getattr(part, "inline_data", None)
            data = getattr(inline_data, "data", None)
            if data:
                return bytes(data)
    return None


def _synthesize_gemini_tts_pcm(
    *,
    api_key: str,
    voice: str,
    language: str,
    text: str,
) -> bytes:
    attempts = [text]
    if text != DEFAULT_OPENING:
        attempts.append(DEFAULT_OPENING)

    client = genai.Client(api_key=api_key)
    last_error: Exception | None = None
    for attempt in attempts:
        cache_key = _gemini_tts_cache_key(
            api_key=api_key, voice=voice, language=language, text=attempt
        )
        cached = _GEMINI_TTS_CACHE.get(cache_key)
        if cached:
            return cached

        try:
            response = client.models.generate_content(
                model=GEMINI_TTS_MODEL,
                contents=attempt,
                config=genai_types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=genai_types.SpeechConfig(
                        language_code=language,
                        voice_config=genai_types.VoiceConfig(
                            prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        ),
                    ),
                ),
            )
            audio = _extract_gemini_audio(response)
            if audio:
                _GEMINI_TTS_CACHE[cache_key] = audio
                return audio
            logger.warning(
                f"[LiveKit] Gemini TTS returned no audio for {attempt!r}; "
                "trying fallback"
            )
        except Exception as exc:
            last_error = exc
            logger.warning(f"[LiveKit] Gemini TTS failed: {_safe_finalize_error(exc)}")

    if last_error:
        raise last_error
    raise RuntimeError("Gemini TTS returned no audio")


async def _gemini_tts_audio_frames(
    *,
    api_key: str,
    voice: str,
    language: str,
    text: str,
):
    audio = await asyncio.to_thread(
        _synthesize_gemini_tts_pcm,
        api_key=api_key,
        voice=voice,
        language=language,
        text=text,
    )
    bytes_per_sample = 2 * GEMINI_TTS_CHANNELS
    frame_size = int(
        GEMINI_TTS_SAMPLE_RATE * (GEMINI_TTS_FRAME_MS / 1000) * bytes_per_sample
    )
    for offset in range(0, len(audio), frame_size):
        chunk = audio[offset : offset + frame_size]
        if len(chunk) < bytes_per_sample:
            continue
        if len(chunk) % bytes_per_sample:
            chunk = chunk[: -(len(chunk) % bytes_per_sample)]
        yield rtc.AudioFrame(
            chunk,
            sample_rate=GEMINI_TTS_SAMPLE_RATE,
            num_channels=GEMINI_TTS_CHANNELS,
            samples_per_channel=len(chunk) // bytes_per_sample,
        )


def _opening_audio_cache_path(
    *,
    text: str,
    model: str | None,
    voice: str | None,
    language: str | None,
) -> Path:
    cache_input = json.dumps(
        {
            "text": text,
            "model": model or "",
            "voice": voice or "",
            "language": language or "",
            "format": OPENING_AUDIO_CACHE_FORMAT,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(cache_input.encode("utf-8")).hexdigest()[:24]
    return OPENING_AUDIO_CACHE_DIR / f"{digest}.wav"


def _silence_pcm(duration_ms: int, *, sample_rate: int) -> bytes:
    if duration_ms <= 0:
        return b""
    samples = int(sample_rate * (duration_ms / 1000))
    return b"\x00\x00" * samples


def _write_pcm_wav(
    path: Path,
    pcm: bytes,
    *,
    sample_rate: int = 24000,
    leading_silence_ms: int = 0,
    trailing_silence_ms: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp.wav")
    padded_pcm = (
        _silence_pcm(leading_silence_ms, sample_rate=sample_rate)
        + pcm
        + _silence_pcm(trailing_silence_ms, sample_rate=sample_rate)
    )
    with wave.open(str(tmp_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(padded_pcm)
    tmp_path.replace(path)


async def _generate_live_opening_audio(
    *,
    api_key: str,
    model: str,
    voice: str,
    language: str,
    text: str,
    output_path: Path,
    leading_silence_ms: int = OPENING_AUDIO_LEADING_SILENCE_MS,
    trailing_silence_ms: int = OPENING_AUDIO_TRAILING_SILENCE_MS,
    max_duration_seconds: float = OPENING_AUDIO_MAX_DURATION_SECONDS,
) -> Path:
    client = genai.Client(api_key=api_key)
    audio = bytearray()
    config = genai_types.LiveConnectConfig(
        response_modalities=[genai_types.Modality.AUDIO],
        speech_config=genai_types.SpeechConfig(
            language_code=language,
            voice_config=genai_types.VoiceConfig(
                prebuilt_voice_config=genai_types.PrebuiltVoiceConfig(voice_name=voice)
            ),
        ),
        temperature=0.0,
        system_instruction=(
            "You are a TTS engine. Speak only the exact quoted text. "
            "No explanation. Stop after the text."
        ),
    )

    async def _run() -> None:
        async with client.aio.live.connect(model=model, config=config) as session:
            await session.send_client_content(
                turns=genai_types.Content(
                    role="user",
                    parts=[genai_types.Part(text=f'"{text}"')],
                ),
                turn_complete=True,
            )
            async for message in session.receive():
                server_content = getattr(message, "server_content", None)
                if server_content is None:
                    continue
                model_turn = getattr(server_content, "model_turn", None)
                for part in getattr(model_turn, "parts", None) or []:
                    inline_data = getattr(part, "inline_data", None)
                    data = getattr(inline_data, "data", None)
                    if data:
                        audio.extend(bytes(data))
                if getattr(server_content, "turn_complete", False):
                    break

    await asyncio.wait_for(_run(), timeout=OPENING_AUDIO_GENERATION_TIMEOUT_SECONDS)
    duration_seconds = len(audio) / (24000 * 2)
    if not audio or duration_seconds > max_duration_seconds:
        raise RuntimeError(
            f"Generated opening audio was empty or too long ({duration_seconds:.2f}s)"
        )
    _write_pcm_wav(
        output_path,
        bytes(audio),
        sample_rate=24000,
        leading_silence_ms=leading_silence_ms,
        trailing_silence_ms=trailing_silence_ms,
    )
    return output_path


async def _wav_audio_frames(path: Path):
    with wave.open(str(path), "rb") as wav:
        sample_width = wav.getsampwidth()
        if sample_width != 2:
            raise ValueError(f"Unsupported WAV sample width: {sample_width}")

        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        frames_per_chunk = max(1, int(sample_rate * (WAV_FRAME_MS / 1000)))
        bytes_per_sample = sample_width * channels

        while True:
            chunk = wav.readframes(frames_per_chunk)
            if not chunk:
                break
            yield rtc.AudioFrame(
                chunk,
                sample_rate=sample_rate,
                num_channels=channels,
                samples_per_channel=len(chunk) // bytes_per_sample,
            )


async def _live_opening_audio_path(
    *,
    api_key: str | None,
    model: str | None,
    voice: str | None,
    language: str | None,
    text: str,
) -> Path | None:
    if not (api_key and model and voice and language and text):
        return None

    path = _opening_audio_cache_path(
        text=text,
        model=model,
        voice=voice,
        language=language,
    )
    if path.exists():
        return path

    task = _OPENING_AUDIO_INFLIGHT.get(path)
    if task is None or task.done():
        logger.info(
            "[LiveKit] generating cached opening audio "
            f"model={model!r} voice={voice!r} language={language!r}"
        )
        task = asyncio.create_task(
            _generate_live_opening_audio(
                api_key=api_key,
                model=model,
                voice=voice,
                language=language,
                text=text,
                output_path=path,
            )
        )
        _OPENING_AUDIO_INFLIGHT[path] = task

    try:
        return await asyncio.shield(task)
    finally:
        if task.done() and _OPENING_AUDIO_INFLIGHT.get(path) is task:
            _OPENING_AUDIO_INFLIGHT.pop(path, None)


def _metadata_from_job(ctx: JobContext) -> dict[str, Any]:
    return _metadata_from_json(ctx.job.metadata, "job")


def _metadata_from_room(ctx: JobContext) -> dict[str, Any]:
    return _metadata_from_json(getattr(ctx.room, "metadata", ""), "room")


def _metadata_from_json(value: str | None, source: str) -> dict[str, Any]:
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        logger.warning(f"Invalid LiveKit {source} metadata: {value!r}")
        return {}


def _participant_context(participant) -> dict[str, Any]:
    attrs = dict(getattr(participant, "attributes", {}) or {})
    metadata = getattr(participant, "metadata", "") or ""
    return {
        "participant_identity": getattr(participant, "identity", ""),
        "participant_name": getattr(participant, "name", ""),
        "participant_kind": str(getattr(participant, "kind", "")),
        "participant_metadata": metadata,
        "participant_attributes": attrs,
        "caller_number": attrs.get("sip.phoneNumber")
        or attrs.get("sip.from")
        or attrs.get("livekit.sip.phoneNumber"),
        "called_number": attrs.get("sip.trunkPhoneNumber")
        or attrs.get("sip.to")
        or attrs.get("livekit.sip.trunkPhoneNumber"),
        "call_id": attrs.get("sip.callID") or attrs.get("livekit.sip.callID"),
    }


def _safe_error_text(exc: Exception, *, limit: int = 1000) -> str:
    """Category-only failure text; exception messages may contain secrets.

    Provider and LiveKit client exceptions (Gemini, OpenAI, SIP trunking,
    etc.) may embed API keys, bearer tokens, or credential-bearing URLs in
    their string representation -- e.g. Google's Generative AI key is
    commonly passed as a URL query parameter, and some HTTP clients include
    the full request URL in a raised exception's str(). Never include
    str(exc) here. Delegates to _safe_finalize_error so there is a single
    source of truth for what "safe" means in this module. `limit` is kept
    for call-site compatibility; unused now that output is a short fixed
    format.
    """
    return _safe_finalize_error(exc)


async def _append_livekit_run_event(
    workflow_run_id: int,
    event: dict[str, Any],
) -> None:
    """Persist a compact LiveKit runtime event on the workflow run logs."""
    try:
        workflow_run = await db_client.get_workflow_run_by_id(workflow_run_id)
        current_logs = workflow_run.logs if workflow_run else {}
        current_events = (current_logs or {}).get("livekit_events")
        if not isinstance(current_events, list):
            current_events = []
        await db_client.update_workflow_run(
            workflow_run_id,
            logs={
                "livekit_events": [
                    *current_events,
                    {
                        "timestamp": time.time(),
                        **event,
                    },
                ]
            },
        )
    except Exception as log_exc:
        logger.debug(f"[LiveKit] failed to persist runtime event: {log_exc}")


def _render_node_prompt(prompt: str | None, call_context_vars: dict[str, Any]) -> str:
    return render_template(prompt or "", call_context_vars)


def _compose_system_prompt(
    *,
    node: Node,
    workflow: WorkflowGraph,
    call_context_vars: dict[str, Any],
    has_recordings: bool = False,
    latency_profile: str | None = None,
) -> str:
    system_prompt = compose_system_prompt_for_node(
        node=node,
        workflow=workflow,
        format_prompt=lambda prompt: _render_node_prompt(prompt, call_context_vars),
        has_recordings=has_recordings,
    )
    latency_instructions = _latency_response_instructions(latency_profile)
    if latency_instructions:
        system_prompt = "\n\n".join(
            part for part in (latency_instructions, system_prompt) if part
        )
    if node.document_uuids:
        system_prompt = "\n\n".join(
            part
            for part in (system_prompt, KNOWLEDGE_BASE_GROUNDING_INSTRUCTIONS)
            if part
        )
    return system_prompt


def _tool_schema(name: str, description: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": description or name,
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    }


def _knowledge_base_tool_schema(document_uuids: list[str]) -> dict[str, Any]:
    tool_def = get_knowledge_base_tool(document_uuids)["function"]
    return {
        "name": tool_def["name"],
        "description": tool_def["description"],
        "parameters": tool_def["parameters"],
    }


def _provider_value(provider: Any) -> str | None:
    if provider is None:
        return None
    return provider.value if hasattr(provider, "value") else str(provider)


def _google_thinking_config(model: str) -> dict[str, Any] | None:
    model_name = (model or "").lower()
    # gemini-3.8-live has interleaved reasoning built in and rejects an explicit
    # thinking_level/thinking_config (Google migration guidance: omit it). The
    # "-extended-thinking" variant still accepts configurable thinking and keeps
    # the gemini-3 default below.
    if model_name.startswith("gemini-3.8-live") and "extended-thinking" not in model_name:
        return None
    if "gemini-2.5" in model_name or model_name.startswith("gemini-2.5"):
        return {"thinking_budget": 0}
    if "gemini-3" in model_name or model_name.startswith("gemini-3"):
        return {"thinking_level": "minimal"}
    return None


# Realtime (speech-to-speech) Google models that do NOT support the
# generate_reply + local-VAD turn handling and must fall back to server-side
# VAD. Matched as a prefix so preview/version suffixes are tolerated. This
# replaces a brittle ``"3.1" in model_name`` substring check that would also
# misclassify unrelated names (e.g. a future ``gemini-3.10``). Add a model here
# only when it genuinely lacks generate_reply support.
_REALTIME_GENERATE_REPLY_UNSUPPORTED_PREFIXES = ("gemini-3.1-flash-live",)


def _supports_realtime_generate_reply(provider: str | None, model: str | None) -> bool:
    if provider not in (
        ServiceProviders.GOOGLE_REALTIME.value,
        ServiceProviders.GOOGLE_VERTEX_REALTIME.value,
    ):
        return True
    model_name = (model or "").lower()
    return not any(
        model_name.startswith(prefix)
        for prefix in _REALTIME_GENERATE_REPLY_UNSUPPORTED_PREFIXES
    )


def _supports_realtime_tool_choice(provider: str | None) -> bool:
    return provider == ServiceProviders.OPENAI_REALTIME.value


def _log_emergency_cleanup(task: asyncio.Task) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        logger.warning("[LiveKit] emergency shutdown cleanup was cancelled")
    except Exception as exc:
        logger.warning(f"[LiveKit] emergency shutdown cleanup failed: {_safe_finalize_error(exc)}")


CALLER_DISCONNECT_SHUTDOWN_DELAY_SECONDS = 3.0


def _is_caller_participant(participant) -> bool:
    """True only for participants that represent callers (standard/SIP).

    Agent, assistant, ingress and egress (recording) participants must never
    be mistaken for a caller, otherwise infrastructure churn could tear down a
    live call.
    """
    kind = str(getattr(participant, "kind", "") or "").lower()
    return "standard" in kind or "sip" in kind


def _active_caller_participants(
    room, *, exclude_identity: str | None = None
) -> list[Any]:
    callers: list[Any] = []
    for participant in getattr(room, "remote_participants", {}).values():
        identity = str(getattr(participant, "identity", "") or "")
        if exclude_identity and identity == exclude_identity:
            continue
        if _is_caller_participant(participant):
            callers.append(participant)
    return callers


async def _caller_disconnect_cleanup(agent, room) -> None:
    """Bounded, reconnection-aware cleanup after the last caller leaves.

    A short delay gives a rejoining caller a chance to reconnect before the
    call is torn down. Silence is never routed here: only authoritative
    participant-removal events trigger this path.
    """
    try:
        await asyncio.sleep(CALLER_DISCONNECT_SHUTDOWN_DELAY_SECONDS)
        if _active_caller_participants(room):
            return
        # Authoritative end of the caller session: bounds caller-connected
        # duration (a rejoining caller cancels cleanup before this point).
        setattr(agent, "_caller_disconnected_monotonic", time.monotonic())
        agent._schedule_shutdown("caller_disconnected", delay=0.0)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning(
            f"[LiveKit] caller-disconnect cleanup failed: {_safe_finalize_error(exc)}"
        )
    finally:
        setattr(agent, "_caller_disconnect_cleanup_pending", False)


def _register_caller_disconnect_handler(agent, room) -> None:
    """Schedule bounded cleanup when the last caller disconnects.

    Guards: non-caller participants are ignored, remaining callers block
    cleanup, duplicate events cannot start a second cleanup, and an existing
    shutdown task is never superseded by this path.
    """

    def _on_participant_disconnected(participant) -> None:
        if not _is_caller_participant(participant):
            return
        identity = str(getattr(participant, "identity", "") or "")
        if _active_caller_participants(room, exclude_identity=identity):
            return
        if getattr(agent, "_caller_disconnect_cleanup_pending", False):
            return
        if agent._shutdown_task and not agent._shutdown_task.done():
            return
        setattr(agent, "_caller_disconnect_cleanup_pending", True)
        asyncio.create_task(_caller_disconnect_cleanup(agent, room))

    if callable(getattr(room, "on", None)):
        room.on("participant_disconnected", _on_participant_disconnected)


class LiveKitWorkflowAgent(Agent):
    def __init__(
        self,
        *,
        ctx: JobContext,
        workflow: WorkflowGraph,
        workflow_run_id: int,
        organization_id: int,
        call_context_vars: dict[str, Any],
        embeddings_api_key: str | None = None,
        embeddings_provider: str | None = None,
        embeddings_model: str | None = None,
        embeddings_base_url: str | None = None,
        has_recordings: bool = False,
        uses_realtime: bool = False,
        realtime_generate_reply_supported: bool = True,
        realtime_tool_choice_supported: bool = True,
        realtime_exact_speech_uses_tts: bool = False,
        tts_api_key: str | None = None,
        opening_model: str | None = None,
        tts_voice: str = DEFAULT_REALTIME_VOICE,  # Gemini voice id, not assistant name.
        tts_language: str = DEFAULT_REALTIME_LANGUAGE,
        latency_profile: str | None = None,
        silence_config: dict[str, Any] | None = None,
        interruption_config: dict[str, Any] | None = None,
        max_duration_seconds: int | None = None,
    ) -> None:
        self._ctx = ctx
        self._workflow = workflow
        self._workflow_run_id = workflow_run_id
        self._organization_id = organization_id
        self._call_context_vars = call_context_vars
        self._embeddings_api_key = embeddings_api_key
        self._embeddings_provider = embeddings_provider
        self._embeddings_model = embeddings_model
        self._embeddings_base_url = embeddings_base_url
        self._has_recordings = has_recordings
        self._uses_realtime = uses_realtime
        self._realtime_generate_reply_supported = realtime_generate_reply_supported
        self._realtime_tool_choice_supported = realtime_tool_choice_supported
        self._realtime_exact_speech_uses_tts = realtime_exact_speech_uses_tts
        self._tts_api_key = tts_api_key
        self._opening_model = opening_model
        self._tts_voice = tts_voice
        self._tts_language = tts_language
        self._latency_profile = latency_profile
        self._silence_config = silence_config or {}
        self._interruption_config = interruption_config or {}
        self._max_duration_seconds = max_duration_seconds
        self._language_pack = get_language_pack(self._tts_language)
        backchannel_words = set(self._interruption_config.get("custom_backchannel_words") or [])
        if self._language_pack and self._language_pack.backchannel_keywords:
            backchannel_words.update(self._language_pack.backchannel_keywords)

        self._state_machine = ConversationStateMachine(
            run_id=workflow_run_id,
            on_state_changed=self._on_conversation_state_changed,
        )
        self._interruption_filter = InterruptionFilter(
            min_duration_seconds=float(self._interruption_config.get("min_duration_seconds", 0.45)),
            min_words_for_barge_in=int(self._interruption_config.get("min_words", 2)),
            backchannel_filter_enabled=bool(self._interruption_config.get("backchannel_filter_enabled", True)),
            custom_backchannel_words=backchannel_words,
        )
        self._latency_tracker = LatencyTracker(run_id=workflow_run_id)
        # VOICE-01 runtime observability: counts derived from real SDK/provider
        # signals (local VAD user_state, SpeechHandle.interrupted), not from
        # delayed input transcripts.
        self._interruption_events: dict[str, int] = {
            "caller_speech_during_agent_speech": 0,
            "accepted": 0,
            "rejected": 0,
        }
        self._silence_manager = SilenceManager(
            stage1_seconds=float(self._silence_config.get("stage1_seconds", 4.0)),
            stage2_seconds=float(self._silence_config.get("stage2_seconds", 8.0)),
            stage3_seconds=float(self._silence_config.get("stage3_seconds", 14.0)),
            language=self._tts_language,
            custom_prompts=self._silence_config.get("custom_prompts"),
            speak_callback=self._speak_silence_prompt,
            disconnect_callback=self._handle_silence_disconnect,
        )
        self._silence_watchdog_task: asyncio.Task | None = None
        self._current_node: Node | None = None
        self._visited_nodes: list[str] = []
        self._started_at = time.monotonic()
        # Caller-connection timestamps: set by the entrypoint when the caller
        # participant joins. Distinct from self._started_at (worker lifetime)
        # so billing/reporting can separate caller-connected time from
        # room/session time (run #345 class defect).
        self._caller_connected_monotonic: float | None = None
        self._caller_connected_wall: str | None = None
        # Set by the bounded disconnect cleanup when the authoritative last
        # caller leaves; used to bound caller-connected duration.
        self._caller_disconnected_monotonic: float | None = None
        self._feedback_persist_lock = asyncio.Lock()
        self._feedback_buffer: list[dict[str, Any]] = []
        self._feedback_flush_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._shutdown_deadline: float | None = None
        self._shutdown_generation = 0
        self._opening_started = False
        self._opening_active = False
        self._opening_caller_speech_seen = False
        self._opening_cancel_reason: str | None = None
        self._session_closed = False
        # VOICE-01: the initiating reason survives the shutdown race, and the
        # shutdown callback coordinates exactly one tracked finalization.
        self._requested_shutdown_reason: str | None = None
        self._finalize_task: asyncio.Task | None = None
        self._finalize_state: str | None = None
        self._finalize_error: str | None = None
        self._finalized_elapsed_seconds: int | None = None
        self._opening_prewarm_task: asyncio.Task | None = None
        self._user_speech_started_at: float | None = None
        self._last_user_speech_end_at: float | None = None
        self._last_final_transcript_at: float | None = None
        self._recording_state: post_call.LiveKitRecordingState | None = None
        self._recording_stop_lock = asyncio.Lock()
        start_node = workflow.nodes[workflow.start_node_id]
        self._start_opening_text = self._opening_text_for_start_node(start_node)
        initial_session_node = self._initial_session_node(start_node)
        post_opening_text = (
            self._start_opening_text
            if initial_session_node.id != start_node.id
            else None
        )
        super().__init__(
            instructions=self._compose_node_instructions(
                initial_session_node,
                post_opening_text=post_opening_text,
            ),
            tools=self._tools_for_node(initial_session_node),
        )

    def _supports_mid_call_session_updates(self) -> bool:
        return not (self._uses_realtime and not self._realtime_generate_reply_supported)

    def _initial_session_node(self, start_node: Node) -> Node:
        if self._supports_mid_call_session_updates():
            return start_node
        edge = self._auto_advance_edge_after_opening(start_node)
        if edge is None:
            return start_node
        return self._workflow.nodes[edge.target]

    def _compose_node_instructions(
        self,
        node: Node,
        *,
        post_opening_text: str | None = None,
    ) -> str:
        instructions = _compose_system_prompt(
            node=node,
            workflow=self._workflow,
            call_context_vars=self._call_context_vars,
            has_recordings=self._has_recordings,
            latency_profile=self._latency_profile,
        )
        if post_opening_text and not node.is_start and not node.is_end:
            instructions = "\n\n".join(
                [
                    instructions,
                    POST_OPENING_STATE_INSTRUCTIONS.format(
                        opening=json.dumps(post_opening_text, ensure_ascii=False)
                    ),
                ]
            )
        if hasattr(self, "_language_pack") and self._language_pack and self._language_pack.system_prompt_language_rule:
            instructions = f"{instructions}\n\nLanguage & Pronunciation Guidance:\n{self._language_pack.system_prompt_language_rule}"
        return instructions

    async def _publish_feedback(self, message: dict[str, Any]) -> None:
        try:
            payload = json.dumps(message, separators=(",", ":"), default=str)
            await self._ctx.room.local_participant.publish_data(
                payload,
                reliable=True,
                topic=FEEDBACK_TOPIC,
            )
        except Exception as exc:
            logger.debug(f"Failed to publish LiveKit feedback event: {exc}")

    async def flush_feedback(self) -> None:
        messages: list[dict[str, Any]] = []
        try:
            async with self._feedback_persist_lock:
                if not self._feedback_buffer:
                    return
                messages = self._feedback_buffer
                self._feedback_buffer = []
                workflow_run = await db_client.get_workflow_run_by_id(
                    self._workflow_run_id
                )
                current_logs = workflow_run.logs if workflow_run else {}
                current_events = (current_logs or {}).get("realtime_feedback_events")
                if not isinstance(current_events, list):
                    current_events = []
                await db_client.update_workflow_run(
                    self._workflow_run_id,
                    logs={"realtime_feedback_events": [*current_events, *messages]},
                )
        except Exception as exc:
            self._feedback_buffer = [*messages, *self._feedback_buffer]
            logger.debug(f"Failed to persist LiveKit feedback event: {exc}")

    def _queue_feedback_persist(self, message: dict[str, Any]) -> None:
        self._feedback_buffer.append(message)
        if self._feedback_flush_task and not self._feedback_flush_task.done():
            return

        async def _flush_later() -> None:
            try:
                await asyncio.sleep(FEEDBACK_FLUSH_INTERVAL_SECONDS)
                await self.flush_feedback()
            except asyncio.CancelledError:
                raise

        try:
            self._feedback_flush_task = asyncio.create_task(_flush_later())
        except RuntimeError:
            pass

    async def _emit_feedback_now(self, message: dict[str, Any]) -> None:
        await self._publish_feedback(message)
        self._queue_feedback_persist(message)

    def _emit_feedback(self, message: dict[str, Any]) -> None:
        try:
            asyncio.create_task(self._emit_feedback_now(message))
        except RuntimeError:
            pass

    def _tools_for_node(self, node: Node) -> list[llm.Tool]:
        if node.is_end:
            return []

        tools: list[llm.Tool] = []
        if node.document_uuids:
            tools.append(self._make_knowledge_base_tool(node.document_uuids))
        for edge in node.out_edges:
            tools.append(self._make_transition_tool(edge))
        return tools

    def _make_knowledge_base_tool(self, document_uuids: list[str]) -> llm.Tool:
        async def retrieve_kb_tool(
            raw_arguments: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self._silence_manager.set_tool_running(True)
            try:
                query = str((raw_arguments or {}).get("query") or "").strip()
                if not query:
                    return {
                        "error": "query is required",
                        "chunks": [],
                        "query": query,
                        "total_results": 0,
                        "instruction": "Ask the user a brief clarifying question instead of guessing.",
                    }

                result = await retrieve_from_knowledge_base(
                    query=query,
                    organization_id=self._organization_id,
                    document_uuids=document_uuids,
                    limit=3,
                    embeddings_api_key=self._embeddings_api_key,
                    embeddings_provider=self._embeddings_provider,
                    embeddings_model=self._embeddings_model,
                    embeddings_base_url=self._embeddings_base_url,
                )
                if result.get("total_results", 0) == 0:
                    result = {
                        **result,
                        "instruction": (
                            "No matching knowledge base content was found. "
                            "Do not invent an answer; say you do not have that information."
                        ),
                    }
                return result
            finally:
                self._silence_manager.set_tool_running(False)

        return llm.function_tool(
            retrieve_kb_tool,
            raw_schema=_knowledge_base_tool_schema(document_uuids),
        )

    def _make_transition_tool(self, edge: Edge) -> llm.Tool:
        async def transition_tool(
            raw_arguments: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            self._silence_manager.set_tool_running(True)
            try:
                target_node = self._workflow.nodes[edge.target]
                logger.info(
                    "[LiveKit] transition tool called "
                    f"run_id={self._workflow_run_id} "
                    f"edge={edge.label!r} target={edge.target!r} "
                    f"raw_arguments={raw_arguments or {}}"
                )
                if edge.transition_speech:
                    transition_speech = render_template(
                        edge.transition_speech, self._call_context_vars
                    )
                    if transition_speech:
                        speech = await self._speak_text(str(transition_speech))
                        if speech:
                            await speech.wait_for_playout()

                await self.set_node(edge.target)
                active_node = self._current_node or target_node
                result: dict[str, Any] = {
                    "status": "done",
                    "transition": edge.label,
                    "node": active_node.name,
                }
                if not active_node.is_end:
                    result["node_instructions"] = _compose_system_prompt(
                        node=active_node,
                        workflow=self._workflow,
                        call_context_vars=self._call_context_vars,
                        has_recordings=self._has_recordings,
                        latency_profile=self._latency_profile,
                    )
                    result["available_transitions"] = [
                        {
                            "name": next_edge.get_function_name(),
                            "condition": next_edge.condition,
                        }
                        for next_edge in active_node.out_edges
                    ]
                return result
            finally:
                self._silence_manager.set_tool_running(False)

        return llm.function_tool(
            transition_tool,
            raw_schema=_tool_schema(edge.get_function_name(), edge.condition),
        )

    def _auto_advance_edge_after_opening(self, node: Node) -> Edge | None:
        if not node.is_start:
            return None

        non_end_edges = [
            edge
            for edge in node.out_edges
            if not self._workflow.nodes[edge.target].is_end
        ]
        if len(non_end_edges) != 1:
            return None

        edge = non_end_edges[0]
        if edge.transition_speech:
            return None
        return edge

    async def _auto_advance_after_opening(self, node: Node) -> None:
        edge = self._auto_advance_edge_after_opening(node)
        if edge is None:
            return

        logger.info(
            "[LiveKit] auto advancing after opening "
            f"run_id={self._workflow_run_id} "
            f"edge={edge.label!r} target={edge.target!r}"
        )
        await self.set_node(edge.target)

    def _opening_text_for_start_node(self, start_node: Node) -> str | None:
        opening = self._render_greeting(start_node) or _extract_exact_say_text(
            start_node.prompt
        )
        if (
            not opening
            and self._uses_realtime
            and not self._realtime_generate_reply_supported
        ):
            opening = DEFAULT_OPENING
        return opening

    def prewarm_opening_audio(self) -> None:
        if not (self._uses_realtime and self._realtime_exact_speech_uses_tts):
            return
        if self._opening_prewarm_task and not self._opening_prewarm_task.done():
            return

        opening = self._opening_text_for_start_node(
            self._workflow.nodes[self._workflow.start_node_id]
        )
        if not opening:
            return

        self._opening_prewarm_task = asyncio.create_task(
            _live_opening_audio_path(
                api_key=self._tts_api_key,
                model=self._opening_model,
                voice=self._tts_voice,
                language=self._tts_language,
                text=opening,
            )
        )

        def _log_prewarm_result(task: asyncio.Task) -> None:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(f"[LiveKit] opening audio prewarm failed: {_safe_finalize_error(exc)}")

        self._opening_prewarm_task.add_done_callback(_log_prewarm_result)

    async def _speak_text(self, text: str, *, allow_interruptions: bool = True):
        if self._uses_realtime and self._realtime_exact_speech_uses_tts:
            return self._say_text(text, allow_interruptions=allow_interruptions)

        if self._uses_realtime:
            if not self._realtime_generate_reply_supported:
                logger.warning(
                    "[LiveKit] realtime model cannot be manually prompted to speak "
                    "and no realtime TTS fallback is configured"
                )
                return None
            try:
                kwargs: dict[str, Any] = {
                    "instructions": f"Say exactly and only this text: {text}",
                    "allow_interruptions": allow_interruptions,
                }
                if self._realtime_tool_choice_supported:
                    kwargs["tool_choice"] = "none"
                return self.session.generate_reply(**kwargs)
            except RuntimeError as exc:
                logger.warning(
                    f"[LiveKit] realtime exact speech failed, falling back to TTS: {_safe_finalize_error(exc)}"
                )
        return self._say_text(text, allow_interruptions=allow_interruptions)

    async def _speak_opening(self, text: str, *, allow_interruptions: bool = True):
        if self._uses_realtime and self._realtime_exact_speech_uses_tts:
            try:
                opening_audio_path = await _live_opening_audio_path(
                    api_key=self._tts_api_key,
                    model=self._opening_model,
                    voice=self._tts_voice,
                    language=self._tts_language,
                    text=text,
                )
            except Exception as exc:
                opening_audio_path = None
                error_text = _safe_error_text(exc)
                logger.warning(
                    "[LiveKit] live opening audio cache failed; "
                    f"falling back to Gemini TTS: {error_text}"
                )
                await _append_livekit_run_event(
                    self._workflow_run_id,
                    {
                        "type": "opening_audio_cache_failed",
                        "level": "warning",
                        "message": error_text,
                        "fallback": "gemini_tts",
                        "model": self._opening_model,
                        "voice": self._tts_voice,
                        "language": self._tts_language,
                    },
                )

            if opening_audio_path:
                logger.info(
                    "[LiveKit] using cached live opening audio "
                    f"run_id={self._workflow_run_id} path={opening_audio_path.name!r}"
                )
                return self._say_text(
                    text,
                    allow_interruptions=allow_interruptions,
                    audio=_wav_audio_frames(opening_audio_path),
                )

        return await self._speak_text(text, allow_interruptions=allow_interruptions)

    def _schedule_shutdown(self, reason: str, *, delay: float = 0.0) -> None:
        # Record the initiating reason synchronously, before any await or
        # scheduler work, so a concurrent SDK disconnect cannot replace it.
        if self._requested_shutdown_reason is None:
            self._requested_shutdown_reason = reason
        if self._opening_active:
            self._opening_cancel_reason = "shutdown_requested"
        deadline = time.monotonic() + delay
        if (
            self._shutdown_task
            and not self._shutdown_task.done()
            and self._shutdown_deadline is not None
            and self._shutdown_deadline <= deadline
        ):
            return

        if self._shutdown_task and not self._shutdown_task.done():
            self._shutdown_task.cancel()
            # A superseded request is the effective one for the new task.
            self._requested_shutdown_reason = reason

        self._shutdown_generation += 1
        generation = self._shutdown_generation

        async def _shutdown_later() -> None:
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._emit_feedback_now(
                    {
                        "type": "rtf-run-completed",
                        "payload": {"reason": reason},
                        "timestamp": time.time(),
                    }
                )
                await self._shutdown_call(reason, generation=generation)
            except asyncio.CancelledError:
                raise

        try:
            self._shutdown_deadline = deadline
            self._shutdown_task = asyncio.create_task(_shutdown_later())
        except RuntimeError:
            self._requested_shutdown_reason = reason
            self._ctx.shutdown(reason)

    def _on_conversation_state_changed(
        self, prev: ConversationState, new_st: ConversationState, reason: str
    ) -> None:
        self._emit_feedback(
            {
                "type": "rtf-conversation-state",
                "payload": {
                    "state": new_st.value,
                    "previous_state": prev.value,
                    "reason": reason,
                    "turn_id": self._state_machine.turn_id,
                },
                "timestamp": time.time(),
            }
        )

    async def _speak_silence_prompt(self, text: str) -> None:
        self._state_machine.transition_to(
            ConversationState.AGENT_SPEAKING,
            reason="silence_recovery_prompt",
            force=True,
        )
        await self._speak_text(text, allow_interruptions=True)

    async def _handle_silence_disconnect(self, reason: str) -> None:
        self._state_machine.transition_to(
            ConversationState.WRAPUP,
            reason="silence_timeout_disconnect",
            force=True,
        )
        self._schedule_shutdown("no_response_timeout", delay=2.0)

    def start_silence_watchdog(self) -> None:
        if self._silence_watchdog_task is None or self._silence_watchdog_task.done():
            self._silence_watchdog_task = asyncio.create_task(self._silence_watchdog_loop())

    async def _silence_watchdog_loop(self) -> None:
        try:
            while not self._shutdown_task:
                await asyncio.sleep(0.5)
                self._state_machine.check_timeouts()
                await self._silence_manager.evaluate_silence(self._state_machine.state)
                if self._max_duration_seconds:
                    elapsed = time.monotonic() - self._started_at
                    if elapsed >= self._max_duration_seconds and not self._shutdown_task:
                        logger.info(
                            f"[LiveKit] maximum call duration reached ({elapsed:.1f}s >= {self._max_duration_seconds}s)"
                        )
                        # VOICE-01: this previously called a non-existent
                        # ``schedule_shutdown`` and raised AttributeError, so the
                        # maximum-duration limit was never enforced at runtime.
                        self._schedule_shutdown("max_duration_reached")
                        break
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error(f"[LiveKit] silence watchdog error: {exc}")

    def set_recording_state(
        self, recording_state: post_call.LiveKitRecordingState | None
    ) -> None:
        self._recording_state = recording_state

    async def stop_recording(self) -> post_call.LiveKitRecordingState | None:
        async with self._recording_stop_lock:
            if self._recording_state is None:
                return None
            self._recording_state = await post_call.stop_livekit_room_recording(
                self._recording_state
            )
            return self._recording_state

    async def _shutdown_call(
        self, reason: str, *, generation: int | None = None
    ) -> None:
        if self._requested_shutdown_reason is None:
            self._requested_shutdown_reason = reason

        async def auxiliary_cleanup():
            if self._opening_active:
                self._opening_cancel_reason = "shutdown_requested"
            if self._silence_watchdog_task and not self._silence_watchdog_task.done():
                self._silence_watchdog_task.cancel()
            await self.flush_feedback()
            state = await self.stop_recording()
            if state is not None:
                await db_client.update_workflow_run(
                    self._workflow_run_id,
                    recording_url=post_call.recording_available_url(state),
                    gathered_context={"livekit_recording": state.to_log()},
                )

        try:
            await asyncio.wait_for(
                auxiliary_cleanup(), timeout=_SHUTDOWN_AUX_TIMEOUT_SECONDS
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[LiveKit] auxiliary cleanup failed: {_safe_finalize_error(exc)}")
        finally:
            # Check supersession now, after any cancellation, rather than
            # caching it before the asynchronous cleanup.
            superseded = generation is not None and generation < self._shutdown_generation
            if not superseded:
                cleanup = getattr(self, "_room_cleanup_task", None)
                if cleanup is None:
                    cleanup = asyncio.create_task(
                        self._emergency_shutdown_cleanup(
                            self._requested_shutdown_reason or reason
                        )
                    )
                    self._room_cleanup_task = cleanup
                    cleanup.add_done_callback(_log_emergency_cleanup)
                # Cancellation of this waiter cannot cancel bounded deletion.
                await asyncio.shield(cleanup)

    async def _emergency_shutdown_cleanup(self, reason: str) -> None:
        """Best-effort room cleanup + SDK shutdown after task cancellation."""
        try:
            delete_room = getattr(self._ctx, "delete_room", None)
            if callable(delete_room):
                result = delete_room()
                if hasattr(result, "__await__"):
                    await asyncio.wait_for(
                        result, timeout=_ROOM_DELETE_TIMEOUT_SECONDS
                    )
        except asyncio.TimeoutError:
            logger.warning("[LiveKit] emergency room deletion timed out")
        except Exception as exc:
            logger.warning(f"[LiveKit] emergency room deletion failed: {_safe_finalize_error(exc)}")
        finally:
            self._ctx.shutdown(reason)

    def _say_text(
        self,
        text: str,
        *,
        allow_interruptions: bool = True,
        audio=None,
    ):
        kwargs: dict[str, Any] = {
            "allow_interruptions": allow_interruptions,
            "add_to_chat_ctx": True,
        }
        if audio is not None:
            kwargs["audio"] = audio
        elif self._tts_api_key:
            kwargs["audio"] = _gemini_tts_audio_frames(
                api_key=self._tts_api_key,
                voice=self._tts_voice,
                language=self._tts_language,
                text=text,
            )
        try:
            return self.session.say(text, **kwargs)
        except RuntimeError as exc:
            logger.warning(f"[LiveKit] cannot speak text without TTS: {exc}")
            return None

    async def _record_node_transition(
        self, node: Node, previous_node: Node | None
    ) -> None:
        feedback = {
            "type": "rtf-node-transition",
            "payload": {
                "node_id": node.id,
                "node_name": node.name,
                "previous_node_id": previous_node.id if previous_node else None,
                "previous_node_name": previous_node.name if previous_node else None,
                "allow_interrupt": node.allow_interrupt,
            },
            "timestamp": time.time(),
        }
        await self._emit_feedback_now(feedback)
        await db_client.update_workflow_run(
            self._workflow_run_id,
            gathered_context={"nodes_visited": self._visited_nodes},
        )

    async def set_node(self, node_id: str) -> None:
        node = self._workflow.nodes[node_id]
        previous_node = self._current_node
        self._current_node = node
        if node.name not in self._visited_nodes:
            self._visited_nodes.append(node.name)

        if self._supports_mid_call_session_updates():
            post_opening_text = (
                self._start_opening_text
                if previous_node is not None
                and previous_node.is_start
                and not node.is_start
                else None
            )
            await self.update_instructions(
                self._compose_node_instructions(
                    node,
                    post_opening_text=post_opening_text,
                )
            )
            await self.update_tools(self._tools_for_node(node))
        else:
            logger.info(
                "[LiveKit] skipping realtime mid-call session update "
                f"run_id={self._workflow_run_id} node={node.id!r} "
                "because this realtime model would restart the audio session"
            )

        await self._record_node_transition(node, previous_node)

        if node.is_end:
            await self._complete_end_node(node)

    def _emit_opening_event(self, phase: str, **details: Any) -> None:
        payload = {
            "phase": phase,
            "node_id": self._workflow.start_node_id,
            "turn_id": self._state_machine.turn_id,
            **details,
        }
        logger.info(
            "[LiveKit] opening "
            f"run_id={self._workflow_run_id} phase={phase} "
            f"node={self._workflow.start_node_id!r}"
        )
        message = {"type": "rtf-opening", "payload": payload, "timestamp": time.time()}
        # Queue synchronously so an immediate shutdown flush includes the
        # terminal opening event. Publishing remains best-effort and async.
        queue = getattr(self, "_queue_feedback_persist", None)
        if callable(queue):
            queue(message)
            asyncio.create_task(self._publish_feedback(message))
        else:
            self._emit_feedback(message)

    async def start_opening(self) -> None:
        if self._opening_started:
            return
        self._opening_started = True
        self._opening_active = True
        start_node = self._workflow.nodes[self._workflow.start_node_id]
        previous_node = self._current_node
        self._current_node = start_node
        if start_node.name not in self._visited_nodes:
            self._visited_nodes.append(start_node.name)

        opening = self._start_opening_text
        self._emit_opening_event("opening_started", mode="explicit" if opening else "generated")
        prior_state = self._state_machine.state
        self._state_machine.transition_to(
            ConversationState.AGENT_SPEAKING,
            reason="opening_greeting",
            force=True,
        )
        if prior_state != ConversationState.AGENT_SPEAKING:
            self._emit_opening_event(
                "opening_state_transition",
                previous_state=prior_state.value,
                state=ConversationState.AGENT_SPEAKING.value,
            )
        terminal_reported = False
        try:
            if opening:
                speech = await self._speak_opening(
                    opening,
                    allow_interruptions=start_node.allow_interrupt,
                )
            else:
                speech = self.session.generate_reply(
                    instructions=(
                        "Start the conversation now. Follow the current node instructions, "
                        "use the available tools when needed, and do not invent facts."
                    ),
                    allow_interruptions=start_node.allow_interrupt,
                )
            record_task = asyncio.create_task(
                self._record_node_transition(start_node, previous_node)
            )
            if opening:
                await record_task
            if speech:
                await speech.wait_for_playout()
            if not opening:
                await record_task

            cancel_reason = self._opening_cancel_reason
            if self._shutdown_task is not None:
                cancel_reason = cancel_reason or "shutdown_requested"
            room = getattr(self._ctx, "room", None)
            isconnected = getattr(room, "isconnected", None)
            if callable(isconnected) and not isconnected():
                cancel_reason = cancel_reason or "session_disconnected"
            if speech is None:
                cancel_reason = cancel_reason or "opening_unavailable"

            if cancel_reason:
                self._emit_opening_event("opening_cancelled", reason=cancel_reason)
            elif getattr(speech, "interrupted", False):
                if self._opening_caller_speech_seen:
                    self._emit_opening_event("opening_interrupted", reason="caller_speech")
                else:
                    self._emit_opening_event(
                        "opening_cancelled", reason="unattributed_interruption"
                    )
            else:
                self._emit_opening_event("opening_completed")
            terminal_reported = True

            if cancel_reason and cancel_reason != "opening_unavailable":
                return

            # SDK state events own active caller/agent turns. Only fill in a
            # missing idle transition after a genuinely completed opening.
            try:
                session = self.session
            except RuntimeError:
                session = None  # offline agent tests have no running session
            if (
                speech is not None
                and not getattr(speech, "interrupted", False)
                and self._state_machine.state == ConversationState.AGENT_SPEAKING
                and getattr(session, "user_state", None) == "listening"
                and getattr(session, "agent_state", None) == "listening"
            ):
                prior_state = self._state_machine.state
                self._state_machine.transition_to(
                    ConversationState.LISTENING, reason="opening_greeting_ended"
                )
                self._silence_manager.reset_silence(reason="opening_greeting_ended")
                self._emit_opening_event(
                    "opening_state_transition",
                    previous_state=prior_state.value,
                    state=ConversationState.LISTENING.value,
                )
            if (
                self._opening_cancel_reason is not None
                or self._shutdown_task is not None
                or self._session_closed
            ):
                return
            if opening:
                await self._auto_advance_after_opening(start_node)
        except asyncio.CancelledError:
            if not terminal_reported:
                self._emit_opening_event("opening_cancelled", reason="task_cancelled")
            raise
        except Exception:
            if not terminal_reported:
                self._emit_opening_event("opening_cancelled", reason="opening_failed")
            raise
        finally:
            self._opening_active = False

    def _render_greeting(self, node: Node) -> str | None:
        if node.greeting_type == "audio" and node.greeting_recording_id:
            logger.warning(
                "LiveKit runtime does not yet play pre-recorded greetings; "
                "falling back to generated opening."
            )
            return None
        if node.greeting:
            return str(render_template(node.greeting, self._call_context_vars))
        return None

    async def _complete_end_node(self, node: Node) -> None:
        exact_text = _extract_exact_say_text(node.prompt)
        if (
            not exact_text
            and self._uses_realtime
            and not self._realtime_generate_reply_supported
        ):
            exact_text = DEFAULT_END_CALL_TEXT
        if exact_text:
            speech = await self._speak_text(
                exact_text,
                allow_interruptions=node.allow_interrupt,
            )
        else:
            kwargs: dict[str, Any] = {
                "instructions": (
                    "Close the conversation according to the current end node "
                    "instructions, then stop speaking."
                ),
                "allow_interruptions": node.allow_interrupt,
            }
            if self._realtime_tool_choice_supported:
                kwargs["tool_choice"] = "none"
            speech = self.session.generate_reply(**kwargs)
        if speech:
            await speech.wait_for_playout()
        await self._emit_feedback_now(
            {
                "type": "rtf-run-completed",
                "payload": {"reason": "workflow_end", "node_id": node.id},
                "timestamp": time.time(),
            }
        )
        await self._shutdown_call("workflow reached end node")


def _first_content_text(item) -> str:
    content = getattr(item, "content", None) or []
    parts: list[str] = []
    for part in content:
        if isinstance(part, str):
            parts.append(part)
        elif hasattr(part, "text"):
            parts.append(str(part.text))
    return " ".join(parts).strip()


def _message_metrics_payload(item) -> dict[str, float]:
    metrics = getattr(item, "metrics", None) or {}
    if not isinstance(metrics, dict):
        metrics = dict(metrics)
    metric_keys = {
        "transcription_delay",
        "end_of_turn_delay",
        "on_user_turn_completed_delay",
        "llm_node_ttft",
        "tts_node_ttfb",
        "playback_latency",
        "e2e_latency",
    }
    return {
        key: round(float(value), 4)
        for key, value in metrics.items()
        if isinstance(value, (int, float))
        and (key.endswith("_latency") or key in metric_keys)
    }


def _uses_realtime(user_config) -> bool:
    """Single source of truth for whether the worker runs in realtime mode.

    Mirrors ``_create_session``: realtime requires both the ``is_realtime`` flag
    and a populated ``realtime`` section. Using only ``is_realtime`` elsewhere
    produced a split-brain agent (realtime code paths over a pipeline session).
    """

    return bool(user_config.is_realtime and user_config.realtime is not None)


def _runtime_configuration_from_user_config(user_config) -> dict[str, str]:
    runtime_configuration: dict[str, str] = {
        "mode": "realtime" if _uses_realtime(user_config) else "pipeline",
    }
    if user_config.is_realtime and user_config.realtime:
        runtime_configuration["realtime_provider"] = _provider_value(
            user_config.realtime.provider
        )
        realtime_model = getattr(user_config.realtime, "model", None)
        if realtime_model:
            runtime_configuration["realtime_model"] = realtime_model
    if getattr(user_config, "llm", None):
        runtime_configuration["llm_provider"] = _provider_value(
            user_config.llm.provider
        )
        llm_model = getattr(user_config.llm, "model", None)
        if llm_model:
            runtime_configuration["llm_model"] = llm_model
    if getattr(user_config, "stt", None):
        runtime_configuration["stt_provider"] = _provider_value(
            user_config.stt.provider
        )
        stt_model = getattr(user_config.stt, "model", None)
        if stt_model:
            runtime_configuration["stt_model"] = stt_model
    if getattr(user_config, "tts", None):
        runtime_configuration["tts_provider"] = _provider_value(
            user_config.tts.provider
        )
        tts_model = getattr(user_config.tts, "model", None)
        if tts_model:
            runtime_configuration["tts_model"] = tts_model
    return runtime_configuration


def _model_usage_item_dict(item) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    return {
        key: getattr(item, key)
        for key in dir(item)
        if not key.startswith("_") and not callable(getattr(item, key, None))
    }


def _livekit_usage_info(
    session_usage,
    duration_seconds: int,
    *,
    caller_connected_seconds: int | None = None,
    caller_connected_at: str | None = None,
) -> dict[str, Any]:
    usage_info: dict[str, Any] = {
        "llm": {},
        "tts": {},
        "stt": {},
        "call_duration_seconds": duration_seconds,
    }
    if caller_connected_seconds is not None:
        usage_info["caller_connected_duration_seconds"] = caller_connected_seconds
    if caller_connected_at:
        usage_info["caller_connected_at"] = caller_connected_at
    model_usage = getattr(session_usage, "model_usage", None) or []
    for index, item in enumerate(model_usage):
        item_data = _model_usage_item_dict(item)
        usage_type = item_data.get("type")
        provider = str(item_data.get("provider") or "livekit").replace("-", "_")
        model = str(item_data.get("model") or "unknown")
        if usage_type == "llm_usage":
            input_tokens = int(item_data.get("input_tokens") or 0)
            output_tokens = int(item_data.get("output_tokens") or 0)
            total_tokens = int(item_data.get("total_tokens") or 0) or (
                input_tokens + output_tokens
            )
            if total_tokens <= 0:
                continue
            key = f"LiveKit{provider}LLMService#{index}|||{model}"
            usage_info["llm"][key] = {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": total_tokens,
                "cache_read_input_tokens": int(
                    item_data.get("input_cached_tokens") or 0
                ),
                "cache_creation_input_tokens": 0,
                "input_audio_tokens": int(item_data.get("input_audio_tokens") or 0),
                "input_cached_audio_tokens": int(
                    item_data.get("input_cached_audio_tokens") or 0
                ),
                "input_text_tokens": int(item_data.get("input_text_tokens") or 0),
                "input_cached_text_tokens": int(
                    item_data.get("input_cached_text_tokens") or 0
                ),
                "output_audio_tokens": int(item_data.get("output_audio_tokens") or 0),
                "output_text_tokens": int(item_data.get("output_text_tokens") or 0),
                "session_duration": float(item_data.get("session_duration") or 0),
            }
        elif usage_type == "tts_usage":
            characters_count = int(item_data.get("characters_count") or 0)
            if characters_count <= 0:
                continue
            key = f"LiveKit{provider}TTSService#{index}|||{model}"
            usage_info["tts"][key] = characters_count
        elif usage_type == "stt_usage":
            audio_duration = float(item_data.get("audio_duration") or 0)
            if audio_duration <= 0:
                continue
            key = f"LiveKit{provider}STTService#{index}|||{model}"
            usage_info["stt"][key] = audio_duration
    return usage_info


async def _calculate_livekit_workflow_run_cost(workflow_run_id: int) -> None:
    from api.services.pricing.workflow_run_cost import calculate_workflow_run_cost

    await calculate_workflow_run_cost(workflow_run_id)


def _register_feedback_handlers(
    session: AgentSession, agent: LiveKitWorkflowAgent
) -> None:
    def _note_interruption(phase: str, timestamp: float) -> None:
        """VOICE-01 diagnostics: compact, transcript-free interruption events."""
        counters = getattr(agent, "_interruption_events", None)
        counter_key = {
            "caller_speech_detected": "caller_speech_during_agent_speech",
            "accepted": "accepted",
            "rejected": "rejected",
        }[phase]
        if isinstance(counters, dict):
            counters[counter_key] = counters.get(counter_key, 0) + 1
        turn_id = agent._state_machine.turn_id
        logger.info(
            "[LiveKit] interruption "
            f"run_id={agent._workflow_run_id} phase={phase} turn={turn_id}"
        )
        agent._emit_feedback(
            {
                "type": "rtf-interruption",
                "payload": {"phase": phase, "turn_id": turn_id},
                "timestamp": timestamp,
            }
        )

    @session.on("user_state_changed")
    def _on_user_state_changed(ev: agents.UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            agent._user_speech_started_at = ev.created_at
            if getattr(agent, "_opening_active", False):
                agent._opening_caller_speech_seen = True
            agent._silence_manager.reset_silence(reason="user_speaking")
            agent._latency_tracker.record_user_speech_start(agent._state_machine.turn_id, ev.created_at)
            if agent._state_machine.state == ConversationState.AGENT_SPEAKING:
                # Caller speech detected while the agent is speaking. Whether
                # the provider/SDK accepts it as a barge-in is reported
                # separately (agent_state_changed / speech done callback).
                agent._latency_tracker.record_interruption(agent._state_machine.turn_id, ev.created_at)
                agent._state_machine.transition_to(
                    ConversationState.INTERRUPTED, reason="caller_speech_during_agent_speech"
                )
                _note_interruption("caller_speech_detected", ev.created_at)
            else:
                agent._state_machine.transition_to(ConversationState.CUSTOMER_SPEAKING, reason="user_started_speaking")

        elif ev.old_state == "speaking" and ev.new_state == "listening":
            agent._last_user_speech_end_at = ev.created_at
            agent._latency_tracker.record_user_speech_end(agent._state_machine.turn_id, ev.created_at)
            if (
                agent._state_machine.state == ConversationState.INTERRUPTED
                and getattr(session, "agent_state", None) == "speaking"
            ):
                # The agent never stopped: the SDK/provider did not accept this
                # speech as a barge-in (too short, noise, echo, or the speech
                # was uninterruptible). Return to AGENT_SPEAKING honestly.
                _note_interruption("rejected", ev.created_at)
                agent._state_machine.transition_to(
                    ConversationState.AGENT_SPEAKING,
                    reason="interruption_not_accepted",
                    force=True,
                )
            elif agent._state_machine.state in (ConversationState.CUSTOMER_SPEAKING, ConversationState.INTERRUPTED):
                agent._latency_tracker.record_thinking_start(agent._state_machine.turn_id, ev.created_at)
                agent._state_machine.transition_to(ConversationState.THINKING, reason="user_speech_finished")

    @session.on("speech_created")
    def _on_speech_created(ev: agents.SpeechCreatedEvent) -> None:
        handle = getattr(ev, "speech_handle", None)
        add_done_callback = getattr(handle, "add_done_callback", None)
        if not callable(add_done_callback):
            return

        if getattr(agent, "_opening_active", False):
            if getattr(ev, "source", None) == "generate_reply":
                agent._emit_opening_event(
                    "opening_generation_created",
                    user_initiated=ev.user_initiated,
                )
                # Suppress server-VAD auto-reply only when the opening uses
                # cached local audio (_say_text path, not generate_reply itself).
                # For the standard Gemini Live path (generate_reply used for the
                # opening), this guard is False and the opening speech is left
                # alone. Suppression applies only when _realtime_exact_speech_uses_tts
                # is True: a TTS-cached audio path where any concurrent
                # generate_reply is spurious server-VAD firing before caller speech.
                if (
                    getattr(agent, "_realtime_exact_speech_uses_tts", False)
                    and not getattr(agent, "_opening_caller_speech_seen", False)
                    and handle is not None
                ):
                    interrupted = False
                    try:
                        handle.interrupt(force=True)
                        interrupted = True
                    except Exception:
                        pass
                    agent._emit_opening_event(
                        "opening_spurious_reply_suppressed",
                        suppressed=interrupted,
                    )

        caller_events_at_creation = getattr(agent, "_interruption_events", {}).get(
            "caller_speech_during_agent_speech", 0
        )
        reported = False

        def _on_speech_done(done_handle) -> None:
            nonlocal reported
            if reported or not getattr(done_handle, "interrupted", False):
                return
            reported = True
            # A forced SDK shutdown also interrupts handles. Count a barge-in
            # only when caller activity was observed during this speech.
            caller_events = getattr(agent, "_interruption_events", {}).get(
                "caller_speech_during_agent_speech", 0
            )
            room = getattr(getattr(agent, "_ctx", None), "room", None)
            isconnected = getattr(room, "isconnected", None)
            if (
                caller_events <= caller_events_at_creation
                or getattr(agent, "_shutdown_task", None) is not None
                or getattr(agent, "_opening_cancel_reason", None) is not None
                or getattr(agent, "_session_closed", False)
                or (callable(isconnected) and not isconnected())
            ):
                return
            _note_interruption("accepted", time.time())

        try:
            add_done_callback(_on_speech_done)
        except RuntimeError:
            # add_done_callback needs a running loop when the handle is already done.
            pass

    @session.on("close")
    def _on_session_close(ev: agents.CloseEvent) -> None:
        agent._session_closed = True
        if getattr(agent, "_opening_active", False):
            reason = getattr(ev.reason, "value", str(ev.reason))
            agent._opening_cancel_reason = f"session_{reason}"

    @session.on("agent_state_changed")
    def _on_agent_state_changed(ev: agents.AgentStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            agent._latency_tracker.record_thinking_end(agent._state_machine.turn_id, ev.created_at)
            agent._latency_tracker.record_agent_speech_start(agent._state_machine.turn_id, ev.created_at)
            agent._state_machine.transition_to(ConversationState.AGENT_SPEAKING, reason="agent_speech_started")
            if agent._last_user_speech_end_at is not None:
                latency_ms = max(0.0, (ev.created_at - agent._last_user_speech_end_at) * 1000)
                logger.info(
                    "[LiveKit] agent speech latency "
                    f"run_id={agent._workflow_run_id} "
                    f"speech_start_after_user_speech_ms={latency_ms:.0f}"
                )
                agent._emit_feedback(
                    {
                        "type": "rtf-latency-measured",
                        "payload": {"speech_start_after_user_speech_ms": round(latency_ms, 1)},
                        "timestamp": ev.created_at,
                    }
                )
        elif ev.new_state == "listening" and ev.old_state == "speaking":
            agent._latency_tracker.record_agent_speech_end(agent._state_machine.turn_id, ev.created_at)
            if agent._state_machine.state == ConversationState.INTERRUPTED:
                agent._latency_tracker.record_interruption_stopped(agent._state_machine.turn_id, ev.created_at)
                agent._state_machine.transition_to(ConversationState.CUSTOMER_SPEAKING, reason="agent_silenced_after_barge_in")
            else:
                agent._state_machine.transition_to(ConversationState.LISTENING, reason="agent_speech_ended")
            agent._silence_manager.reset_silence(reason="agent_speech_ended")

    @session.on("user_input_transcribed")
    def _on_user_input_transcribed(ev: agents.UserInputTranscribedEvent) -> None:
        if ev.is_final:
            agent._last_final_transcript_at = ev.created_at
            logger.info(
                "[LiveKit] user transcript "
                f"run_id={agent._workflow_run_id} text={ev.transcript!r}"
            )
            # VOICE-01: never interrupt from a final transcript. With the Google
            # realtime plugin a *final* input transcript is only emitted when
            # Gemini's generation for that very turn completes, so acting on it
            # here (a) fired long after any real barge-in and (b) invoked the
            # session-level interrupt, which cancels the current speech AND every
            # queued speech — including the fresh answer to the caller. Barge-in
            # is owned by the SDK/provider (local VAD or Gemini server VAD);
            # this handler only retains the caller's words.

        agent._emit_feedback(
            {
                "type": "rtf-user-transcription",
                "payload": {"text": ev.transcript, "final": ev.is_final},
                "timestamp": ev.created_at,
            }
        )

    @session.on("conversation_item_added")
    def _on_conversation_item_added(ev: agents.ConversationItemAddedEvent) -> None:
        item = ev.item
        role = getattr(item, "role", "")
        if role != "assistant":
            return
        text = _first_content_text(item)
        if not text:
            return
        logger.info(
            f"[LiveKit] assistant text run_id={agent._workflow_run_id} text={text!r}"
        )
        metrics = _message_metrics_payload(item)
        if agent._last_final_transcript_at is not None:
            metrics["assistant_after_final_transcript_ms"] = round(
                max(0.0, (ev.created_at - agent._last_final_transcript_at) * 1000),
                1,
            )
        if metrics:
            logger.info(
                "[LiveKit] assistant latency metrics "
                f"run_id={agent._workflow_run_id} metrics={metrics}"
            )
        agent._emit_feedback(
            {
                "type": "rtf-bot-text",
                "payload": {"text": text, "metrics": metrics},
                "timestamp": ev.created_at,
            }
        )

    @session.on("function_tools_executed")
    def _on_function_tools_executed(ev: agents.FunctionToolsExecutedEvent) -> None:
        agent._silence_manager.set_tool_running(False)
        agent._silence_manager.reset_silence(reason="tool_executed")
        for call, output in zip(ev.function_calls, ev.function_call_outputs):
            logger.info(
                "[LiveKit] function tool completed "
                f"run_id={agent._workflow_run_id} name={call.name!r} "
                f"output={getattr(output, 'output', None)!r}"
            )
            agent._emit_feedback(
                {
                    "type": "rtf-function-call-end",
                    "payload": {
                        "tool_call_id": call.call_id,
                        "function_name": call.name,
                        "result": getattr(output, "output", None) if output else None,
                    },
                    "timestamp": ev.created_at,
                }
            )


_FINALIZE_TIMEOUT_SECONDS = 60.0
_POST_CALL_TIMEOUT_SECONDS = 30.0
_ROOM_DELETE_TIMEOUT_SECONDS = 5.0
_SHUTDOWN_AUX_TIMEOUT_SECONDS = 3.0
_FINALIZATION_ANNOTATION = "livekit_finalization"
_FINALIZATION_RECOVERY_VERSION = 1


async def _set_finalization_state(
    workflow_run_id: int,
    state: str,
    *,
    reason: str | None = None,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Persist progress without discarding recovery inputs; failures propagate."""
    existing = await db_client.get_workflow_run_by_id(workflow_run_id)
    prior = (getattr(existing, "annotations", None) or {}).get(_FINALIZATION_ANNOTATION) or {}
    payload: dict[str, Any] = {
        **prior, "state": state, "updated_at": datetime.now(UTC).isoformat(),
        "recovery_version": _FINALIZATION_RECOVERY_VERSION,
    }
    if reason:
        payload["reason"] = reason
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)
    await db_client.update_workflow_run(
        workflow_run_id, annotations={_FINALIZATION_ANNOTATION: payload}
    )



def _safe_finalize_error(exc: BaseException) -> str:
    """Use a fixed failure category; exception messages may contain secrets."""
    # Do not attempt to blacklist every credential syntax or truncate before
    # scrubbing. Even JSON, URLs and driver parameter dumps remain private.
    category = {
        RuntimeError: "RuntimeError", ValueError: "ValueError",
        TimeoutError: "TimeoutError", OSError: "OSError",
    }.get(type(exc), "OperationError")
    return f"{category}: <redacted>"


def _settle_finalize_task(agent, task) -> str:
    """Derive the outcome of a completed finalize task and record it once."""
    if task.cancelled():
        state = "cancelled"
        error = None
    else:
        exc = task.exception()
        if exc is not None:
            state = "failed"
            error = _safe_finalize_error(exc)
        else:
            state = "succeeded"
            error = None
    if getattr(agent, "_finalize_task", None) is task:
        setattr(agent, "_finalize_state", state)
        setattr(agent, "_finalize_error", error)
    return state


def _on_finalize_task_done(task, agent) -> None:
    """Supervise the task even when every waiter has been cancelled."""
    state = _settle_finalize_task(agent, task)
    if state == "failed":
        logger.error(
            "[LiveKit] workflow run finalization failed: "
            f"{getattr(agent, '_finalize_error', None)}"
        )
    elif state == "cancelled":
        logger.warning("[LiveKit] workflow run finalization task was cancelled")


async def _finalize_once(
    *,
    agent,
    reason: str,
    finalize,
    timeout_seconds: float = _FINALIZE_TIMEOUT_SECONDS,
) -> str:
    """Coordinate exactly one authoritative finalization per run.

    The shared task is supervised by a done-callback, so its state settles even
    if every waiter is cancelled. State checks and task creation happen without
    an intervening await, so two callers cannot launch duplicate work. A
    succeeded attempt is never re-run; failed/cancelled attempts are retryable.
    """
    effective_reason = (
        getattr(agent, "_requested_shutdown_reason", None) or reason or "unknown"
    )
    task = getattr(agent, "_finalize_task", None)
    if task is not None and task.done():
        state = _settle_finalize_task(agent, task)
        if state == "succeeded":
            return "succeeded"
        task = None
    if task is None:
        # Synchronous check-and-create: no await between the read above and
        # this assignment, so concurrent callers share one operation.
        async def bounded_finalize():
            # The deadline belongs to shared work, independent of waiters.
            return await asyncio.wait_for(
                finalize(effective_reason), timeout=timeout_seconds
            )

        task = asyncio.create_task(bounded_finalize())
        setattr(agent, "_finalize_task", task)
        setattr(agent, "_finalize_state", "pending")
        setattr(agent, "_finalize_error", None)
        task.add_done_callback(
            lambda completed: _on_finalize_task_done(completed, agent)
        )

    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        # Only this waiter was cancelled; the supervised task continues.
        if task.done():
            _settle_finalize_task(agent, task)
        raise
    except asyncio.TimeoutError:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        if getattr(agent, "_finalize_task", None) is task:
            setattr(agent, "_finalize_state", "failed")
            setattr(agent, "_finalize_error", "timeout")
        logger.error("[LiveKit] workflow run finalization timed out")
        return "failed"
    except Exception as exc:
        # The task failed; the done-callback settles the state.
        _settle_finalize_task(agent, task)
        logger.error(f"[LiveKit] workflow run finalization failed: {_safe_finalize_error(exc)}")
        return "failed"
    return _settle_finalize_task(agent, task)


def _caller_connected_duration(agent, *, now: float | None = None) -> int | None:
    """Caller-connected seconds, distinct from worker lifetime.

    Returns None when no caller connection was recorded (e.g., startup
    failures before a participant joined), so consumers never invent a value.
    """
    connected = getattr(agent, "_caller_connected_monotonic", None)
    if not connected:
        return None
    end = getattr(agent, "_caller_disconnected_monotonic", None)
    if end is None:
        # No authoritative disconnect recorded (worker crash, provider close):
        # bound by the caller's current session time at finalization.
        end = now if now is not None else time.monotonic()
    return int(max(0.0, end - connected))


async def _finalize_livekit_workflow_run(
    *,
    workflow_run_id: int,
    session: AgentSession,
    agent: LiveKitWorkflowAgent,
    room_name: str,
    reason: str,
) -> dict[str, Any]:
    # Retries reuse the first attempt's duration so a slow retry cannot inflate
    # billing.
    elapsed = getattr(agent, "_finalized_elapsed_seconds", None)
    if elapsed is None:
        elapsed = int(time.monotonic() - agent._started_at)
        agent._finalized_elapsed_seconds = elapsed
    caller_connected_seconds = _caller_connected_duration(agent)
    caller_connected_at = getattr(agent, "_caller_connected_wall", None)

    recording_stop = "not_run"
    recording_error: str | None = None
    post_call_status = "not_run"
    try:
        # Snapshot before asynchronous recording cleanup. The pending marker,
        # terminal state, usage and history commit together below.
        final_recording_state = getattr(agent, "_recording_state", None)
        livekit_history = session.history.to_dict()
        latency_summary = (
            agent._latency_tracker.summary()
            if hasattr(agent, "_latency_tracker")
            else {}
        )
        state_history = (
            agent._state_machine.history()
            if hasattr(agent, "_state_machine")
            else []
        )
        interruption_stats = dict(
            agent._interruption_filter.stats
            if hasattr(agent, "_interruption_filter")
            else {}
        )
        interruption_events = getattr(agent, "_interruption_events", None) or {}
        if interruption_events:
            interruption_stats.update(interruption_events)
            # "interruptions" now means interruptions the SDK actually accepted
            # (agent speech was cancelled), not transcript classifications.
            interruption_stats["interruptions"] = interruption_events.get("accepted", 0)
        silence_disposition = (
            getattr(agent._silence_manager, "disposition", None)
            if hasattr(agent, "_silence_manager")
            else None
        )
        gathered_context = {
            "livekit_room": room_name,
            "livekit_shutdown_reason": reason,
            "turn_latency_metrics": latency_summary,
            "conversation_history": state_history,
            "interruption_stats": interruption_stats,
        }
        if silence_disposition:
            gathered_context["disposition"] = silence_disposition
        recording_url = None
        if final_recording_state is not None:
            gathered_context["livekit_recording"] = final_recording_state.to_log()
            recording_url = post_call.recording_available_url(final_recording_state)

        existing_cost_info: dict[str, Any] = {}
        try:
            existing_run = await db_client.get_workflow_run_by_id(workflow_run_id)
            existing_cost_info = dict(getattr(existing_run, "cost_info", None) or {})
        except Exception as exc:
            logger.warning(
                f"[LiveKit] failed to load existing cost info before finalize: {_safe_finalize_error(exc)}"
            )

        cost_info_extra: dict[str, Any] = {"call_duration_seconds": elapsed}
        if caller_connected_seconds is not None:
            cost_info_extra["caller_connected_duration_seconds"] = caller_connected_seconds
        await db_client.update_workflow_run(
            workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            recording_url=recording_url,
            usage_info=_livekit_usage_info(
                getattr(session, "usage", None),
                elapsed,
                caller_connected_seconds=caller_connected_seconds,
                caller_connected_at=caller_connected_at,
            ),
            cost_info={**existing_cost_info, **cost_info_extra},
            logs={"livekit_history": livekit_history},
            gathered_context=gathered_context,
            annotations={_FINALIZATION_ANNOTATION: {
                "state": "pending", "updated_at": datetime.now(UTC).isoformat(),
                "recovery_version": _FINALIZATION_RECOVERY_VERSION,
                "reason": reason, "elapsed_seconds": elapsed, "room": room_name,
                "caller_connected_seconds": caller_connected_seconds,
            }},
        )
        try:
            stopped = await asyncio.wait_for(
                agent.stop_recording(), timeout=_SHUTDOWN_AUX_TIMEOUT_SECONDS
            )
            recording_stop = (
                "failed" if stopped is not None and
                getattr(stopped, "status", None) in ("failed", "stop_failed") else "ok"
            )
            if stopped is not None:
                await db_client.update_workflow_run(
                    workflow_run_id,
                    recording_url=post_call.recording_available_url(stopped),
                    gathered_context={"livekit_recording": stopped.to_log()},
                )
        except Exception as exc:
            recording_stop = "failed"
            recording_error = _safe_finalize_error(exc)
            logger.warning(f"[LiveKit] recording cleanup failed: {recording_error}")
        # Required persistence: the run may only be reported as fully
        # finalized after the cost/usage work succeeds.
        await _calculate_livekit_workflow_run_cost(workflow_run_id)

        # Post-call processing is deterministic and idempotent; awaiting it
        # makes its outcome visible. The bound prevents an indefinite shutdown
        # wait and cancels rather than orphaning the work.
        from api.services.livekit.post_call_triggers import (
            process_post_call_intelligence_and_triggers,
        )

        post_call_result = await asyncio.wait_for(
            process_post_call_intelligence_and_triggers(
                workflow_run_id=workflow_run_id,
                livekit_history=livekit_history,
                gathered_context=gathered_context,
            ),
            timeout=_POST_CALL_TIMEOUT_SECONDS,
        )
        post_call_status = (
            str(post_call_result.get("status") or "unknown")
            if isinstance(post_call_result, dict)
            else "unknown"
        )
        if post_call_status != "success":
            raise RuntimeError(
                f"post_call_error: {post_call_result.get('reason') or 'unknown'}"
            )

        if recording_stop == "failed":
            raise RuntimeError("recording_stop_incomplete")
        finalization_extra: dict[str, Any] = {
            "elapsed_seconds": elapsed,
            "room": room_name,
            "recording_stop": recording_stop,
            "post_call_status": post_call_status,
        }
        if recording_error:
            finalization_extra["recording_error"] = recording_error
        await _set_finalization_state(
            workflow_run_id,
            "succeeded",
            reason=reason,
            extra=finalization_extra,
        )
        return {"state": "succeeded", "reason": reason, "elapsed_seconds": elapsed}
    except asyncio.CancelledError:
        # The durable marker stays "pending"; recovery/retry can finish the work.
        raise
    except Exception as exc:
        failure_extra: dict[str, Any] = {
            "elapsed_seconds": elapsed,
            "room": room_name,
            "recording_stop": recording_stop,
            "post_call_status": (
                "timeout"
                if isinstance(exc, asyncio.TimeoutError)
                else post_call_status
            ),
        }
        if recording_error:
            failure_extra["recording_error"] = recording_error
        await _set_finalization_state(
            workflow_run_id,
            "failed",
            reason=reason,
            error=_safe_finalize_error(exc),
            extra=failure_extra,
        )
        raise


def _google_realtime_generation_options(
    latency_profile: str | None,
) -> dict[str, Any]:
    if not _is_fast_latency_profile(latency_profile):
        return {"temperature": 0.1}
    return {
        "temperature": 0.0,
        "top_p": 0.8,
        "top_k": 20,
        "tool_response_scheduling": genai_types.FunctionResponseScheduling.INTERRUPT,
    }


def _silero_vad_options() -> dict[str, Any]:
    return {
        "sample_rate": 16000,
        "min_speech_duration": 0.04,
        "min_silence_duration": 0.2,
        "prefix_padding_duration": 0.18,
        "activation_threshold": 0.35,
    }


def _create_session(
    user_config,
    *,
    vad,
    latency_profile: str | None = None,
) -> AgentSession:
    if user_config.is_realtime and user_config.realtime is not None:
        realtime = user_config.realtime
        provider = _provider_value(realtime.provider)
        model = getattr(realtime, "model", "")
        thinking_config = _google_thinking_config(model)
        turn_detection_mode = "realtime_llm"
        if provider == ServiceProviders.OPENAI_REALTIME.value:
            realtime_llm = openai.realtime.RealtimeModel(
                model=model,
                voice=getattr(realtime, "voice", "alloy"),
                api_key=realtime.api_key,
            )
        elif provider == ServiceProviders.GOOGLE_REALTIME.value:
            kwargs: dict[str, Any] = {
                "model": model,
                "voice": getattr(realtime, "voice", "Puck"),
                "language": getattr(realtime, "language", "en"),
                "api_key": realtime.api_key,
                **_google_realtime_generation_options(latency_profile),
            }
            if _supports_realtime_generate_reply(provider, model):
                kwargs["realtime_input_config"] = _local_vad_realtime_input_config()
                turn_detection_mode = "vad"
            else:
                kwargs["realtime_input_config"] = (
                    _fast_server_vad_realtime_input_config(latency_profile)
                )
            if thinking_config:
                kwargs["thinking_config"] = genai_types.ThinkingConfig(
                    **thinking_config
                )
            realtime_llm = google.beta.realtime.RealtimeModel(
                **kwargs,
            )
        elif provider == ServiceProviders.GOOGLE_VERTEX_REALTIME.value:
            kwargs = {
                "model": model,
                "voice": getattr(realtime, "voice", "Charon"),
                "language": getattr(realtime, "language", "en-US"),
                "vertexai": True,
                "project": getattr(realtime, "project_id", None),
                "location": getattr(realtime, "location", "us-east4"),
                **_google_realtime_generation_options(latency_profile),
            }
            if _supports_realtime_generate_reply(provider, model):
                kwargs["realtime_input_config"] = _local_vad_realtime_input_config()
                turn_detection_mode = "vad"
            else:
                kwargs["realtime_input_config"] = (
                    _fast_server_vad_realtime_input_config(latency_profile)
                )
            if thinking_config:
                kwargs["thinking_config"] = genai_types.ThinkingConfig(
                    **thinking_config
                )
            realtime_llm = google.beta.realtime.RealtimeModel(
                **kwargs,
            )
        else:
            raise ValueError(f"LiveKit realtime provider is unsupported: {provider}")
        return AgentSession(
            llm=realtime_llm,
            vad=vad,
            **_session_latency_options(
                turn_detection_mode,
                latency_profile=latency_profile,
            ),
        )

    if not (user_config.llm and user_config.stt and user_config.tts):
        raise ValueError(
            "LiveKit non-realtime mode requires llm, stt, and tts configuration"
        )

    llm_provider = _provider_value(user_config.llm.provider)
    stt_provider = _provider_value(user_config.stt.provider)
    tts_provider = _provider_value(user_config.tts.provider)
    llm_model = user_config.llm.model

    # A stored key may only travel to the provider's official host or an
    # operator-approved endpoint; checked here, immediately before use.
    check_endpoint(
        llm_provider,
        getattr(user_config.llm, "base_url", None),
        field="base_url",
        has_credential=bool(user_config.llm.api_key)
        or (llm_provider == "deepseek" and bool(os.environ.get("DEEPSEEK_API_KEY"))),
    )
    if llm_provider == ServiceProviders.OPENAI.value:
        kwargs = {
            "model": llm_model,
            "api_key": user_config.llm.api_key,
            "base_url": getattr(user_config.llm, "base_url", None),
            "parallel_tool_calls": False,
        }
        if "gpt-5" in (llm_model or "").lower():
            kwargs["reasoning_effort"] = "minimal"
        else:
            kwargs["temperature"] = 0.1
        livekit_llm = openai.LLM(
            **kwargs,
        )
    elif llm_provider == ServiceProviders.GOOGLE.value:
        kwargs = {
            "model": llm_model,
            "api_key": user_config.llm.api_key,
            "temperature": 0.1,
        }
        thinking_config = _google_thinking_config(llm_model)
        if thinking_config:
            kwargs["thinking_config"] = thinking_config
        livekit_llm = google.LLM(
            **kwargs,
        )
    elif llm_provider in ("deepseek", getattr(getattr(ServiceProviders, "DEEPSEEK", None), "value", "deepseek")):
        api_key = user_config.llm.api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DeepSeek LLM requires api_key in user_config.llm or DEEPSEEK_API_KEY environment variable")
        kwargs = {
            "model": llm_model or "deepseek-chat",
            "api_key": api_key,
            "base_url": getattr(user_config.llm, "base_url", None) or "https://api.deepseek.com",
            "parallel_tool_calls": False,
            "temperature": 0.1,
        }
        livekit_llm = openai.LLM(
            **kwargs,
        )
    else:
        raise ValueError(f"LiveKit LLM provider is unsupported: {llm_provider}")

    if stt_provider == ServiceProviders.OPENAI.value:
        livekit_stt = openai.STT(
            model=user_config.stt.model,
            api_key=user_config.stt.api_key,
        )
    elif stt_provider == ServiceProviders.GOOGLE.value:
        livekit_stt = google.STT(
            model=user_config.stt.model,
            languages=getattr(user_config.stt, "language", "en-US"),
        )
    elif stt_provider in ("deepgram", getattr(getattr(ServiceProviders, "DEEPGRAM", None), "value", "deepgram")):
        api_key = user_config.stt.api_key or os.environ.get("DEEPGRAM_API_KEY")
        if not api_key:
            raise ValueError("Deepgram STT requires api_key in user_config.stt or DEEPGRAM_API_KEY environment variable")
        from livekit.plugins import deepgram
        livekit_stt = deepgram.STT(
            model=getattr(user_config.stt, "model", "nova-3-general") or "nova-3-general",
            api_key=api_key,
            language=getattr(user_config.stt, "language", "en-US") or "en-US",
        )
    else:
        raise ValueError(f"LiveKit STT provider is unsupported: {stt_provider}")

    if tts_provider == ServiceProviders.OPENAI.value:
        livekit_tts = openai.TTS(
            model=user_config.tts.model,
            voice=getattr(user_config.tts, "voice", "alloy"),
            api_key=user_config.tts.api_key,
        )
    elif tts_provider == ServiceProviders.GOOGLE.value:
        livekit_tts = google.TTS(
            voice_name=getattr(user_config.tts, "voice", None),
            language=getattr(user_config.tts, "language", "en-US"),
        )
    elif tts_provider in ("cartesia", getattr(getattr(ServiceProviders, "CARTESIA", None), "value", "cartesia")):
        api_key = user_config.tts.api_key or os.environ.get("CARTESIA_API_KEY")
        if not api_key:
            raise ValueError("Cartesia TTS requires api_key in user_config.tts or CARTESIA_API_KEY environment variable")
        from livekit.plugins import cartesia
        cartesia_kwargs: dict[str, Any] = {
            "model": getattr(user_config.tts, "model", "sonic-3") or "sonic-3",
            "voice": getattr(user_config.tts, "voice", "248be419-c632-4f23-adf1-5324ed7dbf10") or "248be419-c632-4f23-adf1-5324ed7dbf10",
            "api_key": api_key,
        }
        if getattr(user_config.tts, "speed", None) is not None:
            cartesia_kwargs["speed"] = float(user_config.tts.speed)
        if getattr(user_config.tts, "volume", None) is not None:
            cartesia_kwargs["volume"] = float(user_config.tts.volume)
        if getattr(user_config.tts, "emotion", None) is not None:
            cartesia_kwargs["emotion"] = user_config.tts.emotion
        livekit_tts = cartesia.TTS(
            **cartesia_kwargs,
        )
    else:
        raise ValueError(f"LiveKit TTS provider is unsupported: {tts_provider}")

    return AgentSession(
        stt=livekit_stt,
        llm=livekit_llm,
        tts=livekit_tts,
        vad=vad,
        **_session_latency_options("vad", latency_profile=latency_profile),
    )


def prewarm(proc: agents.JobProcess) -> None:
    proc.userdata["vad"] = silero.VAD.load(**_silero_vad_options())


async def _resolve_or_create_workflow_run(
    metadata: dict[str, Any],
    participant_context: dict[str, Any],
) -> tuple[int, int, int, int, dict[str, Any]]:
    workflow_id = int(metadata["workflow_id"])
    user_id = int(metadata["user_id"])
    organization_id = int(metadata["organization_id"])
    call_type = metadata.get("call_type") or "web"
    workflow_run_id = metadata.get("workflow_run_id")
    initial_context = {
        **(metadata.get("initial_context") or {}),
        **{k: v for k, v in participant_context.items() if v not in (None, "", {})},
        "provider": WorkflowRunMode.LIVEKIT.value,
    }

    if workflow_run_id:
        return (
            workflow_id,
            int(workflow_run_id),
            user_id,
            organization_id,
            initial_context,
        )

    numeric_suffix = int(str(uuid.uuid4()).replace("-", "")[:8], 16) % 100000000
    workflow_run = await db_client.create_workflow_run(
        f"WR-LK-IN-{numeric_suffix:08d}",
        workflow_id,
        WorkflowRunMode.LIVEKIT.value,
        user_id=user_id,
        call_type=CallType.INBOUND if call_type == "inbound" else CallType.OUTBOUND,
        initial_context=initial_context,
        gathered_context={"call_id": participant_context.get("call_id")},
        use_draft=False,
        organization_id=organization_id,
    )
    return workflow_id, workflow_run.id, user_id, organization_id, initial_context


# Stable, owner-facing codes for a startup failure. Keyed on built-in and
# project-defined exception types only -- never a provider SDK exception
# type, since we do not control that library's message contract and cannot
# assume isinstance() alone makes its text safe to surface. Anything not
# matched here (including every provider/client-library exception) falls
# through to the generic STARTUP_FAILED code and never contributes its own
# message. Order matters: more specific subclasses must precede their base
# class, since this is checked in insertion order (InvalidCallDurationError
# before ValueError; PermissionError -- a subclass of OSError -- before
# OSError itself).
_STARTUP_ERROR_CODES: dict[type[BaseException], str] = {
    InvalidCallDurationError: "INVALID_CALL_DURATION",
    ValueError: "INVALID_CONFIGURATION",
    TimeoutError: "TIMEOUT",
    ConnectionError: "CONNECTION_FAILED",
    PermissionError: "AUTH_FAILED",
    OSError: "CONNECTION_FAILED",
}
_DEFAULT_STARTUP_ERROR_CODE = "STARTUP_FAILED"


def _startup_error_code(exc: BaseException) -> str:
    """Map an exception to a stable, owner-facing error code (no message)."""
    for exc_type, code in _STARTUP_ERROR_CODES.items():
        if isinstance(exc, exc_type):
            return code
    return _DEFAULT_STARTUP_ERROR_CODE


def _startup_sanitized_message(exc: BaseException, *, stage: str) -> str:
    """Category-only text: exception class + stable error code + stage.

    Never includes str(exc). Single source of truth shared by
    ``_record_livekit_startup_failure`` (what gets persisted) and every
    sanitized re-raise point in ``entrypoint()`` (what gets raised in place
    of the original exception, so the LiveKit Agents job runner's own
    ``exc_info=`` logging of an uncaught exception can never render the
    original message either).
    """
    return f"{type(exc).__name__}: {_startup_error_code(exc)} (stage={stage})"


class LiveKitStartupError(RuntimeError):
    """Sanitized exception raised in place of the original startup failure.

    ``entrypoint()`` must never let the original exception escape to the
    LiveKit Agents job runner: that runner logs any uncaught exception from
    the entrypoint task via ``logger.error(..., exc_info=task.exception())``,
    which renders the exception's own ``str()`` and traceback regardless of
    anything this module logs or persists. Raising this type instead, always
    with ``from None``, means the escaping exception's own message is
    exactly ``_startup_sanitized_message(...)`` -- category-only -- and
    ``__cause__``/``__context__`` carry nothing renderable back to the
    original exception.
    """


async def _record_livekit_startup_failure(
    workflow_run_id: int,
    exc: Exception,
    *,
    stage: str = "startup",
    provider: str | None = None,
    model: str | None = None,
    user_config=None,
) -> None:
    """Make a startup failure visible from the workflow run, not only logs.

    Emits a ``startup_failed`` event into ``logs.livekit_events`` and records
    ``annotations.livekit_startup_error`` so the run UI can surface why a call
    never started. Safe to call from any startup failure path.

    Persists category-only data -- exception class, a stable error code, and
    the operational ``stage`` -- never the exception's own message. Provider
    and LiveKit client exceptions may embed API keys, bearer tokens, or
    credential-bearing URLs in their string representation, and that text
    would otherwise reach the API log and the workflow run's DB-persisted
    logs/annotations (and anything that later reads them). This deliberately
    does not call ``logger.exception()``: that attaches the raw exception and
    traceback to the sink regardless of the message text passed in, which
    would reintroduce the same leak through a different door. The caller is
    also responsible for never re-raising ``exc`` itself afterwards -- see
    ``LiveKitStartupError``.
    """

    error_class = type(exc).__name__
    error_code = _startup_error_code(exc)
    safe_message = _startup_sanitized_message(exc, stage=stage)

    logger.error(
        "[LiveKit] workflow run startup failed "
        f"run_id={workflow_run_id} stage={stage} provider={provider} "
        f"model={model} error_class={error_class} error_code={error_code}"
    )
    await _append_livekit_run_event(
        workflow_run_id,
        {
            "type": "startup_failed",
            "level": "error",
            "message": safe_message,
            "error_class": error_class,
            "error_code": error_code,
            "stage": stage,
            "provider": provider,
            "model": model,
            "is_realtime": bool(getattr(user_config, "is_realtime", False)),
        },
    )
    try:
        await db_client.update_workflow_run(
            workflow_run_id,
            is_completed=True,
            state=WorkflowRunState.COMPLETED.value,
            annotations={"livekit_startup_error": safe_message},
            call_disposition="FAILED",
        )
    except Exception as persist_exc:
        logger.warning(
            "[LiveKit] failed to persist startup failure: "
            f"{_safe_finalize_error(persist_exc)}"
        )


async def entrypoint(ctx: JobContext) -> None:
    """Public entrypoint registered with the LiveKit Agents job runner.

    Wraps ``_run_entrypoint`` in a final safety net so no exception -- not
    even one raised before a workflow run exists to record a failure
    against (e.g. ``ctx.connect()`` or ``_resolve_or_create_workflow_run``
    itself failing) -- can ever escape with its original message. The job
    runner logs any uncaught exception from this task via
    ``logger.error(..., exc_info=task.exception())``, which would otherwise
    render the original exception's full text and traceback regardless of
    what ``_run_entrypoint``'s own inner handlers already sanitized.
    """
    try:
        await _run_entrypoint(ctx)
    except LiveKitStartupError:
        raise  # already sanitized by an inner handler below; nothing to add
    except Exception as exc:
        safe_message = _startup_sanitized_message(exc, stage="pre_run_resolution")
        logger.error(f"[LiveKit] unrecorded startup failure: {safe_message}")
        raise LiveKitStartupError(safe_message) from None


async def _run_entrypoint(ctx: JobContext) -> None:
    metadata = _metadata_from_job(ctx)
    await ctx.connect()
    room_metadata = _metadata_from_room(ctx)
    if room_metadata:
        metadata = {**room_metadata, **metadata}
    logger.info(
        "[LiveKit] dispatch received "
        f"job_id={ctx.job.id} room={ctx.room.name} "
        f"workflow_id={metadata.get('workflow_id')} "
        f"call_type={metadata.get('call_type')}"
    )
    participant = await ctx.wait_for_participant()
    participant_context = _participant_context(participant)
    # Authoritative caller-connection timestamps (run #345 class separation):
    # worker lifetime (_started_at) must never be reported as caller time.
    caller_connected_monotonic = time.monotonic()
    caller_connected_wall = datetime.now(UTC).isoformat()
    logger.info(
        "[LiveKit] SIP participant connected "
        f"room={ctx.room.name} "
        f"caller={participant_context.get('caller_number')} "
        f"called={participant_context.get('called_number')} "
        f"call_id={participant_context.get('call_id')}"
    )
    (
        workflow_id,
        workflow_run_id,
        user_id,
        organization_id,
        initial_context,
    ) = await _resolve_or_create_workflow_run(metadata, participant_context)

    await db_client.update_workflow_run(
        workflow_run_id,
        state=WorkflowRunState.RUNNING.value,
        initial_context=initial_context,
        gathered_context={"livekit_room": ctx.room.name},
    )

    # Resolve workflow/config before the call starts. A failure here (missing
    # workflow, invalid saved config, bad workflow JSON) must still be recorded
    # on the run instead of leaving it stuck in RUNNING with no signal.
    user_config = None
    realtime_provider = None
    realtime_model = None
    startup_tasks: list[asyncio.Task] = []
    try:
        workflow_run_task = asyncio.create_task(
            db_client.get_workflow_run(workflow_run_id, organization_id=organization_id)
        )
        workflow_task = asyncio.create_task(
            db_client.get_workflow(workflow_id, organization_id=organization_id)
        )
        user_config_task = asyncio.create_task(
            db_client.get_user_configurations(user_id)
        )
        has_recordings_task = asyncio.create_task(
            db_client.has_active_recordings(organization_id)
        )
        startup_tasks.extend(
            [
                workflow_run_task,
                workflow_task,
                user_config_task,
                has_recordings_task,
            ]
        )

        workflow_run = await workflow_run_task
        if not workflow_run:
            raise ValueError(f"Workflow run {workflow_run_id} not found")

        workflow = await workflow_task
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        run_definition = workflow_run.definition
        run_configs = run_definition.workflow_configurations or {}
        latency_profile = run_configs.get("latency_profile")
        silence_config = run_configs.get("silence_config") or {
            "stage1_seconds": float(run_configs.get("silence_stage1_seconds", 4.0)),
            "stage2_seconds": float(run_configs.get("silence_stage2_seconds", 8.0)),
            "stage3_seconds": float(run_configs.get("silence_stage3_seconds", 14.0)),
            "custom_prompts": run_configs.get("silence_prompts"),
        }
        interruption_config = run_configs.get("interruption_config") or {
            "min_duration_seconds": float(run_configs.get("min_interruption_duration", 0.45)),
            "min_words": int(run_configs.get("min_interruption_words", 2)),
            "backchannel_filter_enabled": bool(run_configs.get("backchannel_filter_enabled", True)),
            "custom_backchannel_words": run_configs.get("custom_backchannel_words"),
        }
        # economy_ivr is the zero-AI Asterisk mode. Never let it fall through
        # to the global model configuration and start a paid AI session.
        if str(run_configs.get("product_mode") or "").strip().lower() == "economy_ivr":
            raise ValueError(
                "Workflow is in economy_ivr (zero-AI) mode and cannot start a "
                "LiveKit AI session."
            )
        # G-48: the definition pinned to this run is the authority. Reject it
        # before model resolution or provider-session construction when its
        # application-level duration limit is missing or invalid.
        max_duration_seconds = _validate_pinned_call_duration(run_configs)
        user_config = await user_config_task
        user_config = resolve_effective_config(
            user_config, run_configs.get("model_overrides")
        )
        uses_realtime = _uses_realtime(user_config)
        runtime_configuration = _runtime_configuration_from_user_config(user_config)
        if runtime_configuration:
            initial_context = {
                **initial_context,
                "runtime_configuration": runtime_configuration,
            }
            await db_client.update_workflow_run(
                workflow_run_id,
                initial_context=initial_context,
            )
        workflow_graph = WorkflowGraph(
            ReactFlowDTO.model_validate(run_definition.workflow_json)
        )
        embedding_settings = resolve_embedding_settings(user_config)
        has_recordings = await has_recordings_task
        realtime_provider = (
            _provider_value(user_config.realtime.provider)
            if user_config.is_realtime and user_config.realtime
            else None
        )
        realtime_model = (
            getattr(user_config.realtime, "model", None)
            if user_config.is_realtime and user_config.realtime
            else None
        )
        realtime_generate_reply_supported = _supports_realtime_generate_reply(
            realtime_provider, realtime_model
        )
        realtime_tts_api_key = (
            getattr(user_config.realtime, "api_key", None)
            if user_config.is_realtime and user_config.realtime
            else None
        )
    except Exception as exc:
        await _settle_startup_tasks(startup_tasks)
        await _record_livekit_startup_failure(
            workflow_run_id,
            exc,
            stage="config_resolution",
            provider=realtime_provider,
            model=realtime_model,
            user_config=user_config,
        )
        if isinstance(exc, InvalidCallDurationError):
            await _cleanup_rejected_startup(ctx, "invalid_call_duration")
        raise LiveKitStartupError(
            _startup_sanitized_message(exc, stage="config_resolution")
        ) from None

    try:
        if user_config.is_realtime and user_config.realtime is None:
            raise ValueError(
                "Realtime mode is enabled but no realtime model is configured "
                "for this run. Configure a realtime model (e.g. Google Gemini "
                "realtime) or turn realtime mode off."
            )
        vad = getattr(ctx.proc, "userdata", {}).get("vad") or silero.VAD.load(
            **_silero_vad_options()
        )
        session = _create_session(
            user_config,
            vad=vad,
            latency_profile=latency_profile,
        )
        agent = LiveKitWorkflowAgent(
            ctx=ctx,
            workflow=workflow_graph,
            workflow_run_id=workflow_run_id,
            organization_id=organization_id,
            call_context_vars=initial_context,
            embeddings_api_key=embedding_settings.get("api_key"),
            embeddings_provider=embedding_settings.get("provider"),
            embeddings_model=embedding_settings.get("model"),
            embeddings_base_url=embedding_settings.get("base_url"),
            has_recordings=has_recordings,
            uses_realtime=uses_realtime,
            realtime_generate_reply_supported=realtime_generate_reply_supported,
            realtime_tool_choice_supported=_supports_realtime_tool_choice(
                realtime_provider
            ),
            realtime_exact_speech_uses_tts=(
                uses_realtime
                and not realtime_generate_reply_supported
                and realtime_provider == ServiceProviders.GOOGLE_REALTIME.value
                and bool(realtime_tts_api_key)
            ),
            tts_api_key=(
                realtime_tts_api_key
                if realtime_provider == ServiceProviders.GOOGLE_REALTIME.value
                else None
            ),
            opening_model=(
                getattr(user_config.realtime, "model", None)
                if user_config.is_realtime and user_config.realtime
                else None
            ),
            tts_voice=(
                getattr(user_config.realtime, "voice", None)
                if user_config.is_realtime and user_config.realtime
                else None
            )
            or DEFAULT_REALTIME_VOICE,  # Gemini voice id, not the assistant name.
            tts_language=(
                getattr(user_config.realtime, "language", None)
                if user_config.is_realtime and user_config.realtime
                else None
            )
            or DEFAULT_REALTIME_LANGUAGE,
            latency_profile=latency_profile,
            silence_config=silence_config,
            interruption_config=interruption_config,
            max_duration_seconds=max_duration_seconds,
        )
        agent._caller_connected_monotonic = caller_connected_monotonic
        agent._caller_connected_wall = caller_connected_wall
        _register_feedback_handlers(session, agent)
        _register_caller_disconnect_handler(agent, ctx.room)
        agent.prewarm_opening_audio()
        try:
            recording_state = await asyncio.wait_for(
                post_call.start_livekit_room_recording(
                    room_name=ctx.room.name,
                    workflow_run_id=workflow_run_id,
                ),
                timeout=5.0,
            )
        except Exception as exc:
            logger.warning(
                f"[LiveKit] recording start skipped: {_safe_finalize_error(exc)}"
            )
            recording_state = None
        agent.set_recording_state(recording_state)
        if recording_state is not None:
            try:
                await db_client.update_workflow_run(
                    workflow_run_id,
                    recording_url=post_call.recording_available_url(recording_state),
                    gathered_context={"livekit_recording": recording_state.to_log()},
                )
            except Exception as exc:
                logger.warning(
                    "[LiveKit] failed to persist recording start: "
                    f"{_safe_finalize_error(exc)}"
                )

        async def _shutdown(reason: str) -> None:
            await _finalize_once(
                agent=agent,
                reason=reason,
                finalize=lambda effective_reason: _finalize_livekit_workflow_run(
                    workflow_run_id=workflow_run_id,
                    session=session,
                    agent=agent,
                    room_name=ctx.room.name,
                    reason=effective_reason,
                ),
            )

        ctx.add_shutdown_callback(_shutdown)

        await session.start(agent=agent, room=ctx.room)
        agent.start_silence_watchdog()
        await agent.start_opening()
    except Exception as exc:
        await _record_livekit_startup_failure(
            workflow_run_id,
            exc,
            stage="session_start",
            provider=realtime_provider,
            model=realtime_model,
            user_config=user_config,
        )
        raise LiveKitStartupError(
            _startup_sanitized_message(exc, stage="session_start")
        ) from None


if __name__ == "__main__":
    settings = effective_livekit_settings()
    import os

    os.environ.update(livekit_environment(settings))
    cli.run_app(
        agents.WorkerOptions(
            entrypoint_fnc=entrypoint,
            prewarm_fnc=prewarm,
            agent_name=settings.livekit_agent_name,
            num_idle_processes=2,
        )
    )
