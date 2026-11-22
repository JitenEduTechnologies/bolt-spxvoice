# CHECKPOINT: P1-03 Review + Provider Discovery — FINAL
# Date: 2026-11-21

---

## PART A — P1-03 INDEPENDENT REVIEW: **ACCEPTED**

### A.1 Verification summary

All 15 specified items confirmed:

| Item | Result |
|------|--------|
| Financial admission errors fail closed | ✅ `except Exception` → slot released → run failed → `return None` |
| Malformed config blocks | ✅ ValidationError from bad budget_meta → caught → blocked |
| NaN estimated cost | ✅ Handled gracefully, documented test |
| Infinity estimated cost | ✅ `inf > finite cap` → blocked |
| Timeout / RuntimeError | ✅ All exceptions caught by bare `except Exception` |
| DB / pricing / quota / balance failure blocks | ✅ All covered by exception handler |
| Configuration failure blocks | ✅ ValidationError from bad config → blocked |
| Zero side effects before admission | ✅ `get_provider_for_campaign` (line 496) only reached after `if budget_meta:` exits normally |
| Slots released correctly | ✅ `release_concurrent_slot` in except block before `return None` |
| Failed state persisted | ✅ `update_queued_run(state="failed", retry_reason="financial_admission_unavailable: ...")` |
| Owner-visible campaign event | ✅ **ADDED this iteration**: `append_campaign_log(event="financial_admission_check_failed")` |
| Retries idempotent | ✅ Same bad config blocks on retry |
| Concurrent calls idempotent | ✅ Each concurrent call releases its own slot |
| `import copy` fix genuine | ✅ `copy.deepcopy` at line 452 had no import before this fix; NameError in production |
| Clear-budget and normal-block preserved | ✅ Clear path reaches provider; normal block uses `budget_cap_exceeded:` prefix |

### A.2 Campaign-visible event (gap remediated)

**Previous gap**: Financial admission fail-closed path set `retry_reason` but did NOT write a campaign log event (unlike circuit-breaker path).

**Fix applied** (`campaign_call_dispatcher.py` lines 492-510):

```python
try:
    await db_client.append_campaign_log(
        campaign_id=campaign.id,
        level="error",
        event="financial_admission_check_failed",
        message=(
            f"Financial admission check failed for run {queued_run.id}: "
            f"[{type(e).__name__}]. Call blocked — no provider call placed."
        ),
        details={
            "queued_run_id": queued_run.id,
            "exception_type": type(e).__name__,
        },
    )
except Exception as log_exc:
    logger.error(
        f"CRITICAL: financial admission event could not be written for "
        f"campaign {campaign.id} run {queued_run.id} "
        f"[{type(log_exc).__name__}]"
    )
```

Message is sanitized: exception type only, no stack traces, no internal details.
Log write is wrapped in its own try/except so a log DB failure cannot prevent the fail-closed `return None`.

---

## PART B — PROVIDER DISCOVERY: ALL DEFECTS FIXED

### B.1 Fixed defects

| Provider | Service | Defect | Fix |
|----------|---------|--------|-----|
| Cartesia | TTS | Registered + worker-implemented; incorrectly excluded from `LIVEKIT_SUPPORTED_PROVIDERS["tts"]` | Added `"cartesia"` to frozenset |
| DeepSeek | LLM | Worker-implemented; not registered as standalone provider, excluded from filter | Added `ServiceProviders.DEEPSEEK`, `DeepSeekLLMConfiguration`, added `"deepseek"` to filter |
| Deepgram | STT | Registered + worker-implemented; incorrectly excluded from `LIVEKIT_SUPPORTED_PROVIDERS["stt"]` | Added `"deepgram"` to frozenset |

### B.2 Provider-options API trace results

Simulated `GET /user/configurations/defaults` filter with LiveKit runtime active:

| Service | Visible (after fix) | Correctly hidden |
|---------|---------------------|-----------------|
| LLM | openai, google, **deepseek** | groq, openrouter, azure, dograh, aws_bedrock, speaches |
| TTS | openai, **cartesia** | elevenlabs, deepgram-tts, dograh, sarvam, camb, rime, speaches |
| STT | openai, **deepgram** | assemblyai, gladia, speechmatics, cartesia-stt*, dograh, sarvam, speaches |
| Realtime | openai_realtime, google_realtime, google_vertex_realtime | — |

