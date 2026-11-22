# SPX Voice - Jiten Progress Ledger

Last reconciled: 2026-11-21 (Asia/Kolkata)

This is a sanitized resumption ledger for the remediation candidate. It is navigation evidence, not proof; claims must be checked against source, tests, logs, and checkpoints.

## Boundaries

- Active candidate: `C:\Users\JITEN\spx-voice-releases\spx-production-remediation-20261120-1615`
- `D:\spx-voice-mvp`: protected/read-only until owner-approved integration.
- `C:\Users\JITEN\spx-voice-releases\spx-full-implementation-20261120-0710`: frozen.
- `F:\spxv1\spx-voice`: never access.
- Never place secrets in source, chat, logs, evidence, manifests, workflow JSON, or screenshots.
- No paid provider call, real carrier operation, integration, or deployment without owner approval.

## Gate status

| Gate | Status |
|---|---|
| P1-01 circuit breaker | Accepted for code-review scope |
| P1-02 DNC fail-closed | Accepted for code-review scope |
| P1-03 financial admission | Accepted for code-review scope |
| Gate 1 LiveKit startup redaction | Independently accepted |
| Gate 2 isolated LiveKit/Gemini acceptance | In progress |
| Gate 3 runtime/UI correctness | Pending |
| Gate 4 / P1-04 atomic financial ledger | Not started |
| Gate 5 / P1-05 Vobiz webhook security | Not started |
| Gate 6 full automated verification | Pending |
| Gate 7 capacity/cost verification | Pending |
| Gate 8 final freeze/review | Pending |
| Owner live acceptance | Pending |
| Integration/deployment | Not authorized |

## Accepted safety work

### P1-01

- Redis refusal, timeout, auth failure, and unavailable state fail closed.
- Scheduling and per-recipient dispatch stop before provider effects.
- Order A 53 passed; Order B 53 passed; T4-08 1 passed; real wrong-auth 1 passed; stale recovery 1 passed.
- Collection reconciliation: 54 behavioral tests including standalone T4-08.
- Evidence: `evidence/remediation/checkpoint-P1-01-iter52-ACCEPTED.md` and `p1-01-iter52-collection-reconciliation.md`.

### P1-02

- DNC database/compliance uncertainty blocks calls.
- Standalone: 11 passed; combined order runs: 64 passed each.
- Evidence: `evidence/remediation/checkpoint-P1-02-iter01-ACCEPTED.md`.
- Real PostgreSQL fault injection remains for Gate 6.

### P1-03

- Budget/pricing/admission exceptions stop dispatch.
- Slot release, failed queued-run state, and sanitized campaign event added.
- Missing `copy` import fixed.
- P1-03 suite: 19 tests; combined P1 run reported 83 passed.
- DeepSeek registered under LLM; Cartesia under LiveKit TTS; Deepgram under LiveKit STT.
- Evidence: `checkpoint-P1-03-iter01-ACCEPTED.md` and `checkpoint-P1-03-review-provider-discovery-final.md`.
- Registration is not live-provider acceptance.

### Gate 1 - startup redaction

- Raw provider errors no longer persist through startup or opening-audio-cache paths.
- Worker escapes only sanitized `LiveKitStartupError`; chained context is suppressed with `from None`.
- Adversarial markers covered keys, tokens, headers, credential URLs, JSON, and multiline errors.
- Redaction suite: 42 passed; four relevant suites: 231 passed; required seven-suite run: 268 passed, 4 known unrelated failures.
- Independent verdict: ACCEPTED.
- Evidence: `evidence/remediation/checkpoint-livekit-startup-redaction-final.md`.
- Any stale subtotal of 207 must be corrected to 231.

## Gate 2 work completed

- Isolated ports: API 8015, UI 3001, PostgreSQL 55444, Redis 6384, LiveKit 7880.
- LiveKit UDP media range moved from Windows-reserved 50000-50019 to 51000-51019 and server advertisement was aligned.
- Redis readiness uses `REDISCLI_AUTH`; focused tests 5/5.
- Gemini parent-process cleanup uses an outer `try/finally`; focused tests 4/4.
- Candidate `.deps` is on `PYTHONPATH`; Pipecat imports from the candidate dependency directory.
- The temporary import guard that silently disabled Text Chat was reverted.
- Raw Windows path embedding in `python -c` was replaced by temporary `PYTHONPATH`; focused path/preflight tests passed.
- Next.js backend targeting/stale-build checks and candidate health probes were corrected in acceptance scripts.
- Latest handoff reported PostgreSQL, Redis, LiveKit, migrations, candidate API/UI startup without Gemini, `provider_status=provider_unconfigured`, and `livekit_enabled=true`.
- Acceptance dashboard target: `http://127.0.0.1:3001`.

