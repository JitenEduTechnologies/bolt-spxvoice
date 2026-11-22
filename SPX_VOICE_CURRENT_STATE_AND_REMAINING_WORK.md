# SPX VOICE — CURRENT STATE AND REMAINING WORK

**Report date:** 14 Nov 2026 · **Type:** read-only status report (no implementation performed for this report)
**Repository:** `D:\spx-voice-mvp` · baseline commit `ca56a4a` (12 Nov 2026) + 11 modified and 3 untracked files (uncommitted)
**Authoritative plan:** `SPX_VOICE_ENGINEERING_BLUEPRINT.md` (not modified by this report)
**Requirements:** `C:\Users\JITEN\Downloads\SPX_VOICE_MASTER_ENGINEERING_SPECIFICATION.md`
**Protected original:** `F:\spxv1\spx-voice` — not accessed

## How to read this report

Status vocabulary used throughout:

| Label | Meaning |
|---|---|
| **VERIFIED-LIVE** | Observed in the running system or database during the current engineering program (13–14 Nov) |
| **VERIFIED-TEST** | Passed focused tests that I ran myself in this program (mocks, no providers) |
| **REPORTED-TEST** | Test files and/or evidence files exist and an earlier agent reported a pass; **not re-run by me** |
| **IMPLEMENTED-UNVERIFIED** | Code exists; never exercised end to end with a real provider or browser |
| **PARTIAL** | Some of the required behaviour exists |
| **DEFECT** | Confirmed by code location or reproduction |
| **NOT IMPLEMENTED** | No implementation located |
| **EXTERNAL** | Blocked on an account, credential, provider, carrier, host or regulator |
| **DEFERRED** | Intentionally outside the current release |
| **UNKNOWN** | Evidence unavailable |

A mocked or synthetic pass is never presented as operational proof. Evidence references are indexed in Part 13.

**Two corrections to earlier statements, found while preparing this report (details in Part 5):**
1. **No inbound monetary cap exists in runtime code.** Earlier documents (including the blueprint, G-40) described an "inbound wallet/overdraft cap verified locally". The only "wallet" code is inside `api/tests/test_stage5a_voice_telephony.py`; `check_financial_admission` is called only by the campaign dispatcher; the `DEFAULT_WALLET_OVERDRAFT_LIMIT_PAISE` / `INBOUND_*_RATE_*` environment variables are not read by any runtime module. G-40 is reclassified **NOT IMPLEMENTED**.
2. **The LiveKit worker does not reject a session that has no duration limit.** The enforcement loop is `if self._max_duration_seconds:` (`api/services/livekit/worker.py:1241`); a missing value means an unbounded call. Logged as **G-48**.

---

# PART 1 — EXECUTIVE CURRENT STATUS

1. **Intended product.** One AI voice SaaS for independent Indian businesses with three independently controlled voice modes — Budget IVR (Asterisk, no AI), Standard (Gemini Live via LiveKit), Premium (Deepgram → DeepSeek → Cartesia via LiveKit) — sharing dashboard, CRM, billing, security and reporting; deployable per customer on compatible VPS hosting; with a measured path toward 30,000 authorized call attempts/day in aggregate.

2. **Implemented.** FastAPI backend, Next.js dashboard (Overview, Business, Build, Observe, Admin areas), versioned workflows with draft/publish/restore, Agent Studio with ten industry presets, SSE live-event bus with replay, lead intelligence/CRM/appointments/callback inbox, cost controller and cost classification, Asterisk economy IVR, campaign dispatcher with leases/reclaim/quarantine, LiveKit worker supporting Gemini Live and the Premium provider chain, Vobiz/VoiceLink adapters with signed-callback verification, health monitoring/alert manager/durable incident store/audit service, storage quota and retention.

3. **Verified through tests I ran or live observation (this program).** Workflow lifecycle and mode-mapping fixes (87 focused tests); G-45 duration mapping live in the running API; disposable Workflow 181 published with a worker-readable 60-second limit; invalid limits rejected with HTTP 422; protected records unchanged; API/UI/DB/Redis/MinIO containers healthy; LiveKit packages present in the API image; worker CLI available offline.

4. **Exists but never tested end to end.** Every path involving LiveKit, Gemini, Deepgram, DeepSeek, Cartesia, Vobiz or a real telephone line: real conversation, transcript from real audio, cost from real usage, duration termination, inbound routing, human-audible quality, signed callbacks from the real carrier.

5. **Missing or defective (highest impact).** Inbound monetary cap (G-40, not implemented); worker fail-open without duration limit (G-48); outbound budget check fail-open (G-13); queued/IVR calls not pinned (G-15); pause race (G-14); DeepSeek absent from the LLM registry so Premium cannot be published/run (G-17); "Budget" label publishes Gemini, not the IVR (G-26/G-18); human transfer for LiveKit (G-42); production secrets/HTTPS/cookies (G-08); backup/restore (G-09); server-side disabling of unfinished features (G-41); customer invoicing/payments (no code).

6. **Blocked externally.** LiveKit project and credentials; Gemini spend cap confirmation; Vobiz account/DID/signing secret and eligibility; Deepgram/DeepSeek/Cartesia accounts; VPS/domain/TLS; DLT/TRAI review; carrier CPS/channel allocation; pilot customer acceptance.

7. **Release/package status.** Release 1 in progress: **PKG-1a ACCEPTED (offline scope)**; PKG-1b prepared, not started; PKG-2 partially complete (code fixes and tests done; browser acceptance and commit pending); all other packages not started. Releases 2 and 3 not started.

8. **Next recommended action.** Before any paid call: approve a small worker safety change (G-48: refuse or cap sessions lacking a valid duration limit) and decide the inbound spend-cap approach for the pilot (G-40); then continue the approved offline-first order with **PKG-2**.

---

# PART 2 — DEVELOPMENT HISTORY (from accessible evidence only)

Two naming schemes exist in the repository: "Stage 1A–5A" and "Phase 2–7 / C2–C4". Both are kept as found. Unless marked VERIFIED-TEST or VERIFIED-LIVE, test results below are **REPORTED-TEST** (files exist; pass counts come from earlier agent reports and were not re-run for this report).

