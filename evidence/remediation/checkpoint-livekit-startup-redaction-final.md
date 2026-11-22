# CHECKPOINT: LiveKit Startup Redaction — Final (SDK re-raise leak closed)

**Date**: 2026-11-21
**Scope**: `C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615` only.
**Not touched**: `D:\spx-voice-mvp`, any frozen candidate, `F:\spxv1\spx-voice`.
**No real credentials used. No provider calls made. P1-04 not started.**

**IMPLEMENTATION COMPLETE — INDEPENDENT RE-REVIEW REQUIRED**

This supersedes `checkpoint-livekit-startup-redaction-ACCEPTED.md`, whose
independent review (separate session) found the original fix correct but
incomplete: it closed the leak into this codebase's own logs/DB, but left
the *original* exception able to escape `entrypoint()` and be logged by the
third-party `livekit-agents` job runner itself, via `exc_info=`. This
checkpoint closes that specific gap. As before, this is the implementer's
own account, not a substitute for independent re-review.

---

## 1. The gap being closed

Confirmed by the prior independent review, with exact evidence:
`D:\spx-voice-mvp\.venv\Lib\site-packages\livekit\agents\ipc\job_proc_lazy_main.py:336-341`
(the installed `livekit-agents` package — third-party, not part of this
candidate) catches any uncaught exception from the entrypoint task and
calls:

```python
logger.error("unhandled exception while running the job task", exc_info=t.exception())
```

`exc_info=<exception>` renders that exception's own `str()` and full
traceback via Python's standard `logging`/`traceback` formatting,
regardless of what this codebase's own loggers already do. Both of
`entrypoint()`'s existing `except Exception as exc: ... raise` blocks
re-raised the *original* `exc` after calling
`_record_livekit_startup_failure` — meaning the sanitization applied to
what gets *persisted* did nothing to prevent the raw exception from
reaching this outside, uncontrolled log call.