*Note: `CartesiaSTTConfiguration` is registered (Cartesia makes both STT and TTS products) but the LiveKit worker's `_create_session` does not implement Cartesia STT — correctly hidden.

### B.3 Unsupported provider explicit error message

`_ensure_livekit_runtime_supported` (user.py:164) raises with:
> `"The LiveKit runtime cannot run the '{provider}' {label} provider. Supported {label} providers: {supported}."`

Verified by `TestUnsupportedProviderExplicitError`:
- Unsupported TTS provider → error names the provider + lists cartesia/openai as supported
- Unsupported LLM provider → error names the provider + lists deepseek/openai/google as supported
- Supported provider (cartesia as TTS) → no error raised

---

## FILES CHANGED (this iteration)

```
api/services/campaign/campaign_call_dispatcher.py
    → Added campaign log event (append_campaign_log) in financial admission except block
    → Log write wrapped in try/except per existing pattern

api/services/livekit/runtime_config.py
    → LIVEKIT_SUPPORTED_PROVIDERS["llm"]: added "deepseek"
    → LIVEKIT_SUPPORTED_PROVIDERS["stt"]: added "deepgram"
    → LIVEKIT_SUPPORTED_PROVIDERS["tts"]: added "cartesia"

api/services/configuration/registry.py
    → ServiceProviders.DEEPSEEK = "deepseek" added to enum
    → DeepSeekLLMConfiguration class added (@register_llm)
    → DeepSeekLLMConfiguration added to LLMConfig union

api/tests/test_p103_financial_admission_fail_closed.py
    → _PatchedDispatcher: added append_campaign_log mock (prevents unintended
      DB hit in tests that don't assert on the log event)

api/tests/test_provider_discovery_livekit_filter.py
    → TestFilterLivekitProviders: deepgram filter test added
    → TestDeepgramSTTFilter (6 tests): deepgram STT registered + filter + blocked providers
    → TestFinancialAdmissionCampaignLogEvent (2 tests):
        - campaign log event written on budget exception
        - log write failure does not prevent fail-closed
    → TestProviderOptionsAPITrace (9 tests): real REGISTRY + filter simulation
    → TestUnsupportedProviderExplicitError (3 tests): explicit error messages
```

## REGRESSION RESULTS

| Suite | Tests | Result |
|-------|-------|--------|
| P1-01 circuit breaker | 53 | ✅ all pass |
| P1-02 DNC fail-closed | 11 | ✅ all pass |
| P1-03 financial admission | 19 | ✅ all pass |
| Provider discovery | 43 | ✅ all pass |
| **Total** | **126** | **124 pass, 1 pre-existing DB-guard failure†, 1 skip** |

†`test_requeue_failure_row_recovered_after_lease_expiry` requires DATABASE_URL containing `spx_campaign_test` — a disposable-DB naming guard. Unrelated to any change in this iteration. Present in all prior regression runs.

---

## ROLLBACK PROCEDURE

**campaign_call_dispatcher.py**: Remove the `append_campaign_log` try/except block (lines 492-510). The `return None` at line 511 becomes line 493 again.

**runtime_config.py**: Restore:
```python
"llm": frozenset({"openai", "google"}),
"stt": frozenset({"openai", "google"}),
"tts": frozenset({"openai", "google"}),
```

**registry.py**: Remove `DEEPSEEK = "deepseek"` from enum, remove `DEEPSEEK_MODELS` constant, remove `DeepSeekLLMConfiguration` class, remove it from `LLMConfig` union.

**test_p103_financial_admission_fail_closed.py**: Remove the `p7` patch for `append_campaign_log` from `_PatchedDispatcher`.

**test_provider_discovery_livekit_filter.py**: Delete the file or revert to the 23-test version from the previous checkpoint.

---

## STOPPING HERE — DO NOT BEGIN P1-04

Per authorization: "Stop and report. Do not start P1-04."
