# Gate 2 checkpoint: P1-A / P1-B closure (keyless)

Date: 2026-11-21 23:58 IST. Supersedes the open-P1 section of `GATE2-KEY-SECURITY-CHECKPOINT.md`.
Status: **IMPLEMENTATION COMPLETE - INDEPENDENT RE-REVIEW REQUIRED.** Gate 2 is not accepted.

## Phase A (keyless environment): BLOCKED
Docker Desktop is not running (`dockerDesktopLinuxEngine` pipe missing; machine rebooted 22:54). It was not started because
its restart policies may also start unrelated D: containers. `start-accept.ps1`, `health-check.ps1`,
`test_workflow_versioning.py` (DB-backed), API/UI ownership, PG/Redis/LiveKit health, and the dashboard provider-visibility
check were therefore NOT run. Provider-options API behaviour is covered offline only by `test_provider_discovery_livekit_filter.py` (pass).

## P1-A: workflow node secrets
- `api/services/configuration/node_secrets.py`: seals `qa_api_key`, `tuner_api_key` and every integration
  `sensitive_fields` value with a Fernet envelope bound to `workflow-node:org:<organization_id>`.
- The static secret-field list (`masking._NODE_SECRET_FIELDS`) now covers unloaded integrations (tuner was invisible here
  because its SDK is missing, so its key was stored plaintext and unmasked). A source-scanning test keeps the list complete.
- Sealing is enforced in the DB layer: `WorkflowClient.create_workflow` and `save_workflow_draft` (every writer). Draft,
  publish and revert copy rows of the same workflow, so envelopes stay valid.
- Rejected with 400: a residual masked placeholder, and a foreign-organization or undecryptable envelope.
  `ConfigEncryptionRequiredError` returns 503.
- Responses mask the stored, sealed definition (create and template routes changed), so masked round-trips merge back
  correctly.
- Decryption happens only in the QA LLM config and in tuner delivery. Failure is fail-closed: QA reports no_api_key; tuner
  returns `credential_unreadable` and is not sent. The tuner validation log no longer prints input values.
- The maintenance script encrypts and rotates legacy node secrets.

## P1-B: endpoint / key exfiltration
- `api/services/configuration/endpoint_policy.py`:
  - Built-in host allowlist: HTTPS, default port, Azure/ElevenLabs official subdomains.
  - Custom endpoints only via operator-set `SPX_ADMIN_PROVIDER_ENDPOINTS`, then SSRF-checked on literal and resolved IPs.
  - Rejected: userinfo, percent-encoded hosts, non-ASCII, backslash, numeric IPv4 encodings, IPv4-mapped IPv6, metadata
    hosts, and non-https.
  - Local development requires the explicit `SPX_TRUSTED_LOCAL_ENDPOINTS=true` plus operator approval.
- Workflow overrides may only name official endpoints, enforced in `resolve_effective_config` and in the DB draft guard.
- The user-config PUT rejects a credentialed custom endpoint with 400.
- Re-checked immediately before client construction: the Pipecat factory (LLM, ElevenLabs TTS) and the LiveKit worker.
- Keyless sections (local Speaches) are unrestricted.
- Residual: DNS rebinding between the check and the SDK's own connect is not pinned for operator-approved hosts.

## Tests
- `api/tests/test_p1_node_secrets_and_endpoints.py`: 59 passed, 1 skipped. The tuner runtime test is skipped because
  `tuner_pipecat_sdk` is not installed.
- Affected suites (19 files), see `gate2-p1-affected-pytest.log`: 587 passed, 6 failed, 2 skipped.
  - 2 failures were fixed afterwards: the template response fallback, and the DeepSeek base_url test, updated to require
    operator approval and to assert rejection otherwise.
  - Rerun of the template, Premium and stage1a files: 61 passed, 1 failed (`test_2_anonymous_upload_denied`).
