# CHECKPOINT: LiveKit Startup-Failure Secret Redaction

**Date**: 2026-11-21
**Scope**: `C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615` only.
**Not touched**: `D:\spx-voice-mvp`, any frozen candidate, `F:\spxv1\spx-voice`.
**No real credentials used. No provider calls made. P1-04 not started.**

**IMPLEMENTATION COMPLETE — INDEPENDENT REVIEW REQUIRED**

This checkpoint is written by the same agent that made the change. Everything
below is a claim to be verified, not a self-certified pass. Section 10 states
exactly what an independent reviewer must check before Phase 6/7 proceed.

---

## 1. Root cause

`api/services/livekit/worker.py::_record_livekit_startup_failure` persisted
`f"{type(exc).__name__}: {exc}"` (via a helper also named `_safe_error_text`,
despite the name) into:

- the API log, via `logger.exception(...)` — which additionally attaches the
  raw exception **and full traceback** to the sink regardless of the message
  string passed to it, independent of whatever `error_text` computed;
- `workflow_run.logs.livekit_events[].message`;
- `workflow_run.annotations.livekit_startup_error`.

Provider and LiveKit client exceptions can embed API keys, bearer tokens, or
credential-bearing URLs in their `str()` (e.g. Google's Generative AI key is
commonly passed as a URL query parameter; some HTTP clients include the full
request URL, including `Authorization` headers, in a raised exception's
string form). This is exactly the failure mode most likely to occur right
when it matters most: an invalid or revoked Gemini/LiveKit credential.

**A second instance of the same defect** was found during the required
call-site audit: `LiveKitWorkflowAgent._speak_opening`'s
`opening_audio_cache_failed` handler used the same unsafe helper when the
live opening-audio pre-cache call (which is passed `api_key=self._tts_api_key`
directly) fails. This path is more routinely triggered than a full startup
failure, since it fires on any opening-audio-cache failure, not only
unrecoverable startup errors.

The same file already contains the **correct** pattern for this exact
problem class: `_safe_finalize_error()`, whose own docstring states
*"exception messages may contain secrets"* and which returns a fixed
`"<category>: <redacted>"` string. `_record_livekit_startup_failure` and
`_speak_opening`'s cache-failure handler did not use it.

---

## 2. Exact changed files and hunks

### `api/services/livekit/worker.py`

**2a. `_safe_error_text` redefined** (was: `f"{type(exc).__name__}: {exc}"[:limit]`)
Now delegates to `_safe_finalize_error`, making every remaining call site of
`_safe_error_text` safe by construction without touching those call sites'
surrounding code:

```python
def _safe_error_text(exc: Exception, *, limit: int = 1000) -> str:
    """Category-only failure text; exception messages may contain secrets.
    ...
    """
    return _safe_finalize_error(exc)
```

This directly fixes `_speak_opening`'s `opening_audio_cache_failed` handler
(no other change needed there — `error_text` becomes safe automatically).

**2b. New stable error-code taxonomy**, added just before
`_record_livekit_startup_failure`:

```python
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
    for exc_type, code in _STARTUP_ERROR_CODES.items():
        if isinstance(exc, exc_type):
            return code
    return _DEFAULT_STARTUP_ERROR_CODE
```

Keyed only on built-in/project exception types, never a provider SDK
exception type — anything unrecognized (including every provider/client
exception) falls through to the generic `STARTUP_FAILED` code and never
contributes its own message. `PermissionError` is listed before `OSError`
because it is a subclass of it (dict iteration order matters here).

**2c. `_record_livekit_startup_failure` rewritten**: added a `stage: str =
"startup"` keyword parameter; replaced `logger.exception(...)` with
`logger.error(...)` (no automatic traceback/exception attachment); persisted
event and annotation now carry `error_class`, `error_code`, `stage`, and a
`message` built only from those three — never `str(exc)`.

**2d. Both call sites** in `entrypoint()` updated to pass `stage=`:
`stage="config_resolution"` (model/workflow/duration resolution) and
`stage="session_start"` (agent session creation/start).