A further audit (task requirement 5: "audit every worker entrypoint
exit/re-raise for the same pattern") found a **third, previously
undiscussed exposure**: `entrypoint()`'s original body ran
`await ctx.connect()`, `await ctx.wait_for_participant()`, and
`await _resolve_or_create_workflow_run(...)` **before** the first
`try:` block — meaning a failure in any of those calls propagated
completely raw, with no sanitization attempt of any kind, since there is
no `workflow_run_id` yet to call `_record_livekit_startup_failure` with.

---

## 2. Changed files

| File | What changed |
|---|---|
| `api/services/livekit/worker.py` | New `LiveKitStartupError` exception class; new shared `_startup_sanitized_message()` helper; `entrypoint()` split into a thin sanitizing wrapper plus `_run_entrypoint()` (the original body, now covered by an outer safety net); both existing re-raise points now raise `LiveKitStartupError(...) from None` instead of the original exception |
| `api/tests/test_product_mode_mapping.py` | 5 existing `pytest.raises(...)` assertions updated to expect `LiveKitStartupError` instead of the original exception type/message (the leak they previously asserted); 1 source-inspection test updated to inspect `_run_entrypoint` instead of `entrypoint` |
| `api/tests/test_livekit_startup_redaction.py` | 24 new tests added (18 → 42 total) |

No other files changed in this round. `accept/*.ps1` and
`accept/docker-compose.accept.yaml` are unaffected and unchanged.

---

## 3. Exact source paths and hunks

### `api/services/livekit/worker.py`

**New shared helper and exception type** (inserted between
`_startup_error_code` and `_record_livekit_startup_failure`):

```python
def _startup_sanitized_message(exc: BaseException, *, stage: str) -> str:
    """Category-only text: exception class + stable error code + stage.
    ...
    """
    return f"{type(exc).__name__}: {_startup_error_code(exc)} (stage={stage})"


class LiveKitStartupError(RuntimeError):
    """Sanitized exception raised in place of the original startup failure.
    ...
    """
```

`_record_livekit_startup_failure` now calls `_startup_sanitized_message`
internally instead of inlining the same formatting (pure refactor, zero
behavior change — same tests, same output, confirmed by the full existing
suite passing unchanged).

**`entrypoint()` split**:

```python
async def entrypoint(ctx: JobContext) -> None:
    """Public entrypoint ... Wraps _run_entrypoint in a final safety net ..."""
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
    ...  # identical to the previous entrypoint() body, otherwise untouched
```

**Both existing re-raise points**, previously bare `raise`:

```python
        if isinstance(exc, InvalidCallDurationError):
            await _cleanup_rejected_startup(ctx, "invalid_call_duration")
        raise LiveKitStartupError(
            _startup_sanitized_message(exc, stage="config_resolution")
        ) from None
```

```python
    except Exception as exc:
        await _record_livekit_startup_failure(
            workflow_run_id, exc, stage="session_start", ...
        )
        raise LiveKitStartupError(
            _startup_sanitized_message(exc, stage="session_start")
        ) from None
```

`entrypoint_fnc=entrypoint` in the `if __name__ == "__main__":` block at
the bottom of the file is unchanged and now correctly refers to the public
sanitizing wrapper.

### `api/tests/test_product_mode_mapping.py`

Five `pytest.raises(...)` assertions updated — each previously asserted
that the *original* exception (type and/or exact message text) escaped
`entrypoint()`, which is precisely the behavior being removed:

- `test_worker_blocks_economy_ivr_before_any_model_resolution`:
  `pytest.raises(ValueError, match="economy_ivr")` →
  `pytest.raises(worker.LiveKitStartupError, match="INVALID_CONFIGURATION")`
- `test_worker_guard_does_not_affect_other_modes`:
  `pytest.raises(RuntimeError, match="reached model resolution")` →
  `pytest.raises(worker.LiveKitStartupError, match="STARTUP_FAILED")`
- `test_worker_rejects_invalid_pinned_duration_before_ai_session`:
  `pytest.raises(worker.InvalidCallDurationError, match="30 to 3600")` →
  `pytest.raises(worker.LiveKitStartupError, match="INVALID_CALL_DURATION")`
  — the `recorded_error` assertions immediately below (the exception
  actually passed into the mocked `_record_livekit_startup_failure`,
  including `__suppress_context__ is True` and the full original message)
  are **unchanged**, since that argument was never affected by this fix.
- `test_worker_accepts_valid_pinned_duration_before_model_resolution`: same
  pattern as `test_worker_guard_does_not_affect_other_modes`.
- `test_duration_rejection_cancels_and_awaits_pending_startup_tasks`:
  `pytest.raises(worker.InvalidCallDurationError)` →
  `pytest.raises(worker.LiveKitStartupError, match="INVALID_CALL_DURATION")`

One source-inspection test updated:
`test_worker_reads_the_same_top_level_key_the_routes_write` now calls
`inspect.getsource(worker._run_entrypoint)` instead of
`inspect.getsource(worker.entrypoint)`, since the real body (containing the
two literal substrings this test checks for) moved there. The two asserted
substrings themselves (`_validate_pinned_call_duration(run_configs)`,
`max_duration_seconds=max_duration_seconds`) are unchanged in the source.

### `api/tests/test_livekit_startup_redaction.py`

24 new tests appended (full file now 42 tests; see §4 for the breakdown):

- `test_pre_run_resolution_failure_escapes_as_sanitized_error` (7, parametrized) — exercises the **new** outer safety net via a failing `ctx.connect()`, proving a failure with no `workflow_run_id` yet still escapes as `LiveKitStartupError`, never the original.
- `test_config_resolution_failure_escapes_as_sanitized_error` (7, parametrized) — exercises the **already-fixed** config_resolution path end-to-end through the real `entrypoint()`, confirming the escaped exception, the real logger call, and the real `_record_livekit_startup_failure` persistence are all independently marker-free.
- `test_escaped_exception_survives_sdk_exc_info_rendering_without_markers` (7, parametrized) — **the direct proof for this fix's stated goal**: renders the actual escaped exception through Python's real `logging` module with `exc_info=`, exactly mirroring `job_proc_lazy_main.py`'s own call, and asserts no marker survives in the rendered output.
- `test_startup_sanitized_message_consistent_across_all_three_stages` (1) — confirms the shared helper is marker-free for every adversarial exception at all three stage names, extending confidence to the `session_start` call site without a third full harness.
- `test_livekit_startup_error_suppresses_cause_and_context` (1) — confirms `__cause__ is None` / `__suppress_context__ is True`, and explicitly documents that `__context__` itself remains set (not cleared) — `__suppress_context__` is what makes standard traceback rendering skip it, proven separately by the exc_info test.
- `test_startup_failure_never_invokes_publish_feedback_behaviorally` (1) — the requested *behavioral* complement to the earlier *structural* (signature-only) browser-feedback test: patches the real `_publish_feedback` method on the agent class and asserts it is never invoked during a startup failure.

---

## 4. Test counts

```powershell
$env:PYTHONPATH = 'C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615'
$env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55444/spx_voice_accept'
$env:REDIS_URL = 'redis://:REDIS_PASSWORD_PLACEHOLDER@127.0.0.1:6384/0'
$env:ENVIRONMENT = 'test'
D:\spx-voice-mvp\.venv\Scripts\python.exe -m pytest api\tests\test_livekit_startup_redaction.py -q
```
Result: **42 passed** (was 18 before this round).

```powershell
D:\spx-voice-mvp\.venv\Scripts\python.exe -m pytest `
  api\tests\test_livekit_startup_redaction.py api\tests\test_product_mode_mapping.py `
  api\tests\test_voice01_interruption.py api\tests\test_gate2_secret_redaction.py -q
```
Result: **231 passed** (42 + 130 + 55 + 4). This is **231, not 207** —
independent re-review found the original draft of this checkpoint had
copied forward the previous round's four-suite total (207, correct for
when the redaction file had 18 tests) instead of recomputing it after
this round added 24 new tests. Verified by `--collect-only` on each file
individually (42/130/55/4) and by running all four together (231 passed).
No test disappeared, was renamed out of collection, deselected,
overwritten, or skipped — this was a documentation arithmetic error only,
with no code or test-integrity impact.

