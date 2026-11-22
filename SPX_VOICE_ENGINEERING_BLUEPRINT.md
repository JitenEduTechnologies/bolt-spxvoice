# SPX VOICE — ENGINEERING BLUEPRINT (AUTHORITATIVE)

**Status:** PLANNING DOCUMENT · authorizes no implementation · created 13 Nov 2026
**Workspace:** `D:\spx-voice-mvp` at commit `ca56a4a` (12 Nov 2026 22:56 IST) plus 13 uncommitted files (624 insertions / 185 deletions, all from the current engineering session)
**Protected original:** `F:\spxv1\spx-voice` — never accessed
**Requirements source:** `SPX_VOICE_MASTER_ENGINEERING_SPECIFICATION.md` (53,253 bytes, md5 `9b7e8edd7a13c765fd1a499b4969c21c`, currently at `C:\Users\JITEN\Downloads\`, not in the repository)
**Maintenance rule:** this is the ONE roadmap and ONE issue register. Historical trackers in the repository are evidence, not plans. Package status changes are appended to Appendix B; IDs are never renumbered.

> Early authorized test calls are NOT production approval. Customer deployment stays blocked until the Release 1 security, monitoring, financial and recovery gates in §13 pass.

---

## 0. Evidence register

### 0.1 Accessible and used

| Source | Location | Date | Used for |
|---|---|---|---|
| Master Engineering Specification | `C:\Users\JITEN\Downloads\SPX_VOICE_MASTER_ENGINEERING_SPECIFICATION.md` | 13 Nov | Requirements (authoritative) |
| Fable Architecture & Readiness Assessment, addendums (hosting, hold music, industry, voice, numbers, capacity), reconciliation, expert review, consolidation review | this engineering conversation | 13 Nov | G-01…G-43, PKG-1…13, corrections H-1…H-10 |
| Claude Code engineering session | code traces, 59 focused tests, read-only DB checks, container inspection | 13 Nov | Code-level evidence |
| `SPX_VOICE_REQUIREMENTS_VERIFICATION.md` | repo | 1 Nov | Stage 1B–5 matrix |
| `STAGE5_VOICE_TELEPHONY_VERIFICATION.md`, `STAGE4_EVIDENCE_REVIEW.md`, `STAGE5_MASTER_DIRECTIVE.md` | repo | 1 Nov | Stage 4/5 reconciliation, real local SIP/RTP evidence |
| `PROJECT_IMPLEMENTATION_TRACKER.md`, `PROJECT_STATUS.md`, `SPX_VOICE_HANDOFF.md` | repo | 31 Oct–1 Nov | REQ-* implementation list (note: REQ-C1…C4 ≠ Pre-sales C1–C4) |
| `PHASE_5_TRACKER.md` | repo | 4 Nov | Phase 5 SSE/CRM acceptance (20/20 incl. Playwright T5-20) |
| `scratch/phase6b/test_stage_a_integration.py`, `test_stage_b.py` | repo (tracked) | 11–12 Nov | Phase 6B provider-readiness synthetic suites (see naming note 0.3) |
| `scratch/phase7/*.json`, `progressive_load_runner.py`, `mock_telephony_ai.py` | repo (tracked) | 10–11 Nov | Phase 7 synthetic capacity, fairness, recovery, safety, hardening evidence |
| `scratch/phase7_signoff/test_signoff.py`, `test_arq_worker_cron_execution.py`, seeds | repo (tracked) | 11 Nov | Lease/reclaim/quarantine sign-off tests |
| `scratch/perf_bench_summary.json`, benchmark scripts | repo (tracked) | 10 Nov | Five-area latency measurements |
| Business-section video audit report (`voice_console_saas_business_section_audit_report.md`) | `C:\Users\JITEN\Downloads\` | 7 Nov | ISS-01…ISS-13 (Pre-sales A, Business area) |
| Audit log export (`spx_voice_audit_export.csv`, 100 rows) | `C:\Users\JITEN\Downloads\` | 9 Nov | Emergency-stop activate/resume/verified events; incident records |

### 0.2 Referenced but NOT PROVIDED (evidence gaps kept open — G-25)

Pre-sales A video audits for Overview, Build, Observe, Admin; Pre-sales B (logging inventory); Pre-sales C1–C4 reports (logging foundations, billing/audit integrity, monitoring/alerts, System Health & Audit Center); Pre-sales D (integrated acceptance); Phase 6B–10 narrative reports; "Antigravity" session outputs other than the artefacts above; the earlier 883-line brief and 1,422-line draft (only their pasted content is known). `spx_voice_audit_export.json` was listed by the shell but is not readable at that path. Nothing from these is assumed. To close G-25 the owner supplies file paths or exports.

### 0.3 Contradictions and naming collisions (resolved here)

| Item | Conflict | Resolution |
|---|---|---|
| "Stage A/B" | `scratch/phase6b` uses Stage A = provider integration readiness, Stage B = provider protocol corrections. This blueprint's Stages A–F are the spending/switching/pinning/handoff/Premium/regression safety stages. | Phase 6B suites are referred to as **P6B-A** and **P6B-B**; safety stages keep **Stage A–F**. |
| Emergency Stop | 7 Nov audit: P0 failure (ISS-01). 9 Nov audit export: 19 activations, 11 resumes, 1 `emergency_stop_verified`. Current code: `AlertDialogDescription asChild` present (`ui/src/app/costs/page.tsx:280`). | Fix is in code; status **IMPLEMENTED, browser re-verification pending**. |
| Room timeouts as spend guard | Earlier Fable text implied `empty_timeout` bounds a call. LiveKit docs: it applies only before anyone joins; `departure_timeout` after the last participant leaves. | Neither ends an active conversation. Corrected in §8. |
| Vobiz callback auth | Earlier Fable text proposed IP/shared-secret fallback. Code verifies `x-vobiz-signature-v3` + nonce (legacy signature + timestamp) and fails closed with 401 when the secret is unset (`api/services/telephony/providers/vobiz/routes.py:38-182`); Phase 7 hardening evidence shows forged/tampered/replayed callbacks rejected. | Fallback withdrawn. Real provider-issued evidence still required (§12 PKG-3). |
| Pinning model | Fable blueprint: pin at enqueue + re-bind on Resume (contradictory). | Replaced by immutable stamping at atomic dispatch admission (§5.3). |
| 30,000/day | Phase 7B evidence: 30,000 synthetic calls processed through mock telephony/AI in isolated containers (72 calls/s, p95 456 ms). | Proves the software dispatch/persistence path only. Not carrier, media, AI or capacity certification (§10). |

---

## 1. Executive readiness assessment

SPX Voice is a substantially built Dograh-derived platform. Backend, dashboard, versioned workflows, SSE live bus, CRM/lead intelligence, cost ledger, Asterisk IVR, campaign dispatcher with leases/reclaim/quarantine, and signature-verified carrier callbacks exist with focused test and synthetic evidence. No real AI conversation has ever occurred: LiveKit is not connected (`LIVEKIT_URL/API_KEY/SECRET` unset; `/api/v1/health` → `livekit_enabled:false`), so Gemini, Vobiz, transcript, cost and duration-cap behaviour are unseen live.

| Milestone | Verdict | Proven | Not proven |
|---|---|---|---|
| Local dashboard demo | GO | Login; 35 pages; SSE (T5-20 Playwright); lifecycle fixes (59 tests) served by restarted containers; Business-area ISS fixes present in code | Browser click-through of today's `/agent` changes; ISS re-verification |
| Browser Standard call | NO-GO | Route `POST /api/v1/livekit/session/{wf}/{run}`; worker `_create_session(google_realtime)`; key present and unmasked; duration loop in code | LiveKit connection, key validity, audio, transcript, cost, duration cap live |
| Vobiz inbound | NO-GO | Wizard, trunk/dispatch-rule code, signature verification, `WR-LK-IN-*` path | Account, DID, trunk, provider-issued callback evidence, audio |
| First paying pilot | NO-GO | Isolation (Stage 1B 38/38; T5-06/17; Phase 7E/hardening); inbound wallet cap (S5-06 local); audit events | All above + HTTPS/secrets/restore drill/alerts/3 stable days/owner self-operation |
| Three-mode SaaS | NO-GO | IVR 26/26 + real local RTP; LiveKit pinning (T5-09); fail-closed fallback; lease/reclaim recovery (Phase 7D) | Stages A–F; Premium registry; Budget label; transfer; real calls per mode |
| 30K/day | NO-GO | Synthetic software path to 30,000 (Phase 7B); fairness (7C); recovery (7D) | Carrier CPS/channels, Gemini quota, sessions/worker, VPS profiles, staged real traffic |

**First package:** PKG-1 (LiveKit/Gemini readiness), configuration only, after the §8.3 financial pre-conditions.

---

## 2. Existing architecture and verified implementation

```
                 Next.js 15 dashboard (ui/src/app): overview · agent · workflow · campaigns · leads
                 conversations · appointments · costs · telephony-configurations · settings · superadmin/*
                        │ REST /api/v1 (Bearer)                 │ SSE /api/v1/business/events/stream
┌───────────────────────▼────────────────────────────────────────▼──────────────────────────────┐
│ FastAPI (api/)                                                                                   │
│ routes: auth business workflow livekit telephony campaign superuser reports health               │
│ services: workflow (versions, graph) · business (agent_training, lead_intelligence,              │
│   callback_scheduler, action_inbox, action_safeguards, identity_verification)                   │
│   pricing/cost_controller · campaign (campaign_call_dispatcher, runner, campaign_tasks)          │
│   telephony (ari_manager, inbound_queue, smart_did, compliance, status_processor, providers/*)   │
│   livekit (client, vobiz, worker, conversation_state, silence_manager, interruption_filter)      │
│   events/realtime_bus · chatwoot/outbox · health/system_monitor                                  │
│ db: workflows · workflow_definitions · workflow_runs · campaigns · queued runs ·                 │
│   organization_configurations · telephony_configurations · customer_profiles · dashboard_events │
│   action_idempotency_records · customer_suppression_dnc · telephony_number_health ·             │
│   chatwoot_outbox_jobs · objection_responses                                                     │
└──────┬───────────────┬───────────────┬─────────────────────┬───────────────────┬───────────────┘
   PostgreSQL 17    Redis 7 / ARQ     MinIO               LiveKit Agents worker   Asterisk 20 ARI
   (pgvector)       (jobs, pub/sub,   (recordings,        (worker.py; separate    (economy IVR;
                    emergency flag,   quota, retention)   container; same image)  real RTP verified)
                    leases)                                     │
                                                     LiveKit server — NOT CONNECTED
                                                     ├─ Gemini Live (google_realtime)
                                                     └─ Deepgram → DeepSeek → Cartesia
                                                                │
                                                     Vobiz SIP trunk — NOT PRESENT · VoiceLink routes exist
```

### 2.1 Verified by test or running system

| Area | Evidence |
|---|---|
| Org scoping, secret encryption, masking | Stage 1B 38/38; T5-06/17; Phase 7E tenant isolation; hardening `task_c_d_tenant_authorization` PASS |
| Asterisk IVR | Stage 2 26/26; real SIP/RTP loopback (400 Hz tone, RMS 5290); 10 concurrent sessions; 10+5+busy admission |
| Three-mode cost tiers, pinned-vs-follow-latest, emergency stop, wholesale privacy | Stage 3 8/8; Phase 7E kill-switch 5 ms; hardening 29 ms |
| SSE bus with replay, owner actions, objection approval, customer continuity | Phase 5 20/20 incl. Playwright login/overview |
| Campaign dispatcher: cancel re-check, DB concurrency locks, 20k ingest, lease renewal (`campaign_client.py:799`), reclaim, quarantine, ownership fencing, ambiguous-outcome handling | Phase 4 16/16; `scratch/phase7_signoff/test_signoff.py` (10 tests); hardening `task_a_d_crash_recovery` PASS |
| Redis outage fail-closed; duplicate webhook idempotency; ambiguous 504 cooldown | Phase 7D PASS |
| Vobiz v3 / VoiceLink HMAC callback verification incl. forged, tampered, replay, unconfigured | hardening `task_b_d_webhook_auth` PASS (synthetic) |
| Synthetic software throughput 100→30,000 calls, 0 errors, p95 ≤ 565 ms, 20 PG connections | Phase 7B (mock telephony/AI, isolated DB/Redis) |
| Multi-campaign fairness, burst rate limiting | Phase 7C |
| Workflow lifecycle: explicit `workflow_id` on agent/ivr/restore routes, no fallback to Workflow 1, template route de-shadowed, duplicate-click guard, publish readiness gate, audit events | 59 tests (this session); containers restarted; `tsc` clean |
| Business-area ISS fixes in code | ISS-01 `asChild` (`costs/page.tsx:280`); ISS-03 `runId` deep links (`leads/page.tsx:681`, `appointments/page.tsx:433`, `conversations/page.tsx:103`); ISS-05 toast (`appointments/page.tsx:120`); ISS-07 disposable filter (`campaigns/new/page.tsx:119`); ISS-10 `custom_field_` slug (`agent/page.tsx:747`); ISS-06 raw env-var string no longer present |
| Backend latency (five areas) | `perf_bench_summary.json`: overview 47 ms avg, agent 36 ms, workflow/count 23 ms, health 78 ms |

### 2.2 Implemented but unverified (code exists, never exercised live)

Everything touching LiveKit, Gemini, Deepgram, DeepSeek, Cartesia, Vobiz or PSTN; duration-cap loop (`worker.py:1241-1248`); session revocation on role change; multi-node idempotency; ISS-02/04/08/09/11/12/13 (no code evidence found either way); Phase 7 evidence under real providers.

### 2.3 Confirmed defects (code-located, open)

| ID | Location | Defect |
|---|---|---|
| G-13 | `api/services/campaign/campaign_call_dispatcher.py:290-317` | Budget admission skipped when no `budget_config`; spend read from campaign metadata not ledger; exception → debug log pass-through (fail-open) |
| G-15 | `api/services/telephony/ari_manager.py:672`; queued runs carry no definition id | Asterisk reads live `economy_ivr` flag; queued calls not pinned until dispatch |
| G-17 | `api/services/configuration/registry.py:205-304` | No DeepSeek LLM class; `resolve_effective_config` yields `llm=None`; worker (`worker.py:1986`) supports DeepSeek |
| G-18/G-26 | `agent_training.resolve_product_mode_overrides`; `cost_controller.py:111` | UI "Budget" → `budget` → Gemini overrides (= Standard); no `budget` pricing branch; Asterisk IVR keyed on `economy_ivr` |
| G-29/G-33 | `docker-compose.yaml:169,175,333-335`; `system_monitor.py:341-359` | `REDIS_URL`, `MINIO_ENDPOINT`, `ENABLE_AWS_S3` literal; Hostinger production label |
| G-42 | `api/enums.py:136` | `TRANSFER_CALL` tool is Twilio-only; no LiveKit SIP transfer |
| G-12 | `api/requirements.txt` | `pipecat-ai`, `pytest-timeout` not pinned (Gate 5 report) |
| G-08 | compose, UI env | default secrets (`redissecret`), `NODE_ENV=oss` → non-Secure cookies |

---

## 3. Business-owner lifecycle

"Pending" = exists in code, not exercised in a browser or on a real call. Older owners get one primary action per screen and plain-language errors; advanced controls stay behind an "Advanced" toggle, never removed.

| Step | Owner sees / does | Frontend → API → records | Today | Outstanding |
|---|---|---|---|---|
| Login | `/auth`; single org auto-selected | `routes/auth.py` → session cookie | Works | Secure cookie needs `NODE_ENV=production` (PKG-4a) |
| Onboarding, industry | Agent Studio 7 tabs; 10 presets visible | `/agent` → `/api/v1/business/agent*` → `workflow_configurations.agent_training` | Works at API | Industry not stored per org (PKG-12a) |
| Profile, hours, FAQs | Profile/FAQ tabs | same | Works | Browser acceptance (PKG-2) |
| Agent creation | Create Agent → template / Blank Canvas | `POST /workflow/templates/duplicate`, `/workflow/create` | Fixed | Browser acceptance |
| Draft / Publish / Restore | Confirm dialog names the workflow | `POST /business/agent/draft|publish?workflow_id`, `/agent/restore/{def}?workflow_id` → `workflow_definitions`, audit events | Fixed; 422 if provider config missing | Browser acceptance |
| Plan selection | Standard / Premium / Budget | `product_mode` → `resolve_product_mode_overrides` | Standard OK; Premium 422 (G-17); Budget mislabelled (G-26) | PKG-E |
| Provider readiness | Model configurations; toast on save | `PUT user_configurations` | Works | Key validity only on first real call (PKG-1) |
| Number | Telephony wizard | `POST /livekit/vobiz/setup` → `telephony_configurations`, LiveKit trunk + dispatch rule | Code only | PKG-3 |
| Inbound call | Live status; run in Conversations | Vobiz → LiveKit SIP → worker → `workflow_runs` → SSE | Code only | PKG-1, PKG-3 |
| Outbound campaign | Create/start/pause/resume/stop | `campaign_tasks` → dispatcher → queued runs → `/telephony/initiate-call` | Works locally; unsafe switching | Disabled in R1 (PKG-4a); Stages C/A/B |
| Transcript → lead → inbox | Conversations, Leads, Tasks | worker → `lead_intelligence` → `customer_profiles`, inbox, `dashboard_events` | Mock-tested | First real transcript (PKG-3) |
| Appointment, callback | `requested` until confirmed | `callback_scheduler`, `action_idempotency_records` | Tested (T5-14) | ISS-12 duplicate appointments UNVERIFIED |
| Staff escalation | Callback request in inbox (R1); transfer (R2) | `action_inbox`; `TRANSFER_CALL` Twilio-only | Inbox exists; transfer missing (G-42) | R1 policy approval; PKG-12b |
| Costs, caps, Emergency Stop | Costs page | `GET /business/costs`, `POST /business/costs/budget` (caps only), Redis `emergency_stop:{org}` | Works; outbound cap advisory (G-13) | Stage A |
| Reports, recordings | Pre-signed MinIO URL; cross-org 403 | `routes/reports.py`, storage quota | Tested | Real recording |
| Staff and roles | Settings; superadmin | RBAC matrix (S4-02) | Tested | Revocation on role change (G-19) |
| Health | `superadmin/health-audit` | `routes/health.py`, `system_monitor.py` | Exists; incidents logged (audit export shows `incident.postgresql/redis`) | Hostinger label (G-33); owner-level status card |
| Export, offboarding | Partial | `routes/reports.py` | Partial | Procedure (PKG-4b docs) |
| Sign-out, return | Session cleared | `routes/auth.py` | Works | TTL/revocation evidence (G-19) |

---

## 4. Three voice-engine architecture

### 4.1 Execution paths as they exist

```
STANDARD / PREMIUM
carrier or browser → LiveKit room → agent dispatch (metadata: workflow_id, run_id, call_type)
  → worker.entrypoint (api/services/livekit/worker.py ~2153)
      ├─ workflow_run resolved/created, pinned workflow_definition_id
      ├─ run_configs = definition.workflow_configurations
      ├─ guard: product_mode == economy_ivr → ValueError (this session)
      ├─ user_config = resolve_effective_config(global, run_configs.model_overrides)
      ├─ _create_session(user_config)  (worker.py:1875)
      │     realtime: google_realtime ✔ openai_realtime ✔ vertex ✔
      │     pipeline: STT deepgram ✔ · LLM openai/google/groq/openrouter/azure/bedrock/deepseek ✔ · TTS cartesia ✔
      ├─ conversation_state · silence_manager · interruption_filter
      ├─ livekit_events · transcript · lead_intelligence · cost row
      └─ max_duration_seconds loop (worker.py:1241-1248) → schedule_shutdown → _shutdown_call → delete_room
         (code-level; live enforcement UNVERIFIED — G-27)

ECONOMY IVR
carrier → Asterisk → ARI Stasis → ari_manager (reads configs["economy_ivr"] LIVE at :672 — G-15)
  → economy_ivr_controller (DTMF state machine, auto-hangup) → CDR → cost (economy_ivr rate)
```

### 4.2 Remaining work per mode

| Mode | Engineering | Configuration | Real-world verification |
|---|---|---|---|
| Standard | none for R1 | LiveKit env; worker start | PKG-1 browser call; PKG-3 inbound; language set (Te/Hi/En) per model version (PKG-12e) |
| Premium | `DeepSeekLLMConfiguration` in `registry.py` (G-17) | Deepgram, DeepSeek, Cartesia keys | one real call; Cartesia speed/volume/emotion vs current model (G-43) |
| Economy IVR | stamp IVR config to the definition at admission (G-15); UI Budget → `economy_ivr` (G-26); `budget` pricing branch (G-18); handoff (G-16) | carrier trunk to Asterisk | one real IVR call with DTMF |

### 4.3 Plan-label decision (owner decision required before PKG-E)

Code reality: UI offers Standard / Premium / Budget; `budget` publishes Gemini overrides (so today "Budget" is Standard); the Asterisk IVR is a separate flag; tariffs differ (`economy_ivr` ₹0.80 vs UI Budget ₹0.85, both baseline software rates, not invoices — G-28). **Recommended:** UI "Budget" = Economy IVR: publishing sets `product_mode="economy_ivr"`, writes the IVR config, removes `model_overrides`; `budget` remains a read alias of `economy_ivr`; `cost_controller` maps `budget` → economy rate. The alternative (Budget as a cheaper Gemini tier) has no pricing basis in code and leaves the IVR plan unnamed.

---

## 5. Inbound/outbound and campaign safety

### 5.1 Inbound lifecycle (target after PKG-3/PKG-4)

```
DID rings → carrier → LiveKit SIP inbound trunk → dispatch rule(workflow_id)
  → worker: SIP participant wait → WR-LK-IN run (initialized → running)
  → admission: INBOUND_CONCURRENT_CALL_LIMIT (org config key exists) + wallet balance
        full → inbound_queue (hold ≤ INBOUND_QUEUE_MAX_WAIT_SECONDS, then busy)
  → pinned definition → session → conversation
  → hangup / max_duration → completed → CDR reconcile (idempotent) → cost row
  → lead_intelligence → customer_profiles / inbox → dashboard_events (SSE)
```
Existing failure rules: emergency stop checked fail-closed; fail-closed worker teardown on crash (S5-05); startup failures written to `livekit_events` / `annotations.livekit_startup_error` and shown on the run page.

### 5.2 Outbound lifecycle today and defects

```
campaign(created → syncing → running ⇄ paused → completed | failed)   [enum campaign_state]
  → contacts → queued runs (queued → processing → processed | failed)   [enum queued_run_state]
  → dispatcher batch under DB lock, lease renewal (campaign_client.renew_queued_run_lease)
        emergency stop ✔ fail-closed
        budget check ✘ fail-open (G-13)
        DNC, calling window ✔ (Phase 7E)
        number selection smart_did ✔ local
        initiate-call → workflow_run pinned at THIS moment (G-15: too late for queued rows)
  → crash → reclaim / quarantine / ownership fencing ✔ (Phase 7 sign-off)
```

### 5.3 Immutable configuration stamping at atomic dispatch admission (Stage C) and safe switching (Stage B) — PROPOSED

- A campaign carries one pointer `active_definition_id` (PROPOSED field in `campaigns.orchestrator_metadata`; no migration). It may change only while `state=paused` and the dispatch barrier is clear.
- When the dispatcher moves a row `queued → processing` inside the existing DB-locked, lease-renewing batch, it stamps the row with `workflow_definition_id` and `product_mode` taken from the pointer. From that instant the row is immutable. `initiate-call`, the worker and `ari_manager` read the stamped id. **No row is ever re-bound.**
- Pause: set `barrier_pending=true` (PROPOSED, in `orchestrator_metadata`) → dispatcher skips the campaign → a reconciler flips `state=paused` and clears the flag only when zero rows of that campaign are `processing`. The mode-change endpoint returns 409 unless `paused` and barrier clear. Explicit Resume sets `state=running`; admission continues with the current pointer.
- Rows stamped before the pause finish on their old configuration; rows admitted after Resume use the new one. Different campaigns run different pointers concurrently.

```
running ──Pause──► barrier_pending ──(0 processing rows)──► paused
paused  ──Change mode──► validate readiness + cost delta ──Publish──► paused (pointer updated)
paused  ──Resume──► running (admission continues; stamping uses new pointer)
running/barrier_pending ──Change mode──► 409 "pause and wait for calls in progress to finish"
```

### 5.4 Durable spending reservation (Stage A) — PROPOSED

```
admission (same transaction as stamping):
  SELECT budget_accounts FOR UPDATE (org, period)
  exposure = max_call_duration_s/60 × (mode_rate + carrier_rate)      rates = baseline (G-28)
  if no account OR available < exposure → ROLLBACK; row → failed(reason=budget); never pass-through
  INSERT budget_reservations(run_id, exposure, state=held, expires_at); account.reserved += exposure
settle: existing reconcile_workflow_run_cdr (idempotent via ActionIdempotencyRecordModel) → reserved −= exposure; spent += actual
sweeper (ARQ cron): reservation past expires_at AND run terminal → release; run not terminal → extend + alert
```
Enforceability depends on the worker's duration loop being proven live (G-27) and tested in PKG-A. Feature flag `BUDGET_RESERVATIONS_ENABLED` (PROPOSED): off = **reject all outbound** (fail-closed), never legacy fail-open.

### 5.5 Budget IVR → AI handoff (Stage D) — PROPOSED

Asterisk IVR → owner-enabled "Press 9 for assistant" → `economy_ivr_controller` checks entitlement (`FEATURE_IVR_HANDOFF`), capacity, budget → Asterisk originates a SIP leg into LiveKit (trunk already modelled) → child `workflow_run` pinned to the AI definition; parent IVR run closed with disposition `handoff` → two cost lines (IVR minutes, AI minutes) → audit event. AI leg failure → IVR announces callback option; never silent, never a different engine.

---

## 6. CRM, transcripts, appointments and integrations

Existing pipeline: worker → `lead_intelligence.py` (score 0–100, intent, objections) → `customer_profiles` (tenant-scoped phone index) → `action_inbox` → `dashboard_events` with `last_event_id` replay (bounded queues, maxsize 200). Appointments stay `requested` until a scheduler confirms (T5-14). Lead capture dedupes within 24 h (`business.py:3394`). ISS-12 (duplicate appointments at identical timestamps, 6 Nov) has no located fix: UNVERIFIED, carried in the register.

PROPOSED (no migration): `provenance: stated|inferred` on each extracted fact in the run's `gathered_context`, surfaced in Leads as "Customer said" vs "Agent inferred".

Partial transcripts: interim text events replace the previous interim for the same `(run_id, turn_index)`; a final persists once; a later final for the same turn supersedes. UI keys on `(run_id, turn_index)`.

Integrations: the async outbox (`chatwoot_outbox_jobs`, DND-checked, idempotent, T5-18) is the template for WhatsApp, CRM, calendar, webhooks. All disabled in Release 1; each gets an enable flag, Fernet-encrypted credential and a health row before Release 2.

---

## 7. UX, accessibility and performance

Measured (10 Nov, `perf_bench_summary.json`, dev-mode browser): backend averages 23–78 ms; browser cold loads 3.9–5.2 s, warm 2.3–2.9 s. The 7 Nov audit (ISS-08/13) saw the same cold delays and missing click acknowledgment. Diagnosis: Turbopack dev compilation and stale bundles (confirmed this session), not backend latency.

Targets, measured only on `npm run build && next start` with 1k runs / 5k leads seeded: click acknowledgment ≤200 ms; warm navigation p95 <2 s; dashboard ready p95 <3 s; SSE event-to-UI <2 s; zero duplicate writes under double-click (Create fixed; extend to Publish, Import, Start Campaign); no stale data after workflow switch (fixed). Accessibility: Radix primitives in use; contrast of muted badges (ISS-05 note) and focus order checked in PKG-2 with the browser; small-screen layout of the five areas unverified.

---

## 8. Security, auditing and financial controls

### 8.1 Control matrix

| Control | Exists | Remaining |
|---|---|---|
| Org scoping on every read/write | Verified (Stage 1B; T5-06/17; Phase 7E; hardening) | — |
| Credential encryption | Fernet (`TELEPHONY_CREDENTIALS_ENCRYPTION_KEYS`) | Rotation procedure (PKG-4b) |
| Secret masking in logs/responses | Verified | ISS-06 fixed in code; browser re-check |
| Carrier callback verification | Vobiz v3 + nonce, VoiceLink HMAC; fail-closed | Provider-issued evidence (PKG-3) |
| Audit events | login, emergency stop, budget change, publish/restore/config | number changes, campaign transitions, handoff |
| Secure cookies, HTTPS | No (`NODE_ENV=oss`; TLS unverified) | PKG-4a |
| Non-default secrets | No (`redissecret`, compose defaults) | PKG-4a |
| Disabled features reachable by API | UNVERIFIED (G-41) | PKG-4a entitlement flags |
| Session revocation on role change | Partial (G-19) | PKG-F |
| Inbound spend cap | Wallet + overdraft (S5-06 local); admission-time only | Live proof (G-40) |
| Outbound spend cap | Fail-open (G-13) | Stage A |

### 8.2 Financial safeguard classification (verified against provider documentation, 13 Nov 2026)

| Mechanism | Type | Verified behaviour | Use |
|---|---|---|---|
| Google Cloud standard budget | **Alert only** | Does not stop usage | Set; not relied on |
| Gemini API project monthly spend cap (AI Studio → Spend) and Cloud Billing spend-cap budget | **Enforceable admission control with delay** | Blocks Gemini usage for the project when reached; enforcement not instant (~10 min reporting lag reported by Google staff); long-running agent sessions may overrun | **Required before any paid call**; small amount; owner states the figure, never the key |
| Gemini billing tier cap (Tier 1 = $250) | Enforceable ceiling | Fixed per tier | Backstop |
| LiveKit Cloud Build (free) plan | **Hard cap** | Requests fail past allowance; no overage | **Preferred for PKG-1** |
| LiveKit Cloud paid plans | **No dollar cap** | Overage billed; only per-project quotas (concurrency, session duration) enforce | If paid: concurrency quota = 1 for tests |
| Room `empty_timeout=60` / `departure_timeout=30` (`client.py:174-178`) | Idle-room cleanup | Apply before anyone joins / after the last participant leaves; **do not end an active conversation** | Prevent orphaned rooms only |
| Worker `max_duration_seconds` loop (`worker.py:1241-1248`) | **Application duration limit** | Code-level; live enforcement UNVERIFIED | 60 s in PKG-1; PASS requires the log line and room deletion |
| Emergency stop (Redis flag, fail-closed) | Enforceable admission control | Outbound only; 5–29 ms latency (synthetic) | Not relevant to browser test |
| Inbound wallet/overdraft | Admission-time control | No mid-call check in worker | Minimal value for pilot |
| Owner stops worker container | Manual kill | Immediate | Always available |

**Conclusion:** mid-call spend is bounded only by the duration limit until Stage A; Stage A itself depends on that limit being proven. No alert or delayed cap is treated as a guarantee of zero overspend.

### 8.3 Pre-conditions for the first paid call (PKG-1)

1. Gemini project monthly spend cap set in AI Studio (amount reported by owner).
2. LiveKit Cloud Build plan, or paid plan with concurrency quota 1.
3. `max_call_duration_seconds=60` in the run's `initial_context`.
4. Worker container stoppable by the owner at any moment.
5. Inbound wallet/overdraft set to a minimal value.
6. Acceptance records the `maximum call duration reached` log line and room deletion.

---

## 9. Hosting independence and deployment

Current: `docker-compose.yaml` services postgres, redis, minio, spx-voice-init (migrations), nginx, coturn, api, ui, worker, cloudflared; no LiveKit SFU; Asterisk only in `local`, `coolify`, `phase7-test` overlays; no resource limits; `DATABASE_URL`, `LIVEKIT_URL` env-driven; `REDIS_URL`, `MINIO_ENDPOINT`, `ENABLE_AWS_S3` literal (G-29). `deploy/templates/nginx.remote.conf.template` has `proxy_buffering off` and `proxy_read_timeout 3600s` (SSE-safe) — must be the template actually deployed.

Target (PKG-4a/4b/4c): one base compose + overlays `profiles/{lite,standard,high,distributed}`; every external endpoint `${VAR:-default}`; compose `profiles:` to disable local postgres/redis/minio when external; `deploy.resources.limits`; worker `MAX_AI_SESSIONS` (PROPOSED env) refusing jobs above capacity with a `livekit_events.capacity_refused` record and SSE alert; `INBOUND_CONCURRENT_CALL_LIMIT` per profile; `install.sh` preflight (RAM, CPU, disk, public IP, UDP range for coturn/SIP, 443/80, DNS, clock); `backup.sh` / `restore.sh` covering Postgres, MinIO bucket, `.env`, image tags, encrypted; `upgrade.sh` with tagged images; rollback = restore or forward fix, never `alembic downgrade` alone; generic `DEPLOYMENT_PROFILE` label.

Profiles are **UNMEASURED** until PKG-13. No RAM figure is promised; 1 GB is explicitly not supported.

---

## 10. 30,000 attempts/day scalability architecture

### 10.1 What Phase 7 proved and did not prove

Phase 7B processed 100 → 30,000 synthetic calls through `mock_telephony_ai.py` in isolated containers at 72 calls/s with p95 456 ms and zero errors; 7C showed fairness and burst limiting; 7D crash/outage/duplicate/ambiguous recovery; 7E compliance gates. This certifies the **software dispatch, persistence, idempotency and recovery path**. It says nothing about carrier CPS/channels, SIP media, AI session concurrency, worker memory, or VPS sizes.

### 10.2 Capacity model (variables, not figures)

`CPS_peak = A·P/(W·3600)`; `ringing = CPS·r`; `answered = CPS·a·d`; `AI_sessions = answered·f`; `channels ≈ ringing + answered`, where A attempts/day, W operating hours (after regulatory review), P peak factor, a answer rate, r ring seconds, d talk seconds, f AI share. Inputs come from the carrier's written allocation, Google's quota and PKG-13 measurement. Per-deployment and aggregate figures are recorded separately; aggregate = sum of certified deployments plus a shared-carrier check.

### 10.3 Architecture additions (PKG-11), all on existing modules

Per-carrier/per-number token buckets in the dispatcher (reuse `campaign.rate_limit_per_second`, `telephony_number_health`); weighted round-robin across campaigns and orgs; reservation-based admission (Stage A); reconcile-on-boot (Phase 7 reclaim already exists; extend to `processing` rows older than threshold); graceful shutdown (worker drains sessions; dispatcher stops batches); metrics export (`core/monitoring.py` → text endpoint, PROPOSED); alerts on reject ratio, 429/503 ratio, reservation failures, queue depth. Stop thresholds: carrier rejects >5 %/5 min, provider 429/503 >2 %/5 min, Redis unreachable, spend ≥ cap → dispatch halts; resume is human.

### 10.4 Proof ladder

Synthetic (done for software path) → local media (Asterisk loopback, done) → approved provider tests (PKG-1/3) → staged real traffic ≥10 % of per-deployment target → ramp with provider-written limits → explicit owner acceptance. No step is skipped; no capacity is claimed before its step.

---

## 11. Consolidated issues and remaining work

Severity P0–P3 (spec §10.1). Evidence status: VL verified live · VT verified by local test · IU implemented unverified · DEF confirmed defect · MISS missing · EXT external blocker · DEFER deferred · EU evidence unavailable. Gate: R1 / R2 / R3.

| ID | Sev | Status | Issue | Module / evidence | Package | Gate |
|---|---|---|---|---|---|---|
| G-01 | P0 | EXT | LiveKit not connected | `LIVEKIT_*` unset; `livekit_enabled:false` | PKG-1 | R1 |
| G-02 | P1 | IU | Gemini key never exercised | user config key present, unmasked | PKG-1 | R1 |
| G-03 | P1 | IU | Standard conversation unverified | mock handshake only (S5-02) | PKG-1 | R1 |
| G-04 | P1 | EXT | Vobiz account/DID/trunk; eligibility | `vobiz.py`, wizard | PKG-3 | R1 |
| G-05 | P1 | IU | Lifecycle fixes browser-unverified | 59 tests; containers restarted | PKG-2 | R1 |
| G-06/07 | — | VT | Mode mapping; publish readiness gate | tests pass (uncommitted) | PKG-2 commit | R1 |
| G-08 | P0 | DEF | Default secrets; `NODE_ENV=oss`; HTTPS unverified | compose; UI env | PKG-4a | R1 |
| G-09 | P0 | MISS | Backup/restore procedure | none | PKG-4b | R1 |
| G-10 | P1 | DEF | 13 files uncommitted | `git status` | PKG-2 | R1 |
| G-11 | P2 | IU | 976-suite: 64 fail / 1 error (1 Nov) unclassified | tests | PKG-F0/F | R2 |
| G-12 | P1 | DEF | `pipecat-ai`, `pytest-timeout` unpinned | `requirements.txt` | PKG-4a | R1 |
| G-13 | P0 | DEF | Campaign budget fail-open | dispatcher:290-317 | PKG-A | R2 |
| G-14 | P1 | DEF | Pause race; no barrier | `campaign_tasks.py` | PKG-B | R2 |
| G-15 | P0 | DEF | Queued runs unpinned; ARI reads live flag | dispatcher; `ari_manager.py:672` | PKG-C | R2 |
| G-16 | P1 | MISS | IVR→AI handoff + split billing | `economy_ivr_controller` | PKG-D | R2 |
| G-17 | P1 | DEF | DeepSeek missing from LLM registry | `registry.py` | PKG-E | R2 |
| G-18 | P1 | DEF | `budget` billed as Standard | `cost_controller.py:111` | PKG-E | R2 |
| G-19 | P2 | IU | Session revocation on role change | `auth.py` | PKG-F | R2 |
| G-20 | P2 | IU | Multi-node idempotency contention | `action_safeguards.py` | PKG-11 | R3 |
| G-21 | P1 | MISS | Carrier CPS/channel limiter, fairness (fairness synthetic-proven 7C) | dispatcher | PKG-11 | R3 |
| G-22 | P0 | EXT | Real capacity unproven | — | PKG-13 | R3 |
| G-23 | P2 | MISS | Metrics export, alert delivery (minimal subset → R1) | `core/monitoring.py` | PKG-4b / PKG-11 | R1 / R3 |
| G-24 | P3 | DEFER | WhatsApp/CRM/calendar live | outbox template | — | — |
| G-25 | — | EU | Pre-sales A (4 areas), B, C1–C4, D; Phase 6B–10 reports | see §0.2 | owner locator | — |
| G-26 | P1 | DEF | UI "Budget" ≠ Economy IVR | `agent_training.py`, `agent/page.tsx` | PKG-E | R2 |
| G-27 | P1 | IU | Duration cap unproven live | `worker.py:1241-1248` | PKG-1 | R1 |
| G-28 | P2 | IU | ₹/min tiers are baseline rates | `/costs` | PKG-4b label; invoices | R1 |
| G-29 | P1 | DEF | Redis/MinIO/S3 literal in compose | compose | PKG-4c | R1 rec / R2 |
| G-30 | P2 | DEF | Five compose files, no profiles | compose | PKG-4c | R2 |
| G-31 | P1 | MISS | No resource limits / session admission | compose, worker | PKG-4c | R1 rec / R2 |
| G-32 | P1 | MISS | Installer/preflight/backup/upgrade scripts | `scripts/` | PKG-4b/4c | R1 |
| G-33 | P3 | DEF | Hostinger label | `system_monitor.py` | PKG-4c | R2 |
| G-34 | P2 | MISS | arm64 untested | CI | PKG-13 | R3 |
| G-35 | P2 | MISS | Hold music; routing comparison | none | PKG-12c | R2 |
| G-36 | P2 | MISS | Per-install industry scope | presets only; `organization_configurations` available | PKG-12a | R2 (R1 optional) |
| G-37 | P2 | IU | Owner silence/barge-in settings; compatibility warnings | `silence_manager`, `interruption_filter` | PKG-12d | R2 |
| G-38 | P2 | MISS | Per-stage latency timestamps; listening set | worker events | PKG-12e | R2 |
| G-39 | P1 | MISS | Number pacing, retry caps, health rules | `telephony_number_health`, `rate_limit_per_second` | PKG-12f | R2 |
| G-40 | P0 | VT | Inbound wallet cap live proof | S5-06 local | PKG-4a/PKG-3 | R1 |
| G-41 | P0 | IU | Disabled features reachable via API | no server-side flags | PKG-4a | R1 |
| G-42 | P1 | MISS | Human transfer for LiveKit (Twilio-only tool) | `enums.py:136` | PKG-12b (R1 = callback policy) | R2 |
| G-43 | P2 | IU | Cartesia speed/volume/emotion vs current model | `registry.py:600-620`, `worker.py:2042-2052` | PKG-E | R2 |
| G-44 | P1 | DEF | Stage naming collision (Phase 6B Stage A/B vs safety Stage A–F) | `scratch/phase6b` | resolved in §0.3 | — |
| ISS-01 | P0 | IU | Emergency Stop failure + hydration (7 Nov) | fix in `costs/page.tsx:280`; audit export 9 Nov `emergency_stop_verified` | PKG-2 browser re-check | R1 |
| ISS-02 | P0 | IU | Cross-page KPI desync | `get_business_overview` (business.py:1197); other aggregations not located | PKG-2 browser re-check; fix if reproduced | R1 |
| ISS-03 | P1 | IU | Deep links leads/appointments → conversations | `runId` links present | PKG-2 | R1 |
| ISS-04 | P1 | IU | Human Handover filter mismatch in Leads | no fix located | PKG-2 | R1 |
| ISS-05 | P1 | IU | Appointment counters/toast | toast present; counter math not located | PKG-2 | R1 |
| ISS-06 | P1 | IU | Env-var names in client toast | string no longer present | PKG-2 | R1 |
| ISS-07 | P1 | IU | Test fixtures in dropdowns | UI filter present (`campaigns/new/page.tsx:119`) | PKG-2 | R1 |
| ISS-08 | P2 | IU | Cold navigation 3–5 s | dev-mode compile (confirmed) | PKG-2 on prod build | R1 |
| ISS-09 | P2 | IU | Archived missed call still actionable | no fix located | PKG-2 | R2 |
| ISS-10 | P2 | IU | Raw field keys; double-click; dirty state | slug fixed; dirty-state not located | PKG-2 / PKG-12d | R2 |
| ISS-11 | P2 | IU | Simulation calls polluting CRM | no fix located | PKG-2 | R2 |
| ISS-12 | P2 | IU | Duplicate appointments same timestamp | no fix located; `action_safeguards` claims coverage | PKG-2; fix in PKG-12 if reproduced | R2 |
| ISS-13 | P3 | IU | Click acknowledgment >200 ms | no fix located | PKG-12d | R2 |

Closed with evidence and not reopened: Stage 1B, 2, 3; Phase 2–5 tests; Phase 7 synthetic suites (software path); G-06/G-07.

---

## 12. Detailed phase-by-phase engineering plan

Each package: A objective · B current implementation · C gaps · D solution · E sequence · F owner journey · G edge cases · H safety · I verification · J DONE · K regression/rollback · L approvals. Standing rules for all packages: protected records (Workflow 1 v39/v40 def 97, Workflow 180 v1 def 248, Workflow 128, Campaign 158) are read-only; write-path tests use a disposable workflow created from a template and archived after; pytest fixtures use fresh organizations on `postgres_test`; one branch per package; no secrets read or printed; stop at every PASS/FAIL gate.

### Release 1

#### PKG-1 — LiveKit / Gemini readiness (configuration only)
- **A.** First real AI conversation; unblocks every later gate; proves the duration limit on which Stage A depends.
- **B.** `routes/livekit.py` `/runtime`, `/settings`, `/session/{wf}/{run}`; `services/livekit/client.py` (`livekit_configured`, `create_room_session`, `AccessToken`, room timeouts); compose `worker` service (API image); `worker.py` entrypoint, `_create_session`, `google_realtime`, duration loop; Workflow 180 published v1 inheriting global `google_realtime` (`gemini-3.1-flash-live-preview`, voice Puck, key present).
- **C.** EXT G-01; IU G-02, G-03, G-27. No code gap.
- **D.** Owner sets `LIVEKIT_URL/API_KEY/SECRET` on API and worker environments; API keeps `LIVEKIT_WORKER_MANAGED_BY_API=false`; one dedicated worker container from the same image. LiveKit Cloud Build plan preferred (hard allowance cap). Browser test via the run page → `/livekit/session` → room token.
- **E.** (1) §8.3 pre-conditions confirmed by owner. (2) Env set by owner. (3) Restart `spx-api-preflight` (approval). (4) Start worker; confirm exactly one worker registered in LiveKit. (5) `GET /api/v1/health` → `livekit_enabled:true`; `GET /api/v1/livekit/runtime`. (6) One browser session on Workflow 180 with `max_call_duration_seconds=60`. (7) Inspect run row, `livekit_events`, transcript, cost row. (8) Read-only DB check of protected records.
- **F.** Owner opens the Workflow 180 run page, starts the test call, grants microphone, converses, hears the closing at 60 s; run appears in Conversations with transcript and cost.
- **G.** Invalid key → `startup_failed` visible on run page; worker absent → `/session` 503 `LiveKitConfigurationError`; network drop → LiveKit reconnect window then completion; double worker → duplicate dispatch (prevented by step 4).
- **H.** Only the test run row written; outbound off; §8.3 caps; owner can kill the worker.
- **I.** Real-provider browser call; DB read-only assertions; log inspection.
- **J. PASS:** `livekit_enabled:true`; exactly one worker; `[LiveKit] dispatch received`; run `completed` with transcript; Gemini usage and cost row; `maximum call duration reached` logged and room deleted; no `startup_failed`; protected rows unchanged. **FAIL** on any miss.
- **K.** Unset env; stop worker. No schema or data change.
- **L.** LiveKit choice; env set by owner; API restart; worker start; one paid 60 s call; §8.3 confirmations.

#### PKG-2 — Browser acceptance of lifecycle fixes and ISS re-check, then commit
- **A.** Owner can trust Save/Publish/Restore target the right workflow; Business-area P0/P1 observations from 7 Nov are re-verified; the working tree is committed.
- **B.** 13 uncommitted files; 59 tests; ISS fixes in code (§2.1).
- **C.** IU G-05, ISS-01…13; DEF G-10.
- **D.** Owner-driven click-through on the running UI (dev) plus one pass on a production build (`npm run build && next start`) for ISS-08/13 timing; Claude verifies DB rows and logs; then one commit of the reviewed diff (CRLF/LF warnings noted; no normalisation changes).
- **E.** (1) `/agent?workflow_id=180`: banner "Editing workflow: Virtual Receptionist (#180)" and amber starter notice; (2) switch to `?workflow_id=1`: banner changes, no field leakage either way; (3) create a disposable workflow from a template and one from Blank Canvas (single click each → exactly two new rows); (4) on the disposable workflow: Save Draft → Publish (confirm) → Restore (confirm) → audit events present; (5) `/costs` Emergency Stop activate/resume with reason → audit rows, no console errors (ISS-01); (6) Overview vs Leads vs Appointments counts reconcile (ISS-02) or defect documented; (7) deep links from Leads/Appointments open the right conversation (ISS-03); (8) Human Handover filter (ISS-04); (9) appointment status toast and counters (ISS-05); (10) redial error message has no env-var names and shows a reference id (ISS-06); (11) campaign workflow dropdown hides disposable fixtures (ISS-07); (12) archive missed call state (ISS-09); (13) read-only DB check of protected rows; (14) archive the disposable workflows; (15) `git add` the 13 files and commit with the required attribution line.
- **F.** The clicks above, in the owner's browser, with Claude reading results.
- **G.** Double-click Publish → one definition; reload mid-save → state reset, no partial write; stale bundle → restart UI container (approval).
- **H.** All routes org-scoped; `requireLoadedTarget` blocks writes on load error; protected rows read-only.
- **I.** Browser + DB + console.
- **J. PASS:** every step's expected row/state observed; Workflow 1 v39/v40 and 180 v1 unchanged; ISS items each marked VERIFIED or reopened with reproduction; commit present. **FAIL:** any write lands on a protected workflow, or a P0 ISS reproduces.
- **K.** `git revert`; disposable workflows archived.
- **L.** Owner performs clicks; approval to commit; approval for UI restart if needed.

#### PKG-3 — Authorized Vobiz inbound route and Release-1 CRM/escalation acceptance
- **A.** A real caller reaches the receptionist; the owner sees transcript, lead, appointment request and callback request without touching code.
- **B.** `VobizLiveKitSetupWizard.tsx`; `POST /livekit/vobiz/test-connection`, `/vobiz/setup`, `/sip/dispatch-rules/{workflow_id}`; `services/livekit/vobiz.py`; `telephony_configurations` (Fernet); Vobiz callback routes with v3 signature verification; `status_processor.py`; worker inbound path; `lead_intelligence`, `callback_scheduler`, `action_inbox`, SSE.
- **C.** EXT G-04; IU callback behaviour under real Vobiz, CRM chain on real transcript; decision: direct carrier→LiveKit (default) vs Asterisk hop (only if contract needs queue/hold music).
- **D.** Direct SIP: DID → LiveKit SIP inbound trunk → dispatch rule bound to Workflow 180 → worker. Asterisk not in path for R1. Webhook signing secret configured; unsigned/replayed callbacks must 401.
- **E.** (1) Owner enters Vobiz credentials in the wizard; (2) test-connection; (3) setup creates trunk + rule; (4) configure signing secret; (5) one inbound call from the owner's phone, 60 s cap; (6) verify `WR-LK-IN-*` run, two-way audio, transcript, lead row with score and reason, appointment request `requested`, callback request in inbox, Overview SSE update, cost row, CDR reconcile; (7) hang-up mid-call; (8) second call while limit = 1 → queue message then busy; (9) capture a signed callback (success) and an unsigned replay (401); (10) Vobiz's published callback-signing documentation attached to the evidence.
- **F.** Wizard with three steps ending "Number attached to Virtual Receptionist"; Telephony page shows the number, workflow and last call; the callback request appears in Tasks within seconds and the owner acknowledges it (R1 human-escalation path).
- **G.** Duplicate webhook → idempotent reconcile (Phase 7D); carrier disconnect → terminal disposition; worker crash → fail-closed teardown and later CDR reconcile; unsigned callback → 401 and alert.
- **H.** Credentials encrypted; signature verification fail-closed; wallet cap; outbound off.
- **I.** Real carrier calls; failure injection (kill worker mid-call); signature negative tests against the live route.
- **J. PASS:** `WR-LK-IN-*` completed with transcript, lead, appointment request, callback inbox item, SSE update and cost row; audio both ways; mid-call hang-up terminal within 30 s; queue path proven; signed callback accepted, unsigned rejected; Vobiz documentation on file. **FAIL:** any missing CRM artefact, any accepted unsigned callback, or Asterisk required without decision.
- **K.** Delete trunk/rule; credentials remain encrypted; no schema change.
- **L.** Vobiz account, DID, signing secret; approval for test calls and carrier spend; R1 escalation policy (callback/inbox) approved.

#### PKG-4a — Security hardening (R1-mandatory)
- **A.** The pilot runs with real secrets, HTTPS, Secure cookies, and no reachable disabled features.
- **B.** compose, nginx template, `routes/auth.py`, org config client (`organization_configuration_client.py`), campaign start / Premium publish / `initiate-call` routes, `requirements.txt`.
- **C.** DEF G-08, G-12; IU G-41; VT G-40.
- **D.** Non-default secrets generated by owner; TLS via nginx/Coolify template; `NODE_ENV=production`; DB role without superuser; PROPOSED org-config keys `FEATURE_OUTBOUND`, `FEATURE_PREMIUM`, `FEATURE_IVR_HANDOFF` checked server-side (403/422) regardless of UI; pins added.
- **E.** (1) pins; (2) flags + tests; (3) secrets/env; (4) TLS and cookie flags; (5) DB role; (6) inbound wallet cap live test.
- **F.** Disabled features show "Not included in your plan"; login over HTTPS.
- **G.** Flag missing → treated as disabled (fail-closed); secret rotation documented.
- **H.** No secrets in images or logs.
- **I.** Header/cookie audit; 403/422 tests for each flag; real inbound call blocked by exhausted wallet.
- **J. PASS:** Secure/HttpOnly cookie observed; HTTPS login; every disabled path returns 403/422 via API; wallet cap blocks a real call; image builds reproducibly. **FAIL:** any reachable disabled path.
- **K.** Previous compose retained; defaults unchanged when env unset.
- **L.** Secrets and domain by owner; approval to recreate containers on the pilot host.

#### PKG-4b — Backup, recovery, minimal monitoring (R1-mandatory)
- **A.** The pilot can be restored and someone is told when it breaks.
- **B.** `spx-voice-init` migrations; MinIO; health route; `system_monitor` incidents (audit export shows `incident.postgresql/redis` records exist); SSE.
- **C.** MISS G-09, G-32 (backup part), G-23 minimal; IU G-28 label; proxy SSE check.
- **D.** `backup.sh` (pg_dump + MinIO mirror + `.env` + image tags, encrypted), `restore.sh`, one restore drill into an empty host; external uptime check; alerts (worker down, API unhealthy, `startup_failed`, wallet exhausted) delivered to the owner's channel; "estimated" label on ₹ figures; offboarding/export procedure documented.
- **E.** scripts → drill → uptime → alerts → labels → docs.
- **F.** Owner sees "Last backup", "Restore tested on", and receives an alert in a test.
- **G.** Restore into mismatched image tag refused; alert channel down → fallback email.
- **H.** Backups encrypted; no secrets in alert text.
- **I.** Restore drill; alert fire test by stopping the worker.
- **J. PASS:** restored host serves Workflow 1 v39/v40 and a recording; alert received within 5 min of worker stop; SSE works through the proxy. **FAIL:** restore incomplete or no alert.
- **K.** No app code change beyond labels.
- **L.** Alert channel and backup storage by owner.

#### PKG-4c — Hosting portability (R1-recommended, R2-mandatory)
- **A.** Same codebase installs on any compatible VPS with the right profile.
- **B.** compose variants, `public_url.py`, `system_monitor.py`, org config limits.
- **C.** DEF G-29, G-30, G-33; MISS G-31, G-32 (installer part).
- **D.** Base + overlays; env-ised endpoints; resource limits; `MAX_AI_SESSIONS`; `install.sh` preflight; `upgrade.sh`; generic label.
- **E.** env-ise (no behaviour change with defaults) → overlays → limits → session admission → scripts → label.
- **F.** Owner sees a Server card with profile and headroom; installer refuses underprovisioned hosts with a plain message.
- **G.** OOM → new calls refused, existing survive; external Redis unreachable → fail-closed as today.
- **H.** Isolation unchanged.
- **I.** compose lint; fresh install on a non-Hostinger VPS; OOM injection.
- **J. PASS:** install with external Redis/S3 passes health; refusal not crash above limit. **FAIL:** any default behaviour change on the existing stack.
- **K.** Old compose kept.
- **L.** VPS access.

### Release 2

#### PKG-F0 — Regression baseline triage (no code)
Classify each of the 64 failures / 1 error (1 Nov run) as fixed / environment / obsolete / real; produce the list; no fixes. **PASS:** every item classified. Prerequisite for PKG-C.

#### PKG-C — Stage C: immutable stamping at admission; ARI reads stamped config
- **A/B/C/D.** §5.3; reuse dispatcher batch lock and lease path; queued-run model (`models.py:707`); `ari_manager`. Gap G-15.
- **E.** (1) decide storage: queued-run columns (`workflow_definition_id`, `product_mode`; nullable; migration + backfill from campaign published definition) **or** stamped values in the run's metadata JSON if the model allows (no migration) — decided in-package with owner approval; (2) stamp in the admission transaction; (3) `initiate-call` and worker consume the stamp; (4) ARI reads pinned config, falling back to live only for legacy rows with a warning; (5) tests.
- **F.** Campaign page shows "Calls in progress use version vN"; publish shows "applies after Resume".
- **G.** Definition archived after stamping → dispatch refuses with reason; mixed versions after resume handled per row.
- **H.** Stamp immutable; audit on pointer change.
- **I.** Unit (publish during queue; ARI pinned vs live); migration tested on a DB copy; 2-process admission race.
- **J. PASS:** after publish, stamped rows unchanged; ARI serves pinned IVR config; exactly one stamp per row under race. **FAIL:** any row re-stamped.
- **K.** Nullable column + legacy fallback; recovery = restore.
- **L.** Migration approval if columns chosen.

#### PKG-A — Stage A: durable atomic spending reservations
- §5.4. **B.** `cost_controller.check_financial_admission`, `BudgetConfig`, `reconcile_workflow_run_cdr`, `ActionIdempotencyRecordModel`, `/business/costs`, Costs page. **C.** G-13; depends on G-27 proven and PKG-C.
- **E.** (1) migration `budget_accounts`, `budget_reservations` (PROPOSED); (2) reservation inside the admission transaction replacing dispatcher:290-317, exception → reject; (3) settle in CDR reconcile; (4) ARQ sweeper; (5) Costs page shows reserved vs spent; (6) SSE alerts; (7) flag `BUDGET_RESERVATIONS_ENABLED` default off = reject all outbound.
- **G.** Two dispatchers serialised by `FOR UPDATE`; duplicate CDR idempotent; CDR never arrives → held until terminal, then released; worker ignores duration → detected by the termination test.
- **J. PASS:** no account → every dispatch rejected; cap = 2 × exposure → third rejected; crash between reserve and dial → released after terminal; no double settlement; a run exceeding `max_duration` is terminated by the worker (live). **FAIL:** any admitted dispatch without reservation.
- **K.** Flag; migration recovery = restore.
- **L.** Migration approval; rates confirmed baseline (G-28).

#### PKG-E — Stage E: Premium registry; Budget = Economy IVR; pricing branch
- **B.** `registry.py` `@register_llm` pattern; `worker.py:1986-2001` DeepSeek branch (OpenAI-compatible `base_url`); `resolve_product_mode_overrides`; `cost_controller.py:111`; Voice & Mode tab. **C.** G-17, G-26, G-18, G-43.
- **E.** (1) `DeepSeekLLMConfiguration` (provider `deepseek`, default `deepseek-chat`, `base_url`) + test that `resolve_effective_config` builds `llm`; (2) UI Budget → `economy_ivr` per §4.3, `budget` read alias; (3) `cost_controller` `budget` → economy rate + tests; (4) Cartesia controls kept only if documented for the chosen model, else disabled with reason; (5) one real Premium call; (6) one real IVR call.
- **F.** Premium shows "Needs Deepgram, DeepSeek, Cartesia keys" until present; Budget opens the IVR editor.
- **G.** Missing key → 422 at publish; provider 5xx → fail-closed, callback offered; `allow_engine_fallback` stays false.
- **J. PASS:** Premium run with three provider usage rows; Budget publish yields `economy_ivr` definition, worker refuses it, ARI serves it, cost row at economy rate. **FAIL:** any silent substitution.
- **K.** Revert; historical `budget` rows readable.
- **L.** Owner label decision; three provider keys; paid test calls.

#### PKG-B — Stage B: Pause → barrier → change → validate → publish → explicit Resume
- §5.3. **B.** `campaign_tasks.py`, dispatcher re-check (T5-07), SSE publisher (T5-15), leases, PKG-C stamps, PKG-A reservations. **C.** G-14.
- **E.** (1) `barrier_pending` + dispatcher skip; (2) reconciler to `paused`; (3) 409 guard on mode change; (4) Resume; (5) UI states; (6) failure injection.
- **F.** Pause → "Finishing calls in progress…" → "Paused" → Change plan (readiness, cost delta) → Publish → "Resume with new plan".
- **G.** Pause during batch → batch completes, no new admissions; worker crash → `processing` rows reconciled via lease/CDR before `paused`; two staff click Resume → idempotent.
- **J. PASS:** 0 lost / 0 duplicate contacts across 100 queued rows with pause injected mid-batch and one worker killed; every dispatched run's stamp equals the pointer at its admission. **FAIL:** any row dispatched under a pointer that changed after its admission.
- **K.** Flag-based; no enum change.
- **L.** none beyond PKG-A/C.

#### PKG-D — Stage D: authorized IVR→AI handoff
§5.5. **B.** `economy_ivr_controller`, `ari_manager` originate (verified locally), LiveKit SIP trunk, cost tiers. **C.** G-16. **E.** flag → DTMF option → originate AI leg → child run + parent disposition → split billing → audit. **J. PASS:** one real IVR call escalates; two cost rows; parent/child linked; AI-leg failure returns to IVR with callback offer. **L.** Owner policy; carrier trunk to Asterisk.

#### PKG-12 — Owner voice and experience (sub-packages, separately approvable)
- **12a Industry scoping.** Store `BUSINESS_INDUSTRY` in `organization_configurations` (client exists; **no migration**); filter presets, templates, qualification fields, FAQ samples, report columns; superadmin unlock with audit; industry change shows impact preview and never alters published definitions or leads. **PASS:** real-estate install shows no clinic items in any of the five areas; API still enforces org scoping.
- **12b Human transfer.** LiveKit SIP transfer from the worker to a staff number (PROPOSED; `TRANSFER_CALL` is Twilio-only). Staff no-answer → return to agent with callback offer. **PASS:** real transfer; identity preserved on the run; cost attributed.
- **12c Hold music / queue announcements.** Decision after PKG-3: worker-played owner audio from MinIO for transfer waits (consumes a slot) or Asterisk MoH if the Asterisk hop is chosen for queueing. Validate type/size, licence attestation, tenant-private storage. **PASS:** queued caller hears owner audio; stops within 1 s on admission or hang-up.
- **12d Voice settings and compatibility truth.** Regroup Agent Studio into Voice / Conversation / Functions / Transcripts / Advanced; bind each control to a worker parameter (`min_interruption_duration`, `min_interruption_words`, `backchannel_filter_enabled`, silence thresholds, `max_call_duration_seconds`, provider timeout); unsupported controls disabled with reason; dirty-state tracking (ISS-10); click acknowledgment (ISS-13); preview requires paid-call consent. **PASS:** each setting round-trips UI → `workflow_configurations` → worker log.
- **12e Latency timestamps and listening set.** Per-turn timestamps in `livekit_events` (speech-end, first token, first audio, stop-on-interrupt); 30-utterance set per language scored by a human and stored on the run. **PASS:** median/p95 per model version recorded; Te/Hi/En results recorded.
- **12f Number health and pacing.** Per-number daily attempt cap and pacing (reuse `rate_limit_per_second`, `telephony_number_health`); retries only on definitive outcomes (`max_retries` exists); cooldown + owner alert on short-call/complaint ratios; audit on number changes; no rotation. **PASS:** injected failure ratio triggers cooldown and alert; uncertain outcome never redialled automatically.

#### PKG-F — Stage F: cross-component regression
Resolve PKG-F0 list; pins (if not done in 4a); G-19 revocation test and fix; 2-worker idempotency test (G-20) where feasible; full suite on `postgres_test` with global timeout. **PASS:** suite green or every failure documented with cause; no production behaviour change except the revocation fix.

### Release 3

#### PKG-11 — Capacity controls and observability
§10.3. **PASS:** injected carrier limit respected within 1 CPS; fairness within 10 % across two campaigns; boot after kill reconciles all `processing` rows; alert fires on simulated 503 storm; shutdown drains without dropped audio.

#### PKG-13 — Profile measurement and staged ramp
Measure lite/standard/high on real VPS sizes (RAM, sessions per worker, DB write p95 at 3× peak CPS); arm64 build test; staged real traffic ≥10 % of the per-deployment target under provider-written limits. **PASS:** compatibility matrix filled with measured values only; per-deployment and aggregate figures recorded separately; explicit owner acceptance.

---

## 13. Three commercial release gates

| Release | Mandatory (all PASS) | Explicitly disabled (server-side flags) | External prerequisites |
|---|---|---|---|
| **R1 — first paying inbound Standard pilot** | PKG-1, PKG-2, PKG-3, PKG-4a, PKG-4b; G-27 and G-40 proven live; ISS P0/P1 re-verified; callback/inbox escalation proven; 3 consecutive stable days of real inbound calls; owner completes §3 journey unaided; consent/recording notice; support contact; PKG-4c recommended | outbound campaigns, Premium, IVR/handoff, WhatsApp/CRM, hold music, transfer, imports beyond CSV | LiveKit, Vobiz DID + signing secret, Gemini cap, VPS + domain + TLS, consent/contract |
| **R2 — complete three-mode SaaS** | PKG-F0, C, A, E, B, D, 12a–f, F, 4c; real Budget, Standard, Premium calls; one supervised pause→change→resume; invoices reconciled once; language results recorded; DLT/TRAI review before outbound | high-volume limiter, autoscaling, integrations beyond contract | Deepgram/DeepSeek/Cartesia keys; carrier trunk to Asterisk; regulatory review |
| **R3 — measured high-volume operation** | PKG-11, PKG-13; carrier written CPS/channels; Gemini quota; sessions/replica measured; staged run per deployment; alerting proven | — | carrier/provider allocations; VPS sizes |

GO/NO-GO for each release is decided only on the PASS records in Appendix B.

---

## 14. Acceptance and regression strategy

- **Protected invariants** checked read-only after every package: Workflow 1 (released def 97; v39 published, v40 draft), Workflow 180 (v1, def 248), Workflow 128, Campaign 158 (`created`), workflow count unchanged unless the package creates one, no masked placeholder keys persisted.
- **Fixtures:** disposable workflows from templates (archived afterwards); fresh organizations on `postgres_test`; never the owner's records. ISS-07 showed fixture pollution of production dropdowns — fixtures are archived, not left.
- **Test tiers by risk:** configuration packages → real-provider + DB assertions; data-path packages (C, A, B) → unit + 2-process integration + failure injection; UI packages → `tsc`, production build, browser; capacity → synthetic then staged.
- **Regression gate per package:** the two lifecycle test files (59) + worker suite + Stage 1B security tests; full suite only in PKG-F.
- **Rollback principle:** configuration → revert env; code → flags defaulting to the safe (closed) state; schema → restore or forward fix, never `alembic downgrade` alone.
- **Evidence levels** stated on every PASS: code-level / local test / real-provider / customer acceptance / operational certification.
- **Register discipline:** every PASS/FAIL updates §11 and Appendix B of this file; no new phase systems.

---

## 15. Exact first recommended engineering package

**PKG-1 — LiveKit/Gemini readiness.** Zero code, zero schema, one approved 60-second paid call under provider-side caps. It converts the largest unknown into evidence, proves the duration limit on which Stage A depends, and gates PKG-2/3/4.

Required from the owner before it starts:
1. LiveKit Cloud (Build plan preferred) or self-hosted.
2. `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` set by the owner on API and worker environments (never shown to the engineer).
3. Gemini project monthly spend cap set in AI Studio; amount stated.
4. If LiveKit paid plan: concurrency quota 1.
5. Approval to restart `spx-api-preflight` with the new env and start one worker container.
6. Approval for one Gemini Live browser call, 60-second cap, on Workflow 180.

PASS/FAIL: §12 PKG-1 J. A PASS here is a test-call result, **not** production approval.

---

## Appendix A — Open evidence and unresolved items

- G-25: Pre-sales A (Overview, Build, Observe, Admin videos), B, C1–C4, D; Phase 6B–10 narrative reports; other Antigravity outputs — owner to supply paths.
- ISS-02, 04, 05 (counter math), 09, 11, 12, 13: no code fix located; status unknown until PKG-2.
- G-27, G-40: live proof pending PKG-1 / PKG-3.
- Pilot language(s) beyond English: undecided; may become R1-blocking.
- Asterisk hop vs direct SIP for queues/hold music: decided after PKG-3.
- Plan-label mapping (G-26): owner decision before PKG-E.
- Profiles and all capacity figures: UNMEASURED.
- `spx_voice_audit_export.json`: listed but unreadable at the stated path.

## Appendix B — Package status log

| Date | Package | Status | Evidence level | Notes |
|---|---|---|---|---|
| 2026-11-13 | — | Blueprint created | — | No package started. Awaiting owner approval of PKG-1. |
