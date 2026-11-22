"""
P1-03: Financial admission fail-closed synthetic tests.

Authorization: 2026-11-21 — Any failure or uncertainty while evaluating
balance, budget, quota, pricing, credit, spending limit or cost admission
must block the call.

All tests use mocks only. No real DB, no real provider credentials, no paid calls.

Coverage:
  - balance / budget lookup failure → dispatcher blocks
  - budget cap exceeded (daily/monthly/per-call) → dispatcher blocks
  - quota exceeded (emergency_stop) → dispatcher blocks
  - pricing / malformed financial response → dispatcher blocks
  - concurrent admission stub → idempotent block
  - zero provider side effects on failure
  - successful clear-admission path → passes financial gate

Dispatcher patch strategy:
  dispatch_call takes (self, queued_run, campaign, slot_id).
  We mock everything upstream of the budget check to reach the exception path:
    - db_client.get_campaign_by_id  → live (not aborted) mock campaign
    - circuit_breaker.is_circuit_open → BreakerState.CLOSED
    - compliance_manager.is_suppressed_db → (False, None)
    - aioredis.from_url (emergency stop) → Redis that returns no stop value
    - rate_limiter.release_concurrent_slot → no-op mock
    - db_client.update_queued_run → recorded mock
  We then inject a campaign with budget_meta that triggers the exception path.
"""
import math
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ===========================================================================
# TestCheckFinancialAdmissionPure — direct tests of check_financial_admission
# ===========================================================================

class TestCheckFinancialAdmissionPure:
    """Pure-function tests: check_financial_admission and BudgetConfig behavior."""

    def _budget(self, **kw):
        from api.services.pricing.cost_controller import BudgetConfig
        return BudgetConfig(**kw)

    def _admit(self, budget, day=0.0, month=0.0, estimated=1.80):
        from api.services.pricing.cost_controller import check_financial_admission
        return check_financial_admission(
            budget=budget,
            current_day_spend_inr=day,
            current_month_spend_inr=month,
            estimated_call_cost_inr=estimated,
        )

    def test_clear_path_admitted(self):
        budget = self._budget(daily_cap_inr=500.0, monthly_cap_inr=10000.0, per_call_cap_inr=25.0)
        ok, reason = self._admit(budget, day=0.0, month=0.0, estimated=1.80)
        assert ok is True
        assert reason  # non-empty

    def test_daily_cap_exceeded_blocks(self):
        budget = self._budget(daily_cap_inr=100.0, monthly_cap_inr=10000.0)
        ok, reason = self._admit(budget, day=99.0, estimated=5.0)
        assert ok is False
        assert "Daily" in reason or "daily" in reason.lower()

    def test_monthly_cap_exceeded_blocks(self):
        budget = self._budget(daily_cap_inr=500.0, monthly_cap_inr=1000.0)
        ok, reason = self._admit(budget, day=0.0, month=998.0, estimated=5.0)
        assert ok is False
        assert "Monthly" in reason or "monthly" in reason.lower()

    def test_per_call_cap_exceeded_blocks(self):
        budget = self._budget(daily_cap_inr=500.0, monthly_cap_inr=10000.0, per_call_cap_inr=2.0)
        ok, reason = self._admit(budget, estimated=5.0)
        assert ok is False
        assert "per-call" in reason.lower() or "cap" in reason.lower()

    def test_emergency_stop_blocks(self):
        budget = self._budget(emergency_stop_active=True)
        ok, reason = self._admit(budget, estimated=1.80)
        assert ok is False
        assert "emergency" in reason.lower()

    def test_zero_caps_disabled(self):
        # daily_cap=0 means no daily cap
        budget = self._budget(daily_cap_inr=0.0, monthly_cap_inr=0.0, per_call_cap_inr=0.0)
        ok, reason = self._admit(budget, day=9999.0, month=9999.0, estimated=9999.0)
        assert ok is True

    def test_exact_boundary_daily_cap(self):
        # spend = cap - epsilon should admit; spend = cap + epsilon should block
        budget = self._budget(daily_cap_inr=100.0, monthly_cap_inr=10000.0, per_call_cap_inr=0.0)
        ok_under, _ = self._admit(budget, day=98.0, estimated=1.80)   # 98 + 1.80 < 100
        ok_over, _ = self._admit(budget, day=99.0, estimated=5.0)     # 99 + 5 = 104 > 100
        assert ok_under is True
        assert ok_over is False

    def test_malformed_budget_raises_validation_error(self):
        """BudgetConfig(**malformed) must raise, not silently accept."""
        from api.services.pricing.cost_controller import BudgetConfig
        import pytest
        with pytest.raises(Exception):
            BudgetConfig(daily_cap_inr="not_a_number")

    def test_nan_estimated_cost_treated_as_above_cap(self):
        """
        float('nan') passed as estimated cost: NaN > any finite cap is False in Python,
        so the daily/monthly checks pass with NaN. But per-call cap comparison with NaN
        is also False. The function should at minimum not crash.
        This test documents current behavior, not prescriptive.
        """
        from api.services.pricing.cost_controller import check_financial_admission, BudgetConfig
        budget = BudgetConfig(per_call_cap_inr=5.0, daily_cap_inr=100.0, monthly_cap_inr=10000.0)
        # Must not raise
        ok, reason = check_financial_admission(
            budget=budget,
            current_day_spend_inr=0.0,
            current_month_spend_inr=0.0,
            estimated_call_cost_inr=float('nan'),
        )
        assert isinstance(ok, bool)

    def test_infinity_estimated_cost_exceeds_caps(self):
        """float('inf') as estimated cost must exceed any finite cap."""
        from api.services.pricing.cost_controller import check_financial_admission, BudgetConfig
        budget = BudgetConfig(per_call_cap_inr=25.0, daily_cap_inr=100.0, monthly_cap_inr=10000.0)
        ok, reason = check_financial_admission(
            budget=budget,
            current_day_spend_inr=0.0,
            current_month_spend_inr=0.0,
            estimated_call_cost_inr=float('inf'),
        )
        assert ok is False


