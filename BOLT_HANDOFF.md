# BOLT HANDOFF — SPX Voice Gate 2 (PRIVATE WIP SNAPSHOT)

**Status: private Gate 2 work-in-progress snapshot. NOT production-ready. Gate 2 is OPEN.**
Snapshot of `spx-production-remediation-20261120-1615` taken 2026-11-22. Source-only: no `.git`, no env files, no keys, no logs, no dependencies, no build output, no raw evidence. Keep the GitHub repo **private**.

## What is in here
`api/` (FastAPI + Alembic + tests), `ui/` (Next.js + tests/scripts), `accept/` (acceptance-stack scripts, no secrets), `docs/`, `deploy/`, `scripts/`, Docker compose files, root Markdown trackers, `evidence/remediation/*.md` (sanitized checkpoints only), and placeholder templates (`.env.example`, `ui/.env.example`, `accept/env.accept.example`). `SNAPSHOT_MANIFEST.sha256` lists every file with its SHA-256; `SNAPSHOT_SCAN_REPORT.txt` is the secret-scan result (file/line/category only).

## Gate 2 state (see `evidence/remediation/GATE2-FINAL-ACCEPTANCE.md`)
- Done and independently reviewed (read-only): P1-A workflow node-secret encryption/masking/org binding; P1-B provider endpoint policy (allowlist, SSRF, runtime recheck); unavailable-provider API + UI. Reviews: CONDITIONAL → P2 follow-ups fixed → ACCEPTED; later ProviderSelect fix → ACCEPTED.
- Last recorded results: 187 Python tests passed / 1 skipped (Tuner SDK not installed, explicit `importorskip`); UI logic test 6/6; real-browser render test 11/11; `tsc` clean; `next build` OK; keyless health 9 PASS / 0 FAIL / 2 SKIP (worker + workflow need a login session).
- Six pre-existing failures are identical on the frozen baseline and unrelated: `test_livekit_worker.py` (3), `test_stage1a_security.py::test_2_anonymous_upload_denied`, `test_phase_c3_health_monitoring.py::test_01_healthy_dependencies_report_healthy`, `test_stage1b_blocker_fixes.py::test_playback_uses_get_recording_by_recording_id_and_streams_200_and_206`.
- Documented accepted residuals (docs/developer/secret-storage.md): DNS rebinding for operator-approved hosts is not pinned; org-less workflows share one encryption context; blocking DNS lookup for approved custom hosts; non-`Authorization` key headers are not stripped on redirect.

## model-configurations UI crash — review status
The owner reproduced `SelectLabel must be used within SelectGroup` at `/model-configurations` (the "Not available here" label sat outside a Radix `SelectGroup`). Fixed by extracting `ui/src/components/ProviderSelect.tsx` (label + disabled items inside `SelectGroup`). A real-render regression test (`npm run test:provider-select-render`, headless Chrome) reproduces the old crash with a control bundle and passes on the fix; reviewer verdict ACCEPTED. **The live authenticated render has NOT yet been verified** — owner screenshots (desktop + ~390 px) are still pending.

## What is NOT accepted
No live provider acceptance is complete. No provider key has been entered, no Gemini Test Audio session has run, no provider call has ever been made against this build. Do not claim Gate 2 complete.

## Exact next task
1. Owner opens `/model-configurations` (signed in, Realtime off, do not Save) and confirms: LLM → OpenAI/Google/DeepSeek selectable; Voice → cartesia/openai; Transcriber → deepgram/openai; greyed "Not available here" group with static reasons; no horizontal scroll at ~390 px; no `PUT /user/configurations/user`. (Checklist in GATE2-FINAL-ACCEPTANCE.md.)
2. Owner gate: owner privately enters ONE disposable Gemini key in the dashboard and explicitly authorizes exactly one Test Audio session.
3. Phase 2D: one bounded Gemini Test Audio session (greeting once, transcripts, interruption, end-call, finalize once, truthful recording status, cleanup, no key anywhere). Then rewrite GATE2-FINAL-ACCEPTANCE.md as COMPLETE/BLOCKED. Do not start Gate 3 / P1-04 / P1-05 before that.

