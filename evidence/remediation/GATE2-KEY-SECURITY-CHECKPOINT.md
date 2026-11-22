# Gate 2 checkpoint: dashboard key security + Premium providers

Date: 2026-11-21 23:45 IST. Candidate: this directory. Status: **CONDITIONAL**.
Nothing live-verified: Docker Desktop down after the 22:54 reboot; no provider call made; no credential entered.

## Storage model (verified offline)
- User/dashboard secrets (`api_key`, `aws_secret_key`, `credentials` in llm/tts/stt/embeddings/realtime) are stored as Fernet `enc:v1:<kid>:<payload>`. The Fernet plaintext is `user:<id>` + 0x1f + secret, so the value is owner-bound.
- Workflow `model_overrides` secrets: new plaintext is rejected by the route and by `save_workflow_draft`. An envelope is accepted only if it decrypts to the value already stored in that slot. Legacy plaintext is encrypted in place by `api/scripts/config_secrets_maintenance.py`, which runs at every acceptance start. Legacy envelopes are NOT owner-bound.
- Read failures return 503 with a content-free message. Login no longer fails (as 401) on undecryptable credentials.
- Key file: `%LOCALAPPDATA%\SPXVoiceAccept\config-encryption.key`. Outside the tree, owner-only DACL (verified), atomic creation, never regenerated while any text/json column holds `enc:v1:`. The DB check fails closed.
- `-PromptGeminiKey` is refused; it was renamed to `-SeedGeminiKeyToDb` (persistent, encrypted, requires typing SEED). The default start has no key; the key is entered in the dashboard.
- Docs: `docs/developer/secret-storage.md` (backup, rotation including the telephony caveat, recovery, production KMS).

## Tests (evidence in this folder)
- `gate2-pytest-affected.log`: 15 affected files, **413 passed / 6 failed**. The extra failure was `test_realtime_mask_check_ignores_inactive_stt_tts_placeholders`, caused by my masking change. That change was REVERTED (the existing contract was kept) and replaced with a readiness guard.
- `gate2-pytest-rerun-after-masking-revert.log`: realtime, hardening, dashboard and masked-key files, **81 passed / 0 failed**.
- PS5.1 logs: key-store 11/11, dashboard-key-startup 9/9, preflight 5/5, runtime-readiness 9/9.
- `gate2-frozen-baseline-5-failures.log`: the 5 residual failures fail identically on an unmodified scratch copy of `spx-full-implementation-20261120-0710`. They are pre-existing.
- DB-backed suites (e.g. `test_workflow_versioning.py`) are HISTORICAL ONLY: they passed at 21:27, before the latest edits. They currently ERROR with ConnectionRefused (Docker down). Not re-verified.

## Residual failures (pre-existing, baseline-proven)
1. `test_livekit_worker.py::test_livekit_google_31_realtime_say_text_allows_interruptions` (line 475 dict mismatch)
2. `test_livekit_worker.py::test_livekit_finalize_persists_usage_and_calculates_cost` (post_call_error: un-awaited AsyncMock)
3. `test_livekit_worker.py::test_livekit_opening_auto_advances_single_non_end_start_transition` (line 1158)
4. `test_phase_c3_health_monitoring.py::test_01_healthy_dependencies_report_healthy` (environment: host metrics / infra UNHEALTHY)
5. `test_stage1b_blocker_fixes.py::test_playback_uses_get_recording_by_recording_id_and_streams_200_and_206` (Windows NamedTemporaryFile Errno 13; `workflow_recording.py` and the test file are byte-identical in all 6 trees)

## Open findings
- P1: workflow node secrets (`qa_api_key`, integration node fields) are still plaintext in `workflow_json`.
- P1: `llm.base_url` is user-editable, so an org member's workflow override can send the owner's key to an arbitrary host (pre-existing design; DeepSeek/OpenAI/OpenRouter).
- P2: legacy override envelopes are not owner-bound. Telephony creds are not rotated by the maintenance script (procedure documented). Last-write-wins concurrency. `/health` provider_status ignores decryptability (single-owner aggregate). `organization_usage_client` reads raw config (timezone only). `useWebSocketRTC.tsx:99` falls back to 127.0.0.1:8000. Infra ports and the Redis password are literals in the scripts. Pipecat DeepSeek has no `DEEPSEEK_API_KEY` env fallback (the worker has one). A masked placeholder can be stored in an inactive section (existing contract; readiness now treats it as missing). Tests: the fake session ignores WHERE; the concurrency test is weak.
- Cartesia cancellation/barge-in: hooks verified to be registered (structural only), not exercised live.

## Hashes
`gate2-changed-files.sha256`: SHA-256 of the 36 files changed by this work.

## Resume
Start Docker Desktop, run `.\accept\start-accept.ps1` (no flag), then `.\accept\health-check.ps1`, then re-run `api/tests/test_workflow_versioning.py` against the live DB. Then, with owner authorization, enter ONE disposable Gemini key at `/model-configurations`. Do not edit workflow model overrides or base_url. Test Audio needs separate explicit authorization.
