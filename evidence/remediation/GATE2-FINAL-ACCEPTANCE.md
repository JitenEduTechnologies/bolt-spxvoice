# GATE 2 — FINAL ACCEPTANCE (status: OPEN — keyless work complete, owner steps remain)

Candidate: spx-production-remediation-20261120-1615 · Updated 2026-11-22
Decision: **NOT COMPLETE.** Gate 2 completes only after the owner steps below (2C-1 screenshots, re-review sign-off, one authorized Gemini Test Audio session). No provider key has been entered and no provider call has been made.

## What is done (keyless, offline-verified)
| Item | Result | Evidence |
|---|---|---|
| P1-A node-secret encryption/masking/org binding/migration/runtime decrypt | Reviewer: sound | GATE2-P1-CLOSURE-CHECKPOINT.md |
| P1-B endpoint policy / SSRF / runtime recheck | Reviewer: sound; P2 follow-ups fixed (below) | same |
| 2C-2 unavailable-provider API + UI | Implemented; reviewer: sound | provider_availability.py, user.py, ServiceConfigurationForm.tsx |
| Affected Python tests | **187 passed, 1 skipped** (skip = Tuner SDK importorskip, explicit) | gate2-final-pytest-affected.log |
| UI logic test / real-render test | 6 passed / 11 passed (headless Chrome, 390px; control reproduces the original crash) | gate2-final-ui-node-test.log, gate2-final-ui-render-test.log |
| `tsc --noEmit` | exit 0 | gate2-final-tsc.log |
| UI `next build` (after ProviderSelect fix; exit 0) | complete | gate2-final-ui-build.log |
| Keyless health | **9 PASS / 0 FAIL / 2 SKIP** (worker + workflow 1 need a login session) | gate2-final-health-check.log |
| Manifest | 52 files re-hashed | gate2-changed-files.sha256 |
| Secret-shape scan of new logs | 0 hits | (scan run 2026-11-22) |

## Re-review P2s addressed this pass
- Embeddings client now endpoint-checked at construction (`gen_ai/embedding/factory.py`) — tests added.
- CGNAT 100.64/10, NAT64 64:ff9b::/96, 6to4 2002::/16 rejected (public embedded IPv4 still allowed) — tests added.
- Plaintext node-secret storage without a key now logs a secret-free warning — test added.
- Documented as accepted residuals (docs/developer/secret-storage.md): null-org shared context, blocking `getaddrinfo`, non-Authorization key headers on redirect.
- Realtime rejection test: not applicable — no realtime provider is unavailable (`test_nothing_disappears_silently` covers it).
- Pre-existing failures (6) unchanged and unrelated; stage1a test_2 justified by file identity with baseline.

## Owner cross-verification checklist (2C-1, no key, do NOT click Save)
Open http://127.0.0.1:3001/model-configurations in your signed-in Chrome; turn Realtime OFF (page state only).
1. **LLM** dropdown — selectable: OpenAI, Google, DeepSeek. Under "Not available here" (greyed, unclickable): aws_bedrock, azure, dograh, groq, openrouter, speaches. Pick DeepSeek → model field appears.
2. **Voice** — selectable: cartesia, openai. Disabled: camb, deepgram, dograh, elevenlabs, rime, sarvam, speaches. Pick cartesia → model + voice fields.
3. **Transcriber** — selectable: deepgram, openai. Disabled: assemblyai, cartesia, dograh, gladia, sarvam, speaches, speechmatics. Pick deepgram → model + language fields.
4. Expand "N providers not available in this deployment": only fixed text per provider, no paths/errors/URLs/keys.
5. Shrink the window to ~390 px: no horizontal scrollbar; lists wrap.
6. DevTools → Network: no PUT/POST to `/user/configurations/user`.
Save screenshots of steps 1–3 and 5 to evidence\remediation\render\ (or tell me where they are).

Independent spot checks you can run:
- `accept\health-check.ps1` → expect 9 PASS / 0 FAIL / 2 SKIP.
- Hash drift: `Get-FileHash` any file vs gate2-changed-files.sha256.
- Pytest (PYTHONPATH=<candidate>\.deps;<candidate>, ENVIRONMENT=test, DATABASE_URL/REDIS_URL = accept values): `api\tests\test_p1_node_secrets_and_endpoints.py test_provider_availability.py test_embedding_settings.py` → all pass, 1 skip.

## Remaining to COMPLETE Gate 2
1. 2C-1 screenshots (owner) → 2C-2 formally ACCEPTED.
2. DONE 2026-11-22: re-review of the 4 post-verdict files → ACCEPTED (see GATE2-P1-CLOSURE-CHECKPOINT.md).
3. OWNER GATE: owner privately enters one disposable Gemini key via the dashboard AND explicitly authorizes exactly one Gemini Test Audio session.
4. 2D: one bounded session with the full checklist (greeting once, transcripts, interruption, end-call, finalize once, recording status, cleanup, no key anywhere).
5. Then rewrite this file with the 2D result and mark COMPLETE/BLOCKED.