| Phase / stage | Intended functionality | Implemented components (located) | Test / evidence | Outstanding | Status | Supporting files |
|---|---|---|---|---|---|---|
| Original application | Dograh-derived voice platform, LiveKit + Gemini first | `api/`, `ui/`, compose stack, 87 Alembic migrations (head `f1a2b3c4d5e6`) | `AGENTS.md`; running stack | — | Baseline | `AGENTS.md`, `docker-compose.yaml` |
| Stage 1A / 1B Security | Credential encryption, private storage, tenant isolation | `api/utils/crypto.py`, `services/telephony/crypto_helper.py`, private MinIO, org-scoped routes | `test_stage1a_security.py` (23), `test_stage1b_blocker_fixes.py` (10); 38 passed reported | Production secrets, rotation | REPORTED-TEST | `SPX_VOICE_HANDOFF.md` §2.1, `SPX_VOICE_REQUIREMENTS_VERIFICATION.md` |
| Stage 2 Economy IVR | Asterisk ARI prerecorded IVR, DTMF | `services/telephony/ari_manager.py`, `ivr/economy_ivr_controller.py`, `asterisk/config/` | 26 passed reported; real local SIP/RTP loopback and 10 concurrent sessions | Carrier trunk; pinned config (G-15) | REPORTED-TEST (local real media) | `STAGE5_VOICE_TELEPHONY_VERIFICATION.md` §3–4 |
| Stage 3 Modes/pricing/storage | Three tiers, caps, storage quota/retention | `services/pricing/cost_controller.py`, `services/filesystem/storage_quota.py` | `test_stage3_modes_pricing_storage.py` (8) | Rates are baseline, not invoices (G-28); outbound cap fail-open (G-13) | REPORTED-TEST | handoff §2.4 |
| Stage 4 Complete SaaS | Onboarding, RBAC, CRM, campaigns, numbers | `services/business/*`, `services/campaign/*`, `smart_did`, `compliance` | `test_stage4_complete_saas.py` (18); matrix: 0 verified / 15 partial / 1 externally blocked | Real provider and browser proof | PARTIAL | `STAGE4_EVIDENCE_REVIEW.md`, verification matrix |
| Stage 5A Voice/telephony local | Local engine verification | conversation state, silence, interruption, language packs | `test_stage5a_voice_telephony.py` (17); matrix: 6 partial | 5B (real AI) and 5C (real carrier) never executed | PARTIAL | `STAGE5_VOICE_TELEPHONY_VERIFICATION.md` |
| Full-suite run (1 Nov) | Whole test suite | — | 976 collected: 910 passed, **64 failed, 1 error**, 2 skipped | Failures never classified (G-11) | OPEN | handoff §4.1 |
| Phase 2 | Lead/objection intelligence; LiveKit dispatch metadata; opening-audio remediation | `lead_intelligence.py`, `objection_responses`, worker opening audio | `test_phase2_lead_and_objections.py` (14), `test_phase2_livekit_integration.py` (5), `test_phase2_remediation.py` (8) | — | REPORTED-TEST | `PHASE_5_TRACKER.md` §4 |
| Phase 3 | Telephony status, smart DID | `status_processor.py`, `smart_did.py` | `test_phase3_telephony_and_smart_did.py` (15) | Real CLI presentation | REPORTED-TEST | `PHASE_5_TRACKER.md` §4 |
| Phase 4 | Campaign safety | dispatcher, cancel re-check, DB locks | `test_phase4_campaign_safety.py` (16) | G-13, G-14, G-15 | REPORTED-TEST + open defects | `PHASE_5_TRACKER.md` §4 |
| Phase 5 | Dashboard integration, SSE bus, owner actions, CRM continuity, outbox | `services/events/realtime_bus.py`, `useRealtimeDashboard.ts`, `chatwoot/outbox.py` | `test_phase5_saas_integration.py` (20, incl. Playwright T5-20), `test_phase5_gap_closure.py` (9) | Real-call data | REPORTED-TEST | `PHASE_5_TRACKER.md` |
| Phase 6A | — | — | **No artefact located** | — | UNKNOWN | — |
| Phase 6B | Provider integration readiness (zero-egress synthetic) | Vobiz/VoiceLink adapters, v3 signature, IVR XML, premium pipeline compatibility | `scratch/phase6b/test_stage_a_integration.py` (6 checks), `test_stage_b.py` (6 checks); no result file located | Real provider evidence; naming collides with safety Stages A–F (G-44) | IMPLEMENTED; results UNKNOWN | `scratch/phase6b/` |
| Phase 7 synthetic | Capacity, fairness, recovery, safety, hardening | lease/reclaim/quarantine in `db/campaign_client.py`, ARQ cron `recover_stale_campaign_runs` (every 2 min), mock telephony/AI | 7A isolation PASS; 7B 100→30,000 synthetic calls, 0 errors, 72 calls/s, p95 456 ms; 7C fairness PASS; 7D recovery PASS; 7E safety PASS; hardening PASS (signed-callback negative tests); sign-off suite (10 + 1 tests) | All with **mock** telephony/AI in isolated containers | REPORTED-TEST (synthetic) | `scratch/phase7/*.json`, `scratch/phase7_signoff/` |
| Pre-sales A (UI acceptance) | Five-area functional acceptance | workflow editor acceptance, multi-node acceptance, benchmarks | `test_workflow_acceptance_presales.py` (8), `test_multi_node_presales_acceptance.py` (3); Business-area video audit 7 Nov (ISS-01…13); `scratch/perf_bench_summary.json` (10 Nov) | Audits for Overview/Build/Observe/Admin **not provided**; ISS re-verification | PARTIAL / evidence incomplete | `C:\Users\JITEN\Downloads\voice_console_saas_business_section_audit_report.md` |
| Pre-sales B (logging inventory) | — | — | **No report located** | — | UNKNOWN | — |
| C1 (logging foundations) | Request traces / correlation IDs | Referenced by C4 ("C1 request traces"); no dedicated test file or report located | — | — | UNKNOWN (code referenced, evidence unavailable) | `services/health/observability_service.py` docstring |
| C2 (billing and audit integrity) | Itemized call cost, CDR reconciliation, audit durability | `services/audit/audit_service.py`, `reconcile_workflow_run_cdr`, `ActionIdempotencyRecordModel` | `test_phase_c2_acceptance.py` (13), `test_phase_c2_safety_corrections.py` (9) | Real CDRs/invoices | REPORTED-TEST | test files (6 Nov) |
| C3 (monitoring and alerts) | Health checks, alert rules, external notification, autonomous scheduler | `services/health/alert_manager.py`, `external_notifier.py` (HTTPS webhook via `SPX_ALERT_WEBHOOK_URL`; unconfigured ⇒ "BLOCKED — OWNER CONFIGURATION"), `provider_monitor.py`, `scheduler.py` (Redis leader election) | `test_phase_c3_health_monitoring.py` (15), `test_phase_c3_autonomous_scheduler.py` (8) | Real alert delivery not configured | REPORTED-TEST | test files (6 Nov) |
| C4 (System Health & Audit Center) | Log explorer, call timeline, incidents, retention, export | `services/health/observability_service.py`, `durable_incident_store.py` (`/app/api/.runtime/incidents.jsonl`), `routes/health.py` (`/status`, `/tenant`, `/incidents`, `/logs`, `/calls/{id}/timeline`, `/retention/*`, `/export`), `ui/src/app/superadmin/health-audit` | `test_phase_c4_observability.py` (19); audit export CSV (9 Nov, 100 rows) | Hostinger-specific label (G-33) | REPORTED-TEST | test file; Downloads CSV |
| Pre-sales D (integrated acceptance) | — | — | **No report located** | — | UNKNOWN | — |
| Claude Code session fixes (13 Nov) | Mode mapping, budget endpoint safety, publish readiness gate, explicit workflow targeting, template route, duplicate-click guard, save feedback, audit on publish/restore, economy-IVR worker guard | `api/routes/business.py`, `api/routes/workflow.py`, `agent_training.py`, `worker.py`, five UI files | 59 focused tests | Browser acceptance; commit | VERIFIED-TEST | uncommitted diff; two test files |
| PKG-1 preflight (14 Nov) | Read-only readiness | — | Found G-45; confirmed credential path, worker command, duplicate-worker guard | — | VERIFIED-LIVE (read-only) | Part 6 |
| G-45 fix | Duration limit reaches worker key | `resolve_max_call_duration_seconds`; both draft and publish handlers | **87 passed** (two focused files) | Live termination proof (G-27) | VERIFIED-TEST + VERIFIED-LIVE (mapping) | Part 6 |
| PKG-1a | Offline readiness | Workflow 181 / definition 250 | Part 6 | — | **ACCEPTED (offline scope)** | Part 6 |

**Historical overclaims to disregard:** `PROJECT_STATUS.md` (31 Oct) states "GATE B Real AI Voice — PASS" and "COMMERCIAL DEMO / CONTROLLED LOCAL PILOT READY", and lists "calculated blended rates". No real Gemini call has ever been made, and the rates are estimates; the later verification matrix (1 Nov) already downgraded these.

---

# PART 3 — FEATURE INVENTORY

Columns: **Status** · **Implementation** · **Evidence** · **Remaining / risks** · **External** · **Package** · **Acceptance**.