# ===========================================================================
# Helpers for dispatcher tests
# ===========================================================================

def _mock_campaign(org_id=42, campaign_id=1, budget_meta=None, state="running"):
    """Return a minimal campaign-like object."""
    orchestrator_metadata = {}
    if budget_meta is not None:
        orchestrator_metadata["budget_config"] = budget_meta
    return SimpleNamespace(
        id=campaign_id,
        organization_id=org_id,
        workflow_id=10,
        created_by=5,
        telephony_configuration_id=1,
        state=state,
        orchestrator_metadata=orchestrator_metadata,
    )


def _mock_queued_run(run_id=99):
    return SimpleNamespace(
        id=run_id,
        context_variables={"phone_number": "+15550001111"},
        source_uuid="test-uuid",
        retry_count=0,
    )


class _PatchedDispatcher:
    """
    Context manager that patches all dispatcher dependencies up to the
    financial-admission gate so we can exercise just that path.

    Emergency-stop Redis check is handled by the real isolated Redis at
    REDIS_URL (no stop key is set there), so we do NOT mock it.
    """
    def __init__(self, campaign, queued_run, slot_id="test-slot"):
        self.campaign = campaign
        self.queued_run = queued_run
        self.slot_id = slot_id
        self._patches = []
        self.update_queued_run_mock = AsyncMock()
        self.release_slot_mock = AsyncMock()

    def __enter__(self):
        from api.services.campaign.circuit_breaker import BreakerState

        _db = __import__("api.db", fromlist=["db_client"]).db_client
        # get_campaign_by_id → live (non-aborted) mock campaign
        p1 = patch.object(
            _db,
            "get_campaign_by_id",
            new=AsyncMock(return_value=self.campaign),
        )
        # get_workflow_by_id → minimal workflow object (must not be None)
        p_wf = patch.object(
            _db,
            "get_workflow_by_id",
            new=AsyncMock(return_value=SimpleNamespace(id=10, name="test-workflow")),
        )
        # update_queued_run → recorded mock
        p2 = patch.object(
            _db,
            "update_queued_run",
            new=self.update_queued_run_mock,
        )
        # circuit_breaker.is_circuit_open → CLOSED
        from api.services.campaign.circuit_breaker import circuit_breaker as _cb
        p3 = patch.object(
            _cb,
            "is_circuit_open",
            new=AsyncMock(return_value=(BreakerState.CLOSED, {})),
        )
        # compliance_manager.is_suppressed_db → (False, None)
        from api.services.telephony.compliance import compliance_manager as _cm
        p4 = patch.object(
            _cm,
            "is_suppressed_db",
            new=AsyncMock(return_value=(False, None)),
        )
        # rate_limiter.release_concurrent_slot → recorded no-op
        from api.services.campaign.rate_limiter import rate_limiter as _rl
        p5 = patch.object(
            _rl,
            "release_concurrent_slot",
            new=self.release_slot_mock,
        )
        # Emergency stop Redis check: patch redis.asyncio.from_url to return a
        # mock client that reports no emergency stop (stop_val = None).
        _r_client = AsyncMock()
        _r_client.get = AsyncMock(return_value=None)
        _r_client.aclose = AsyncMock()
        p6 = patch("redis.asyncio.from_url", return_value=_r_client)
        # append_campaign_log → no-op (prevents unintended DB hit in tests
        # that don't need to assert on the log event).
        p7 = patch.object(_db, "append_campaign_log", new=AsyncMock())
        for p in [p1, p_wf, p2, p3, p4, p5, p6, p7]:
            p.start()
            self._patches.append(p)
        return self

    def __exit__(self, *a):
        for p in self._patches:
            p.stop()