### `api/tests/test_product_mode_mapping.py`

Updated `test_duration_rejection_records_failed_terminal_run`, which
previously asserted the exact vulnerable format
(`f"InvalidCallDurationError: {error}"`) as the *expected* behavior. That
assertion validated the bug itself, not a property worth preserving; it now
asserts the new safe format and the new structured fields, while the
assertions that **must** survive unchanged — correlation id (`17`),
`is_completed=True`, `state=COMPLETED`, `call_disposition="FAILED"`, event
`type == "startup_failed"` — are kept exactly as before.

### `api/tests/test_livekit_startup_redaction.py` (new, 18 tests)

Full file at that path. Injects synthetic markers for every category named
in the fix request (Gemini-key-shaped string, LiveKit-secret-shaped string,
bearer token, authorization header, credential-bearing URL with query
string, JSON body, and a combined multiline provider exception) and asserts
none of them survive into: logged messages, the persisted
`livekit_events` entry, or the persisted `annotations`. Also asserts
`logger.exception` is never called on this path (a fake logger raises if it
is), that the function's own parameter list has no session/room/ctx
argument (so it structurally cannot reach a browser-feedback payload), and
exercises the equivalent `opening_audio_cache_failed` path with the same
adversarial inputs.

### `accept/health-check.ps1`

Check 9 changed from a static `WARN` ("not fixed by this script") to an
actual pytest run of `test_livekit_startup_redaction.py`, scored PASS/FAIL
on the real exit code, with full output saved to
`accept/logs/sanitization-tests.log`. A future regression that reintroduces
`str(exc)` on this path will make this check FAIL, not silently stay green.

Three unrelated, pre-existing script bugs were also found and fixed while
touching this file (full detail in §9):
- Windows PowerShell 5.1 parses `.ps1` files without a BOM using the system
  ANSI codepage; the em dash character used throughout the file's comments
  and strings was getting corrupted on read, causing hard parse failures.
  All non-ASCII characters removed from `health-check.ps1`, `start-accept.ps1`,
  `stop-accept.ps1`.
- Four `Join-Path` calls used the PowerShell 7+-only multi-segment form,
  which Windows PowerShell 5.1 (confirmed version: 5.1.26100) rejects.
  Collapsed to the two-argument form PS 5.1 supports.
- `$geminiSet = $true` inside a `ForEach-Object` script block (a child
  scope) never escaped to the outer variable, making that specific check
  always report FAIL regardless of the file's actual contents. Switched to
  the `foreach` statement (no new scope), matching the pattern already used
  correctly in `start-accept.ps1`.

### `accept/start-accept.ps1`, `accept/stop-accept.ps1`

Only the same non-ASCII-character removal (no logic change). One stray text
fix in `start-accept.ps1`'s closing banner (referred to "8 checks," now just
"all checks," since the check count changed across this and the prior
review).

---

## 3. Test commands and counts

```powershell
$env:PYTHONPATH = 'C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615'
$env:DATABASE_URL = 'postgresql+asyncpg://postgres:postgres@127.0.0.1:55444/spx_voice_accept'
$env:REDIS_URL = 'redis://:REDIS_PASSWORD_PLACEHOLDER@127.0.0.1:6384/0'
$env:ENVIRONMENT = 'test'
D:\spx-voice-mvp\.venv\Scripts\python.exe -m pytest `
  api\tests\test_livekit_startup_redaction.py `
  api\tests\test_product_mode_mapping.py `
  api\tests\test_voice01_interruption.py `
  api\tests\test_gate2_secret_redaction.py -q
```

Result: **207 passed**, 0 failed.

Smallest relevant broader regression (adds the other LiveKit worker suites):

```powershell
D:\spx-voice-mvp\.venv\Scripts\python.exe -m pytest `
  api\tests\test_livekit_startup_redaction.py api\tests\test_product_mode_mapping.py `
  api\tests\test_voice01_interruption.py api\tests\test_gate2_secret_redaction.py `
  api\tests\test_phase2_livekit_integration.py api\tests\test_livekit_worker.py `
  api\tests\test_phase2_remediation.py -q
```