| # | Feature | Status | Implementation | Evidence | Remaining / risks | External | Package | Acceptance |
|---|---|---|---|---|---|---|---|---|
| A | Authentication, accounts, permissions | PARTIAL | `routes/auth.py` (signup, login, me), JWT (`utils/auth.py`), `services/auth/depends.py`, superuser `POST /superuser/users`, impersonate, API keys | Login works (VERIFIED-LIVE); RBAC matrix REPORTED-TEST (S4-02); login audit events in export | Secure cookies (G-08); role-change revocation (G-19); **no owner-facing staff invite/role route located (G-49, unverified)** | — | PKG-4a, PKG-F | HTTPS login with Secure cookie; demoted user loses access within one request |
| B | Onboarding, org configuration, industry | PARTIAL | Agent Studio presets (`UNIVERSAL_INDUSTRY_PRESETS`, 10), `organization_configurations` key/value table | Presets validate (VERIFIED-TEST) | Industry not stored per organization; all presets visible (G-36) | — | PKG-12a | Real-estate install shows no clinic items in any area |
| C | Dashboard Overview / Business / Build / Observe / Admin | PARTIAL | Pages under `ui/src/app` (overview, agent, leads, conversations, appointments, campaigns, costs, workflow, model-configurations, telephony-configurations, tools, recordings, reports, usage, settings, superadmin/health-audit, superadmin/runs, superadmin/costs) | Playwright T5-20 (REPORTED); Business-area audit 7 Nov; lifecycle fixes (VERIFIED-TEST) | ISS-01…13 re-verification; G-47; four area audits not provided; production-build timing | — | PKG-2 | Each ISS item verified or reopened; prod-build p95 targets measured |
| D | Agent creation, training, draft, publish, versions | VERIFIED-TEST + VERIFIED-LIVE (API) | `routes/business.py` agent routes with explicit `workflow_id`; `routes/workflow.py` duplicate/templates/versions; audit events | 87 tests; Workflow 181 published v2 via real route; versions route returns v1 archived, v2 published | Browser acceptance; G-47 rename 500 | — | PKG-2 | Draft/publish/restore on a disposable workflow; protected workflows untouched |
| E | Budget IVR (Asterisk) | PARTIAL | `ari_manager.py`, `economy_ivr_controller.py`, `ivr/actions.py`, `ivr/audio_cache.py`, `/business/economy-ivr` | Stage 2 REPORTED-TEST; real local SIP/RTP | Not in main compose; live flag not pinned (G-15); UI label mismatch (G-26); pricing branch (G-18); no real carrier call | Carrier trunk to Asterisk | PKG-C, PKG-E, PKG-D | One real IVR call with DTMF; economy-rate cost row; worker refuses IVR mode (VERIFIED-TEST) |
| F | Standard Gemini Live via LiveKit | IMPLEMENTED-UNVERIFIED | `services/livekit/worker.py` (`_create_session`, `google_realtime`), `routes/livekit.py` session route, UI `useWebSocketRTC.tsx` | Mock handshake (S5-02); packages in image (VERIFIED-LIVE) | No real session ever; G-27; G-48 | LiveKit, Gemini | PKG-1b | Part 7 checklist |
| G | Premium Deepgram → DeepSeek → Cartesia | DEFECT (blocked in config) | Worker branches at `worker.py:1986-2057`; registry has Deepgram STT and Cartesia TTS, **no DeepSeek LLM** | Code-located | Register DeepSeek (G-17); verify Cartesia controls (G-43); `LIVEKIT_SUPPORTED_PROVIDERS` gate lists only openai/google for llm/stt/tts (`runtime_config.py`) and must be aligned | Three provider accounts | PKG-E | Real Premium call with three usage rows; no substitution |
| H | Inbound telephony, Vobiz | IMPLEMENTED-UNVERIFIED | `services/livekit/vobiz.py`, wizard, `/livekit/vobiz/setup`, dispatch rules, `providers/vobiz/routes.py` (v3 signature + nonce, 401 when unconfigured) | Synthetic negative tests (Phase 7 hardening) | No account/DID; provider-issued evidence; inbound cap (G-40) | Vobiz | PKG-3 | Real `WR-LK-IN-*` run; signed callback accepted, unsigned rejected |
| I | Outbound calling, campaigns | PARTIAL + DEFECTS | `campaign_call_dispatcher.py`, `campaign_tasks.py`, `runner.py`, CSV preview, source sync | Phase 4 and Phase 7 synthetic | G-13, G-14, G-15; must be disabled server-side for pilot (G-41) | Carrier, DLT | PKG-4a (disable), PKG-C/A/B | Failure-injection: 0 lost/duplicate; no dispatch without reservation |
| J | Pause → Barrier → Change → Validate → Publish → Resume | NOT IMPLEMENTED (pause/resume exist; barrier and guard do not) | `campaign_state` enum has `paused`; pre-dial cancel re-check | T5-07, T5-15 (REPORTED) | Barrier, 409 guard, pointer model | — | PKG-B | Blueprint §12 PKG-B J |
| K | Immutable stamping at admission | PARTIAL | `workflow_runs.workflow_definition_id` pinned at run creation | T5-09 (REPORTED) | Queued rows unstamped; ARI live read (G-15) | — | PKG-C | Stamp unchanged after publish; ARI serves pinned config |
| L | Numbers, carrier limits, consent, DNC | PARTIAL | `telephony_number_health`, `customer_suppression_dnc`, calling-window gate, `campaign.rate_limit_per_second`, `max_retries` | Phase 3, Phase 7E (REPORTED) | Per-number pacing/health rules (G-39); carrier limiter (G-21); DLT review | Carrier, regulator | PKG-12f, PKG-11 | Injected failures trigger cooldown; uncertain outcome never auto-redialled |
| M | Duration limits, reservations, spend controls | PARTIAL + DEFECTS | Duration mapping (G-45 fixed); worker loop; emergency stop (Redis, fail-closed); `cost_controller` caps | 87 tests; Workflow 181 definition; audit export shows stop/resume events | **Inbound monetary cap not implemented (G-40)**; worker fail-open without limit (G-48); outbound fail-open (G-13); live termination unproven (G-27) | Provider-side caps | PKG-1b, PKG-4a, PKG-A | Live termination log; inbound call refused when cap exhausted; outbound rejected without reservation |
| N | Live conversation, interruption, silence, multilingual | IMPLEMENTED-UNVERIFIED | `conversation_state.py`, `silence_manager.py`, `interruption_filter.py`, `language_pack.py`; thresholds read from definition config | Unit suites (REPORTED) | No real audio; owner settings and compatibility warnings (G-37); listening set and timestamps (G-38) | LiveKit, Gemini | PKG-1b, PKG-12d/e | Human listening set per language recorded |
| O | Transcripts, recordings, history | IMPLEMENTED-UNVERIFIED | Worker message logs, MinIO recordings, pre-signed URLs, quota/retention | T5-03/17, S3-E (REPORTED) | Real transcript/recording | LiveKit | PKG-1b, PKG-3 | Transcript and recording from a real call visible to owner only |
| P | CRM, lead scoring, appointments, callbacks | REPORTED-TEST | `lead_intelligence.py`, `customer_profiles`, `callback_scheduler.py`, `action_inbox.py` | Phase 2/5 tests; simulation | ISS-02/04/05/12 unverified; provenance (stated vs inferred) not stored | — | PKG-2, PKG-3 | One real call yields lead, appointment request (`requested`), callback inbox item, SSE update |
| Q | Human escalation, transfers | PARTIAL | Callback/inbox path exists; `TRANSFER_CALL` tool is Twilio-only (`enums.py:136`) | Code-located | LiveKit SIP transfer (G-42); R1 policy approval | — | PKG-3 (callback), PKG-12b | R1: callback reaches owner inbox; R2: real transfer with fallback |
| R | WhatsApp and optional integrations | DEFERRED | `chatwoot/outbox.py`, template registry | T5-18, Phase 5 gap closure (REPORTED) | No live Meta verification | Meta, CRM vendors | — (R2+) | Disabled for pilot |
| S | Usage, provider costs, customer billing | PARTIAL / NOT IMPLEMENTED | Cost classification (estimated/provider-reported), CDR reconcile, `/business/costs`; org quota fields | C2 tests (REPORTED) | Rates unverified (G-28); `budget` pricing branch (G-18); **customer subscriptions/invoices/payments: no code located** | Invoices, payment gateway | PKG-E; proposed PKG-14 (not approved) | Ledger reconciled against one real invoice; invoicing per PKG-14 once approved |
| T | Reports, monitoring, audit, health | REPORTED-TEST | `services/health/*`, `services/audit/audit_service.py`, `routes/health.py`, health-audit page, health scheduler | C2–C4 tests; audit export CSV | External alert webhook unconfigured; metrics export (G-23); label (G-33) | Alert channel | PKG-4b | Alert received after stopping worker |
| U | Deployment, Docker, backups, DR, security | PARTIAL / DEFECTS | Five compose files, nginx/coturn templates (`deploy/templates`), Fernet credentials | Running dev stack | G-08, G-09, G-29–G-32, G-46; unpinned `pytest-timeout` (G-12, narrowed) | VPS, domain | PKG-4a/4b/4c | Restore drill serves protected workflows; Secure cookies; refusal not crash at limit |
| V | Tenant isolation, per-business deployment | REPORTED-TEST | Org scoping on routes, explicit workflow ownership checks | Stage 1B, T5-06/17, Phase 7E, hardening; this program's 403/404 tests (VERIFIED-TEST) | Entitlement flags (G-41) | — | PKG-4a | Disabled paths return 403/422 |
| W | Scalability, 30,000/day | SYNTHETIC ONLY | Dispatcher, leases, recovery cron | Phase 7B–7E | Everything real (Part 10) | Carrier, providers, VPS | PKG-11, PKG-13 | Measured matrix; staged traffic; owner acceptance |

---

# PART 4 — PACKAGE STATUS

The blueprint file documents PKG-1, PKG-4a and PKG-4b as single packages. The **1a/1b** and **code/host** subdivisions were approved by the owner in the engineering conversation (offline-first order) and are not yet written into the blueprint.

## Release 1

