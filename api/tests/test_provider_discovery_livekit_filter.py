"""
Provider-discovery / LiveKit-filter tests.

Confirms that:
  - CartesiaTTSConfiguration is registered in REGISTRY[TTS] and
    LIVEKIT_SUPPORTED_PROVIDERS["tts"] includes "cartesia".
  - DeepSeekLLMConfiguration is registered in REGISTRY[LLM] and
    LIVEKIT_SUPPORTED_PROVIDERS["llm"] includes "deepseek".
  - livekit_supports_provider() returns True for the fixed providers.
  - filter_livekit_providers logic (as used in /user/configurations/defaults)
    includes Cartesia/DeepSeek when LiveKit runtime is active.
  - Existing openai/google/realtime entries are unaffected.

All tests are pure-function / in-process only.  No DB, no real providers,
no paid calls.
"""
import pytest


# ---------------------------------------------------------------------------
# LIVEKIT_SUPPORTED_PROVIDERS set membership
# ---------------------------------------------------------------------------

class TestLivekitSupportedProviders:
    """LIVEKIT_SUPPORTED_PROVIDERS must list every provider _create_session handles."""

    def test_cartesia_in_tts_supported_set(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "cartesia" in LIVEKIT_SUPPORTED_PROVIDERS["tts"], (
            "Cartesia is implemented in worker._create_session but was missing from "
            "LIVEKIT_SUPPORTED_PROVIDERS['tts'], causing the UI filter to hide it."
        )

    def test_deepseek_in_llm_supported_set(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "deepseek" in LIVEKIT_SUPPORTED_PROVIDERS["llm"], (
            "DeepSeek is implemented in worker._create_session but was missing from "
            "LIVEKIT_SUPPORTED_PROVIDERS['llm'], causing the UI filter to hide it."
        )

    def test_openai_still_in_all_pipeline_sets(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "openai" in LIVEKIT_SUPPORTED_PROVIDERS["llm"]
        assert "openai" in LIVEKIT_SUPPORTED_PROVIDERS["stt"]
        assert "openai" in LIVEKIT_SUPPORTED_PROVIDERS["tts"]

    def test_google_still_in_all_pipeline_sets(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "google" in LIVEKIT_SUPPORTED_PROVIDERS["llm"]
        assert "google" in LIVEKIT_SUPPORTED_PROVIDERS["stt"]
        assert "google" in LIVEKIT_SUPPORTED_PROVIDERS["tts"]

    def test_realtime_set_unchanged(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "openai_realtime" in LIVEKIT_SUPPORTED_PROVIDERS["realtime"]
        assert "google_realtime" in LIVEKIT_SUPPORTED_PROVIDERS["realtime"]
        assert "google_vertex_realtime" in LIVEKIT_SUPPORTED_PROVIDERS["realtime"]


# ---------------------------------------------------------------------------
# livekit_supports_provider() helper
# ---------------------------------------------------------------------------

class TestLivekitSupportsProvider:
    """livekit_supports_provider() must return True for Cartesia/DeepSeek."""

    def test_supports_cartesia_tts(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("tts", "cartesia") is True

    def test_supports_deepseek_llm(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("llm", "deepseek") is True

    def test_does_not_support_elevenlabs_tts(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("tts", "elevenlabs") is False

    def test_does_not_support_groq_llm(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("llm", "groq") is False

    def test_unknown_service_always_true(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("embeddings", "anything") is True


# ---------------------------------------------------------------------------
# Registry membership
# ---------------------------------------------------------------------------

class TestProviderRegistration:
    """DeepSeekLLMConfiguration and CartesiaTTSConfiguration must be in REGISTRY."""

    def test_cartesia_registered_in_tts_registry(self):
        from api.services.configuration.registry import REGISTRY, ServiceType
        assert "cartesia" in REGISTRY[ServiceType.TTS], (
            "CartesiaTTSConfiguration must be registered under REGISTRY[TTS]['cartesia']."
        )

    def test_deepseek_registered_in_llm_registry(self):
        from api.services.configuration.registry import REGISTRY, ServiceType
        assert "deepseek" in REGISTRY[ServiceType.LLM], (
            "DeepSeekLLMConfiguration must be registered under REGISTRY[LLM]['deepseek']."
        )

    def test_cartesia_registry_entry_is_config_class(self):
        from api.services.configuration.registry import REGISTRY, ServiceType, CartesiaTTSConfiguration
        assert REGISTRY[ServiceType.TTS]["cartesia"] is CartesiaTTSConfiguration

    def test_deepseek_registry_entry_is_config_class(self):
        from api.services.configuration.registry import REGISTRY, ServiceType, DeepSeekLLMConfiguration
        assert REGISTRY[ServiceType.LLM]["deepseek"] is DeepSeekLLMConfiguration

    def test_deepseek_enum_value(self):
        from api.services.configuration.registry import ServiceProviders
        assert ServiceProviders.DEEPSEEK.value == "deepseek"


# ---------------------------------------------------------------------------
# DeepSeekLLMConfiguration schema validation
# ---------------------------------------------------------------------------

class TestDeepSeekLLMConfiguration:
    """DeepSeekLLMConfiguration must accept valid inputs."""

    def test_default_instantiation(self):
        from api.services.configuration.registry import DeepSeekLLMConfiguration, ServiceProviders
        cfg = DeepSeekLLMConfiguration(api_key="sk-test")
        assert cfg.provider == ServiceProviders.DEEPSEEK
        assert cfg.model == "deepseek-chat"
        assert cfg.base_url == "https://api.deepseek.com"

    def test_custom_model_accepted(self):
        from api.services.configuration.registry import DeepSeekLLMConfiguration
        cfg = DeepSeekLLMConfiguration(api_key="sk-test", model="deepseek-reasoner")
        assert cfg.model == "deepseek-reasoner"

    def test_custom_base_url_accepted(self):
        from api.services.configuration.registry import DeepSeekLLMConfiguration
        cfg = DeepSeekLLMConfiguration(api_key="sk-test", base_url="https://proxy.example.com/v1")
        assert cfg.base_url == "https://proxy.example.com/v1"

    def test_schema_has_discriminator_provider(self):
        from api.services.configuration.registry import DeepSeekLLMConfiguration
        schema = DeepSeekLLMConfiguration.model_json_schema()
        assert schema.get("properties", {}).get("provider") is not None


# ---------------------------------------------------------------------------
# UI filter simulation — CartesiaTTSConfiguration visible in LiveKit mode
# ---------------------------------------------------------------------------

class TestFilterLivekitProviders:
    """Simulate the filter_livekit_providers logic used in /user/configurations/defaults."""

    def _filter(self, service: str, items: dict) -> dict:
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        supported = LIVEKIT_SUPPORTED_PROVIDERS.get(service)
        if supported is None:
            return items
        filtered = {p: s for p, s in items.items() if p in supported}
        return filtered or items

    def test_cartesia_passes_livekit_tts_filter(self):
        result = self._filter("tts", {"openai": {}, "cartesia": {}, "elevenlabs": {}})
        assert "cartesia" in result
        assert "elevenlabs" not in result

    def test_deepseek_passes_livekit_llm_filter(self):
        result = self._filter("llm", {"openai": {}, "google": {}, "deepseek": {}, "groq": {}})
        assert "deepseek" in result
        assert "groq" not in result

    def test_openai_still_passes_livekit_tts_filter(self):
        result = self._filter("tts", {"openai": {}, "cartesia": {}})
        assert "openai" in result

    def test_openai_still_passes_livekit_llm_filter(self):
        result = self._filter("llm", {"openai": {}, "deepseek": {}})
        assert "openai" in result

    def test_deepgram_passes_livekit_stt_filter(self):
        result = self._filter("stt", {"openai": {}, "google": {}, "deepgram": {}, "assemblyai": {}})
        assert "deepgram" in result
        assert "assemblyai" not in result


# ---------------------------------------------------------------------------
# Deepgram STT — registry + filter
# ---------------------------------------------------------------------------

class TestDeepgramSTTFilter:
    """Deepgram STT is registered and must pass the LiveKit STT filter."""

    def test_deepgram_in_stt_supported_set(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "deepgram" in LIVEKIT_SUPPORTED_PROVIDERS["stt"], (
            "Deepgram STT is implemented in worker._create_session (line 2729) "
            "but was missing from LIVEKIT_SUPPORTED_PROVIDERS['stt']."
        )

    def test_deepgram_stt_registered_in_registry(self):
        from api.services.configuration.registry import REGISTRY, ServiceType
        assert "deepgram" in REGISTRY[ServiceType.STT], (
            "DeepgramSTTConfiguration must be registered under REGISTRY[STT]['deepgram']."
        )

    def test_livekit_supports_deepgram_stt(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("stt", "deepgram") is True

    def test_unsupported_stt_still_blocked(self):
        from api.services.livekit.runtime_config import livekit_supports_provider
        assert livekit_supports_provider("stt", "assemblyai") is False
        assert livekit_supports_provider("stt", "gladia") is False

    def test_openai_stt_still_passes(self):
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS
        assert "openai" in LIVEKIT_SUPPORTED_PROVIDERS["stt"]

    def test_deepgram_registry_entry_is_config_class(self):
        from api.services.configuration.registry import REGISTRY, ServiceType, DeepgramSTTConfiguration
        assert REGISTRY[ServiceType.STT]["deepgram"] is DeepgramSTTConfiguration


# ---------------------------------------------------------------------------
# Campaign-visible financial admission event (P1-03 gap remediation)
# ---------------------------------------------------------------------------

class TestFinancialAdmissionCampaignLogEvent:
    """When financial admission raises, a campaign log event must be appended."""

    @pytest.mark.asyncio
    async def test_campaign_log_event_appended_on_budget_exception(self):
        """Dispatcher must call append_campaign_log with event=financial_admission_check_failed."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch, MagicMock
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
        from api.services.campaign.circuit_breaker import BreakerState

        campaign = SimpleNamespace(
            id=1, organization_id=42, workflow_id=10,
            created_by=5, telephony_configuration_id=1,
            state="running",
            orchestrator_metadata={"budget_config": {"daily_cap_inr": "not_a_number"}},
        )
        queued_run = SimpleNamespace(
            id=99, context_variables={"phone_number": "+15550001111"},
            source_uuid="test-uuid", retry_count=0,
        )
        dispatcher = CampaignCallDispatcher()
        log_mock = AsyncMock()

        _db = __import__("api.db", fromlist=["db_client"]).db_client
        from api.services.campaign.circuit_breaker import circuit_breaker as _cb
        from api.services.telephony.compliance import compliance_manager as _cm
        from api.services.campaign.rate_limiter import rate_limiter as _rl

        _r_client = AsyncMock()
        _r_client.get = AsyncMock(return_value=None)
        _r_client.aclose = AsyncMock()

        patches = [
            patch.object(_db, "get_campaign_by_id", new=AsyncMock(return_value=campaign)),
            patch.object(_db, "get_workflow_by_id", new=AsyncMock(return_value=SimpleNamespace(id=10, name="wf"))),
            patch.object(_db, "update_queued_run", new=AsyncMock()),
            patch.object(_db, "append_campaign_log", new=log_mock),
            patch.object(_cb, "is_circuit_open", new=AsyncMock(return_value=(BreakerState.CLOSED, {}))),
            patch.object(_cm, "is_suppressed_db", new=AsyncMock(return_value=(False, None))),
            patch.object(_rl, "release_concurrent_slot", new=AsyncMock()),
            patch("redis.asyncio.from_url", return_value=_r_client),
        ]
        for p in patches:
            p.start()
        try:
            result = await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-log-1")
        finally:
            for p in patches:
                p.stop()

        assert result is None
        log_mock.assert_called_once()
        call_kwargs = log_mock.call_args.kwargs
        assert call_kwargs.get("campaign_id") == campaign.id
        assert call_kwargs.get("event") == "financial_admission_check_failed"
        assert call_kwargs.get("level") == "error"
        assert "financial_admission_check_failed" in call_kwargs.get("event", "")
        details = call_kwargs.get("details", {})
        assert details.get("queued_run_id") == queued_run.id
        assert details.get("exception_type") is not None

    @pytest.mark.asyncio
    async def test_campaign_log_failure_does_not_prevent_fail_closed(self):
        """If append_campaign_log itself raises, the fail-closed return None must still happen."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, patch
        from api.services.campaign.campaign_call_dispatcher import CampaignCallDispatcher
        from api.services.campaign.circuit_breaker import BreakerState

        campaign = SimpleNamespace(
            id=2, organization_id=42, workflow_id=10,
            created_by=5, telephony_configuration_id=1,
            state="running",
            orchestrator_metadata={"budget_config": {"daily_cap_inr": "bad"}},
        )
        queued_run = SimpleNamespace(
            id=100, context_variables={"phone_number": "+15550002222"},
            source_uuid="test-uuid2", retry_count=0,
        )
        dispatcher = CampaignCallDispatcher()

        _db = __import__("api.db", fromlist=["db_client"]).db_client
        from api.services.campaign.circuit_breaker import circuit_breaker as _cb
        from api.services.telephony.compliance import compliance_manager as _cm
        from api.services.campaign.rate_limiter import rate_limiter as _rl

        _r_client = AsyncMock()
        _r_client.get = AsyncMock(return_value=None)
        _r_client.aclose = AsyncMock()

        patches = [
            patch.object(_db, "get_campaign_by_id", new=AsyncMock(return_value=campaign)),
            patch.object(_db, "get_workflow_by_id", new=AsyncMock(return_value=SimpleNamespace(id=10, name="wf"))),
            patch.object(_db, "update_queued_run", new=AsyncMock()),
            patch.object(_db, "append_campaign_log", new=AsyncMock(side_effect=RuntimeError("DB log failed"))),
            patch.object(_cb, "is_circuit_open", new=AsyncMock(return_value=(BreakerState.CLOSED, {}))),
            patch.object(_cm, "is_suppressed_db", new=AsyncMock(return_value=(False, None))),
            patch.object(_rl, "release_concurrent_slot", new=AsyncMock()),
            patch("redis.asyncio.from_url", return_value=_r_client),
        ]
        for p in patches:
            p.start()
        try:
            result = await dispatcher.dispatch_call(queued_run, campaign, slot_id="slot-log-2")
        finally:
            for p in patches:
                p.stop()

        assert result is None, "fail-closed must return None even when log event write fails"


# ---------------------------------------------------------------------------
# Provider-options API trace — simulates /user/configurations/defaults filter
# with the real REGISTRY + LIVEKIT_SUPPORTED_PROVIDERS data.
# No server, no DB, no credentials.
# ---------------------------------------------------------------------------

class TestProviderOptionsAPITrace:
    """
    Exercises the same filter logic as GET /user/configurations/defaults
    (user.py:filter_livekit_providers) using the actual REGISTRY contents.

    After the fixes:
      - LLM visible in LiveKit mode: openai, google, deepseek
      - TTS visible in LiveKit mode: openai, cartesia
      - STT visible in LiveKit mode: openai, deepgram
                                     (cartesia STT registered but worker doesn't support it)
      - Realtime visible: openai_realtime, google_realtime, google_vertex_realtime
    """

    def _filter_for_service(self, service: str) -> set:
        """Apply the filter_livekit_providers logic to actual REGISTRY data."""
        from api.services.configuration.registry import REGISTRY, ServiceType
        from api.services.livekit.runtime_config import LIVEKIT_SUPPORTED_PROVIDERS

        stype_map = {
            "llm": ServiceType.LLM,
            "tts": ServiceType.TTS,
            "stt": ServiceType.STT,
            "realtime": ServiceType.REALTIME,
        }
        stype = stype_map[service]
        supported = LIVEKIT_SUPPORTED_PROVIDERS.get(service)
        reg_keys = set(REGISTRY[stype].keys())
        if supported is None:
            return reg_keys
        filtered = {p for p in reg_keys if p in supported}
        return filtered or reg_keys  # fallback: return unfiltered if empty

    def test_deepseek_visible_under_llm(self):
        result = self._filter_for_service("llm")
        assert "deepseek" in result, (
            f"DeepSeek must appear in LLM dropdown after fix. Got: {sorted(result)}"
        )

    def test_cartesia_visible_under_tts(self):
        result = self._filter_for_service("tts")
        assert "cartesia" in result, (
            f"Cartesia must appear in TTS/Voice dropdown after fix. Got: {sorted(result)}"
        )

    def test_deepgram_visible_under_stt(self):
        result = self._filter_for_service("stt")
        assert "deepgram" in result, (
            f"Deepgram must appear in STT dropdown after fix. Got: {sorted(result)}"
        )

    def test_openai_visible_across_all_pipeline_services(self):
        for service in ("llm", "tts", "stt"):
            result = self._filter_for_service(service)
            assert "openai" in result, f"OpenAI must remain visible under {service}"

    def test_all_realtime_providers_visible(self):
        result = self._filter_for_service("realtime")
        assert "openai_realtime" in result
        assert "google_realtime" in result
        assert "google_vertex_realtime" in result

    def test_unsupported_tts_providers_hidden(self):
        """ElevenLabs, Deepgram-TTS, Sarvam, etc. must not appear under TTS in LiveKit mode."""
        result = self._filter_for_service("tts")
        assert "elevenlabs" not in result
        assert "sarvam" not in result

    def test_unsupported_llm_providers_hidden(self):
        """Groq, OpenRouter, Azure, etc. must not appear under LLM in LiveKit mode."""
        result = self._filter_for_service("llm")
        assert "groq" not in result
        assert "openrouter" not in result
        assert "azure" not in result

    def test_unsupported_stt_providers_hidden(self):
        """AssemblyAI, Gladia, etc. must not appear under STT in LiveKit mode."""
        result = self._filter_for_service("stt")
        assert "assemblyai" not in result
        assert "gladia" not in result


class TestUnsupportedProviderExplicitError:
    """
    _ensure_livekit_runtime_supported (user.py) must raise with an explicit
    error message naming the unsupported provider and listing the supported ones.
    """

    def _make_config_with_provider(self, service: str, provider_str: str):
        """Build a minimal UserConfiguration with the given LLM/TTS/STT provider."""
        from types import SimpleNamespace
        section = SimpleNamespace(provider=provider_str)
        return SimpleNamespace(
            llm=section if service == "llm" else SimpleNamespace(provider="openai"),
            tts=section if service == "tts" else SimpleNamespace(provider="openai"),
            stt=section if service == "stt" else SimpleNamespace(provider="openai"),
            realtime=None,
            is_realtime=False,
        )

    def test_unsupported_tts_gives_explicit_error(self):
        from unittest.mock import patch
        from api.routes.user import _ensure_livekit_runtime_supported
        config = self._make_config_with_provider("tts", "elevenlabs")
        with patch("api.routes.user.is_livekit_runtime", return_value=True):
            try:
                _ensure_livekit_runtime_supported(config, {"tts": {"provider": "elevenlabs"}})
                assert False, "Expected ValueError for unsupported TTS provider"
            except ValueError as exc:
                msg = str(exc)
                assert "elevenlabs" in msg
                assert "cartesia" in msg or "openai" in msg  # supported providers listed

    def test_unsupported_llm_gives_explicit_error(self):
        from unittest.mock import patch
        from api.routes.user import _ensure_livekit_runtime_supported
        config = self._make_config_with_provider("llm", "groq")
        with patch("api.routes.user.is_livekit_runtime", return_value=True):
            try:
                _ensure_livekit_runtime_supported(config, {"llm": {"provider": "groq"}})
                assert False, "Expected ValueError for unsupported LLM provider"
            except ValueError as exc:
                msg = str(exc)
                assert "groq" in msg
                assert "deepseek" in msg or "openai" in msg

    def test_supported_provider_no_error(self):
        from unittest.mock import patch
        from api.routes.user import _ensure_livekit_runtime_supported
        config = self._make_config_with_provider("tts", "cartesia")
        with patch("api.routes.user.is_livekit_runtime", return_value=True):
            # Must not raise
            _ensure_livekit_runtime_supported(config, {"tts": {"provider": "cartesia"}})
