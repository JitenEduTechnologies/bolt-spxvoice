"""P1-01 acceptance tests (reconstructed, iteration 5.1)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from api.services.campaign.circuit_breaker import BreakerState


class TestRedisConstructionFailures:
    @pytest.mark.asyncio
    async def test_get_redis_raising_returns_none(self):
        import redis.asyncio as aioredis

        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        with patch.object(
            aioredis, "from_url", AsyncMock(side_effect=ValueError("bad url"))
        ):
            client = await cb._get_redis()
        assert client is None

    @pytest.mark.asyncio
    async def test_is_open_unknown_when_get_redis_returns_none(self):
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        with patch.object(cb, "_get_redis", AsyncMock(return_value=None)):
            state, stats = await cb.is_circuit_open(campaign_id=1)
        assert state == BreakerState.UNKNOWN
        assert stats is None

    @pytest.mark.asyncio
    async def test_record_unknown_when_get_redis_returns_none(self):
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        with patch.object(cb, "_get_redis", AsyncMock(return_value=None)):
            state, stats = await cb.record_call_outcome(campaign_id=1, is_failure=True)
        assert state == BreakerState.UNKNOWN

    @pytest.mark.asyncio
    async def test_reset_false_when_get_redis_returns_none(self):
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        with patch.object(cb, "_get_redis", AsyncMock(return_value=None)):
            ok = await cb.reset(1)
        assert ok is False

    @pytest.mark.asyncio
    async def test_bookkeeping_safe_when_get_redis_returns_none(self):
        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        with patch.object(cb, "_get_redis", AsyncMock(return_value=None)):
            await cb._push_recent_failure(1, 2, "reason")
            recent = await cb._get_recent_failures(1)
        assert recent == []


class TestBreakerErrorRedaction:
    @pytest.mark.asyncio
    async def test_no_credential_leak_on_construction_error(self, caplog):
        import redis.asyncio as aioredis

        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        secret = "redis://:supersecretvalue123@host:6379"
        with patch.object(
            aioredis, "from_url", AsyncMock(side_effect=ValueError(secret))
        ), caplog.at_level("ERROR"):
            await cb._get_redis()
        assert "supersecretvalue123" not in caplog.text

    @pytest.mark.asyncio
    async def test_no_credential_leak_on_eval_error(self, caplog):
        import redis.exceptions as rex

        from api.services.campaign.circuit_breaker import CircuitBreaker

        cb = CircuitBreaker()
        mock_redis = AsyncMock()
        mock_redis.eval = AsyncMock(
            side_effect=rex.RedisError("auth with password supersecretvalue123 failed")
        )
        cb.redis_client = mock_redis
        with caplog.at_level("ERROR"):
            state, _ = await cb.record_call_outcome(campaign_id=1, is_failure=True)
        assert state == BreakerState.UNKNOWN
        assert "supersecretvalue123" not in caplog.text


class TestResumeBlockedEvents:
    @pytest.mark.asyncio
    async def test_unknown_resume_appends_blocked_event(self):
        from api.services.campaign.runner import CampaignRunnerService

        campaign = MagicMock()
        campaign.id = 7
        campaign.state = "paused"
        with (
            patch("api.services.campaign.runner.db_client") as mock_db,
            patch("api.services.campaign.runner.circuit_breaker") as mock_cb,
        ):
            mock_db.get_campaign_by_id = AsyncMock(return_value=campaign)
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_cb.is_circuit_open = AsyncMock(
                return_value=(BreakerState.UNKNOWN, None)
            )
            with pytest.raises(ValueError):
                await CampaignRunnerService().resume_campaign(7)
            kw = mock_db.append_campaign_log.call_args.kwargs
            assert kw["event"] == "circuit_breaker_resume_blocked"
            mock_db.update_campaign.assert_not_called()

    @pytest.mark.asyncio
    async def test_reset_failure_appends_blocked_event(self):
        from api.services.campaign.runner import CampaignRunnerService

        campaign = MagicMock()
        campaign.id = 7
        campaign.state = "paused"
        with (
            patch("api.services.campaign.runner.db_client") as mock_db,
            patch("api.services.campaign.runner.circuit_breaker") as mock_cb,
        ):
            mock_db.get_campaign_by_id = AsyncMock(return_value=campaign)
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_cb.is_circuit_open = AsyncMock(
                return_value=(BreakerState.CLOSED, None)
            )
            mock_cb.reset = AsyncMock(return_value=False)
            with pytest.raises(ValueError):
                await CampaignRunnerService().resume_campaign(7)
            kw = mock_db.append_campaign_log.call_args.kwargs
            assert kw["event"] == "circuit_breaker_resume_blocked"
            mock_db.update_campaign.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_reset_then_resume_sets_running_once(self):
        from api.services.campaign.runner import CampaignRunnerService

        campaign = MagicMock()
        campaign.id = 7
        campaign.state = "paused"
        with (
            patch("api.services.campaign.runner.db_client") as mock_db,
            patch("api.services.campaign.runner.circuit_breaker") as mock_cb,
        ):
            mock_db.get_campaign_by_id = AsyncMock(return_value=campaign)
            mock_db.update_campaign = AsyncMock()
            mock_db.append_campaign_log = AsyncMock()
            mock_cb.is_circuit_open = AsyncMock(
                return_value=(BreakerState.CLOSED, None)
            )
            mock_cb.reset = AsyncMock(return_value=True)
            await CampaignRunnerService().resume_campaign(7)
            mock_db.update_campaign.assert_called_once_with(
                campaign_id=7, state="running"
            )


class TestPerRecipientRaceGate:
    @pytest.mark.asyncio
    async def test_race_change_to_unknown_blocks_everything(self):
        from api.services.campaign import campaign_call_dispatcher as ccd

        campaign = MagicMock()
        campaign.id = 5
        campaign.state = "running"
        campaign.orchestrator_metadata = {}
        campaign.telephony_configuration_id = None
        qr = MagicMock()
        qr.id = 11
        qr.context_variables = {"phone_number": "+919876543210"}

        states = iter([(BreakerState.CLOSED, None), (BreakerState.UNKNOWN, None)])
        mock_breaker = AsyncMock(side_effect=lambda *a, **k: next(states))

        mock_provider = MagicMock()
        mock_provider.from_numbers = []
        mock_provider.initiate_call = AsyncMock()
        mock_release_slot = AsyncMock()
        mock_requeue = AsyncMock()
        mock_pause = AsyncMock()
        mock_log = AsyncMock()
        mock_create_run = AsyncMock()
        mock_get_workflow = AsyncMock()
        mock_acq_number = AsyncMock()
        mock_pool_init = AsyncMock()

        with (
            patch.object(ccd.db_client, "get_campaign_by_id", AsyncMock(return_value=campaign)),
            patch.object(
                ccd.db_client,
                "claim_queued_runs_for_processing",
                AsyncMock(return_value=[qr]),
            ),
            patch.object(ccd.db_client, "get_queued_run_by_id", AsyncMock(return_value=None)),
            patch.object(ccd.db_client, "renew_queued_run_lease", AsyncMock()),
            patch.object(ccd.db_client, "update_queued_run", mock_requeue),
            patch.object(ccd.db_client, "update_campaign", mock_pause),
            patch.object(ccd.db_client, "append_campaign_log", mock_log),
            patch.object(ccd.db_client, "create_workflow_run", mock_create_run),
            patch.object(ccd.db_client, "get_workflow_by_id", mock_get_workflow),
            patch.object(ccd.circuit_breaker, "is_circuit_open", mock_breaker),
            patch.object(
                ccd.campaign_call_dispatcher,
                "get_provider_for_campaign",
                AsyncMock(return_value=mock_provider),
            ),
            patch.object(
                ccd.campaign_call_dispatcher,
                "acquire_concurrent_slot",
                AsyncMock(return_value="slot-1"),
            ),
            patch.object(ccd.campaign_call_dispatcher, "acquire_from_number", mock_acq_number),
            patch.object(ccd.campaign_call_dispatcher, "apply_rate_limit", AsyncMock()),
            patch.object(ccd.rate_limiter, "release_concurrent_slot", mock_release_slot),
            patch.object(ccd.rate_limiter, "initialize_from_number_pool", mock_pool_init),
            patch.object(ccd.rate_limiter, "acquire_from_number", AsyncMock()),
        ):
            result = await ccd.campaign_call_dispatcher.process_batch(5, 10)

            assert result == 0
            assert mock_breaker.call_count == 2
            mock_create_run.assert_not_called()
            mock_get_workflow.assert_not_called()
            mock_acq_number.assert_not_called()
            mock_pool_init.assert_not_called()
            mock_provider.initiate_call.assert_not_called()
            mock_release_slot.assert_called_once()
            requeue_kw = mock_requeue.call_args.kwargs
            assert requeue_kw["state"] == "queued"
            assert "scheduled_for" in requeue_kw
            mock_pause.assert_called_with(campaign_id=5, state="paused")
            assert mock_log.call_args.kwargs.get("event") == "circuit_breaker_unknown"


class TestPerRecipientFaultIsolation:
    async def _run_blocked(self, ccd, fail: str):
        campaign = MagicMock()
        campaign.id = 5
        campaign.state = "running"
        campaign.orchestrator_metadata = {}
        qr = MagicMock()
        qr.id = 11
        qr.context_variables = {"phone_number": "+919876543210"}

        states = iter([(BreakerState.CLOSED, None), (BreakerState.UNKNOWN, None)])
        mock_breaker = AsyncMock(side_effect=lambda *a, **k: next(states))

        mock_provider = MagicMock()
        mock_provider.from_numbers = []
        mock_provider.initiate_call = AsyncMock()
        mock_release = AsyncMock(side_effect=Exception("boom") if fail == "slot" else None)
        mock_requeue = AsyncMock(side_effect=Exception("boom") if fail == "requeue" else None)
        mock_pause = AsyncMock(side_effect=Exception("boom") if fail == "pause" else None)
        mock_log = AsyncMock(side_effect=Exception("boom") if fail == "event" else None)
        mock_create = AsyncMock()
        mock_get_wf = AsyncMock()
        mock_acq_num = AsyncMock()
        mock_pool = AsyncMock()

        with (
            patch.object(ccd.db_client, "get_campaign_by_id", AsyncMock(return_value=campaign)),
            patch.object(
                ccd.db_client,
                "claim_queued_runs_for_processing",
                AsyncMock(return_value=[qr]),
            ),
            patch.object(ccd.db_client, "get_queued_run_by_id", AsyncMock(return_value=None)),
            patch.object(ccd.db_client, "renew_queued_run_lease", AsyncMock()),
            patch.object(ccd.db_client, "update_queued_run", mock_requeue),
            patch.object(ccd.db_client, "update_campaign", mock_pause),
            patch.object(ccd.db_client, "append_campaign_log", mock_log),
            patch.object(ccd.db_client, "create_workflow_run", mock_create),
            patch.object(ccd.db_client, "get_workflow_by_id", mock_get_wf),
            patch.object(ccd.circuit_breaker, "is_circuit_open", mock_breaker),
            patch.object(
                ccd.campaign_call_dispatcher,
                "get_provider_for_campaign",
                AsyncMock(return_value=mock_provider),
            ),
            patch.object(
                ccd.campaign_call_dispatcher,
                "acquire_concurrent_slot",
                AsyncMock(return_value="slot-1"),
            ),
            patch.object(ccd.campaign_call_dispatcher, "acquire_from_number", mock_acq_num),
            patch.object(ccd.campaign_call_dispatcher, "apply_rate_limit", AsyncMock()),
            patch.object(ccd.rate_limiter, "release_concurrent_slot", mock_release),
            patch.object(ccd.rate_limiter, "initialize_from_number_pool", mock_pool),
            patch.object(ccd.rate_limiter, "acquire_from_number", AsyncMock()),
        ):
            result = await ccd.campaign_call_dispatcher.process_batch(5, 10)

        assert result == 0
        assert mock_breaker.call_count == 2
        mock_create.assert_not_called()
        mock_get_wf.assert_not_called()
        mock_acq_num.assert_not_called()
        mock_pool.assert_not_called()
        mock_provider.initiate_call.assert_not_called()
        return mock_release, mock_requeue, mock_pause, mock_log

    @pytest.mark.asyncio
    async def test_slot_release_failure_still_pauses_and_events(self):
        from api.services.campaign import campaign_call_dispatcher as ccd

        _, _, mock_pause, mock_log = await self._run_blocked(ccd, "slot")
        assert mock_pause.called
        assert mock_log.call_args.kwargs["details"]["slot_released"] is False
        assert mock_log.call_args.kwargs["details"]["pause_persisted"] is True

    @pytest.mark.asyncio
    async def test_requeue_failure_still_pauses_and_events(self):
        from api.services.campaign import campaign_call_dispatcher as ccd

        _, _, mock_pause, mock_log = await self._run_blocked(ccd, "requeue")
        assert mock_pause.called
        assert mock_log.call_args.kwargs["details"]["row_requeued"] is False

    @pytest.mark.asyncio
    async def test_pause_failure_event_reports_false(self):
        from api.services.campaign import campaign_call_dispatcher as ccd

        _, _, _, mock_log = await self._run_blocked(ccd, "pause")
        assert mock_log.call_args.kwargs["details"]["pause_persisted"] is False

    @pytest.mark.asyncio
    async def test_event_failure_still_no_dispatch_and_critical_log(self):
        from loguru import logger as loguru_logger

        from api.services.campaign import campaign_call_dispatcher as ccd

        captured: list[str] = []
        sink_id = loguru_logger.add(lambda msg: captured.append(str(msg)), level="ERROR")
        try:
            mock_release, mock_requeue, mock_pause, mock_log = await self._run_blocked(
                ccd, "event"
            )
            assert mock_log.call_count == 1
            assert mock_release.called
            assert mock_requeue.called
            assert mock_pause.called
            assert any("CRITICAL" in msg for msg in captured), (
                f"Expected a log message containing 'CRITICAL' but got: {captured}"
            )
        finally:
            loguru_logger.remove(sink_id)


class TestPartialRequeueFailureFailClosed:
    @pytest.mark.asyncio
    async def test_first_row_requeue_fails_second_succeeds(self):
        from api.services.campaign import campaign_call_dispatcher as ccd

        campaign = MagicMock()
        campaign.id = 5
        campaign.state = "running"
        campaign.orchestrator_metadata = {}
        qr1, qr2 = MagicMock(), MagicMock()
        qr1.id, qr2.id = 11, 12

        mock_requeue = AsyncMock(side_effect=[Exception("db down"), None])
        mock_pause = AsyncMock()
        mock_log = AsyncMock()
        mock_create = AsyncMock()
        mock_pool = AsyncMock()

        with (
            patch.object(ccd.db_client, "get_campaign_by_id", AsyncMock(return_value=campaign)),
            patch.object(
                ccd.db_client,
                "claim_queued_runs_for_processing",
                AsyncMock(return_value=[qr1, qr2]),
            ),
            patch.object(ccd.db_client, "update_queued_run", mock_requeue),
            patch.object(ccd.db_client, "update_campaign", mock_pause),
            patch.object(ccd.db_client, "append_campaign_log", mock_log),
            patch.object(ccd.db_client, "create_workflow_run", mock_create),
            patch.object(
                ccd.circuit_breaker,
                "is_circuit_open",
                AsyncMock(return_value=(BreakerState.UNKNOWN, None)),
            ),
            patch.object(ccd.rate_limiter, "initialize_from_number_pool", mock_pool),
        ):
            result = await ccd.campaign_call_dispatcher.process_batch(5, 10)

            assert result == 0
            assert mock_requeue.call_count == 2
            assert mock_pause.called
            assert mock_log.called
            details = mock_log.call_args.kwargs["details"]
            assert details["requeued_failed"] == [11]
            assert details["requeued_ok"] == [12]
            assert details["pause_persisted"] is True
            mock_create.assert_not_called()
            assert not mock_pool.called


class TestStaleProcessingRecovery:
    @pytest.mark.asyncio
    async def test_requeue_failure_row_recovered_after_lease_expiry(self):
        import os
        import uuid
        from datetime import UTC, datetime, timedelta

        from api.db import db_client
        from api.db.models import CampaignModel, OrganizationModel, QueuedRunModel, UserModel, WorkflowModel

        assert "spx_campaign_test" in os.environ.get("DATABASE_URL", ""), (
            "recovery test requires the explicitly disposable test database"
        )
        token = uuid.uuid4().hex[:12]

        # Create minimal FK prerequisite rows; fresh isolated DB has no seed data.
        async with db_client.async_session() as s:
            org = OrganizationModel(provider_id=f"p1-test-org-{token}")
            s.add(org)
            await s.flush()
            user = UserModel(
                provider_id=f"p1-test-user-{token}",
                email=f"p1-test-{token}@test.local",
            )
            s.add(user)
            await s.flush()
            workflow = WorkflowModel(
                name=f"p1-test-workflow-{token}",
                user_id=user.id,
                organization_id=org.id,
                workflow_definition={},
            )
            s.add(workflow)
            await s.flush()
            oid, uid, wid = org.id, user.id, workflow.id
            await s.commit()

        async with db_client.async_session() as s:
            campaign = CampaignModel(
                name=f"P1-RECOVERY-{token}",
                workflow_id=wid,
                source_type="csv",
                source_id=token,
                created_by=uid,
                organization_id=oid,
                state="paused",
                max_retries=2,
            )
            s.add(campaign)
            await s.flush()
            stale_run = QueuedRunModel(
                campaign_id=campaign.id,
                source_uuid=f"p1-stale-{token}",
                context_variables={
                    "_lease": {
                        "claimed_at": (
                            datetime.now(UTC) - timedelta(seconds=600)
                        ).isoformat(),
                        "lease_duration_sec": 60,
                    }
                },
                state="processing",
                retry_count=0,
            )
            s.add(stale_run)
            await s.flush()
            cid, rid = campaign.id, stale_run.id
            await s.commit()

        try:
            result = await db_client.recover_stale_processing_runs(
                stale_threshold_seconds=300, batch_limit=50
            )
            assert rid in result["recovered_ids"]

            async with db_client.async_session() as s:
                row = await s.get(QueuedRunModel, rid)
                assert row.state == "queued"
                assert row.retry_count == 1
                assert row.retry_reason == "worker_lease_expired_recovered"
                assert row.scheduled_for is not None
                camp = await s.get(CampaignModel, cid)
                assert camp.state == "paused"
        finally:
            async with db_client.async_session() as s:
                row = await s.get(QueuedRunModel, rid)
                if row:
                    await s.delete(row)
                camp = await s.get(CampaignModel, cid)
                if camp:
                    await s.delete(camp)
                await s.commit()
            # Clean up prerequisite rows (FK order: workflow → user/org)
            async with db_client.async_session() as s:
                wf = await s.get(WorkflowModel, wid)
                if wf:
                    await s.delete(wf)
                await s.commit()
            async with db_client.async_session() as s:
                u = await s.get(UserModel, uid)
                if u:
                    await s.delete(u)
                o = await s.get(OrganizationModel, oid)
                if o:
                    await s.delete(o)
                await s.commit()


class TestRealRedisInvalidAuthIntegration:
    ALLOWED_HOST = "127.0.0.1"
    ALLOWED_PORT = 6382  # updated: 6381 occupied by gate3b; new isolated instance at 6382
    ALLOWED_DB = "/15"

    def _urls(self):
        import os
        from urllib.parse import urlsplit, urlunsplit

        raw = os.environ.get("P1_TEST_REDIS_URL")
        if not raw:
            pytest.skip("P1_TEST_REDIS_URL not set (dedicated disposable Redis)")
        parts = urlsplit(raw)
        assert parts.scheme == "redis"
        assert parts.hostname == self.ALLOWED_HOST
        assert parts.port == self.ALLOWED_PORT
        assert parts.path == self.ALLOWED_DB
        wrong = urlunsplit(
            ("redis", f":definitely-wrong-password@{parts.hostname}:{parts.port}", parts.path, "", "")
        )
        return raw, wrong

    @pytest.mark.asyncio
    async def test_wrong_password_yields_unknown_then_recovery_closed(self):
        import uuid

        import redis.asyncio as aioredis

        from api.services.campaign.circuit_breaker import CircuitBreaker

        correct, wrong = self._urls()
        key_campaign = 900000 + (uuid.uuid4().int % 99999)
        cb = CircuitBreaker()
        try:
            health = await aioredis.from_url(
                correct, decode_responses=True, socket_connect_timeout=2
            )
            await health.ping()
            await health.aclose()
        except Exception:
            await cb.close()
            pytest.skip("owned disposable Redis not available")

        try:
            cb.redis_client = await aioredis.from_url(
                wrong, decode_responses=True, socket_connect_timeout=2
            )
            state, stats = await cb.is_circuit_open(campaign_id=key_campaign)
            assert state == BreakerState.UNKNOWN
            assert stats is None
            await cb.close()
            cb.redis_client = None

            cb.redis_client = await aioredis.from_url(
                correct, decode_responses=True, socket_connect_timeout=2
            )
            await cb.reset(key_campaign)
            state2, _ = await cb.is_circuit_open(campaign_id=key_campaign)
            assert state2 == BreakerState.CLOSED
        finally:
            if cb.redis_client is not None:
                await cb.reset(key_campaign)
                await cb.close()
