# Gate 2 Phase A verification (keyless), run 2026-11-22

Status: **BLOCKED on two dashboard items; everything else VERIFIED.** Secret-free. No key requested or entered, no provider call,
no Test Audio, no provider configuration saved, no account created.

## Docker / isolation (verified)
- Daemon 29.8.2 reachable after Docker Desktop was started. Inventory before start: `gate2-phaseA-docker-inventory-before.txt`.
  35 containers, **0 running**, **every restart policy `no`** (nothing auto-revived). Compose projects present: `accept` (this
  candidate), `spx-rc4-stage`, `spx-cp02-stage`, `spx-voice-mvp` (D:), `spx-voice` (F:), `spx-phase1-test`, plus unlabelled ones.
  Labels were read only; no D:/F: path was opened.
- After `start-accept.ps1`: exactly the three `accept` containers changed state (exited -> running): `spx-accept-pg-20261121`
  (127.0.0.1:55444), `spx-accept-redis-20261121` (127.0.0.1:6384), `spx-accept-lk-20261121` (127.0.0.1:7880-7881, UDP 51000-51019).
  No other container changed state.
- Ownership: 8015 = uvicorn child of a `cmd.exe` that `cd`s into this candidate (parent cmdline + `accept/run/api.pid`);
  3001 = `next start` from this candidate's `ui/node_modules`. DB = `spx_voice_accept` (users/workflows/user_configurations = 0 before and after tests).
- All five listeners bind **127.0.0.1** after the fix below.

## Start / health (verified)
- `start-accept.ps1`, no flag, no key: exit 0, "Acceptance environment is RUNNING"; key store ready (outside tree, owner-only);
  maintenance step 0/0/0; pipecat importable. Log: `gate2-phaseA-start-accept.log`, `...-2.log`.
- `health-check.ps1` final: **9 PASS / 0 FAIL / 2 SKIP** (worker-running and workflow-1 need a login). `gate2-phaseA-health-check-final.log`.
  UI `/api/config/version` and API health agree: `livekitEnabled`/`livekit_enabled` = true; `provider_status = provider_unconfigured`.
- Keyless: no Gemini variable in the parent shell; 0 key-shaped or synthetic-key markers and 0 encryption-key values in logs/evidence.

## Defects found and fixed this run (script only; P1-A/P1-B untouched)
1. health-check #3 failed right after start: the start script only slept 4 s, so it reported RUNNING while Next.js was still starting.
   Now waits (up to 90 s) until `/api/config/version` shows the API reachable.
2. UI bound to `::` (all interfaces; Next printed a LAN URL), exposing the page where a provider key is typed over plain HTTP.
   Now `next start --hostname 127.0.0.1`. Static test #8 added; test #7 pattern tolerates comment lines.
3. Maintenance note: `test-dashboard-key-startup.ps1` 10/10, `test-key-store.ps1` 11/11.
Changed since the 59/1 manifest: `accept/start-accept.ps1`, `accept/test-dashboard-key-startup.ps1` only (manifest refreshed). All P1 source
hashes unchanged, so `test_p1_node_secrets_and_endpoints.py` was NOT rerun (still 60 collected, 59 passed / 1 skipped: the tuner runtime test needs
`tuner_pipecat_sdk`, which is not installed).

## Database suite (verified, current code, disposable DB only)
`test_workflow_versioning.py` against `127.0.0.1:55444/spx_voice_accept` + Redis 6384: **26 passed, 0 failed**.
Log: `gate2-phaseA-test-workflow-versioning.log`. The earlier 21:27 pass is historical; this is the current result.

## Provider visibility
Verified through the same unauthenticated endpoint the dashboard form uses (`GET /api/v1/user/configurations/defaults`, directly on 8015
and through the UI proxy on 3001; identical). Raw payload: `gate2-phaseA-provider-defaults.json`.
| Service | Offered |
|---|---|
| LLM | **deepseek**, google, openai |
| TTS (Voice) | **cartesia**, openai |
| STT (Transcriber) | **deepgram**, openai |
| Realtime | google_realtime, google_vertex_realtime, openai_realtime |
Provider-specific options load: DeepSeek model {deepseek-chat, deepseek-reasoner} + base_url; Cartesia model {sonic-3}, voice, speed, volume;
Deepgram model {nova-3-general, flux-general-en, flux-general-multi} + 81 languages; Gemini realtime models {gemini-3.8-live, 3.1-flash-live-preview},
voices, 25 languages.

### NOT verified / failing
- **Unsupported providers have no explicit reason (FAIL).** The API silently drops providers the LiveKit worker cannot run and the UI has no
  text for it (no LiveKit/unsupported wording in `ServiceConfiguration*.tsx`). Hidden without any reason: LLM aws_bedrock, azure, dograh, groq,
  openrouter, speaches; TTS camb, deepgram, dograh, elevenlabs, rime, sarvam, speaches; STT assemblyai, cartesia, dograh, gladia, sarvam, speaches, speechmatics.
- **Rendered dashboard page (BLOCKED).** `/model-configurations` redirects (307) to login. Viewing it needs a session, i.e. creating a user in the acceptance
  DB (and the default starter workflow). Not done: it would write credentials into the DB the owner is about to use and pre-empt their own sign-up.
  Playwright and Chrome are available when a session exists.

## Resume
Owner decides: (a) sign up at http://127.0.0.1:3001 themselves, or allow a throwaway account (to be deleted afterwards) so the rendered
`/model-configurations` lists can be checked without saving anything; (b) whether to add a "not available on LiveKit: <reason>" list
(API field + small UI note, ~1 UI rebuild).