## Local-only acceptance requirements (cannot be done from GitHub/Bolt)
- Docker Desktop with the acceptance stack (`accept/docker-compose.accept.yaml`: Postgres 55444, Redis 6384, LiveKit 7880/7881 + UDP 51000-51019), all bound to 127.0.0.1; API 8015, UI 3001. Start with `accept/start-accept.ps1`; verify with `accept/health-check.ps1`.
- Create `accept/.env.accept` locally from `accept/env.accept.example`. It is git-ignored; never commit it.
- The config-encryption key lives OUTSIDE the tree at `%LOCALAPPDATA%\SPXVoiceAccept\config-encryption.key` (owner-only ACL). Never copy it into the repo. Provider keys are entered via the dashboard and stored encrypted.
- Dependencies are not included: Python from `api/requirements.txt`, UI via `npm ci` in `ui/`. Tests need `ENVIRONMENT=test`, `DATABASE_URL`, `REDIS_URL` set (see `AGENTS.md`).
- Real audio, a browser microphone and a real Gemini key are required for Phase 2D; no hosted CI can substitute.

## Rules for whoever continues
Never commit or print provider keys, tokens or the encryption key. Run a secret scan before the first push. Keep the repo private until the owner decides otherwise. Read `AGENTS.md` and `docs/developer/upstream-compatibility.md` before renames or upstream merges.

## Sanitized in this snapshot (differs from the source candidate)
- `ui/tests/real_browser_test.mjs`: a hard-coded signed JWT test constant replaced with a placeholder string (it had an `email` claim; owner should confirm it was never a real session token).
- `api/tests/test_workflow_qa_masking.py` line 270: random-looking `sk-` fixture replaced with `sk-SYNTHETIC...` of the same length.
- The local acceptance Redis password (a dev default for the 127.0.0.1-only container) replaced everywhere with `REDIS_PASSWORD_PLACEHOLDER` (`accept/docker-compose.accept.yaml`, `accept/*.ps1`, two checkpoint .md files). It is internally consistent, so the stack still starts; set a real local password in your own copy.
- Removed from the copy: `ui/screenshot_login.png`, root screenshots, `.mcp.json`, ad-hoc root scripts (`demo_acceptance_suite.cjs`, `test_login_shot.cjs`), `*.bak` snapshots under `api/tests/_pre_correction_snapshots/`.
- Public default dev credentials (`postgres:postgres`) still appear in docs/CI/compose defaults; they are not secrets.

## Final sanitization pass (2026-11-22)
Removed documentation screenshots that exposed credential-related details: `docs/images/copy-deployment-code.png` (embed token + tunnel endpoint), `docs/images/api-keys.png`, `docs/images/service-keys.png` (partial key prefixes). Their three references (`docs/configurations/api-keys.mdx` x2, `docs/voice-agent/add-to-website.mdx` x1) now carry a neutral "screenshot omitted" note. The scan, exclusion audit and `SNAPSHOT_MANIFEST.sha256` were regenerated afterwards. This remains a private WIP snapshot; **Gate 2 is still OPEN** and nothing here is production-approved.

## Documentation images intentionally excluded (final binary-asset sanitization)
All documentation images were removed because several screenshots showed phone numbers, carrier-account metadata and deployment URLs: the entire `docs/images/` directory, `docs/favicon.ico` and `docs/logo/*.svg` are excluded, and the matching `favicon`/`logo` keys were dropped from `docs/docs.json`. Every Markdown/MDX reference (images and `<img>` tags, 15 files) now reads "Screenshot omitted from the sanitized engineering handoff." No documentation image, GIF, video, audio or other binary asset remains. Only application UI assets stay (`ui/public/*.svg`, `ui/src/app/favicon.ico`), which are part of the Next.js source. This snapshot is still a private WIP snapshot, is not production-approved, and **Gate 2 remains OPEN**.