Result: **244 passed, 4 failed**. All 4 failures reproduce identically when
their file is run in isolation (i.e. not caused by combining test files),
and are in code this change never touches:

| Test | File | Why it's unrelated |
|---|---|---|
| `test_livekit_google_31_realtime_say_text_allows_interruptions` | test_livekit_worker.py | `allow_interruptions` flag logic — no redaction/error-handling involvement |
| `test_livekit_finalize_persists_usage_and_calculates_cost` | test_livekit_worker.py | Finalization/usage/cost calculation — different function entirely |
| `test_livekit_opening_auto_advances_single_non_end_start_transition` | test_livekit_worker.py | Auto-advance-after-opening logic |
| `test_finalize_persists_latency_and_disposition_in_gathered_context` | test_phase2_livekit_integration.py | Fails with `ConnectionRefusedError` inside `post_call_triggers.py` — an actual network call this offline test environment cannot reach |

`python -m compileall api\routes api\services api\tests`: exit 0, no errors.

---

## 4. Preserved log paths and behavior

Confirmed unchanged by the new regression test and by direct review:

- `workflow_run.logs.livekit_events` still receives a `"startup_failed"`
  event on every startup failure (plus the new `error_class`/`error_code`/
  `stage` fields).
- `workflow_run.annotations.livekit_startup_error` is still set.
- `is_completed=True`, `state=WorkflowRunState.COMPLETED`,
  `call_disposition="FAILED"` still persisted identically.
- The `InvalidCallDurationError` cleanup path (`_cleanup_rejected_startup`,
  room deletion, shutdown reason) is untouched — it runs on the exception
  type, not on anything `_record_livekit_startup_failure` returns.
- Finalization (`_finalize_livekit_workflow_run`, `_settle_finalize_task`)
  is untouched; `_safe_finalize_error` (the function this fix reuses) is
  unmodified.
- The known, pre-existing limitation that startup failures are **not**
  pushed live to the browser over the LiveKit data channel is unchanged by
  this fix — this fix changes *what* is persisted, not *where* it is
  surfaced. `accept/health-check.ps1` check 8 states this explicitly.

---

## 5. Proof synthetic secrets never appear

`api/tests/test_livekit_startup_redaction.py` is the proof artifact; running
it is the proof, not reading it. Markers used: a Gemini-key-shaped string, a
LiveKit-secret-shaped string, a bearer token, an `Authorization:` header
value, a credential-bearing URL with the key as a query parameter, a JSON
body containing an `api_key` field, and a combined multiline exception
stacking all of the above. For each, the test asserts the marker string is
absent from: every captured log line, the full persisted `livekit_events`
entry (all fields, recursively flattened), and the full persisted
`annotations` dict. All 18 tests pass (§3). `accept/health-check.ps1` check
9 runs this file on every invocation and fails loudly if it ever stops
passing.

---

## 6. Rollback procedure

All changes are isolated to the files listed in §2; no migrations, no DB
schema changes, no dependency changes.

**`api/services/livekit/worker.py`**: revert to defining
`_safe_error_text` as `return f"{type(exc).__name__}: {exc}"[:limit]`;
remove `_STARTUP_ERROR_CODES`/`_startup_error_code`; revert
`_record_livekit_startup_failure` to its prior body (no `stage` param, uses
`logger.exception`, persists `error_text` from the old helper); remove
`stage=` from both call sites in `entrypoint()`.

**`api/tests/test_product_mode_mapping.py`**: revert
`test_duration_rejection_records_failed_terminal_run` to assert
`safe_message = f"InvalidCallDurationError: {error}"` (not recommended —
this reinstates an assertion that the secret-leaking behavior is correct).

**`api/tests/test_livekit_startup_redaction.py`**: delete the file.