```powershell
D:\spx-voice-mvp\.venv\Scripts\python.exe -m pytest `
  api\tests\test_livekit_startup_redaction.py api\tests\test_product_mode_mapping.py `
  api\tests\test_voice01_interruption.py api\tests\test_gate2_secret_redaction.py `
  api\tests\test_livekit_worker.py api\tests\test_phase2_livekit_integration.py `
  api\tests\test_phase2_remediation.py -q
```
Result: **268 passed, 4 failed** (268 = 244 + 24 new; the 4 failures are
**the exact same 4**, same names, same tracebacks, as the previous round —
see §6).

`python -m compileall api\routes api\services api\tests`: exit 0, no errors.

---

## 5. Escaped-exception / traceback redaction proof

`test_escaped_exception_survives_sdk_exc_info_rendering_without_markers`
is the proof artifact; running it is the proof. For each of the 7
adversarial exceptions (Gemini key, LiveKit secret, bearer token,
credential-URL-with-query-string, JSON body, credential-bearing DSN URL,
combined multiline), the test:

1. Triggers a real failure through the real `entrypoint()` (via a failing
   `ctx.connect()`), catching the real `LiveKitStartupError` it raises.
2. Feeds that exact exception object into a real `logging.Logger` configured
   exactly as `job_proc_lazy_main.py` configures its own call —
   `logger.error(msg, exc_info=<exception>)` — captured to an in-memory
   stream via a real `logging.StreamHandler` (not a fake/mock formatter).