| Package | Purpose | Completed work | Evidence | Remaining implementation | Remaining verification | External | Acceptance | Blocks |
|---|---|---|---|---|---|---|---|---|
| **PKG-1a** | Offline readiness; activate G-45; safe test fixture | API restarted; G-45 live; Workflow 181 published with 60 s; negative 422; worker CLI dry-run; no worker started | Part 6 | None | None (offline) | — | **ACCEPTED (offline scope)** | — |
| **PKG-1b** | First real LiveKit/Gemini conversation | Preflight; credential method identified; duplicate-worker guard confirmed | — | **G-48 worker guard (needs approval)**; worker container start | Part 7 checklist | LiveKit, Gemini | **PENDING (not started)** | PKG-3 |
| **PKG-2** | Dashboard acceptance, ISS re-check, first commit | Lifecycle/mode/template fixes; 87 tests | VERIFIED-TEST | Fix any ISS item that reproduces; G-47 root cause | Browser click-through; production-build timing; commit | — | **PARTIAL (offline tests PASS; browser PENDING)** | Clean baseline for all later code packages |
| **PKG-3** | Vobiz inbound + R1 CRM/escalation acceptance | Adapters, wizard, signature verification (synthetic) | REPORTED-TEST | Inbound cap decision (G-40) | Real call; CRM chain; signed/unsigned callback; queue path | Vobiz | **PENDING** | R1 gate |
| **PKG-4a-code** | Security hardening in code | — | — | Entitlement flags (G-41); inbound cap (G-40) if assigned here; encrypt LiveKit settings file (G-46); pin `pytest-timeout`; cookie/`NODE_ENV` config | Flag tests 403/422; reproducible image build | — | **PENDING** | R1 gate |
| **PKG-4a-host** | Security on the pilot host | — | — | Real secrets, TLS, DB role | Header/cookie audit over HTTPS | VPS, domain | **PENDING** | R1 gate |
| **PKG-4b-offline** | Backup/restore scripts, alert logic | Alert manager, notifier, incident store exist (C3/C4) | REPORTED-TEST | `backup.sh`, `restore.sh`; estimate labels | Restore drill into throwaway containers; proxy SSE check | — | **PENDING** | R1 gate |
| **PKG-4b-host** | Monitoring and recovery on host | — | — | Configure `SPX_ALERT_WEBHOOK_URL`, uptime check | Alert received after worker stop; host restore | VPS, alert channel | **PENDING** | R1 gate |
| **PKG-4c** | Hosting portability | — | — | Env-ised endpoints, overlays, limits, installer (G-29–G-33) | Fresh install on non-Hostinger VPS; OOM injection | VPS | **PENDING** (R1 recommended; R2 mandatory) | R2 gate |

## Release 2

| Package | Purpose | Completed | Remaining implementation | Remaining verification | External | Status | Blocks |
|---|---|---|---|---|---|---|---|
| **PKG-F0** | Classify 64 failures + 1 error | — | None (classification) | Each failure classified | — | PENDING | PKG-C |
| **PKG-C** | Immutable stamping at admission; ARI reads stamp | Run-level pinning exists | Stamp queued rows; ARI pinned read | Publish-during-queue; race test | — | PENDING | PKG-A, PKG-B, PKG-D |
| **PKG-A** | Atomic outbound reservations | `check_financial_admission`, CDR reconcile | Two tables (migration), reserve/settle/sweeper | Fail-closed tests; worker-termination test | — | PENDING | PKG-B, PKG-11 |
| **PKG-E** | Premium registry; Budget = IVR; pricing | Worker provider branches | DeepSeek registry class; mapping; `budget` rate; supported-provider gate | One real Premium call; one real IVR call | Three providers; carrier | PENDING | PKG-D |
| **PKG-B** | Safe pause/switch/resume | Pause/resume, re-check | Barrier flag, reconciler, 409 guard | 0 lost/duplicate under injection | — | PENDING | — |
| **PKG-D** | Authorized IVR→AI handoff | ARI originate verified locally | Handoff flow, split billing | Real IVR escalation | Carrier | PENDING | — |
| **PKG-12a** | Industry scoping | Presets, org config table | Store industry; filter UI | Install shows only one industry | — | PENDING | — |
| **PKG-12b** | Human transfer (LiveKit) | Twilio-only tool | SIP transfer + fallback | Real transfer | LiveKit SIP, carrier | PENDING | — |
| **PKG-12c** | Hold music / announcements | — | Upload, playback path | Caller hears audio; stops ≤1 s | Decision after PKG-3 | PENDING | — |
| **PKG-12d** | Voice settings truth | Thresholds read from config | Regrouped UI; compatibility warnings; dirty state | Each setting round-trips to worker | — | PENDING | — |
| **PKG-12e** | Latency timestamps, listening set | `livekit_events` | Per-turn timestamps; scripted set | Te/Hi/En results recorded | Providers | PENDING | — |
| **PKG-12f** | Number health and pacing | Health table, rate fields | Pacing, retry rules, alerts | Cooldown on injected failures | — | PENDING | — |
| **PKG-F** | Cross-component regression | — | Resolve F0 list; G-19 | Full suite on `postgres_test` | — | PENDING | R2 gate |
| *Proposed PKG-14* | Customer subscriptions, invoices, payments | No code located | Entire feature | — | Payment gateway | **NOT AN APPROVED PACKAGE** | — |

## Release 3

| Package | Purpose | Completed | Remaining | External | Status |
|---|---|---|---|---|---|
| **PKG-11** | Capacity controls, observability | Synthetic fairness/recovery (Phase 7); health scheduler | Carrier/number limiter, fairness in production path, metrics export, stop thresholds, graceful shutdown | — | PENDING |
| **PKG-13** | Measured profiles, staged ramp | — | VPS measurements, arm64, staged real traffic | Carrier allocation, provider quotas, VPS sizes | PENDING |

---

# PART 5 — DEFECT AND GAP REGISTER

"Fixed" = code change present in the working tree. "Tested" = focused tests I ran. IDs G-45 to G-49 were raised after the blueprint was written and are **not yet recorded in the blueprint**.

