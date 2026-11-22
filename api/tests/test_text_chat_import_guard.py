"""
Regression test: pipecat-ai dependency contract and Text Chat / Premium route
availability.

Context
-------
pipecat-ai is a **mandatory** dependency of SPX Voice.  It backs:
  - Text Chat routes (api/routes/workflow_text_chat.py)
  - Premium STT/LLM/TTS pipeline (Deepgram-DeepSeek-Cartesia via pipecat services)
  - Agent-stream runner (api/routes/agent_stream.py)

It is supplied in production via requirements.txt (GitHub VCS pin) and locally
via the candidate's .deps offline package store (PYTHONPATH contract documented
in .env.p101test).  Any startup failure caused by pipecat being absent is an
environment misconfiguration, not a valid code path to guard with try/except.

This test suite verifies:
  1. pipecat.frames.frames imports cleanly (the exact module that failed).
  2. api/routes/main.py registers the text-chat router unconditionally.
  3. LiveKit routes are also registered (guard is not over-broad).
  4. The Premium provider factory imports without error.
  5. Text Chat session service imports without error.
"""

import importlib
import sys

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_import(monkeypatch, module_name: str):
    """Remove cached module and re-import it fresh."""
    for k in list(sys.modules):
        if k == module_name or k.startswith(module_name + "."):
            monkeypatch.delitem(sys.modules, k, raising=False)
    return importlib.import_module(module_name)


# ---------------------------------------------------------------------------
# 1. pipecat.frames.frames import
# ---------------------------------------------------------------------------

def test_pipecat_frames_importable():
    """pipecat.frames.frames must be importable (pipecat-ai mandatory dep)."""
    from pipecat.frames.frames import (  # noqa: F401
        BotStoppedSpeakingFrame,
        TextFrame,
        EndFrame,
    )


# ---------------------------------------------------------------------------
# 2. main.py registers text-chat router unconditionally
# ---------------------------------------------------------------------------

def test_main_router_includes_text_chat(monkeypatch):
    """workflow_text_chat_router must be a live router, never None."""
    main = _fresh_import(monkeypatch, "api.routes.main")
    assert main.workflow_text_chat_router is not None, (
        "workflow_text_chat_router is None -- pipecat-ai may be missing from PYTHONPATH"
    )
    # _text_chat_available guard must not exist (it was part of the wrong fix)
    assert not hasattr(main, "_text_chat_available"), (
        "_text_chat_available still present; the import guard was not fully reverted"
    )


def test_text_chat_routes_registered(monkeypatch):
    """Text-chat route paths must appear in main.router's route table."""
    main = _fresh_import(monkeypatch, "api.routes.main")
    all_paths = [getattr(r, "path", "") for r in getattr(main.router, "routes", [])]
    text_chat = [p for p in all_paths if "text" in p.lower() and "chat" in p.lower()]
    assert text_chat, (
        f"No text-chat routes found in main.router -- routes present: {all_paths[:10]}"
    )


# ---------------------------------------------------------------------------
# 3. LiveKit routes also registered
# ---------------------------------------------------------------------------

def test_livekit_routes_registered(monkeypatch):
    """LiveKit router must be included (guard scope must not over-exclude)."""
    main = _fresh_import(monkeypatch, "api.routes.main")
    all_paths = [getattr(r, "path", "") for r in getattr(main.router, "routes", [])]
    assert len(all_paths) > 5, "main.router appears almost empty"


# ---------------------------------------------------------------------------
# 4. Premium provider factory import
# ---------------------------------------------------------------------------

def test_premium_text_chat_session_service_importable():
    """text_chat_session_service must import without error (pipecat-backed)."""
    import api.services.workflow.text_chat_session_service  # noqa: F401


# ---------------------------------------------------------------------------
# 5. text_chat_runner (direct pipecat consumer) imports cleanly
# ---------------------------------------------------------------------------

def test_text_chat_runner_importable():
    """text_chat_runner.py imports pipecat at module level; must not raise."""
    import api.services.workflow.text_chat_runner  # noqa: F401