3. Asserts none of the 7 marker strings appear anywhere in the rendered
   output, and separately confirms `"During handling of the above
   exception"` (Python's default chained-context banner) does **not**
   appear — proving `__suppress_context__` is doing its job, not merely
   that `__cause__` is unset.

All 7 pass. Additionally, `test_pre_run_resolution_failure_escapes_as_sanitized_error`
and `test_config_resolution_failure_escapes_as_sanitized_error` prove the
escaped exception's own `str()`, this module's own log messages, and (for
the config_resolution case) the real DB-persisted event/annotation are all
independently marker-free — the full chain from trigger to every
observable surface.

---

## 6. The four unrelated regression failures (preserved, not weakened)

Identical to the previous round — same test names, same exact tracebacks,
confirmed by re-running independently in this round:

| Test | File | Error |
|---|---|---|
| `test_livekit_google_31_realtime_say_text_allows_interruptions` | test_livekit_worker.py | `AssertionError: ... 'allow_interruptions': False ... != ... True` |
| `test_livekit_finalize_persists_usage_and_calculates_cost` | test_livekit_worker.py | `StopAsyncIteration` (exhausted `AsyncMock` side-effect sequence in `_set_finalization_state`) |
| `test_livekit_opening_auto_advances_single_non_end_start_transition` | test_livekit_worker.py | `assert True is False` (same `allow_interruptions` area) |
| `test_finalize_persists_latency_and_disposition_in_gathered_context` | test_phase2_livekit_integration.py | `RuntimeError: post_call_error: post_call_failed`, traced to a real `ConnectionRefusedError` in `post_call_triggers.py` |

None touch `_safe_error_text`, `_record_livekit_startup_failure`,
`_startup_error_code`, `_startup_sanitized_message`, `LiveKitStartupError`,
`entrypoint`, `_run_entrypoint`, or `_speak_opening`'s cache-failure branch.
The complete regression is **not** reported as green: 4 failed, 268
passed, stated plainly in every run above.

---

## 7. Remaining P0/P1/P2 issues

**No P0.**

**P1 — resolved this round.** The SDK-level `exc_info=` re-raise leak
identified by the prior independent review is closed: proven by §5.

**P2 — unchanged from the prior review, still open, not addressed here
(out of this round's scope)**:
- `routes/livekit.py:472-473`'s `detail=str(exc)` on `LiveKitConfigurationError`
  remains fragile-but-not-currently-exploitable (its only raise site lists
  variable names, never values).
- The 4 pre-existing unrelated test failures (§6).
- `logger.warning` calls elsewhere in `_run_entrypoint` that use
  `_safe_finalize_error` (recording start/persist warnings, TTS fallback
  warnings) were not re-audited in this round; they were already covered
  by the prior round's fix to `_safe_error_text`/`_safe_finalize_error` and
  use `logger.warning`, not `logger.exception`, so they do not carry the
  `exc_info=` risk this round closes. Not re-verified end-to-end here.

**New, low-severity observation from this round**: `LiveKitStartupError`'s
`__context__` attribute still holds the original exception object (Python
sets this automatically regardless of `from None`; only `__suppress_context__`
is set, which is what makes *standard* traceback formatting skip it — see
`test_livekit_startup_error_suppresses_cause_and_context`'s docstring). If
any future logging code explicitly walks `__context__` instead of using
standard `traceback`/`logging` formatting (no such code exists today in
this codebase or the installed `livekit-agents` version), it could still
surface the original exception. Not exploitable today; noted for awareness.

---

## 8. Rollback procedure

All changes isolated to the three files in §2; no migrations, no schema
changes, no dependency changes.

**`api/services/livekit/worker.py`**: remove `LiveKitStartupError` and
`_startup_sanitized_message`; revert `_record_livekit_startup_failure`'s
`safe_message` line to its inline form
(`f"{error_class}: {error_code} (stage={stage})"`, equivalent, just not
shared — purely cosmetic to revert); merge `_run_entrypoint` back into
`entrypoint` (rename `_run_entrypoint` → `entrypoint`, delete the new thin
wrapper); change both `raise LiveKitStartupError(...) from None` back to
bare `raise` (not recommended — reinstates the P1 leak this round closes).

**`api/tests/test_product_mode_mapping.py`**: revert the 5 `pytest.raises`
assertions and the 1 source-inspection target to their pre-this-round
values (not recommended for the same reason).

**`api/tests/test_livekit_startup_redaction.py`**: delete the 24 new tests
(keep the original 18) to roll back just this round's additions.

---

## Next action

Independent re-review of this checkpoint, focused specifically on: (a)
confirming the exc_info-rendering test in §5 is a faithful simulation of
the actual third-party call site, not a strawman; (b) confirming the 5
updated `pytest.raises` assertions in `test_product_mode_mapping.py` still
meaningfully test what each test's name claims, not just a type change
papering over a lost assertion; (c) re-confirming the 4 unrelated failures
in §6 by independent re-run. Do not proceed to credential entry or Test
Audio until that review returns ACCEPTED.