| ID | Description | Sev | Evidence | Status | Fixed | Tested | Remaining verification | Package |
|---|---|---|---|---|---|---|---|---|
| G-01 | LiveKit not connected | P0 | `/health` `livekit_enabled:false`; settings file absent | EXTERNAL | — | — | Connection | PKG-1b |
| G-02 | Gemini key never exercised | P1 | Key present in user config; no call | UNVERIFIED | — | — | First call | PKG-1b |
| G-03 | Standard conversation unverified | P1 | Mock only | UNVERIFIED | — | — | Browser call | PKG-1b |
| G-04 | Vobiz account/DID/trunk | P1 | No credentials | EXTERNAL | — | — | Real inbound | PKG-3 |
| G-05 | Lifecycle fixes not browser-verified | P1 | 87 tests | Fix pending verification | Yes | Yes | Browser | PKG-2 |
| G-06/07 | Mode mapping; publish readiness gate | — | Tests | CLOSED | Yes | Yes | — | — |
| G-08 | Default secrets, `NODE_ENV=oss`, HTTPS | P0 | Compose; UI env | OPEN | No | — | Host audit | PKG-4a |
| G-09 | No backup/restore | P0 | None in repo | OPEN | No | — | Restore drill | PKG-4b |
| G-10 | Uncommitted work (11 modified + tests + docs) | P1 | `git status` | OPEN | — | — | Commit | PKG-2 |
| G-11 | 64 failures + 1 error unclassified | P2 | Handoff §4.1 | OPEN | — | — | Triage | PKG-F0 |
| G-12 | `pytest-timeout` unpinned (**narrowed**: `pipecat-ai` and LiveKit packages are pinned in `api/requirements.txt`) | P2 | requirements lines 23–30 | OPEN | No | — | Clean image build | PKG-4a-code |
| G-13 | Campaign budget fail-open | P0 | `campaign_call_dispatcher.py:290-317` | OPEN | No | — | Stage A tests | PKG-A |
| G-14 | Pause race, no barrier | P1 | Prior trace | OPEN | No | — | Injection test | PKG-B |
| G-15 | Queued runs unpinned; ARI live flag | P0 | `ari_manager.py:672` | OPEN | No | — | Pin tests | PKG-C |
| G-16 | IVR→AI handoff, split billing | P1 | Not located | NOT IMPLEMENTED | — | — | Real handoff | PKG-D |
| G-17 | DeepSeek missing from LLM registry | P1 | `registry.py:205-304` | OPEN | No | — | Premium call | PKG-E |
| G-18 | `budget` billed as Standard | P1 | `cost_controller.py:111` | OPEN | No | — | Unit + real | PKG-E |
| G-19 | Session revocation on role change | P2 | S4-02 partial | UNVERIFIED | — | — | Test | PKG-F |
| G-20 | Multi-node idempotency | P2 | Sign-off suite covers leases synthetically | UNVERIFIED (real multi-node) | — | — | 2-worker test | PKG-11 |
| G-21 | Carrier CPS/channel limiter | P1 | Campaign-level rate limit only | NOT IMPLEMENTED | — | — | Injected limit | PKG-11 |
| G-22 | Real capacity unproven | P0 | Synthetic only | EXTERNAL | — | — | Staged traffic | PKG-13 |
| G-23 | Metrics export; alert delivery | P2 | Webhook notifier exists, unconfigured | PARTIAL | — | REPORTED | Real delivery | PKG-4b, PKG-11 |
| G-24 | WhatsApp/CRM/calendar live | P3 | Outbox only | DEFERRED | — | — | — | — |
| G-25 | Missing historical reports | — | Part 13 | EVIDENCE UNAVAILABLE | — | — | Owner supplies | — |
| G-26 | UI "Budget" ≠ Economy IVR | P1 | `agent_training.py` mapping | OPEN (owner decision) | No | — | Publish test | PKG-E |
| G-27 | Duration cap unproven live | P1 | Loop at `worker.py:1241-1248` | UNVERIFIED | — | — | Live termination | PKG-1b |
| G-28 | ₹/min figures are baseline estimates | P2 | `cost_controller` defaults | OPEN | No | — | Invoice reconcile | PKG-4b, PKG-E |
| G-29 | Redis/MinIO/S3 literal in compose | P1 | `docker-compose.yaml:169,175,333-335` | OPEN | No | — | External-service install | PKG-4c |
| G-30 | Five compose files, no profiles | P2 | File list | OPEN | No | — | — | PKG-4c |
| G-31 | No resource limits/session admission | P1 | Compose | NOT IMPLEMENTED | — | — | OOM injection | PKG-4c |
| G-32 | No installer/backup/upgrade scripts | P1 | `scripts/` | NOT IMPLEMENTED | — | — | Fresh install | PKG-4b/4c |
| G-33 | Hostinger label in health monitor | P3 | `system_monitor.py:341-359` | OPEN | No | — | — | PKG-4c |
| G-34 | arm64 untested | P2 | — | UNVERIFIED | — | — | Build test | PKG-13 |
| G-35 | Hold music | P2 | Not located | NOT IMPLEMENTED | — | — | — | PKG-12c |
| G-36 | Industry scope per install | P2 | Presets only | NOT IMPLEMENTED | — | — | — | PKG-12a |
| G-37 | Owner silence/barge-in settings, warnings | P2 | Config keys read by worker | PARTIAL | — | — | Round-trip | PKG-12d |
| G-38 | Latency timestamps, listening set | P2 | — | NOT IMPLEMENTED | — | — | — | PKG-12e |
| G-39 | Number pacing/retry/health rules | P1 | Table and fields exist | PARTIAL | — | — | Injection | PKG-12f |
| G-40 | **Inbound monetary cap** (reclassified) | P0 | No runtime "wallet"/overdraft code; `check_financial_admission` used only by dispatcher | **NOT IMPLEMENTED** | No | — | Real inbound refused at cap | Owner decision: PKG-4a-code or PKG-3 |
| G-41 | Disabled features reachable via API | P0 | No entitlement flags | NOT IMPLEMENTED | — | — | 403/422 tests | PKG-4a-code |
| G-42 | Human transfer for LiveKit | P1 | `enums.py:136` Twilio-only | NOT IMPLEMENTED | — | — | Real transfer | PKG-12b |
| G-43 | Cartesia controls vs current model | P2 | `worker.py:2042-2052` | UNVERIFIED | — | — | Real TTS | PKG-E |
| G-44 | Stage naming collision | — | `scratch/phase6b` | RESOLVED (naming) | — | — | — | — |
| **G-45** | Duration limit never reached worker key | P0 | `business.py` handlers; `worker.py:2232` | **FIXED** | Yes | **Yes (87 passed)** | Live enforcement (tracked as G-27) | PKG-1b |
| **G-46** | LiveKit API secret stored unencrypted in runtime volume file | P2 | `runtime_config.py` `save_livekit_settings` | OPEN | No | — | — | PKG-4a-code |
| **G-47** | Workflow rename returns HTTP 500 although the rename persists | P2 | API log `PUT /api/v1/workflow/181` 500; DB name updated | OPEN, root cause unknown | No | — | Reproduce, trace | PKG-2 |
| **G-48** | Worker does not reject/cap sessions without a duration limit | P0 | `worker.py:1241` `if self._max_duration_seconds:` | OPEN | No | — | Unit + live | Before PKG-1b (approval) |
| **G-49** | No owner-facing staff invite/role-management route located | P2 | Only `POST /superuser/users` found | UNVERIFIED | — | — | Confirm or implement | PKG-F / PKG-12 |
| ISS-01 | Emergency Stop failure + hydration errors (7 Nov) | P0 | `costs/page.tsx:280` `asChild`; audit export shows later stop/resume/verified events | Fix in code | Yes | No | Browser | PKG-2 |
| ISS-02 | Cross-page KPI desync | P0 | 7 Nov audit; no fix located | UNVERIFIED | Unknown | No | Browser + DB | PKG-2 |
| ISS-03 | Deep links to conversations | P1 | `runId` links present | Fix in code | Yes | No | Browser | PKG-2 |
| ISS-04 | Human Handover filter mismatch | P1 | No fix located | UNVERIFIED | Unknown | No | Browser | PKG-2 |
| ISS-05 | Appointment counters/toast | P1 | Toast present; counter math not located | PARTIAL | Partly | No | Browser | PKG-2 |
| ISS-06 | Env-var names in client toast | P1 | String no longer in code | Fix in code | Yes | No | Browser | PKG-2 |
| ISS-07 | Test fixtures in dropdowns | P1 | UI filter (`campaigns/new/page.tsx:119`); Workflow 181 name starts with "Disposable-" | Fix in code | Yes | No | Browser | PKG-2 |
| ISS-08 | Cold navigation 3–5 s | P2 | Dev-mode compile; `perf_bench_summary.json` | UNVERIFIED on prod build | — | — | Prod-build timing | PKG-2 |
| ISS-09 | Archived missed call still actionable | P2 | No fix located | UNVERIFIED | Unknown | No | Browser | PKG-2 |
| ISS-10 | Raw field keys; double click; dirty state | P2 | Slug fix present; dirty state not located | PARTIAL | Partly | No | Browser | PKG-2 / 12d |
| ISS-11 | Simulation calls in CRM lists | P2 | No fix located | UNVERIFIED | Unknown | No | Browser | PKG-2 |
| ISS-12 | Duplicate appointments | P2 | No fix located | UNVERIFIED | Unknown | No | Browser + DB | PKG-2 |
| ISS-13 | Click acknowledgment >200 ms | P3 | No fix located | UNVERIFIED | Unknown | No | Prod build | PKG-12d |

**Counts (open or unverified, excluding closed/resolved/deferred/evidence rows):** P0 = 11 (G-01, 08, 09, 13, 15, 22, 40, 41, 48, ISS-01, ISS-02) · P1 = 22 (G-02, 03, 04, 05, 10, 14, 16, 17, 18, 21, 26, 27, 29, 31, 32, 39, 42, ISS-03, 04, 05, 06, 07) · P2 = 21 (G-11, 12, 19, 20, 23, 28, 30, 34, 35, 36, 37, 38, 43, 46, 47, 49, ISS-08, 09, 10, 11, 12) · P3 = 2 (G-33, ISS-13). Of these, 6 are "fix present, verification pending" (G-05, ISS-01, 03, 05 partial, 06, 07). G-45 is fixed and tested; its live proof is counted under G-27.

---

# PART 6 — PKG-1a BASELINE (re-checked 14 Nov for this report)

| Claim | Cross-check | Result |
|---|---|---|
| `spx-api-preflight` restarted | Container running, healthy; command `python -m uvicorn api.app:app --host 0.0.0.0 --port 8000`; four mounts unchanged; port 127.0.0.1:8001 | Confirmed. Note: Docker Desktop stopped once during PKG-1a; all five containers were **started** from existing state (not recreated) |
| G-45 active in running API | In-container introspection: draft and publish handlers reference `resolve_max_call_duration_seconds` (2 references); bounds 30–3600 | Confirmed |
| Workflow 181 disposable copy | DB: id 181, name `Disposable-PKG1a-Receptionist-60s`, status active, `product_mode=standard`; 0 workflow runs | Confirmed |
| Published definition 250, version 2 | DB: definition 249 v1 archived; **250 v2 published, is_current**; `released_definition_id=250` | Confirmed |
| `max_call_duration_seconds=60` | DB: top-level key = 60 on workflow row and on definition 250 | Confirmed |
| 5-second value rejected | Live API returned HTTP 422 "Maximum call duration must be between 30 and 3600 seconds, got 5."; no v3 created | Confirmed (observed during PKG-1a; definitions list unchanged now) |
| Worker command available offline | `python -m api.services.livekit.worker --help` listed `start`, `dev`, `connect`, `console`; `livekit.agents 1.5.13` and plugins import in the image | Confirmed |
| No LiveKit worker started | No worker/livekit container exists; `LIVEKIT_WORKER_MANAGED_BY_API=false` | Confirmed |
| LiveKit configuration absent | `/app/api/.runtime/livekit_settings.json` absent; effective `configured=False`, `source=env`; `/health` `livekit_enabled:false` | Confirmed |
| No provider-backed test | 0 runs on Workflow 181; no LiveKit configuration | Confirmed |
| Protected records unchanged | Workflow 1 → released 97 (v39 published, v40 draft id 108); Workflow 180 → 248, configuration `{}`; definition 248 v1 `{}`; Workflow 128 → 177; Campaign 158 `created` on 128 | Confirmed |
| Uncommitted changes preserved | `git status`: same 11 modified files; untracked: blueprint, two test files; diff 696 insertions / 183 deletions; HEAD `ca56a4a` | Confirmed |
| Database shape | Alembic head `f1a2b3c4d5e6`; workflows 26 (25 + disposable 181); no migration run | Confirmed |