**`accept/health-check.ps1`**: replace check 9's pytest-execution block
with the prior static `Warn "9. ..." "..."` call (not recommended — this
reinstates a check that cannot fail even when the underlying code regresses).

**`accept/*.ps1` encoding/Join-Path fixes**: no rollback recommended; these
are unconditional correctness fixes for Windows PowerShell 5.1 with no
behavioral trade-off.

---

## 7. Remaining uncertainty / residual risk

- **Browser-feedback path, by design, not by exhaustive proof.**
  `_record_livekit_startup_failure` takes no session/room/ctx argument, so
  it cannot call `_publish_feedback` — this is pinned by
  `test_startup_failure_cannot_reach_browser_feedback_by_construction`
  (asserts the exact parameter set). This proves the *current* function
  signature cannot reach the browser; it does not prove no other code path
  anywhere in the worker ever will. A full audit of every `_publish_feedback`
  call site in `worker.py` for similar leakage was out of scope for this
  fix (that function is called from several other places unrelated to
  startup failure) and was not performed.
- **`routes/livekit.py::create_livekit_session`** returns
  `str(exc)` verbatim in an HTTP 503 `detail` field when
  `LiveKitConfigurationError` is raised. Reviewed and confirmed **not**
  currently vulnerable: that exception's only raise site
  (`require_livekit_configured()`) only ever lists missing variable
  *names* ("LIVEKIT_URL", "LIVEKIT_API_KEY", ...), never values, so there is
  no value-bearing message for `str(exc)` to expose today. This is fragile,
  not fixed: if a future change to `require_livekit_configured()` ever
  includes a value in that exception's message, this HTTP response would
  leak it, and nothing in this change would catch that. Not modified, since
  it is not a currently-confirmed defect and is a different module/pattern
  than the one in scope.
- **Loguru sink configuration** was not inspected for `backtrace=False`/
  `diagnose=False` settings; the fix (switching to `logger.error` with a
  pre-redacted string, never `logger.exception`) is correct regardless of
  sink configuration, but this was not independently confirmed against the
  project's actual logger setup in `api/app.py` or a logging config module.
- **4 pre-existing, unrelated test failures** (§3) were observed but not
  investigated further, per the instruction to run "the smallest relevant
  regression" rather than the full suite. They should not block this
  change, but an independent reviewer should confirm they are not secretly
  related (my own read of each is that they are not — see the table in §3
  for the specific reasoning).

---

## 8. Source hashes after changes (SHA-256)

```
api\services\livekit\worker.py                      129F3DBCFE85B4516CC47D81BDF854BBB9AFE7023BF20ACAB5BF7CA76AA2B379
api\tests\test_product_mode_mapping.py               8745D83F4B5663A244686B1D51D2B2302983CC0CD4C11281214D2C68FB1F62DE
api\tests\test_livekit_startup_redaction.py           477DBB5A28C60AE24390AC1A154967EF1B371EFF4AECF0FD72553AEE5A753051
accept\health-check.ps1                               59489D284EC5B5A1CDC52E54B8ABA21C4CCA3E1E4998DA3987442736E0975808
accept\start-accept.ps1                               49CC831FA804D5D42962331C52134FFBE799ADFA0628E3B8DFE9F862E4FAE9BD
accept\stop-accept.ps1                                3EA382C494266AD88D23C44ED69BA0666F07A46A9034FF953088A681C81D7FB7
accept\docker-compose.accept.yaml                     1389B6048FF750D3EDFF4BDF6B14D66C5F5AA6A62B967EF730E9B4D7419977F3
```

---

## 9. Acceptance-script re-audit (Phase 3 checklist)

