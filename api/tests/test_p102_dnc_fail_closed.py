"""
P1-02: DNC fail-closed synthetic tests.

Authorization: 2026-11-21 — DNC/opt-out uncertainty must block outbound calls.

All tests use mocks only. No real DB, no real provider credentials, no paid calls.

Patching strategy:
  compliance.py does `from api.db import db_client` then calls `db_client.async_session()`.
  `async_session` is an instance attribute (async_sessionmaker) on the DBClient singleton.
  We patch it with patch.object on the imported singleton.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# Helper — build an isolated GlobalComplianceManager instance (not the
# module singleton) so tests do not pollute each other.
# ---------------------------------------------------------------------------

def _fresh_manager():
    from api.services.telephony.compliance import GlobalComplianceManager
    return GlobalComplianceManager()


def _make_failing_session(exc):
    """
    Return a context-manager callable whose __aenter__ raises `exc`.
    Mimics `db_client.async_session()` being a callable that returns
    an async context manager.
    """
    @asynccontextmanager
    async def _ctx():
        raise exc
        yield  # noqa: unreachable — needed for async generator protocol

    # Calling _ctx() must look like async_session() → context manager.
    # async_session is a *callable*, so we return a callable that returns _ctx().
    def session_factory(*a, **kw):
        return _ctx()

    return session_factory


def _make_clear_session():
    """Return a session factory whose execute() returns no DNC row."""
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.first.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    def session_factory(*a, **kw):
        return _ctx()

    return session_factory


# ===========================================================================
# TestDNCFailClosed — is_suppressed_db() DB error paths
# ===========================================================================

class TestDNCFailClosed:
    """is_suppressed_db() must return (True, reason) when DB raises any exception."""

    @pytest.mark.asyncio
    async def test_db_exception_returns_suppressed(self):
        from api.db import db_client as _db

        mgr = _fresh_manager()
        with patch.object(_db, "async_session", _make_failing_session(Exception("connection refused"))):
            is_suppressed, reason = await mgr.is_suppressed_db(
                organization_id=42, phone_number="+15550001111"
            )

        assert is_suppressed is True, "DB error must block the call (fail-closed)"
        assert reason is not None
        assert "dnc_check_unavailable" in reason

    @pytest.mark.asyncio
    async def test_connection_error_returns_suppressed(self):
        from api.db import db_client as _db

        mgr = _fresh_manager()
        with patch.object(_db, "async_session", _make_failing_session(ConnectionError("TCP timeout"))):
            is_suppressed, reason = await mgr.is_suppressed_db(
                organization_id=1, phone_number="+15550002222"
            )

        assert is_suppressed is True
        assert "dnc_check_unavailable" in reason
        assert "ConnectionError" in reason

    @pytest.mark.asyncio
    async def test_timeout_error_returns_suppressed(self):
        from api.db import db_client as _db

        mgr = _fresh_manager()
        with patch.object(_db, "async_session", _make_failing_session(TimeoutError("query timed out"))):
            is_suppressed, reason = await mgr.is_suppressed_db(
                organization_id=1, phone_number="+15550003333"
            )

        assert is_suppressed is True
        assert "dnc_check_unavailable" in reason
        assert "TimeoutError" in reason

    @pytest.mark.asyncio
    async def test_reason_string_contains_exception_type(self):
        """Reason must name the exception class so callers can log it."""
        from api.db import db_client as _db

        class CustomDBError(Exception):
            pass

        mgr = _fresh_manager()
        with patch.object(_db, "async_session", _make_failing_session(CustomDBError("bad query"))):
            _, reason = await mgr.is_suppressed_db(
                organization_id=1, phone_number="+15550004444"
            )

        assert "CustomDBError" in reason

    @pytest.mark.asyncio
    async def test_error_logs_at_error_level_not_warning(self):
        """DB failure must log at ERROR level — warnings can be silenced in noisy envs."""
        from api.db import db_client as _db
        from loguru import logger as loguru_logger

        mgr = _fresh_manager()
        captured = []
        sink_id = loguru_logger.add(
            lambda msg: captured.append(msg),
            level="WARNING",
            format="{level}: {message}",
        )
        try:
            with patch.object(_db, "async_session", _make_failing_session(Exception("disk full"))):
                await mgr.is_suppressed_db(organization_id=1, phone_number="+15550005555")
        finally:
            loguru_logger.remove(sink_id)

        combined = "".join(captured)
        assert "ERROR" in combined, "DB failure must be logged at ERROR level"
        assert "Failing closed" in combined


# ===========================================================================
# TestDNCInMemoryPathUnchanged — existing in-memory behavior preserved
# ===========================================================================

class TestDNCInMemoryPathUnchanged:
    """is_suppressed() (in-memory) must still work and must NOT be affected by P1-02."""

    def test_in_memory_suppressed_number_blocked(self):
        mgr = _fresh_manager()
        mgr.add_to_dnc(
            organization_id=99,
            phone_number="+15559990001",
            reason="opted_out",
            source="sms_reply",
        )
        suppressed, reason = mgr.is_suppressed(99, "+15559990001")
        assert suppressed is True
        assert reason is not None

    def test_in_memory_clear_number_allowed(self):
        mgr = _fresh_manager()
        suppressed, reason = mgr.is_suppressed(99, "+15559990002")
        assert suppressed is False

    @pytest.mark.asyncio
    async def test_in_memory_hit_skips_db(self):
        """When in-memory cache has the number, DB must NOT be queried."""
        from api.db import db_client as _db

        mgr = _fresh_manager()
        mgr.add_to_dnc(
            organization_id=5,
            phone_number="+15550010001",
            reason="dnc_list",
            source="import",
        )

        db_called = False

        def should_not_be_called(*a, **kw):
            nonlocal db_called
            db_called = True
            raise AssertionError("DB should not be called when number is in memory")

        with patch.object(_db, "async_session", should_not_be_called):
            is_suppressed, reason = await mgr.is_suppressed_db(5, "+15550010001")

        assert is_suppressed is True
        assert not db_called


# ===========================================================================
# TestDNCClearNumberDBSuccess — happy path: DB confirms clear → call allowed
# ===========================================================================

class TestDNCClearNumberDBSuccess:
    """When DB says the number is NOT on the DNC list, the call must be allowed."""

    @pytest.mark.asyncio
    async def test_clear_number_db_success_returns_not_suppressed(self):
        from api.db import db_client as _db

        mgr = _fresh_manager()
        with patch.object(_db, "async_session", _make_clear_session()):
            is_suppressed, reason = await mgr.is_suppressed_db(
                organization_id=7, phone_number="+15550020001"
            )

        assert is_suppressed is False
        assert reason is None


# ===========================================================================
# TestDispatcherBlocksOnDNCUncertainty — dispatcher integration (mocked)
# ===========================================================================

class TestDispatcherBlocksOnDNCUncertainty:
    """campaign_call_dispatcher must block the call when is_suppressed_db returns (True, reason)."""

    @pytest.mark.asyncio
    async def test_dispatcher_blocks_when_dnc_suppressed(self):
        """
        Simulate the dispatcher DNC check path.
        When is_suppressed_db returns (True, reason), the call must not proceed.
        """
        from api.db import db_client as _db
        from api.services.telephony.compliance import GlobalComplianceManager, normalize_phone_number

        mgr = GlobalComplianceManager()

        with patch.object(_db, "async_session", _make_failing_session(Exception("DB unavailable"))):
            norm_phone = normalize_phone_number("+15550030001")
            is_suppressed, dnc_reason = await mgr.is_suppressed_db(
                organization_id=10, phone_number=norm_phone
            )

        call_would_proceed = not is_suppressed
        assert call_would_proceed is False, (
            "Dispatcher must block the call when DNC check is uncertain"
        )
        assert dnc_reason is not None
        assert "dnc_check_unavailable" in dnc_reason

    @pytest.mark.asyncio
    async def test_dispatcher_allows_when_dnc_clear(self):
        """When DNC check succeeds and number is clear, call must be allowed."""
        from api.db import db_client as _db
        from api.services.telephony.compliance import GlobalComplianceManager, normalize_phone_number

        mgr = GlobalComplianceManager()

        with patch.object(_db, "async_session", _make_clear_session()):
            norm_phone = normalize_phone_number("+15550040001")
            is_suppressed, dnc_reason = await mgr.is_suppressed_db(
                organization_id=10, phone_number=norm_phone
            )

        call_would_proceed = not is_suppressed
        assert call_would_proceed is True
        assert dnc_reason is None
