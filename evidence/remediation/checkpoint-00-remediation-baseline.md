# CHECKPOINT 00 — REMEDIATION BASELINE (2026-11-20 ~16:15 IST)

- **Objective:** Create isolated remediation candidate from the frozen candidate; record a clean, verifiable baseline before any P1 correction.
- **Source (frozen, read-only):** `C:\Users\JITEN\spx-voice-releases\spx-full-implementation-20261120-0710`
- **Destination:** `C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615`
- **Copy method:** `robocopy /E` excluding `.deps`, `.runtime`, `.tmp`, `.pytest_cache`, `node_modules`, `__pycache__`, `.venv`, `.git`, `*.pyc`, `*.pyo`, `.env`, `.env.*`. Result: 1,314 files, **0 reparse points**, 0 `.env*` files, `.deps` not copied (clean restoration possible).
- **Frozen-candidate integrity spot-check:** `api/services/livekit/worker.py` MD5 `3552F1B3A2BF17302ABD942787039B94` — matches pre-remediation record. No runtime process writes into the frozen tree.
- **New source-only baseline manifest:** `evidence\remediation\manifest-release-sha256.txt` — **1,119 rows, SHA-256, verification re-hash = 0 mismatches.** Manifest own SHA-256: `F7DD38D9F0D5AC8AD622E79E96E2D3A2B24223BCEAB17F9A62D94AEFCA67943B`. Covers api source/tests/migrations, ui/src, ui scripts, package lockfiles, compose files, config templates, scripts/, docs/. Excludes: evidence logs, historical zips, runtime state, secrets, caches. (Replaces the old 960-row manifest which included `api/.runtime/incidents.jsonl` and missed some release-critical roots.)
- **Tool versions:** Python 3.12.10 · Node v24.14.1 · Docker 29.8.2 · Windows 11 Pro.
- **Test environment (by name only):** `DATABASE_URL` → owned disposable PostgreSQL (`spx-gate3b-pg-20261120` :55440, db `spx_campaign_test`-style throwaway per suite), `REDIS_URL` → owned disposable Redis (:6381, requirepass set — value never recorded), `ENVIRONMENT=test`, `PYTHONPATH=<candidate>\.deps` (must be restored via win32_deps step or existing venv path), `PYTHONUTF8=1`.
- **Resource note:** ~350 MB free RAM — MinIO and Playwright remain BLOCKED; no large load tests on this host.
- **Known limitations:** `.deps` (pipecat/platformdirs shims) excluded from copy; restored on demand for test runs. Volatile logs live under evidence only.
- **Rollback:** delete this candidate; frozen candidate and D: remain untouched by construction.
- **Next single action:** P1-01 — circuit breaker fail-closed (read `api/services/campaign/circuit_breaker.py`, `campaign_orchestrator.py`, `status_processor.py`, `campaign_call_dispatcher.py`, `api/tests/test_circuit_breaker.py` first).