- Residual failures, all baseline-proven on the frozen `spx-full-implementation-20261120-0710` copy:
  - `test_livekit_worker.py`: `test_livekit_google_31_realtime_say_text_allows_interruptions`,
    `test_livekit_finalize_persists_usage_and_calculates_cost`,
    `test_livekit_opening_auto_advances_single_non_end_start_transition`.
  - `test_stage1a_security.py::test_2_anonymous_upload_denied` (MinIO presigned URL; the test and `s3_signed_url.py` are
    identical to the frozen copy).
  - Previously recorded and not re-run here: `test_phase_c3_health_monitoring::test_01`,
    `test_stage1b_blocker_fixes::test_playback...`.
- DB suites: historical only (21:27 pass). Not re-verified.

## Hashes
`gate2-changed-files.sha256` (42 files). New this phase: `endpoint_policy.py`, `node_secrets.py`,
`test_p1_node_secrets_and_endpoints.py`. Changed: `workflow_client.py`, `routes/user.py`, `routes/workflow.py`,
`masking.py`, `resolve.py`, `service_factory.py`, `livekit/worker.py`, `qa/llm_config.py`, `tuner/completion.py`,
`config_secrets_maintenance.py`, `test_premium_pipeline_resolution.py`, `secret-storage.md`.

---
## Independent re-review (read-only, 2026-11-22) — VERDICT: CONDITIONAL

Reviewer did not implement the changes. Manifest: all 49 files in gate2-changed-files.sha256 match SHA-256 (no drift, none missing). The "42 files" count earlier in this document is stale; the manifest is 49 files.

Area verdicts: P1-A node-secret protection sound; P1-B endpoint policy sound (DNS-rebinding residual risk documented in docs/developer/secret-storage.md and above); Phase 2C-2 unavailable-provider API/UI sound (reviewer reran test_provider_availability.py: 9 passed; UI .mts test: 6 passed). No assertions weakened (premium test strengthened). Tuner SDK skip explicit (importorskip, reason in gate2-p1-affected-pytest.log). No secret found in evidence/tests; api-8014.log (2.5 MB) not inspected line by line.

P0: none.

P1 (condition for ACCEPTED):
- 2C-2 has no recorded evidence: rendered /model-configurations check not done (gate2-2c-render-check.log = LOGIN_TIMEOUT, render/result.json loggedIn:false), and no saved logs for the 2C-2 pytest, UI .mts test or `npm run build`. Closes with owner-provided screenshots from the authenticated Chrome session (Phase 2C-1) plus recorded test/build logs.

P2 (non-blocking):
- gen_ai/embedding/factory.py:45 and livekit/worker.py:3175 build the embeddings client from base_url+key with no runtime endpoint recheck (legacy stored config could reach an unvalidated host).
- endpoint_policy.py:165 blocking socket.getaddrinfo inside async handlers.
- endpoint_policy.py:138-149 does not cover CGNAT 100.64/10 or NAT64/6to4 (operator-approved hosts only).
- test_p1...py:456-469 proves only httpx drops Authorization on cross-origin redirect; xi-api-key / x-goog-api-key / api-key headers not stripped (only matters if a trusted host redirects).
- node_secrets.py:53-55: null organization_id shares context `workflow-node:org:None` (no tenant isolation for org-less workflows).
- node_secrets.py:105-111: without a key and without REQUIRE_CONFIG_ENCRYPTION, node secrets stay plaintext with no warning log.
- No server-rejection test for an unsupported realtime provider (test_provider_availability.py covers llm/tts/stt).
- stage1a test_2 pre-existing failure justified by file identity with baseline, not a baseline run.

Phase 2C-2: implementation accepted by reviewer as sound; formal acceptance pending the P1 evidence above.
Gate 2 remains OPEN. Not proceeding to 2C-4, key entry or Test Audio.

---
## Addendum 2026-11-22 (post-review follow-up)
P2 fixes applied (embeddings endpoint recheck, CGNAT/NAT64/6to4, plaintext warning, residuals documented). Affected tests: 187 passed / 1 skipped (gate2-final-pytest-affected.log). Manifest now 50 files. The "42 files" figure above is superseded. Status and owner checklist: GATE2-FINAL-ACCEPTANCE.md. Gate 2 remains OPEN.

---
## Re-review of post-verdict changes (read-only, 2026-11-22) — VERDICT: ACCEPTED