**Known side effects of PKG-1a:** one additional workflow (181) with two definitions; one API restart; G-47 observed.

---

# PART 7 — REQUIREMENTS FOR PKG-1b

No provider connection or paid call is authorized by this report.

## 7.1 Owner actions

| # | Action | Detail |
|---|---|---|
| O-1 | Create LiveKit Cloud project | Build (free) plan preferred because its allowance is a hard cap; paid plans have no dollar cap, only quotas |
| O-2 | Create a LiveKit API key | Keep WebSocket URL, key and secret in the browser; never paste into chat |
| O-3 | Enter credentials in the dashboard | Telephony Configurations → LiveKit Runtime → Save (writes `/app/api/.runtime/livekit_settings.json` in volume `spx-voice-mvp_api_runtime`; response exposes only "secret configured: true/false") |
| O-4 | Set Gemini spend control | AI Studio → project → Spend → monthly spend cap (enforceable but delayed, roughly ten minutes; long sessions may overrun). A standard Cloud budget is an alert only |
| O-5 | If on a LiveKit paid plan | Set project concurrency quota to 1 for the test |
| O-6 | Approve G-48 change | Decide: worker **rejects** sessions without a valid limit (legacy unpublished-since-fix workflows would fail until republished) or applies a conservative default cap |
| O-7 | Approve worker container start | One container, no API restart needed for credentials |
| O-8 | Approve one paid browser call | 60 seconds, Workflow 181 only |
| O-9 | Perform the call | Speak, interrupt once, stay silent once, confirm what was heard |

## 7.2 Claude Code actions (after approvals)

| # | Action | PASS evidence |
|---|---|---|
| C-1 | Implement and test G-48 (if approved) | Focused tests; session without limit refused or capped; failure visible on run |
| C-2 | Confirm settings saved | `/api/v1/health` `livekit_enabled:true`; `/api/v1/livekit/runtime` configured |
| C-3 | Start one worker from image `spxvoice/spx-voice-api:latest`, network `spx-voice-mvp_app-network`, mounts `spx-voice-mvp_api_runtime:/app/api/.runtime` and the `api` bind, API-equivalent DB/Redis/MinIO env, `LIVEKIT_WORKER_MANAGED_BY_API=false`, command `python -m api.services.livekit.worker start` | Worker log shows registration; **exactly one** worker in LiveKit dashboard |
| C-4 | Verify the call | Log `[LiveKit] dispatch received`; run on Workflow 181 pinned to definition 250 |
| C-5 | Audio, latency, interruption | Owner confirms two-way audio; interruption stops agent speech; `livekit_events` present (latency timestamps are limited until PKG-12e) |
| C-6 | Transcript and usage persistence | Run `completed`; transcript in run logs; usage/cost row |
| C-7 | Real 60-second termination | Log `maximum call duration reached`; room deleted; run ends at about 60 s |
| C-8 | Cleanup and errors | No `startup_failed`; no orphaned room; worker stopped or left running per owner instruction |
| C-9 | Protected data | Workflows 1, 180, 128 and Campaign 158 unchanged; only Workflow 181 gains a run |

## 7.3 Financial exposure limits (honest statement)

- Application bound: 60-second duration limit on definition 250 (mapping verified; live enforcement is what this test proves).
- Provider bounds: LiveKit Build allowance (hard); Gemini project cap (delayed).
- Room `empty_timeout=60` / `departure_timeout=30` clean up idle rooms only; they do not end an active conversation.
- No inbound monetary cap exists in runtime code (G-40); not relevant to a browser test, but relevant before PKG-3.
- Manual stop: the owner or engineer can stop the worker container at any time.

---

# PART 8 — REMAINING WORK BEFORE THE FIRST PAYING PILOT