## Gate 2 blockers before a real key

Do not enter a real Gemini key until these are independently resolved:

1. New Fernet encryption for dashboard provider keys requires final independent security review.
2. `workflow_configurations.model_overrides.api_key` can still store plaintext and must be prohibited, migrated, or encrypted/referenced safely.
3. `accept/run/config-encryption.key` needs current-user ACLs, atomic creation, exclusion from packages/evidence/sharing, rotation/recovery, and no silent regeneration when ciphertext exists.
4. `-PromptGeminiKey` was reported to seed the DB, contradicting its ephemeral name; it must be truly process-only or honestly renamed.
5. Local prefix validation must not claim provider validity or reject future valid formats.
6. Changes to `at_rest.py`, `user_client.py`, health responses, run creation, and `system_monitor.py` need independent review.
7. Pipecat lacks a DeepSeek adapter; Deepgram STT -> DeepSeek LLM -> Cartesia TTS is incomplete.
8. A strict xfail for missing DeepSeek is unresolved coverage, not a pass.

## Exact next task

1. Audit/finalize provider-key encryption, tamper handling, tenant isolation, masking merge, concurrency, backup/export, rotation, and missing-key behavior.
2. Remove plaintext API keys from workflow JSON/DB overrides.
3. Secure/exclude the Fernet key from shareable artifacts.
4. Make Gemini prompt persistence semantics truthful.
5. Implement the Pipecat DeepSeek adapter.
6. Prove the synthetic Premium chain resolves as Deepgram -> DeepSeek -> Cartesia with no provider call.
7. Remove the strict xfail only after real assertions pass.
8. Independently review the API and acceptance-script changes.
9. Only after ACCEPTED may the owner save a disposable key or use a genuinely ephemeral prompt.

## Known unresolved regressions

- Two interruption/auto-advance expectation failures involving `allow_interruptions`.
- One finalization mock sequencing failure (`StopAsyncIteration`).
- One post-call trigger network dependency failure.
- These have been called pre-existing/unrelated but remain unresolved; the full regression is not green.

## Remaining gates

### Gate 3

- Fix fresh-install validation bypass.
- Replace transport-only `Connected` with worker/provider/conversation readiness states.
- Surface sanitized startup errors in the browser.
- Reconcile frontend/backend LiveKit readiness.
- Verify provider dropdowns/fields in the real browser.
- Resolve settings delay/confusion and known regressions.

### Gate 4 / P1-04

- Durable atomic reservation, wallet/budget concurrency, idempotency, settlement/release, duplicate callbacks, worker-crash recovery, audit ledger, migration, rollback, and concurrency tests.

### Gate 5 / P1-05

- Vobiz raw-body signature, timestamp/replay protection, idempotency, tenant/call identity, lifecycle/financial integrity, and secret redaction.

### Gate 6

- VOICE-02/03, Stage 4 hang, LiveKit worker failures, backend/UI regression, order permutations, PostgreSQL/Redis/worker fault injection, migration rollback, backup/restore, secret scan, tenant isolation, realtime dashboard, and MinIO checks where possible.

### Gate 7

- Synthetic average/peak/burst/soak tests for 30,000 calls/day.
- Worker, PostgreSQL, Redis, transcript/event, and recording measurements.
- Synthetic carrier/engine cost reconciliation.
- No INR 1.80-1.85/minute or 30,000/day claim without live evidence.

### Gate 8

- Final regression, clean inventory, double-verified SHA-256 manifest, secret scan, stable evidence, rollback/integration proposal, and independent P0/P1/P2 review.

## Owner/live acceptance

Still required: desktop/mobile UX, real microphone/speaker, Gemini Live, live Deepgram/DeepSeek/Cartesia, silence/turn-taking/barge-in, recordings/MinIO, inbound/outbound Vobiz, CDR/billing, actual cost, provider quotas, capacity, monitoring, backup, and rollback.

## Final release sequence

Gate 2 -> Gate 3 -> P1-04 -> P1-05 -> Gate 6 -> Gate 7 -> Gate 8 -> owner live acceptance -> protected D: decision -> non-destructive integration -> post-integration regression -> deployment approval -> production smoke test -> formal handover.

## Honest status

SPX Voice is not production-approved and is not verified for 30,000 calls/day or INR 1.80-1.85/minute. Important fail-closed and redaction controls are implemented, but Premium runtime, atomic financial accounting, webhook authentication, full regression, live acceptance, integration, and deployment remain open.