| Item | Status | Evidence |
|---|---|---|
| `.env.accept` is the only file intended to hold raw credentials | PASS | Unchanged from prior review; re-confirmed by reading all 5 `accept/` files again |
| UI/npm/Next.js never inherit provider secrets | PASS | `$uiSensitiveVars` strip/restore in `start-accept.ps1`, unaffected by today's edits |
| Frontend port 3001 targets candidate API 8015 | PASS | Manifest-verification guard in `start-accept.ps1` and check 3 of `health-check.ps1`, unaffected by today's edits |
| Docker containers/volumes isolated | PASS | Explicit `container_name`s, single named volume, unchanged |
| Stop/wipe cannot affect unrelated services | PASS | `docker compose -f <file>` always scoped; unchanged |
| LiveKit signaling/media ports correct | PASS | 7880/tcp, 7881/tcp, 50000-50019/udp, bound to 127.0.0.1; unchanged |
| `D:\spx-voice-mvp\.venv` invoked read-only | PASS | Only `python.exe` invoked from that path; `PYTHONDONTWRITEBYTECODE=1` set; unchanged |
| No command writes to `D:` | PASS | Confirmed by reading all 5 files again; no write target under `D:\` anywhere |

**New issues found and fixed during this pass** (not part of the original
secret-leak defect, found while re-touching these files):

1. All three `accept/*.ps1` scripts contained an em dash character that
   Windows PowerShell 5.1 (no UTF-8 BOM on these files) misreads via the
   system ANSI codepage, causing hard parse failures (`MissingEndParenthesisInExpression`,
   `MissingEndCurlyBrace`) the moment `health-check.ps1` was actually run.
   Fixed by removing all non-ASCII characters from all three scripts.
2. Four `Join-Path` calls in `health-check.ps1` used the multi-segment form
   only supported in PowerShell 7+; Windows PowerShell 5.1 throws
   `PositionalParameterNotFound`. Fixed by combining extra segments into the
   child-path argument.
3. The Gemini-key presence check's `ForEach-Object { $geminiSet = $true }`
   never updated the outer-scope variable (child-scope shadowing), so it
   always reported FAIL regardless of the file's actual contents. Fixed by
   switching to the `foreach` statement.

`health-check.ps1` was executed end-to-end after all three fixes (environment
not started, so checks 1/2/3/4/5/7 correctly FAIL on "not running yet" — this
is the expected pre-start state, not a new defect): see §3 for the real,
passing check-9 output embedded in that run.

---

## 10. What the independent reviewer must verify

Per the authorization for this change, the author's own assessment above is
not a substitute for independent review. The reviewer should specifically
check:

1. **Redaction is category-only.** Read `_safe_error_text`,
   `_safe_finalize_error`, `_startup_error_code`, and the rewritten
   `_record_livekit_startup_failure` directly; confirm no code path
   concatenates `str(exc)`, `exc.args`, or any provider-exception attribute
   into a logged or persisted value.
2. **No secret-bearing path remains.** Re-run
   `api/tests/test_livekit_startup_redaction.py` independently; optionally
   add further adversarial inputs (e.g. a provider exception with a nested
   `__cause__`/`__context__` chain) and confirm they still do not leak.
3. **Tests preserve original failure behavior**, not just the new
   redaction: confirm `is_completed`, `state`, `call_disposition`,
   `workflow_run_id` correlation, and the `InvalidCallDurationError`
   cleanup path are bit-for-bit unchanged from before this fix (§4).
4. **Acceptance scripts remain isolated**: re-run the full prior security
   review's checklist (ports, volumes, venv read-only, no `D:` writes) —
   §9 only re-confirms it, an independent pass should not skip re-verifying
   it directly.
5. **No protected path was modified**: confirm via `git`/directory diff (or
   direct inspection, since this location is not a git repository) that
   nothing under `D:\spx-voice-mvp`, any frozen candidate, or
   `F:\spxv1\spx-voice` was touched. This agent made no tool call against
   any of those paths in this session.

Do not proceed to Phase 6 (owner credential handoff) or Phase 7 (start the
isolated environment) if this review returns BLOCKED or CONDITIONAL.

---

## Next action

**Independent review of this checkpoint**, per §10. Until that review
returns ACCEPTED, Phase 6 (owner Gemini-key handoff) and Phase 7 (starting
the isolated acceptance environment) do not proceed.
