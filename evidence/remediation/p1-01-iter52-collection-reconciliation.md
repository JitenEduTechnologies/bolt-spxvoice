# P1-01 Collection Count Reconciliation: 54 → 53

**Date:** 2026-11-21
**Question:** Iter51 collected 54 items. Iter52 collects 53 items. Explain the difference.

---

## Finding

The discrepancy is **not a missing test**. It is a difference in how the two iterations
structured their pytest invocations.

### Iter51 invocation (Order A/B)

The iter51 Order A and Order B runs bundled T4-08 into the same pytest call:

```
python -m pytest tests/test_circuit_breaker_p1_acceptance.py tests/test_circuit_breaker.py
  tests/test_phase4_campaign_safety.py::test_t4_08_automatic_safety_pause_circuit_breaker
  -v --tb=short -s
```

Evidence: `p1-01-iter51-orderB.log` shows `tests\test_phase4_campaign_safety.py` as the first
collected module (1 dot), followed by `test_circuit_breaker_p1_acceptance.py` (18 tests) and
`test_circuit_breaker.py` (35 tests).

Collection: **1 + 18 + 35 = 54**

### Iter52 invocation (Order A/B)

The iter52 authorization explicitly required T4-08 as a **separate standalone run**:

```
# Order A/B — two CB files only
python -m pytest tests/test_circuit_breaker.py tests/test_circuit_breaker_p1_acceptance.py

# T4-08 — standalone separate command
python -m pytest tests/test_phase4_campaign_safety.py::test_t4_08_automatic_safety_pause_circuit_breaker
```

Collection in Order A/B: **35 + 18 = 53**
Collection in T4-08 standalone: **1** (run separately, logged separately)

---

## Proof that no test was deleted

| Proof claim | Evidence |
|-------------|----------|
| No test method deleted from `test_circuit_breaker_p1_acceptance.py` | File has exactly 18 test methods; iter51 also had 18 from this file (54 − 35 CB tests − 1 T4-08 = 18) |
| No test method deleted from `test_circuit_breaker.py` | File has exactly 35 test methods; source is unmodified since iter51 |
| No behavioral assertion removed | Fix 1: caplog→Loguru sink (same 4 assertions); Fix 2: added FK prerequisites (same 5 assertions); Fix 3: port number only |
| No test renamed into uncollected form | `--collect-only -q` lists all 53 tests by name; all names present and valid |
| No test silently skipped | `--collect-only` output shows 53 collected; iter52 Order A shows 53 passed 0 skipped |
| T4-08 itself still passes | `p1-01-iter52-T4-08.log`: 1 passed, 0 failed |

---

## Full iter52 collection (53 tests)

From `evidence/remediation/p1-01-iter52-collect.log`:

**test_circuit_breaker.py (35 tests)**

- TestCircuitBreakerRecordOutcome (6): test_no_trip_below_min_calls, test_trip_when_threshold_exceeded, test_no_trip_below_threshold, test_disabled_circuit_breaker, test_custom_config_override, test_redis_error_fails_closed_unknown
- TestCircuitBreakerIsOpen (2): test_is_open_when_threshold_exceeded, test_is_not_open_when_healthy
- TestCircuitBreakerReset (2): test_reset_deletes_redis_keys, test_reset_on_redis_error
- TestRecordAndEvaluate (5): test_trips_and_pauses_campaign, test_no_pause_when_not_tripped, test_skips_when_campaign_not_running, test_reads_config_from_orchestrator_metadata, test_error_is_swallowed
- TestCircuitBreakerRecentFailures (6): test_failure_pushes_recent_failure_entry, test_success_does_not_push_recent_failure, test_trip_log_includes_recent_failures_in_details, test_push_recent_failure_uses_lpush_and_ltrim, test_get_recent_failures_decodes_lrange, test_reset_clears_recent_failures_key
- TestProcessStatusUpdateCircuitBreaker (3): test_failure_status_calls_record_and_evaluate, test_success_status_calls_record_and_evaluate, test_non_campaign_call_skips_circuit_breaker
- TestResumeCampaignResetsCircuitBreaker (1): test_resume_resets_circuit_breaker
- TestCircuitBreakerFailClosed (5): test_is_open_unknown_on_connection_refused, test_is_open_unknown_on_timeout, test_record_outcome_unknown_on_auth_error, test_unknown_callback_pauses_campaign_durably, test_orchestrator_blocks_dispatch_when_unknown
- TestResumeFailClosedRealPath (3): test_reset_failure_keeps_campaign_paused, test_unknown_dependency_keeps_campaign_paused, test_successful_reset_then_resume_sets_running_once
- TestInvalidRedisAuthZeroProviderCalls (1): test_invalid_auth_blocks_dispatch
- TestDispatcherBatchBlockedByBreaker (1): test_unknown_blocks_batch_and_requeues_rows

**test_circuit_breaker_p1_acceptance.py (18 tests)**

- TestRedisConstructionFailures (5): test_get_redis_raising_returns_none, test_is_open_unknown_when_get_redis_returns_none, test_record_unknown_when_get_redis_returns_none, test_reset_false_when_get_redis_returns_none, test_bookkeeping_safe_when_get_redis_returns_none
- TestBreakerErrorRedaction (2): test_no_credential_leak_on_construction_error, test_no_credential_leak_on_eval_error
- TestResumeBlockedEvents (3): test_unknown_resume_appends_blocked_event, test_reset_failure_appends_blocked_event, test_successful_reset_then_resume_sets_running_once
- TestPerRecipientRaceGate (1): test_race_change_to_unknown_blocks_everything
- TestPerRecipientFaultIsolation (4): test_slot_release_failure_still_pauses_and_events, test_requeue_failure_still_pauses_and_events, test_pause_failure_event_reports_false, test_event_failure_still_no_dispatch_and_critical_log
- TestPartialRequeueFailureFailClosed (1): test_first_row_requeue_fails_second_succeeds
- TestStaleProcessingRecovery (1): test_requeue_failure_row_recovered_after_lease_expiry
- TestRealRedisInvalidAuthIntegration (1): test_wrong_password_yields_unknown_then_recovery_closed

**Plus T4-08 (run separately)**

- test_phase4_campaign_safety.py::test_t4_08_automatic_safety_pause_circuit_breaker — PASSED (p1-01-iter52-T4-08.log)

**Total behavioral coverage: 53 + 1 = 54 tests, 54 passed**

---

## Conclusion

The 53-test collection in iter52 Order A/B is **intentional and correct**. The 54th test
(T4-08) is in a separate file and was run as a separate standalone command per the
authorization requirement. No test was deleted, removed, skipped, or renamed.