| # | Release 1 condition | Status | Package |
|---|---|---|---|
| 1 | Real LiveKit/Gemini browser conversation accepted | NOT YET TESTED | PKG-1b |
| 2 | Duration termination proven live | NOT YET TESTED | PKG-1b |
| 3 | Worker refuses/caps sessions without a limit (G-48) | FAIL (not implemented) | pre-1b |
| 4 | Dashboard/browser acceptance incl. ISS-01…13 | PENDING (offline tests PASS) | PKG-2 |
| 5 | Working tree committed | PENDING | PKG-2 |
| 6 | Vobiz provider-issued callback/signature evidence | NOT YET TESTED | PKG-3 |
| 7 | Real inbound call with two-way audio | NOT YET TESTED | PKG-3 |
| 8 | CRM chain from a real call (transcript, lead, appointment request, callback inbox, SSE, cost) | NOT YET TESTED | PKG-3 |
| 9 | Owner-approved escalation policy (callback/inbox for R1) | PENDING (owner decision) | PKG-3 |
| 10 | Inbound monetary cap (G-40) | FAIL (not implemented) | PKG-4a-code or PKG-3 |
| 11 | Unfinished features disabled server-side (G-41) | FAIL (not implemented) | PKG-4a-code |
| 12 | HTTPS, Secure cookies, non-default secrets (G-08) | FAIL | PKG-4a-host |
| 13 | LiveKit secret at rest (G-46) | FAIL | PKG-4a-code |
| 14 | Backup scripts and restore drill (G-09) | FAIL | PKG-4b |
| 15 | Monitoring and alert delivery to the owner | PENDING (code exists, unconfigured) | PKG-4b |
| 16 | Live dashboard updates through the production proxy | NOT YET TESTED | PKG-4b |
| 17 | Consent and recording notice in the greeting | PENDING | PKG-3 / onboarding |
| 18 | Pilot language acceptance | PENDING (owner decision on language) | PKG-1b / PKG-3 |
| 19 | Pilot workflow republished after G-45 with explicit limit | PENDING | PKG-3 |
| 20 | Support contact and incident path | PENDING | PKG-4b |
| 21 | Three consecutive stable days of real inbound operation | NOT YET TESTED | R1 gate |
| 22 | Business owner completes the journey independently | NOT YET TESTED | R1 gate |
| 23 | Tenant isolation | PASS (tests; REPORTED + this program's 403/404 tests) | — |
| 24 | Audit of publish/restore/config changes | PASS at test level; browser pending | PKG-2 |

Summary: 2 PASS (test level), 6 FAIL, 8 PENDING, 8 NOT YET TESTED.

---

# PART 9 — REMAINING WORK FOR THE COMPLETE THREE-MODE SAAS

| Area | Required for Release 2 | Future enhancement |
|---|---|---|
| Budget IVR | Pin IVR config to the stamped definition; map UI "Budget" to the IVR; `budget` pricing; real carrier IVR call; IVR in the supported compose profile | Multi-level menu editor polish; per-language prompts library |
| Gemini Standard | PKG-1b and PKG-3 acceptance; owner voice settings; language results | Additional voices/models |
| Premium | Register DeepSeek; align supported-provider gate; verify Cartesia controls; one real call with three usage rows; fail-closed proven | Alternative STT/TTS vendors |
| Safe outbound campaigns | Stamping (PKG-C), reservations (PKG-A), barrier and resume (PKG-B), DLT/TRAI review, server-side enablement | A/B split dialer (already deferred, B-04) |
| Immutable configuration and switching | PKG-C + PKG-B acceptance with failure injection | — |
| Atomic financial reservations | PKG-A incl. worker-termination test; inbound cap (G-40) | Per-campaign budgets UI polish |
| Industry-specific operations | PKG-12a | Additional verticals |
| Human transfer and hold music | PKG-12b required; PKG-12c only where a contract needs waiting audio | Queue position announcements |
| Telugu/Hindi/English/mixed | PKG-12e listening set per model version | Automatic language detection tuning |
| Phone-number management | PKG-12f pacing, retries from definitive outcomes, cooldown alerts, number-change audit | Number-level reports |
| Customer subscriptions and invoices | **Not in any approved package** (proposed PKG-14) | Online payments, tax invoices |
| Admin, observability, support | Metrics export, real alert delivery, staff management confirmation (G-49), revocation (G-19), full regression (PKG-F), portability (PKG-4c) | Tracing export |

---

# PART 10 — SCALABILITY READINESS

## What Phase 7 proved (synthetic, mock telephony and AI, isolated DB/Redis)

- Software dispatch and persistence path processed 100, 1,000, 5,000, 10,000 and **30,000 synthetic calls** with zero errors; throughput about 72 calls/s at the 30,000 stage; latency p95 456 ms; 20 Postgres connections; no Redis slot leaks; all runs and queued rows persisted.
- Three concurrent campaigns across two organizations completed without head-of-line blocking; burst limiting admitted 20 of 200 in the window.
- Worker-crash recovery (10 orphaned runs recovered), Redis outage fail-closed (0 dials), duplicate webhook idempotent, ambiguous 504 handled with retry cooldown.
- Calling-window gate, DNC suppression, emergency stop (5–29 ms), tenant isolation.
- Lease renewal, reclaim, quarantine, ownership fencing; ARQ cron registered.

## What remains unverified

| Area | State |
|---|---|
| Carrier CPS and channel allocation | No carrier account; no allocation; no limiter keyed to carrier limits (G-21) |
| LiveKit worker concurrency | No real session ever; sessions per worker unknown |
| Gemini / Premium provider quotas | Unknown; not requested |
| Asterisk/IVR performance | 10 local concurrent sessions only |
| Media processing | No real media through LiveKit; recording bandwidth unmeasured |
| PostgreSQL / Redis throughput | Synthetic figures above only; no measurement with real call event volume |
| VPS resource profiles | Unmeasured; no RAM figure is supported by evidence |
| Distributed worker recovery | Synthetic single-host; multi-node not exercised (G-20) |
| Observability and financial controls under load | Alert delivery unconfigured; outbound reservation absent (G-13); inbound cap absent (G-40) |

**Distinction:** 30,000 synthetic calls prove that the dispatcher, database writes, idempotency and recovery logic can process that many queued records against a mock. They say nothing about 30,000 **authorized real call attempts per day**, which additionally require carrier CPS/channels, provider concurrency, media capacity, worker and host sizing, compliance, and staged real traffic with explicit acceptance. No capacity figure is certified.

---

# PART 11 — QUANTIFIED REMAINING WORK

**Counting rules.** A "package" is a leaf unit of approval. Parents that have been subdivided are not counted (PKG-1, PKG-4a, PKG-4b, PKG-12 are represented by their children). The proposed PKG-14 is not approved and is excluded. "Fully accepted" = every acceptance requirement of that leaf has PASS evidence. "Partially completed" = package-scoped work executed and evidenced in the current program, acceptance incomplete. "Blocked externally" = cannot be **accepted** without an external party; a subset cannot even **start**.

| Metric | Count | Detail |
|---|---|---|
| 1. Documented leaf packages | **24** | R1: 9 (1a, 1b, 2, 3, 4a-code, 4a-host, 4b-offline, 4b-host, 4c) · R2: 13 (F0, C, A, E, B, D, 12a–12f, F) · R3: 2 (11, 13) |
| 2. Fully accepted | **1** | PKG-1a |
| 3. Partially completed | **1** | PKG-2 |
| 4. Not started | **22** | All others (PKG-1b is prepared but not started) |
| 5. Blocked by external dependency | **10** for acceptance: 1b, 3, 4a-host, 4b-host, E, D, 12b, 12e, 13, 4c (needs a VPS) · of which **5** cannot start: 1b, 3, 4a-host, 4b-host, 13 | |
| 6. Unresolved issues | **P0 11 · P1 22 · P2 21** (54) | Part 5; six of these have a fix present awaiting verification |
| 7. Remaining acceptance gates | **23 package gates + 3 release gates**; Release 1 has 24 listed conditions of which 22 are not yet PASS | Parts 4 and 8 |
| 8. Owner decisions required | **14** | List below |

**Task-level count.** The blueprint lists numbered engineering steps for the Release 1 packages (PKG-1: 8, PKG-2: 15, PKG-3: 10, PKG-4a: 6, PKG-4b: 6, PKG-4c: 6 = 51 steps). Of these, the PKG-1 offline steps were completed under PKG-1a in a revised form, and none of the others are complete. For Release 2 and 3 the blueprint gives ordered steps for C, A, E, B, D (5–7 each) but sub-packages 12a–12f, F0, F, 11 and 13 are specified by outcome rather than enumerated tasks. **A precise total task count for the whole program is therefore not established by the documentation.**

**Owner decisions outstanding:** (1) G-48 behaviour: reject vs default cap; (2) inbound cap approach and owning package (G-40); (3) Gemini spend-cap amount; (4) pilot language(s); (5) Release 1 escalation policy; (6) plan-label mapping (G-26); (7) Asterisk hop vs direct SIP for queues/hold music (after PKG-3); (8) industry list for 12a; (9) whether to approve PKG-14; (10) blueprint edit to record 1a/1b and code/host splits and G-45–G-49; (11) copy of the master specification into the repository; (12) locations of missing historical reports; (13) alert channel and backup storage; (14) fate of disposable Workflow 181 after PKG-1b (archive).

No completion percentage or time estimate is given.

---

# PART 12 — RECOMMENDED EXECUTION ORDER

The owner-approved offline-first order is kept. One insertion is recommended: the small G-48 worker guard before any paid call.

| Stage | Work | Tests | Offline? | Paid provider? | Approvals | PASS gate | Next |
|---|---|---|---|---|---|---|---|
| 1. **PKG-2** | Browser acceptance on disposable workflows; ISS re-check; G-47 root cause; production build timing; first commit | Browser + DB + console | Yes | No | Owner clicks; commit | Every ISS item verified or reopened; protected rows unchanged; commit present | Stage 2 |
| 2. **G-48 + PKG-4a-code** | Worker limit guard; entitlement flags; inbound cap (if assigned here); encrypt LiveKit settings; pin | Focused unit tests; 403/422 tests; image rebuild | Yes | No | Scope approval; image rebuild | Tests pass; disabled paths refuse; build reproducible | Stage 3 |
| 3. **PKG-4b-offline** | Backup/restore scripts; restore drill in throwaway containers; alert logic check; proxy SSE check | Restore drill | Yes | No | Throwaway containers | Restored copy serves protected workflows | Stage 4 |
| 4. **PKG-1b** | Credentials via dashboard; one worker; one 60 s browser call on Workflow 181 | Part 7 | No | LiveKit, Gemini | O-1…O-9 | Part 7 evidence complete | Stage 5 |
| 5. **PKG-3** | Vobiz wizard; signing secret; real inbound calls; CRM chain; callback escalation; republish pilot workflow with limit | Real calls; signature negative test | No | Vobiz, Gemini, LiveKit | Vobiz account; test-call spend; escalation policy | Blueprint §12 PKG-3 J | Stage 6 |
| 6. **PKG-4a-host / PKG-4b-host** | TLS, secrets, DB role, alert webhook, uptime, host restore | Header audit; alert fire test | No | Hosting | VPS, domain | Secure cookie; alert received; restore on host | Stage 7 |
| 7. **Release 1 gate** | Three stable days; owner journey unaided | Observation | — | Live | Owner | All Part 8 conditions PASS | Pilot |
| 8. **PKG-4c** | Portability (recommended during R1, mandatory for R2) | Fresh install | Mostly | Hosting | VPS | Blueprint §12 PKG-4c J | Stage 9 |
| 9. **PKG-F0 → C → A → E → B → D → 12a–f → F** | Release 2 safety and experience | Per package | Mostly offline until real-call acceptance | Premium providers, carrier | Migrations (C, A); label decision; keys | Release 2 gate | Stage 10 |
| 10. **PKG-11 → PKG-13** | Capacity controls; measurement; staged ramp | Synthetic then staged real | Partly | Carrier, providers, VPS | Allocations | Measured matrix; owner acceptance | Scale claim |

---

# PART 13 — CROSS-VERIFICATION EVIDENCE INDEX

## 13.1 Independently checked in this program (code, database, running system)

| Claim | Evidence |
|---|---|
| Baseline and uncommitted state | `git log -1` → `ca56a4a`; `git status --short` (11 M, 3 ??); `git diff --stat` → 11 files, 696+/183− |
| API health and LiveKit state | `GET http://127.0.0.1:8001/api/v1/health` → status ok, version 1.31.0, voice_runtime livekit, livekit_enabled false, auth_provider local |
| LiveKit settings resolution | `api/services/livekit/runtime_config.py` (`SETTINGS_PATH`, `effective_livekit_settings`, `save_livekit_settings`); `api/routes/livekit.py` `/settings`, `/runtime`, `/session/{workflow_id}/{workflow_run_id}` |
| Duplicate-worker guard | `api/constants.py:65-67`; `api/services/livekit/worker_process.py:32,51`; container env `LIVEKIT_WORKER_MANAGED_BY_API=false` |
| Worker launch and provider support | `api/services/livekit/worker.py` `__main__` block (~2401), `_create_session` (1875), DeepSeek/Deepgram/Cartesia branches (1986–2057) |
| Duration enforcement loop and fail-open | `worker.py:1241-1248`, `_shutdown_call` (~1268), reader at `worker.py:2232` |
| G-45 fix | `api/services/business/agent_training.py` `resolve_max_call_duration_seconds`; `api/routes/business.py` draft and publish handlers; `api/tests/test_product_mode_mapping.py` (47 test functions) + `api/tests/test_workflow_template_routes.py` (7) → 87 passed (isolated venv, mocks) |
| PKG-1a records | DB: `workflows.id=181`, `workflow_definitions.id IN (249,250)`; values in Part 6 |
| Protected records | DB: `workflows.id IN (1,180,128)`, `workflow_definitions.id IN (97,108,248)`, `campaigns.id=158` |
| Room timeouts | `api/services/livekit/client.py:174-178` |
| Vobiz signature verification | `api/services/telephony/providers/vobiz/routes.py:38-182` |
| Campaign budget fail-open | `api/services/campaign/campaign_call_dispatcher.py:260-317` |
| ARI live flag | `api/services/telephony/ari_manager.py:672` |
| LLM registry content | `api/services/configuration/registry.py:205-304` (OpenAI, Google, Groq, OpenRouter, Azure, Dograh, Bedrock, Speaches) |
| Cost modes | `api/services/pricing/cost_controller.py:28-126,160` |
| Inbound cap absent | No `wallet`/`overdraft` in non-test `api/**/*.py`; `check_financial_admission` referenced only in dispatcher and its definition |
| Transfer tool scope | `api/enums.py:136` |
| Health/alerting modules | `api/services/health/{alert_manager,external_notifier,durable_incident_store,provider_monitor,scheduler,observability_service,system_monitor}.py`; `api/routes/health.py` routes |
| Recovery cron | `api/tasks/arq.py:66` |
| Compose facts | `docker-compose.yaml` (services; literals at 169, 175, 333–335; ARQ `worker` service at 318); `docker-compose.dev.yaml` |
| Requirements pins | `api/requirements.txt:23-30` |
| ISS code checks | `ui/src/app/costs/page.tsx:280`; `ui/src/app/leads/page.tsx:681`; `ui/src/app/appointments/page.tsx:120,433`; `ui/src/app/conversations/page.tsx:103`; `ui/src/app/campaigns/new/page.tsx:119`; `ui/src/app/agent/page.tsx:747` |
| Provider billing behaviour | Public documentation consulted 13 Nov (Google Cloud budgets and spend caps; Gemini API billing; LiveKit Cloud billing, quotas and room timeouts) |

## 13.2 Historical, agent-reported (files exist; results not re-run)

`SPX_VOICE_REQUIREMENTS_VERIFICATION.md`, `STAGE4_EVIDENCE_REVIEW.md`, `STAGE5_VOICE_TELEPHONY_VERIFICATION.md`, `STAGE5_MASTER_DIRECTIVE.md`, `SPX_VOICE_HANDOFF.md`, `PROJECT_STATUS.md`, `PROJECT_IMPLEMENTATION_TRACKER.md`, `PHASE_5_TRACKER.md`; test files listed in Part 2; `scratch/phase7/phase7{a,b,c,d,e}_*.json`, `production_hardening_evidence.json`; `scratch/phase7_signoff/*`; `scratch/phase6b/*`; `scratch/perf_bench_summary.json`; `C:\Users\JITEN\Downloads\voice_console_saas_business_section_audit_report.md`; `C:\Users\JITEN\Downloads\spx_voice_audit_export.csv`.

## 13.3 Missing or inaccessible

- Pre-sales A audits for Overview, Build, Observe and Admin; Pre-sales B; Pre-sales D.
- Written reports for C1, C2, C3, C4 (code and tests exist; reports not located). C1 has no dedicated test file located.
- Any Phase 6A artefact; result files for the Phase 6B suites; narrative reports for Phases 6B–10.
- `spx_voice_audit_export.json` (listed by the shell, unreadable).
- Backups referenced at `D:\spx-voice-backups\stage1b-pre-deploy\` (reported in the handoff; not inspected).
- Root cause of G-47 (no traceback at current log level).
- Whether `InboundQueueManager` is invoked on the LiveKit inbound path (not traced for this report).
- Staff management routes beyond those listed (G-49 is "not located", not "proven absent").

---

# PART 14 — FINAL ENGINEERING VERDICT

**A. Successfully built.** A broad, coherent platform: versioned agent workflows, owner dashboard, CRM and lead intelligence, live-event bus, cost classification and reconciliation, Asterisk IVR, campaign engine with crash recovery, LiveKit worker with Standard and Premium provider support, signed carrier callbacks, health/alert/audit services, tenant isolation.

**B. Genuinely ready today.** Local dashboard demonstration; agent configuration, draft, publish and version management at API level; a disposable Standard workflow with a worker-readable 60-second limit; the offline prerequisites for the first real voice test.

**C. Not ready.** Any real call; inbound and outbound operation; Premium; Budget as a sellable plan; production hosting; recovery; monitoring delivery; customer billing; capacity claims.

**D. Critical blockers.** G-01 (LiveKit), G-48 (worker fail-open), G-40 (no inbound monetary cap), G-41 (no server-side feature disabling), G-08 (production security), G-09 (backup/restore), G-04 (Vobiz), and for outbound G-13/G-15/G-14.

**E. Remaining workload.** 23 of 24 leaf packages; 54 unresolved P0–P2 register items (six awaiting verification only); 22 of 24 Release 1 conditions not yet PASS; 14 owner decisions. Task-level total not established.

**F. First paying pilot requires.** All Part 8 conditions PASS: real Standard conversation and termination; real Vobiz inbound with provider-issued signature evidence; CRM and callback chain from a real call; inbound cap and feature flags; HTTPS/secrets; backup with restore drill; alert delivery; consent notice; language acceptance; three stable days; owner self-operation.

**G. Complete three-mode SaaS requires.** Release 1 plus PKG-F0, C, A, E, B, D, 12a–f, F and 4c, one real call per mode, a supervised safe mode switch, reconciled invoices, and a decision on customer invoicing (proposed PKG-14).

**H. 30,000 authorized attempts/day requires.** PKG-11 and PKG-13: written carrier CPS/channel allocation, provider quotas, measured sessions per worker and host profiles, staged real traffic with stop thresholds, and explicit acceptance per deployment and in aggregate. Synthetic evidence does not substitute.

**I. Exact next action.** Owner decisions on G-48 (reject vs default cap) and G-40 (inbound cap approach), then approval of **PKG-2**. PKG-1b follows the offline packages as already agreed; it should not be started before the G-48 guard is in place.

**J. Overall verdict.** The codebase is a sound foundation and the program is controllable package by package. The product is **not** commercially ready: it has never carried a real call, and two financial-safety gaps (G-40, G-48) were found during this report that must be closed before any paid or customer-facing use. With the staged plan and the corrections recorded here, completion is feasible; nothing in the evidence indicates a need to redesign.

---

## K. Archival correction (2026-11-15) — G-48 status superseded, original report preserved unchanged above

This report is dated 14 Nov 2026 against baseline `ca56a4a` and is preserved above exactly as originally written, including its characterization of **G-48 as OPEN / worker fail-open / not implemented** (see §D "Critical blockers" and the Part 12 table). That characterization was accurate at the time this report was written.

It no longer reflects current source. A subsequent, separately committed G-45/G-48 safety cluster (commit `dc3ba90`, "checkpoint: preserve G-45/G-48 offline-validated safety cluster") implemented the duration-validation guard this report called for, at `api/services/livekit/worker.py` (`_validate_pinned_call_duration`, single validation call site feeding the session's only construction site) and `api/services/business/agent_training.py` (`resolve_max_call_duration_seconds`). A dedicated focused test suite reports 166/166 passed against that exact committed revision, reproduced independently in a disposable clean Python environment. CP-00 (`docs/phase-checkpoints/CP-00.md`) records owner acceptance of this work, offline scope only.

An independent read-only audit (2026-11-15) re-verified the fix directly against the committed source (not against this report) and confirmed: the duration guard runs before model resolution/session construction; a rejected startup is recorded as `call_disposition="FAILED"` rather than misreported as answered/successful; async cleanup cancels/awaits pending tasks without masking the original failure. See `docs/phase-checkpoints/CP-00.md` for the full evidence trail.

**This correction supersedes only the G-48 row/status in this report.** It does not retroactively validate any of this report's other open items (G-40, G-01, G-08, G-09, G-04, PKG-1b/PKG-3 and beyond), which remain as originally stated unless separately corrected. No live LiveKit/Gemini/Vobiz provider call, and no 30,000-calls/day scalability claim, is made by this correction or by CP-00.