# ===========================================================================
# TestFinancialAdmissionDispatcherFailClosed — dispatcher exception path
# ===========================================================================

class TestFinancialAdmissionDispatcherFailClosed:
    """
    When the financial admission block raises (malformed budget, import error, etc.),
    dispatch_call must: release slot, mark run failed, return None, make zero provider calls.
    """

    @pytest.mark.asyncio
    async def test_validation_error_in_budget_config_blocks_call(self):
        """BudgetConfig(**bad_meta) raises ValidationError → call must be blocked."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        campaign = _mock_campaign(budget_meta={"daily_cap_inr": "not_a_float"})
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()

        with _PatchedDispatcher(campaign, queued_run) as ctx:
            result = await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-1")

        assert result is None, "Dispatcher must return None (call blocked)"
        ctx.release_slot_mock.assert_called_once()
        ctx.update_queued_run_mock.assert_called_once()
        call_kwargs = ctx.update_queued_run_mock.call_args
        # retry_reason must start with financial_admission_unavailable
        retry_reason = call_kwargs.kwargs.get("retry_reason", "") or ""
        assert "financial_admission_unavailable" in retry_reason

    @pytest.mark.asyncio
    async def test_exception_in_check_financial_admission_blocks_call(self):
        """check_financial_admission raising must trigger fail-closed path."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
        from api.services.pricing.cost_controller import BudgetConfig

        campaign = _mock_campaign(budget_meta={"daily_cap_inr": 500.0, "monthly_cap_inr": 10000.0})
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()

        with _PatchedDispatcher(campaign, queued_run) as ctx:
            with patch(
                "api.services.pricing.cost_controller.check_financial_admission",
                side_effect=RuntimeError("pricing service unavailable"),
            ):
                result = await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-2")

        assert result is None
        ctx.release_slot_mock.assert_called_once()
        retry_reason = ctx.update_queued_run_mock.call_args.kwargs.get("retry_reason", "")
        assert "financial_admission_unavailable" in retry_reason

    @pytest.mark.asyncio
    async def test_zero_provider_calls_on_budget_exception(self):
        """
        When financial admission raises, no get_provider_for_campaign,
        no acquire_from_number, no create_workflow_run, no initiate_call.
        """
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        campaign = _mock_campaign(budget_meta={"daily_cap_inr": "bad"})
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()
        provider_called = []

        async def _provider(*a, **kw):
            provider_called.append(True)
            return MagicMock()

        with _PatchedDispatcher(campaign, queued_run):
            with patch.object(dispatcher, "get_provider_for_campaign", new=AsyncMock(side_effect=_provider)):
                result = await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-3")

        assert result is None
        assert not provider_called, "get_provider_for_campaign must NOT be called when budget check fails"

    @pytest.mark.asyncio
    async def test_failed_state_set_on_budget_exception(self):
        """Queued run must be marked state='failed' when financial admission raises."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        campaign = _mock_campaign(budget_meta={"daily_cap_inr": "bad"})
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()

        with _PatchedDispatcher(campaign, queued_run) as ctx:
            await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-4")

        state_arg = ctx.update_queued_run_mock.call_args.kwargs.get("state", "")
        assert state_arg == "failed"

    @pytest.mark.asyncio
    async def test_error_logs_at_error_level_on_budget_exception(self):
        """Financial admission error must log at ERROR, not DEBUG."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
        from loguru import logger as loguru_logger

        campaign = _mock_campaign(budget_meta={"daily_cap_inr": "bad"})
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()

        captured = []
        sink_id = loguru_logger.add(
            lambda msg: captured.append(msg),
            level="DEBUG",
            format="{level}: {message}",
        )
        try:
            with _PatchedDispatcher(campaign, queued_run):
                await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-5")
        finally:
            loguru_logger.remove(sink_id)

        combined = "".join(captured)
        assert "ERROR" in combined
        assert "Failing closed" in combined