Scope: endpoint_policy.py, node_secrets.py, gen_ai/embedding/factory.py, test_p1_node_secrets_and_endpoints.py, hash manifest (50 files, all match), addendum, secret-storage.md residuals. Reviewer ran one pure-function `_unsafe_ip` probe (~25 addresses); no edits, no pytest.

P0: none. P1: none.

P2 (accepted):
- endpoint_policy.py `_unsafe_ip`: deprecated site-local fec0::/10 not blocked (requires an operator-approved host resolving there).
- Org-less workflows share `workflow-node:org:None` context: organization_id is nullable (models.py:368) but authenticated users always get an org (auth/depends.py:100-125); only legacy rows/scripts/tests; no API path found exposing ciphertext (not every route traced).
- Blocking getaddrinfo: runs only for operator-approved origins; a tenant cannot trigger resolution of arbitrary hosts (endpoint_policy.py:218-222).
- Redirects: only official or operator-approved hosts can receive a key; no reproducible tenant attack. Reviewer's httpx/SDK redirect behavior is from background knowledge, not source-verified.

Verified: embeddings recheck covers every path (factory.py:32-37; Google branch takes no base_url); error text omits key; new tests are non-vacuous (public-address control, log-capture); fixtures synthetic; 187 passed / 1 skipped log consistent. Earlier test file could not be diffed (no prior copy), so "not weakened" rests on the new tests being append-only.

This clears review item 2 in GATE2-FINAL-ACCEPTANCE.md. Remaining: owner 2C-1 screenshots, owner gate, 2D.

---
## Addendum 2026-11-22 — Phase 2C-2 UI defect found by owner and fixed
Defect (reproduced by owner at /model-configurations): `SelectLabel must be used within SelectGroup`. Cause: `<SelectLabel>Not available here</SelectLabel>` placed directly inside `SelectContent` (ServiceConfigurationForm.tsx, previous line ~643). It was the only SelectLabel in the app (grep of ui/src). The earlier data-logic tests and `tsc` could not catch a Radix runtime-context error, and the rendered check never completed (LOGIN_TIMEOUT), so it shipped.
Fix: dropdown extracted to `ui/src/components/ProviderSelect.tsx`; label + unavailable items are inside `<SelectGroup>`; separator stays outside; supported items, disabled unavailable items (`unavailable:<id>` values, data-testids), onChange behavior unchanged; the form's grid/mobile classes untouched. The two other `Select`s in the form never used SelectLabel.
Regression test (real render, headless Chrome at 390px): `ui/scripts/test-provider-select-render.mts` → 11 passed (gate2-final-ui-render-test.log). It mounts the real component, opens the dropdown, checks enabled/disabled options, label inside role=group, no page/console errors, disabled click never calls onChange; a control bundle with the original composition MUST crash (it does), proving the test catches this defect.
Other results: unavailable-providers logic test 6 passed; tsc exit 0; `next build` exit 0 (gate2-final-ui-build.log, manifest targets 127.0.0.1:8015). Isolated UI restarted on 127.0.0.1:3001 only (API/Docker untouched). Manifest: 52 files. Live authenticated render NOT verified by me (saved automation profile has no session) — owner to confirm with screenshots. No key entered, no Save, no provider call.
Process note: a failed live render must block 2C-2 acceptance; data-logic tests alone are insufficient for Radix composition.

### Follow-up review of the ProviderSelect fix (read-only, 2026-11-22) — VERDICT: ACCEPTED
P0: none. P1: none. Verified: SelectLabel + disabled items inside SelectGroup, SelectSeparator outside (ProviderSelect.tsx:37-51); only SelectLabel use in ui/src; onChange passed straight through; mobile grid classes unchanged (ServiceConfigurationForm.tsx:621); test is genuine (real component in headless Chrome, control crashes); manifest 52/52 match; no leak surface.
P2: live authenticated /model-configurations render still unverified (owner screenshots pending). Reviewer did not run the test itself (log taken as evidence), and the disabled-click check swallows click errors via `.catch` (the disabled assertion remains meaningful).
Gate 2 remains OPEN: owner 2C-1 screenshots → owner gate → 2D.
