"""Gate 2 Correction C — caller-disconnect lifecycle and duration-compat tests.

Deterministic and offline: fake rooms/participants/agents exercise the new
bounded disconnect-cleanup path and the read-time legacy-duration fallback.
"""
from __future__ import annotations

import asyncio

import pytest

from api.services.livekit import worker


# ---------------------------------------------------------------------------
# C3: read-time duration normalization (legacy max_call_duration)
# ---------------------------------------------------------------------------


def test_validate_pinned_duration_accepts_legacy_key():
    assert worker._validate_pinned_call_duration({"max_call_duration": 600}) == 600
    assert worker._validate_pinned_call_duration({"max_call_duration": "300"}) == 300


def test_validate_pinned_duration_canonical_wins_over_legacy():
    assert (
        worker._validate_pinned_call_duration(
            {"max_call_duration_seconds": 120, "max_call_duration": 600}
        )
        == 120
    )


def test_validate_pinned_duration_rejects_invalid_legacy():
    with pytest.raises(worker.InvalidCallDurationError):
        worker._validate_pinned_call_duration({"max_call_duration": 15})
    with pytest.raises(worker.InvalidCallDurationError):
        worker._validate_pinned_call_duration({"max_call_duration": "not-a-number"})


def test_validate_pinned_duration_fails_closed_when_both_missing():
    with pytest.raises(worker.InvalidCallDurationError):
        worker._validate_pinned_call_duration({})


# ---------------------------------------------------------------------------
# C2: bounded caller-disconnect cleanup
# ---------------------------------------------------------------------------


class _FakeParticipant:
    def __init__(self, identity, kind):
        self.identity = identity
        self.kind = kind


class _FakeRoom:
    def __init__(self, *participants):
        self.remote_participants = {p.identity: p for p in participants}
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


class _FakeAgent:
    def __init__(self):
        self._shutdown_task = None
        self.scheduled = []

    def _schedule_shutdown(self, reason, *, delay=0.0):
        self.scheduled.append((reason, delay))


SIP = "ParticipantKind.SIP"
AGENT = "ParticipantKind.AGENT"
EGRESS = "ParticipantKind.EGRESS"


@pytest.mark.asyncio
async def test_last_caller_disconnect_schedules_shutdown():
    agent = _FakeAgent()
    room = _FakeRoom()
    worker._register_caller_disconnect_handler(agent, room)
    room.handlers["participant_disconnected"](_FakeParticipant("caller1", SIP))
    await asyncio.sleep(worker.CALLER_DISCONNECT_SHUTDOWN_DELAY_SECONDS + 0.2)
    assert agent.scheduled == [("caller_disconnected", 0.0)]


@pytest.mark.asyncio
async def test_disconnect_with_remaining_caller_does_nothing():
    agent = _FakeAgent()
    room = _FakeRoom(_FakeParticipant("caller2", SIP))
    worker._register_caller_disconnect_handler(agent, room)
    room.handlers["participant_disconnected"](_FakeParticipant("caller1", SIP))
    await asyncio.sleep(0.1)
    assert agent.scheduled == []


@pytest.mark.asyncio
async def test_agent_and_egress_disconnects_are_ignored():
    agent = _FakeAgent()
    room = _FakeRoom()
    worker._register_caller_disconnect_handler(agent, room)
    room.handlers["participant_disconnected"](_FakeParticipant("agent", AGENT))
    room.handlers["participant_disconnected"](_FakeParticipant("eg1", EGRESS))
    await asyncio.sleep(0.1)
    assert agent.scheduled == []


@pytest.mark.asyncio
async def test_duplicate_disconnect_events_create_one_cleanup():
    agent = _FakeAgent()
    room = _FakeRoom()
    worker._register_caller_disconnect_handler(agent, room)
    room.handlers["participant_disconnected"](_FakeParticipant("caller1", SIP))
    room.handlers["participant_disconnected"](_FakeParticipant("caller1", SIP))
    await asyncio.sleep(worker.CALLER_DISCONNECT_SHUTDOWN_DELAY_SECONDS + 0.2)
    assert agent.scheduled == [("caller_disconnected", 0.0)]


@pytest.mark.asyncio
async def test_rejoin_cancels_pending_cleanup():
    agent = _FakeAgent()
    room = _FakeRoom()
    worker._register_caller_disconnect_handler(agent, room)
    room.handlers["participant_disconnected"](_FakeParticipant("caller1", SIP))
    # A new caller rejoins before the bounded delay expires.
    room.remote_participants["caller2"] = _FakeParticipant("caller2", SIP)
    await asyncio.sleep(worker.CALLER_DISCONNECT_SHUTDOWN_DELAY_SECONDS + 0.2)
    assert agent.scheduled == []


@pytest.mark.asyncio
async def test_existing_shutdown_not_superseded_by_disconnect():
    agent = _FakeAgent()
    room = _FakeRoom()

    class _DoneTask:
        def done(self):
            return True

    agent._shutdown_task = _DoneTask()
    worker._register_caller_disconnect_handler(agent, room)
    room.handlers["participant_disconnected"](_FakeParticipant("caller1", SIP))
    await asyncio.sleep(0.1)
    assert agent.scheduled == []