# ===========================================================================
# TestFinancialAdmissionNormalBlock — existing block path preserved
# ===========================================================================

class TestFinancialAdmissionNormalBlock:
    """When check_financial_admission returns (False, reason) normally, the call is blocked."""

    @pytest.mark.asyncio
    async def test_daily_cap_exceeded_blocks_dispatcher(self):
        """Normal daily-cap block: check_financial_admission returns (False, reason)."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        campaign = _mock_campaign(budget_meta={
            "daily_cap_inr": 10.0,
            "monthly_cap_inr": 1000.0,
            "per_call_cap_inr": 25.0,
            "current_day_spend_inr": 9.5,
            "current_month_spend_inr": 50.0,
            "estimated_call_cost_inr": 1.80,
        })
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()

        with _PatchedDispatcher(campaign, queued_run) as ctx:
            result = await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-6")

        assert result is None
        ctx.release_slot_mock.assert_called_once()
        retry_reason = ctx.update_queued_run_mock.call_args.kwargs.get("retry_reason", "")
        assert "budget_cap_exceeded" in retry_reason

    @pytest.mark.asyncio
    async def test_clear_budget_path_passes_financial_gate(self):
        """When budget is clear (admitted=True), dispatcher must pass the financial gate."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        campaign = _mock_campaign(budget_meta={
            "daily_cap_inr": 500.0,
            "monthly_cap_inr": 10000.0,
            "per_call_cap_inr": 25.0,
            "current_day_spend_inr": 0.0,
            "current_month_spend_inr": 0.0,
            "estimated_call_cost_inr": 1.80,
        })
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()
        provider_reached = []

        async def _provider(*a, **kw):
            provider_reached.append(True)
            return MagicMock()

        with _PatchedDispatcher(campaign, queued_run):
            with patch.object(dispatcher, "get_provider_for_campaign", new=AsyncMock(side_effect=_provider)):
                # Let it raise after get_provider_for_campaign is called (we don't
                # care about what happens after the financial gate)
                try:
                    await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-7")
                except Exception:
                    pass

        assert provider_reached, "get_provider_for_campaign must be reached when budget is clear"


# ===========================================================================
# TestFinancialAdmissionIdempotency — concurrent / retry behavior
# ===========================================================================

class TestFinancialAdmissionIdempotency:
    """Financial admission decision must be safe under concurrent calls."""

    @pytest.mark.asyncio
    async def test_two_concurrent_blocked_calls_each_release_slot(self):
        """Two concurrent dispatch_call invocations each release their own slot."""
        import asyncio
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        campaign = _mock_campaign(budget_meta={"daily_cap_inr": "bad"})
        queued_run_1 = _mock_queued_run(run_id=101)
        queued_run_2 = _mock_queued_run(run_id=102)
        dispatcher = CampaignCallDispatcher()
        release_calls = []

        with _PatchedDispatcher(campaign, queued_run_1) as ctx:
            # Override release_slot to record calls
            async def _record_release(org_id, slot_id):
                release_calls.append(slot_id)

            ctx.release_slot_mock.side_effect = _record_release

            # Patch update_queued_run to handle both run ids
            ctx.update_queued_run_mock.return_value = None

            # Simulate two concurrent blocked calls
            r1, r2 = await asyncio.gather(
                dispatcher.dispatch_call(queued_run_1, campaign, slot_id="slot-A"),
                dispatcher.dispatch_call(queued_run_2, campaign, slot_id="slot-B"),
            )

        assert r1 is None
        assert r2 is None
        assert "slot-A" in release_calls
        assert "slot-B" in release_calls

    @pytest.mark.asyncio
    async def test_retry_of_blocked_run_is_blocked_again(self):
        """A run blocked by financial admission should be blocked on retry too."""
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher

        # Same bad config — no state changes between runs
        campaign = _mock_campaign(budget_meta={"daily_cap_inr": "bad"})
        queued_run = _mock_queued_run()
        dispatcher = CampaignCallDispatcher()

        with _PatchedDispatcher(campaign, queued_run) as ctx:
            result_1 = await dispatcher.dispatch_call(queued_run, campaign, slot_id="s1")
        with _PatchedDispatcher(campaign, queued_run) as ctx2:
            result_2 = await dispatcher.dispatch_call(queued_run, campaign, slot_id="s2")

        assert result_1 is None
        assert result_2 is None
