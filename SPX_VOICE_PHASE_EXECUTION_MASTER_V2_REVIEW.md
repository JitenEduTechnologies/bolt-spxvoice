# SPX VOICE — RECONCILED PHASE EXECUTION MASTER

**PROPOSED V2 / PLANNING ONLY — NOT AN IMPLEMENTATION AUTHORIZATION**  
**Prepared:** 14 November 2026 (IST)  
**Active working repository on owner's PC:** `D:\spx-voice-mvp`  
**Protected original:** `F:\spxv1\spx-voice` — NEVER ACCESS  
**Purpose:** One readable, phase-by-phase *execution index* across historical work, the **24 already-defined leaf packages**, existing G/ISS issue IDs, and later Codex/Claude work. **This is not a replacement for the approved product specification, engineering blueprint or issue register.**

> **Current state is a reported checkpoint, not live inspection of the Windows repository.** The last shared checkpoint said **G-48: TESTED OFFLINE — CONDITIONAL PASS; corrective follow-up not yet verified; no reviewed commit; PKG-1b not begun**. A coding agent must reconcile against the latest `AGENT_HANDOFF.md`, `git status`, source and tests before acting.

---

## 1. How the project will be managed

**One product and one maintained codebase.** Independent Indian businesses configure separate installations/tenants; Budget = Asterisk prerecorded IVR (no AI before authorized activation), Standard = LiveKit Agents + Gemini Live, Premium = LiveKit Agents + Deepgram STT → DeepSeek LLM → Cartesia TTS. Next.js, FastAPI, PostgreSQL, Redis/ARQ, MinIO, existing SSE/event/CRM/campaign modules remain. **No speculative rewrite, framework migration, new CRM or unnecessary Pipecat expansion.**

**Three commercial gates (from the Master Specification):**

1. **Release 1:** controlled first paying-business **Standard-mode inbound** pilot. Bulk outbound, Premium, and advanced optional features remain **server-side disabled** unless separately accepted.
2. **Release 2:** complete, independently accepted Budget/Standard/Premium inbound/outbound platform with safe mode switching, spending, transfers and business UX.
3. **Release 3:** measured capacity and operational safeguards for **30,000 authorized call attempts per day**, with carrier and per-deployment limits verified; historical synthetic tests are not a certification.

**Four different things, never interchangeable:** historical implementation report; current code; isolated test evidence; real provider/business acceptance.

**Progress vocabulary:** `NOT STARTED`, `IMPLEMENTED—UNVERIFIED`, `TESTED OFFLINE`, `VERIFIED LIVE`, `ACCEPTED`, `BLOCKED EXTERNAL`, `CONFIRMED DEFECT`, `EVIDENCE UNAVAILABLE`, `PROPOSED`. Severity `P0/P1/P2/P3` is a separate field, not a status.

### Canonical existing files (do not overwrite)

- **Product requirements:** `SPX_VOICE_MASTER_ENGINEERING_SPECIFICATION.md` — the original 13 Nov consolidated document found in the user's file library / previously reported at `C:\Users\JITEN\Downloads\SPX_VOICE_MASTER_ENGINEERING_SPECIFICATION.md`. Verify that the coding agent can access its copy; do not assume it is in the working repository.
- **Detailed approved packages:** `D:\spx-voice-mvp\SPX_VOICE_ENGINEERING_BLUEPRINT.md` — includes the release/package plan; may not yet reflect G-45–G-49 and latest splits.
- **Dated factual baseline:** `D:\spx-voice-mvp\SPX_VOICE_CURRENT_STATE_AND_REMAINING_WORK.md` — read-only 14 Nov report; not automatically current.
- **Current task and approvals:** `D:\spx-voice-mvp\AGENT_HANDOFF.md` — operational owner dashboard. **Latest local version was reported updated, but not supplied or independently inspected for this plan**.
- **Existing agent rules:** `D:\spx-voice-mvp\AGENTS.md` (if present); Claude-specific `CLAUDE.md` (if present). Preserve original content.

**Authority ordering:** explicit current owner approvals and original product specification for scope; current code + reliable runtime/test results for observed implementation; blueprint for named work packages and gates; historical status reports as dated leads; this document for a navigable *phase view*. Conflicts must be surfaced rather than silently reconciled.

---

## 2. Historical phase preservation — NEVER reinterpret as new work

Many names were used over time. This section is a **cross-reference, not a completion certificate**.

| Historical label (keep existing name) | Scope supported by documents | Evidence/status at latest dated report | What remains / constraint |
|---|---|---|---|
| Stage 1A/1B | Credentials, private storage, tenant security | Historical focused tests reported | Production secrets, role changes, live host security still unverified |
| Stage 2 | Asterisk prerecorded Economy IVR / DTMF | Local test suite and SIP/RTP loopback historically reported | No carrier-backed IVR proof; call version pinning outstanding |
| Stage 3 | Plans, costs, quotas, retention | Historical tests reported | Actual provider invoices, financial admission, Budget naming |
| Stage 4 / Stage 5A | Owner SaaS/CRM, local voice and telephony | Partial implementation and tests | Provider and owner-browser acceptance |
| **Phase 1–5** (historical labels) | Foundation, intelligence, telephony, campaign safety, SaaS integrations in `PHASE_5_TRACKER.md` | Phase 2–5 suites reported; labels vary across documents | Do not reopen without a demonstrated regression |
| **Phase 6A** | Local voice readiness | Report explicitly says no matching historical artifact located | `EVIDENCE UNAVAILABLE`; do not call it incomplete solely because report missing |
| **Phase 6B Stage A / B** | Vobiz/VoiceLink protocol, provider and Premium dependency offline readiness | Code/tests located; final stage result files not located in status report | Provider acceptance remains outstanding; results to reconcile |
| **Phase 7 (synthetic)** | Campaign scale, ARQ lease/recovery, fairness, hardening | Historical 30,000 mock-call stage reportedly passed; mock telephony/AI | Never present as 30,000 real carrier calls/day |
| **Pre-sales A–D, C1–C4** | UI videos, business pages, logs, billing/audit, alerts, System Health, integrated acceptance | Some suites and 13 business-area issues located; several reports/videos unavailable | Targeted acceptance only; older C1–C4 names are not the same as campaign packages C/A/B |
| **Alternative “Final 9 Phases” / “Post Phase 7 Phases 8–10”** | Older forward-looking planning names | Planning only, not a canonical proof trail | Their requirements are mapped to existing release packages below; **do not use their phase numbers as accepted facts** |

**Decision on numbering:** To avoid confusing historical “Phase 7” or proposed “Phase 8”, the *new, navigational continuation phases* below use **CP-00 through CP-11**. Every CP links to the original `PKG-*` / `G-*` / `ISS-*` identifier. **No new development backlog or duplicative package ID is created.**

---

## 3. Continuation phases — owner view

| Continuation phase | Canonical packages / tracked issues | Outcome | Reported readiness | Primary coding assignment / review |
|---|---|---|---|---|
| **CP-00 — Close G-48 and preserve state** | G-48; G-45/G-27 dependencies; Git/handoff | Fail-closed worker and reliable startup classification; reviewed source checkpoint | **CURRENT: conditional offline pass**, correction pending | DeepSeek (bounded correction) → Claude/Codex **mandatory critical review** |
| **CP-01 — Prove Standard browser voice** | PKG-1b; PKG-1a already accepted offline | One authorized live Gemini/LiveKit browser call with proper termination | Not started / external | DeepSeek code/config support; Claude/Codex review; owner conducts approved paid test |
| **CP-02 — Finish owner UI/CRM acceptance** | PKG-2; G-47; ISS-01…13 | Correct owner click-path, versioning, data synchronization and reliable frontend | Partial | DeepSeek Flash; targeted independent review of safety/security changes |
| **CP-03 — Pilot safety + offline recovery** | PKG-4a-code + PKG-4b-offline; G-40/41/46 + G-08/09 | Fail-closed inbound admission, entitlements, secrets; safe backup restoration on disposable system | Not accepted | DeepSeek for isolated components; **Claude/Codex mandatory** on money/security/restore |
| **CP-04 — Authorized carrier inbound** | PKG-3; G-01..03, G-40 real path | Vobiz DID/SIP/webhooks → real Standard call → CRM/appointment/dashboard/cost | Not started / external | DeepSeek support; Claude/Codex review; owner/carrier live authorization |
| **CP-05 — Secure customer host** | PKG-4a-host + PKG-4b-host | TLS, secrets, monitoring, alerts, host restore, protected deployment | Not started / external | DeepSeek on scripts after approved design; Claude/Codex + human/owner review |
| **CP-06 — First pilot acceptance and portability** | Release 1 gate; PKG-4c | Three stable days, owner can operate, callback escalation, repeatable compatible VPS setup | Not accepted | Strong reviewer, owner/customer acceptance; DeepSeek support for narrowly approved portability fixes |
| **CP-07 — Restore test baseline and safe outbound admission** | PKG-F0 → PKG-C → PKG-A | Historical failure triage; stamped config at admission; atomic budget reservation | Not accepted | DeepSeek only with narrowed tests; **Claude/Codex mandatory** for concurrency and finances |
| **CP-08 — Finish remaining engines and mode barriers** | PKG-E → PKG-B → PKG-D | Premium ready, Budget maps to IVR; safe pause/change/resume; authorized IVR→AI | Not accepted | DeepSeek for registry/UI; strong-model implementation/review for barriers, billing and telephony |
| **CP-09 — Business product and voice refinements** | PKG-12a…12f | Industry isolation, staff escalation, optional hold, settings, languages/latency, number safety | Not accepted | DeepSeek routine changes; Claude/Codex on transfer/compliance/safety; human listening |
| **CP-10 — Three-mode regression and commercial decisions** | PKG-F; Release 2 gate; **PKG-14 PROPOSED only** | Full three-mode business acceptance and commercial scope decisions | Not accepted | Strong reviewer, owner decision; DeepSeek on approved isolated regressions |
| **CP-11 — Real scaling certification** | PKG-11 + PKG-13; Release 3 gate | Certified carrier/provider/VPS capacity and safe staged ramp | Not accepted | Strong-model design + measured human/provider verification; DeepSeek support only |

**Coverage check:** These phases cover **all 24 originally documented leaf packages exactly once**: Release 1 = PKG-1a (historical), 1b, 2, 3, 4a-code, 4a-host, 4b-offline, 4b-host, 4c (**9**). Release 2 = F0, C, A, E, B, D, 12a, 12b, 12c, 12d, 12e, 12f, F (**13**). Release 3 = 11, 13 (**2**). The proposed commercial PKG-14 is **outside** those counts until separately approved. G/ISS are issue IDs, not additional package counts.

---

## 4. Detailed execution and acceptance contract for each continuation phase

### CP-00 — G-48 closure, independent review and recoverable checkpoint

**What exists:** G-45 duration mapping previously tested (87 focused tests in dated report); Codex implemented G-48 worker fail-closed validation and reported **39 dedicated tests**, **153 related tests**. Codex created `AGENT_HANDOFF.md`. A later reviewer flagged **failed-run classification (`COMPLETED` with failure annotations), downstream reporting/billing and pending async task cleanup**. Latest owner update says corrective follow-up is approved/queued, *not verified as finished*.

**Small remaining task:** reconcile newest working tree, fix only proven classification/reporting or cleanup issues (preserve terminal-state conventions), add isolated tests, ensure the guard is before provider-session construction on both browser and inbound SIP, verify no paid startup and no error masking. Verify whether the actual checkpoint contains unintended pasted raw code, but do not silently rewrite it.

**DeepSeek:** permitted as a narrowly supervised implementation agent after separate handoff recovery and owner permission; don't give it a whole-repo refactor. **Claude/Codex review mandatory** for failure semantics, billing and tasks. If strong reviewer unavailable, **hold final acceptance** rather than claim verified.

**Acceptance:** focused G-48 tests pass; reporter queries do not label rejected calls successful/billable; tasks/rooms/jobs terminate as designed in isolated mocks; no G-45 or `economy_ivr` regression; protected records untouched; current Git diff reviewed. **Not** a live-worker acceptance.

**Git:** existing 11 tracked modifications and now 5 untracked (per last shared checkpoint) are not a fixed invariant—recheck today. No automatic `git add -A`, commit, reset, stash, clean, force-push, destructive rollback. After acceptance, propose exact path-specific staging and a separately authorized commit. `AGENT_HANDOFF.md` is a communication record, not a source backup.

**STOP:** no worker start, no paid calls, no Docker/DB/credential changes and no PKG-1b without separate approval.

### CP-01 — Standard-mode LiveKit/Gemini real browser voice (PKG-1b)

**Prerequisite:** CP-00 accepted for offline safety; clear process ownership; a recoverable source checkpoint. Owner confirms LiveKit/Google project permissions and cost exposure. Exact paid-call boundary authorized separately.

**Reported fixture:** disposable Workflow **181**, published definition **250**, version **2**, `max_call_duration_seconds=60`. Verify current DB read-only immediately before live use. **Workflow 180 definition 248 has no duration cap in historical evidence and must NOT be used for a real paid test**; older source recommending WF180 is superseded by this safety finding.

**Work:** check only selected runtime settings; ensure exactly one LiveKit worker registration, validate selected published mode/version, approved 60-second browser call, real 2-way audio, speech interruption, transcript, lead/result persistence, usage and cost line, actual duration termination and room cleanup. Verify single worker ownership; do not apply `docker-compose.dev.yaml` worker-managed setting blindly (can duplicate worker).

**Proof:** provider/session/room IDs and masked logs, timestamps, actual call duration, disconnect reason, CRM/cost/dashboard observations. Provider spend alerts may not be immediate; duration cap alone does not make charges mathematically impossible.

**Agent:** DeepSeek can handle low-risk prep and documentation; Claude/Codex review provider startup, cleanup and spend-control reasoning. Owner controls real provider authorization and human audio listening.

**STOP:** one test only as authorized. Do not enroll first customer from this result.

### CP-02 — Owner-facing application and CRM acceptance (PKG-2)

**Prior work:** 13 Business-area ISS findings; prior workflow lifecycle, explicit workflow ID, draft/publish, duplicate and UI protections implemented/tested in source; G-47 rename persistence + HTTP 500. PKG-2 is partial rather than missing.

**Business journey:** Overview → Business (industry, FAQs, lead fields) → Build (Agent Studio, template, draft, publish) → Observe (transcripts, leads, costs, recordings) → Admin (roles, health, audit). Check source/API/DB interlinking, loading/retries, agent selection/ownership, SSE, dashboard synchronization, appointments, duplicate writes, recording access, error clarity and desktop/mobile usability.

**Tests:** TypeScript check and production build, targeted frontend/backend tests, manually driven browser acceptance and safe disposable workflow creation only with separate DB permission. Do not mutate Workflow 1/180/128 or Campaign 158. Distinguish historical 7 Nov video failures from current behavior; confirm before fixing.

**DeepSeek:** default for React/FastAPI, UI bug fixes, targeted tests; Claude/Codex review where permissions/tenant isolation/publish versioning impacted.

**PASS:** essential owner flows work on authorized test data; all ISS items closed or explicitly deferred with evidence; G-47 classified and resolved if confirmed; page performance measured on production-like build; selected release-critical defects closed.

### CP-03 — Inbound financial safety, feature gates, secrets, backups (PKG-4a-code / 4b-offline)

**P0:** G-40 inbound financial cap is not actually enforced in the live worker path according to last dated report, despite older claims. Specify admission before billable AI, concurrency safety and fail-closed behavior; do not equate a maximum call duration with a monetary cap. G-41 must disable unaccepted outbound/Premium/advanced paths at **API + runtime**, not merely hide UI. G-46 protects LiveKit runtime secret at rest; G-08, G-09 require secure practices and tested restoration.

**Other evidence:** G-12 dependency reproducibility, transactional audit/publish, role/session revocation, idempotency, correct secure secrets, signed callback rules where relevant.

**Proof:** unit + isolated 2-process financial admission/fault-injection tests, unavailable wallet denies AI, tenant negative tests, feature flag 403/422/denied worker behavior, secret non-disclosure, backup and restore drill against **throwaway** services including DB and required object/config storage, alert simulations.

**DeepSeek:** constrained modules/testing only; **Claude/Codex independent review is a release blocker for monetary admission, auth, encryption and restoration design**. No assumption of legal compliance based on code alone.

**STOP:** no live inbound/production if fail-closed financial control absent. No irreversible migrations, real DB touch, service recreation or secret rotation without separate approval.

### CP-04 — Carrier-backed Standard inbound and CRM chain (PKG-3)

**Prereqs:** CP-01 Standard voice works, G-40 safely enforced, approved inbound carrier account/DID/SIP, provider-issued signing verification and India-specific eligibility/consent. Vobiz primary subject to verification, VoiceLink only if separately qualified.

**Business proof:** Caller → authorized DID → Vobiz trunk/webhook (v3 signing verified, fail-closed if absent) → LiveKit selected worker/definition → real audio → transcript → explainable Hot/Warm/Cold lead → callback/appointment `requested` → owner inbox/SSE → costs/call disposition/recordings/dashboard. Callback/manual handoff permitted for pilot where live transfer unverified; do **not** fabricate appointment confirmation or availability.

**Failure proof:** carrier rejects, delayed/duplicate webhook, SIP caller hangup, callback signature failure, spent cap, provider outage, duplicate CRM events, missed callback; preserve correlation IDs and user-facing error.

**DeepSeek:** adapter/config code after read-only protocol verification; **Claude/Codex mandatory** on signed callbacks, SIP routing and cost accuracy; owner approves real external steps and checks provider-issued specifications.

**STOP:** first real call is separately paid-authorized. No real outbound or bulk dial under PKG-3.

### CP-05 — Secure pilot VPS and service operations (PKG-4a-host / 4b-host)

**Work:** HTTPS/TLS, secure cookies, strong secrets, minimal database privileges, role isolation, Docker health/resource limits, correct worker counts, operational alerts, recording retention and backups on selected compatible host, external uptime checks, proxy SSE, incident runbook, test restore, update/rollback strategy. Preserve existing Hostinger deployment and volumes.

**Proof:** live authenticated owner flow over HTTPS, negative auth/cross-org tests, alerts actually delivered, host-level restore rehearsal, worker-down notification, safe rollback/recovery test on disposable copy.

**DeepSeek:** configuration generation and non-destructive checks with explicit filenames. **Claude/Codex + owner** review any deployment, firewall, DNS, recovery or credential action. Consent/privacy/regulatory checks require external/qualified confirmation.

**STOP:** no unsupervised VPS, Docker recreation, migrations, secret rotation or service deletion.

### CP-06 — First paying Standard inbound pilot + portability (Release 1 gate / PKG-4c)

**Pilot:** Selected industry and approved language, callback/support policy, customer consent/recording information, cost caps, owner login/config/publish, authorized inbound calls, transcripts, lead/appointment/callback, dashboard/cost/recording permissions, alert/restore evidence, **three stable days of real pilot operation** and customer/owner ability to operate unaided. Keep bulk outbound, Premium, experimental IVR handoff and unapproved optional integrations **server-side disabled**.

**Portability (PKG-4c):** independently configured customer deployments on compatible VPS/cloud; environment-overridable DB/Redis/MinIO/LiveKit/SIP, no Hostinger-only source assumptions, measured resource profiles, an approved fresh-install proof. Portability is recommended by Release 1 and required before Release 2 broad rollout; it does not need to block one safely managed pilot if the owner explicitly accepts the limitation.

**Proof:** release checklist with real session/provider and business acceptance records; one supported fresh-install profile or documented deferral approved by owner.

**STOP:** never declare broadly commercial-ready from a local browser demo.

### CP-07 — Test baseline + immutable admission + atomic outbound money (PKG-F0 → C → A)

**PKG-F0:** classify historical full-suite **64 fails + 1 error**, by category and reproducibility; do not rerun broad suites without a safe isolated plan. Close old failures only with evidence.

**PKG-C:** assign published workflow definition and execution mode **atomically when a queued run is admitted (`queued → processing`)**. In-progress/ringing/dispatched calls remain pinned. **Do not stamp at enqueue and then rebind**; older blueprint contained that contradiction. Asterisk and LiveKit must read the same authoritative stamped version.

**PKG-A:** within the **same admission transaction**, perform financial reservation against org/campaign/provider rates and budgets; fail closed on ledger/DB outage; idempotent callback/CDR reconciliation; verify that actual duration cap/cleanup remains enforced. No authorization based on stale read + later write.

**Proof:** controlled concurrent workers (at least 2), pause/admission races, worker crash, stale jobs, wallet exhausted, duplicate provider callbacks, unknown carrier result, migration on disposable DB if absolutely required. No bulk paid campaigns.

**Agent:** DeepSeek only for atomic subcomponents with tests; **Claude/Codex strong-model review mandatory**, with independent deterministic concurrency tests; do not treat a second AI opinion as proof without tests.

### CP-08 — Real Budget/Premium and safe mode-switching (PKG-E → B → D)

**PKG-E:** make owner-facing **Budget truly mean no-AI Asterisk IVR** (resolve `budget` alias), ensure correct Economy price rate, register supported DeepSeek LLM in provider configuration and verify Deepgram/DeepSeek/Cartesia dependencies, credentials and an authorized real Premium call. No silent expensive fallback. Provider options only shown when actually compatible.

**PKG-B:** owner sequence **PAUSE → ATOMIC DISPATCH BARRIER → CHANGE → VALIDATE → PUBLISH → EXPLICIT RESUME**; only not-yet-admitted calls use new config on resume. Previously dispatched/ringing/active calls stay on original version. No simultaneous unsafe campaign switch.

**PKG-D:** IVR-only remains zero AI usage. Explicit customer/owner permitted IVR→AI transition requires entitlement, session/cost authorization and separate IVR/AI segments; verify end-to-end with real provider only after approval.

**Proof:** isolated regression, no admission while paused, no wrong-mode bill, no duplicate/lost contacts; one controlled authorized real call in each supported mode; separate cost records; correct user dashboard explanation.

**Agent:** DeepSeek for config registry/mapping/tests; **Claude/Codex for pause barrier and IVR/media billing and review**.

### CP-09 — Industry, people, voice and number experience (PKG-12a–12f)

| Leaf | Deliverable (reuse existing code) | Primary/model and acceptance |
|---|---|---|
| 12a | Industry-specific visible fields, qualification, templates, tenant permissions, preservation of older leads | DeepSeek; role/tenant tests and owner usability |
| 12b | Authorized tested human transfer where actually supported, busy/no-answer/callback fallback | DeepSeek narrow prep; Claude/Codex SIP/transfer review; live transfer proof |
| 12c | Optional lawful owned/licensed hold audio and bounded waiting; only for **connected waiting callers**, never queued outbound contacts | DeepSeek; actual audio/hangup/overlap/billing tests; defer absent need |
| 12d | Voice/style/silence settings roundtrip UI → API → published config → worker; hide unsupported controls | DeepSeek; effective settings proved; costs disclosed |
| 12e | Telugu/Hindi/English and mixed-language listening, latency per stage, barge-in/cancellation, slow speech/noise | DeepSeek test scaffolding; **human listening + provider evidence mandatory** |
| 12f | Authorized DID inventory, provider CPS/channels, cool-down/quarantine, outcome retries, DNC/opt-out/calling windows, complaint thresholds | DeepSeek narrow; Claude/Codex safety/compliance review |

**Intelligence improvement:** versioned scripts/FAQ/objections, analyzing call outcomes and proposing changes **without LLM retraining**, preview/approve/publish/rollback and compare versions; run follow-ups only for eligible/consented contacts. Map to existing Agent Studio/analytics/campaign and these leaf packages. This is product scope, not an additional unapproved phase.

### CP-10 — Full three-mode regression, business readiness and subscriptions decision (PKG-F / Release 2 gate)

**PKG-F:** full compatibility and safe failure matrix on isolated infrastructure: cross-org isolation, data idempotency, provider/session/billing reconciliation, mode switch, transfer, shutdown/timeout, call outcomes, language quality, recording policy, deployment/restore, UI responsiveness, callback ordering. Reproduce only required historical failures, isolate real provider tests and obtain owner approval for every charged action.

**Commercial scope:** Subscription plans, invoicing/tax, onboarding automation, independent per-client VPS provisioning, usage reconciliation and support are mentioned in the Master Specification, but **PKG-14 is only proposed**. Owner must choose what is manual for first customer vs what must be automated for wider sale. Do not add Stripe or another payment stack by assumption. Separate purchased subscription from execution mode and carrier/AI usage.

**Gate:** Budget, Standard and Premium each have real accepted calls; inbound/outbound as sold; campaign/cost integrity; support/consent; complete customer-owner journey. Mark Release 2 accepted only with evidence and explicit owner sign-off.

### CP-11 — Capacity controls and staged certification (PKG-11 / PKG-13 / Release 3 gate)

**PKG-11:** distributed dispatch resource admission, number/channel/CPS ceilings, tenant fairness, retries and DNC, provider budgets, queue age, fail-safe stops, health metrics, crisis recovery.

**PKG-13:** measure resource profiles (RAM/CPU, DB p95, Redis, MinIO, worker sessions), real LiveKit/AI limits, carrier written CPS/channels, actual SIP and media concurrency, error/latency distributions, staged authorized ramp and per-deployment versus aggregate volumes. Historical Phase 7 reportedly processed 30K **mock** dispatch; preserve it as synthetic evidence only.

**Gate:** written provider allocations and real capacity matrix support the requested claim. Never promise 30,000 attempts/day or universal tiny VPS capacity before measured acceptance. Ongoing updates, backup/restore and incident response remain operational responsibilities.

---

## 5. Backlog traceability — all original leaf packages, exactly once

| Release | Original approved leaf package | Continuation phase | Acceptance evidence needed (summary) |
|---|---|---|---|
| R1 | PKG-1a | Historical accepted baseline (CP-01 prerequisite) | 14 Nov offline API/fixture verification; **already accepted offline** |
| R1 | PKG-1b | CP-01 | One authorized real Gemini browser call and 60s end evidence |
| R1 | PKG-2 | CP-02 | Browser/UI and CRM acceptance, ISS issues, safe Git decision |
| R1 | PKG-3 | CP-04 | Vobiz real signed callback/SIP inbound, CRM, cost |
| R1 | PKG-4a-code | CP-03 | Monetary inbound admission, entitlement/security negative tests |
| R1 | PKG-4a-host | CP-05 | TLS/secrets/security host acceptance |
| R1 | PKG-4b-offline | CP-03 | Throwaway restore and offline alerts |
| R1 | PKG-4b-host | CP-05 | External alert delivery/host restore |
| R1 | PKG-4c | CP-06 | Fresh compatible VPS/profile installation |
| R2 | PKG-F0 | CP-07 | Historical regression triage evidence |
| R2 | PKG-C | CP-07 | Atomic admission-time config pinning |
| R2 | PKG-A | CP-07 | Atomic budget reservation + fail-closed tests |
| R2 | PKG-E | CP-08 | Budget/DeepSeek registry and real mode evidence |
| R2 | PKG-B | CP-08 | Pause barrier and version-change race tests |
| R2 | PKG-D | CP-08 | Explicit IVR→AI permission and segmented billing |
| R2 | PKG-12a | CP-09 | Industry scoping and persisted qualification |
| R2 | PKG-12b | CP-09 | Transfer/fallback real acceptance |
| R2 | PKG-12c | CP-09 | Connected hold/queue safety (optional if deferred) |
| R2 | PKG-12d | CP-09 | Model-supported settings roundtrip |
| R2 | PKG-12e | CP-09 | Language/latency/listening set |
| R2 | PKG-12f | CP-09 | Number health and authorized retries |
| R2 | PKG-F | CP-10 | Integrated release regression |
| R3 | PKG-11 | CP-11 | Capacity/governor acceptance |
| R3 | PKG-13 | CP-11 | Real measured provider/media/host capacity |

**Do not add the CP phases to the leaf package count.** G-01…49, ISS-01…13 and historical C1–C4 are distinct issue/workstream references. Their accurate current close/open count requires latest Git/checkpoint reconciliation; the 14 Nov report's 11 P0 / 22 P1 / 21 P2 must not be presented as still current after G-48 code changes.

---

## 6. Model assignment and minimal-spend policy

**Total additional budget:** ₹3,500 across coding API, real voice/provider tests and contingency. **35M tokens is only a speculative cumulative target, NOT prepaid credit or a software-completion guarantee.** No agent is authorized to buy credits.

| Risk class | Permitted default implementation | Review / mandatory evidence | Spending safeguard |
|---|---|---|---|
| Low: docs, UI copy, focused tests, visual fixes | DeepSeek Flash after small verified trial | focused tests, owner-visible result | short new session, narrow file context |
| Moderate: FastAPI, CRM, workflow routes | DeepSeek Flash; Pro only if measured need | code+regression, cross-org tests | review actual API usage and retries |
| High: LiveKit/SIP/worker/room cleanup | DeepSeek Pro if proven; otherwise strongest available Codex/Claude model | independent review + controlled provider test | no auto-start, no provider spending |
| Critical: atomic financial admission, campaign races, tenant security, migrations, real deployments | Prefer strongest available Claude/Codex as implementer; DeepSeek can prepare isolated tests/proposals | independent review + deterministic fault-injection/real acceptance | stop when reviewer unavailable; no automatic changes |

**Do not select a model by an invented “98% success” claim**; benchmark scores are not project success probabilities. Use one active coding agent at a time. Protect provider/source privacy before sending any confidential code to an additional model API. Record measured API token/cost if exposed; mark unknown otherwise. Pause before spending reaches the user's cap.

**Efficient agent steps:** read AGENT_HANDOFF + relevant spec/blueprint sections → `git status` + scoped diff → state one package/issue and permitted files → receive explicit edit/test approval → implement *only that* → run focused isolated tests → inspect diff → update checkpoint → request review/commit/live-step approval. Avoid long prompts, repeated whole repo scans and unapproved full suites.

**Interruption recovery:** If usage ends mid-task, next agent does **read-only** reconciliation of Git, saved source, untracked files, observed processes and uncertain side effects. It must not assume a command succeeded or failed, automatically retry migrations, reset, clean or commit. An idle extension process alone does not mean editing is active. Review/approve before write. Maintain checkpoint after each meaningful verified step.

---

## 7. Permanent protected state and STOP conditions

**Protected:** `F:\spxv1\spx-voice` absolutely off limits. In working tree, Workflow **1** published 97 v39/draft 108 v40; Workflow **180** published 248 v1 (historically unbounded; NOT safe for paid test); Workflow **128** published 177; Campaign **158** (`created`); disposable Workflow **181**, definition **250**, published v2, 60 seconds (historical only; verify before use). Preserve all current DB rows, Redis state, recordings, runtime volume `spx-voice-mvp_api_runtime` and unknown uncommitted changes.

**Never without separate approval:** `git reset --hard`, `git clean -fd`, automatic stash, stage-all, commit, force push; mass formatting; running tests against non-disposable DB; migration/backfill/cleanup; Docker restart/down with volumes; provider key display/rotation; worker launch; paid API/SIP/PSTN calls; publish/archive workflows; customer outreach as if live-ready; VPS or DNS changes.

**Stop immediately on:** unexpected git divergence; unknown running editor/writer; failed tenant or spend-control test; new code reaching a provider before cap validation; wrong-mode calls; unexpected migration; secret exposure; unsupported model claim; customer record mutation; unknown call/charge outcome.

---

## 8. Daily owner dashboard — format for AGENT_HANDOFF.md (not automatically written)

```markdown
## OWNER DASHBOARD — latest verified state
- Phase: CP-XX / existing PKG-ID / issue ID
- Outcome: [one-sentence business value]
- Agent currently editing: [Claude / Codex / DeepSeek / none]
- Status: NOT STARTED / IMPLEMENTED—UNVERIFIED / TESTED OFFLINE / VERIFIED LIVE / ACCEPTED / BLOCKED
- Last source checkpoint: [branch + HEAD + tracked/untracked summary]
- Evidence: [exact command/result, or 'historical' / 'unknown']
- Blocking risks: [maximum 3, with IDs]
- Next safe technical step: [exact one]
- Owner must approve: [permissions / provider spend / commit / next package]
- Release 1: [accepted?]; Release 2: [accepted?]; Release 3: [accepted?]
```

Each coded change must cite `CP → PKG → G/ISS issue → existing requirement → source file → focused test → owner acceptance`. If the mapping is absent, label the work `PROPOSED` and stop for owner scope approval. After a phase ends, do not automatically start the next one.

---

## 9. Single first action for DeepSeek, Claude or Codex — READ ONLY

Use this prompt ONLY when the current coding agent has stopped and no concurrent mutating work is running:

```text
SPX VOICE — READ-ONLY PHASE CONTINUITY CHECK
Workspace: D:\spx-voice-mvp
Protected original: F:\spxv1\spx-voice — NEVER ACCESS.

The owner is adopting a proposed navigational phase index (CP-00..CP-11) which maps to
existing PKG, G and ISS IDs. It does NOT replace the original master requirements,
engineering blueprint, status report or AGENT_HANDOFF.md.

Read the CURRENT AGENT_HANDOFF.md, existing AGENTS.md, relevant master spec and
blueprint sections, and current Git status. Reuse older phase evidence without
repeating comprehensive audits. Do not assume previous agent results remain current.

FIRST: identify actual current task/approval/status. The last shared checkpoint
said G-48 corrective follow-up was pending verification; verify whether that is
still true from current files. Identify exact files/tests and any conflicts.

Return ONLY:
1. Current CP / PKG / G or ISS ID and factual status.
2. Last verified step and evidence; unknowns.
3. Current Git branch/HEAD + modified/untracked preservation.
4. Exact next safe task and its acceptance criteria.
5. DeepSeek vs Claude/Codex review assignment based on task risk.
6. Owner approval needed before any action.

STRICTLY READ-ONLY: no file writes, tests, processes, provider calls, DB queries,
service actions, secrets, purchases, commits, or protected-original access.
STOP after the report.
```

**No agent has been asked by this document to implement anything.** The first post-reconciliation *implementation* approval, when safe, concerns only the current unfinished G-48 corrective action, unless new evidence shows it has already been completed.

---

## 10. Sources, provenance and unresolved evidence

**Directly inspected for this proposed plan:**

1. **Master Engineering Specification**, 13 Nov 2026, file found in the user's Library and inspected in full (590 lines); it is the authoritative product scope.
2. **Current State and Remaining Work**, 14 Nov 2026, retrieved from user's Library (493 lines); dated package/issue and historical phase evidence; predates some Codex changes.
3. **Phase-Wise Engineering Blueprint**, 13 Nov 2026, supplied as `Pasted text(20261114-175601).txt` (373 lines); its early PKG-1 plan used protected WF180 and had admission-time/queue contradictions, corrected by later reviews.
4. **Final Independent Feasibility, Safety & Implementation Review**, supplied as `Pasted text (2)(20261114-175609).txt`; identified correction to admission stamping, disposable test fixture, provider Vobiz signing, pilot safeguards and package split.
5. **Latest remaining-work discussion**, supplied as `Pasted text (3)(1).txt`; summarizes current G-48 follow-up, release packages and pilot dependencies.
6. **Claude-to-Codex Engineering Handoff**, supplied `.md`; precautions, protections and older status, not proof of current working Git.
7. **Latest Codex user-facing checkpoint summary in this conversation**: `AGENT_HANDOFF.md` owner dashboard added; G-48 conditional offline pass and corrective follow-up reported pending. This plan did **not** independently read the latest `D:` local file.

**Unresolved evidence:** current PC Git diff / authoritative latest `AGENT_HANDOFF.md`, final G-48 corrective test result, code contents after later changes, any verified provider credentials/real calls, originally inaccessible Pre-sales video/report artifacts, actual provider pricing and quota, and proof of real 30k/day capacity. These must be reported as unknown, not invented.

**Scope change rule:** no phase name or count silently changes the approved product scope. If a missing requirement is discovered, map it to the original specification and propose the smallest amendment, with owner approval.

---

**NEXT OWNER DECISION:** Approve adopting `CP-00..CP-11` as a *navigation index* to the existing packages—not as new implementation permission. Separately approve each exact scoped coding task, test, Git commit, real call and deployment. The very next technical task is a **read-only current-state reconciliation**, then the narrow G-48 corrective follow-up if still needed.

---

## 11. 14 Nov 2026 safety-and-acceptance reinforcement — proposed, not authorization

This section clarifies **testable acceptance conditions** in the existing approved Master Specification and CP/PKG mapping. It creates **no new package** and does not assert a feature is absent or broken. Only an agent inspecting the CURRENT code and evidence may classify implementation status. It does **not** change historical phase statuses, approved scope, or an already-authorized G-48 task.

### 11.1 Business-number reputation and lawful calling — hard outbound gate (CP-08, CP-09/12f, CP-11)

**Risk:** Business-owned caller ID or DID is restricted or labelled spam during campaigns; blocked or unlawful dialing at scale. The system **cannot guarantee** that a number will never be blocked or flagged.

- Classify every campaign as its genuinely permitted category/purpose and recipient relationship (e.g. promotion, service, transactional), with documented consent basis and carrier approval. Confirm the requirements for automated/AI-generated voice calls, any sender registration/DLT procedures, required number series/caller ID, permitted hours and call class **against the current rules and the specific carrier**. Do not assume generic 10-digit DIDs are valid for promotional dialing.
- Revalidate the latest **Telecom Commercial Communications Customer Preference** amendments shown in TRAI's official index, including the **18 Sep 2026 Third Amendment** and **4 Nov 2026 Fourth Amendment**, and seek qualified local compliance/carrier advice for applicable obligations, before real campaign activation. Sources: https://www.trai.gov.in/release-publication/regulations ; https://www.trai.gov.in/faqcategory/unsolicited-commercial-communicationsucc . Index dates alone are not a legal interpretation.
- Store recipient consent/purpose/source/evidence/timestamps where applicable; honor DNC/DND, opt-outs and complaint escalations; suppress immediately at **dispatch time**, not only CSV import time; preserve a durable suppression ledger across staff, workflows and retries.
- Verify caller-ID ownership, CLI/DID eligibility, incoming callback routing, carrier account KYC/authorization, number lifecycle, CPS/channel allotments, and call-recording/AI disclosure requirements before using real numbers.
- Number-health controller: verified per-number + per-business + per-carrier limits, pacing, cooldown, quarantine, complaint/hangup/failure signals, manual quarantine release with reason, carrier warnings, and automatic *pause* at approved thresholds. No arbitrary frequency, round-robin caller ID, spoofing, or number rotation to evade reputation systems.
- Retrying an ambiguous outcome is not a safe reason to call again. Suppress opt-outs across campaigns; respect limits after crash/restart. Emergency Stop prevents **new admissions** immediately and has an explicit authorized attempt to end already-active calls where required, with auditable exceptions.
- **Required proof before outbound activation:** negative DNC/consent/caller-ID/number-eligibility tests, throttling and emergency stop under concurrency, provider-issued eligibility/limits, a small explicitly authorized real call sample and incident procedure. Close no regulatory gap on code evidence alone.

### 11.2 Full owner UI and workflow acceptance contract (CP-02, CP-06, CP-09, CP-10)

For each applicable page and role (Overview, Business, Build/Agent Studio, Workflow Builder, Campaigns, Observe, Admin, Leads, Conversations, Appointments/Callbacks, Recordings, Costs/Usage, Numbers/Telephony, Settings, Health/Audit, Reports), verify this end-to-end path:

1. Visible user action (Create, Edit, Draft, Publish, Restore, Import, Assign, Pause, Resume, Stop, Book, Transfer, Review, Export) → API auth/validation → durable database/queue/action → event → all dependent views → audit trail.
2. Never show success on a mere HTTP acknowledgement if durable operation failed; show clear **pending / confirmed / failed / stale** states. Appointment cannot be marked confirmed unless an actual authorized booking system or owner confirms it.
3. Verify permission and tenant enforcement on APIs, recordings, exports, routes, WebSocket/SSE data and background jobs; never rely on hiding a button. Test two staff editors, double-click, lost response/retry, tab refresh and publishing conflict.
4. Workflow draft/publish/restore/duplicate/rename/archive/assignment and industry template paths preserve the intended workflow ID and prior published definitions. No fallback to Workflow 1, no edits to protected Workflow 180 or Campaign 158 for testing; use disposable fixtures.
5. Test a nontechnical younger and older owner performing essential journeys unaided; readable labels, onboarding wizard, one obvious next action, role-appropriate defaults, accessible input/focus/contrast, meaningful inline help, confirmation for hazardous actions, mobile/tablet/desktop, and reasonable low-end/slow-network conditions. Age alone is not an accessibility test.
6. Test real-time conversations, leads and appointment status, *including corrected transcripts*, staff override/handoff, external failure/pending states and a business owner returning after a connection interruption. Avoid incorrect sales/medical claims and never fabricate availability or a successful booking.

**Existing Master Specification UI goals, not unconditional guarantees:** visible click acknowledgement around 200 ms normally; p95 warm navigation below 2 seconds; p95 dashboard readiness below 3 seconds on agreed hardware, datasets and production-like builds. Proposed *additional objective*, to approve after measurement: authenticated cross-page event freshness p95 ≤2 seconds after durable commit under a stated workload, measured from server event to browser render. Never say “zero lag”; log actual p50/p95/p99 and acceptable degraded-mode messages.

### 11.3 Realtime state propagation and correctness (CP-02, CP-04, CP-07, CP-10, CP-11)

- Preserve existing SSE + durable event replay design instead of inventing an additional transport. Trace server commit → event publication/sequence → authorized subscription → browser cache/UI invalidation → related-page refresh/reconciliation.
- On disconnect, tab sleep, mobile network change, expired session, server restart, lost/out-of-order/duplicated events and high event volume: replay missed events or fetch an authoritative snapshot, dedupe by event ID, retain monotonic version/sequence semantics where implemented, and never show another business's event.
- Prefer durable DB state as truth; avoid relying solely on optimistic local UI flags. Provide event lag/backlog metrics and staleness indicators. A reconnect must not submit duplicate mutations or reopen a completed call.
- Load-test SSE subscriptions and update fan-out separately from outbound dial/AI media throughput; prevent a large campaign's update volume from freezing the UI or starving the database.
- Acceptance includes two browsers for the same business, two independent businesses, bursty calls, campaign pause/update, appointment change, callback ordering, refresh/reconnect, and eventual consistency/reconciliation after outage; measure latency and correctness.

### 11.4 Model switching, call identity and financial integrity — no partial passes (CP-03, CP-07, CP-08)

- Separate business subscription/entitlement, workflow draft, **published** definition, campaign active pointer, queued request, admitted/ringing/active call snapshot and per-provider billing; no silent Standard/Premium/Budget fallback.
- Only **PAUSE → ATOMIC DISPATCH BARRIER → CHANGE → VALIDATE → PUBLISH → EXPLICIT RESUME** can change a running campaign's future engine. Stamping occurs atomically on queued→processing admission and financial reserve, never retroactively on already admitted calls. Previously ringing/active calls stay pinned until their own completion; switching must not cut them off.
- Inbound calls likewise need immutable validated published snapshot and **enforced** monetary admission. Duration cap is not a spending cap; a stopped worker must not leave a billable orphan session.
- Test concurrent staff publishes, two dispatch workers, uncertain carrier response, budget exhaustion, duplicate/out-of-order webhooks, cancellation, reservation release/reconcile, provider late charges, IVR→AI explicit handoff and distinct segmented cost attribution.
- Report estimated/held/provider/reconciled/customer-billed amounts separately in UI, using audited actual source events and appropriate currency/rounding. No invented provider bill or pretending “call completed” after startup rejection.

### 11.5 Capacity evidence and release thresholds (CP-11; prerequisites in earlier phases)

- **30,000 calls/day is an aggregate authorized-attempt target** unless the owner explicitly contracts a per-tenant target. Capacity plan uses actual allowed calling window, answer rate, ring timeout, average and worst-case call duration, attempts/sec, ringing/answered/AI-active concurrency, staff transfers, provider quotas, number CPS, per-VPS RAM/CPU/network, LiveKit worker reservations, DB connection/lock contention, Redis persistence/queues, event fan-out and MinIO/recording bandwidth.
- Prove **four independently measured quantities:** enqueue/dispatch jobs, carrier-accepted dial attempts, concurrent carrier media legs, and concurrent real AI conversations. The historical 30K synthetic dispatch exercise proves only its stated synthetic scope.
- Every capacity tier has measured maximum safe rates, concurrency headroom, fair scheduling, queuing, overlimit/backpressure action, bounded cost exposure, alert/automatic stop thresholds, and sustained test duration. Provider quotas/written carrier allowances and real authorized staged ramp are mandatory before scaling claims.
- Test forced carrier errors, worker crash, 429s, Redis/Postgres outages, late callbacks, slow event consumers and recovery from paused/drained campaigns. Restore and reconciliation are part of throughput certification, not optional extras.
- Never promise one small VPS or one business DID can carry 30K calls; certify per-deployment/aggregate separately. Avoid generating high-volume traffic merely to verify code.

### 11.6 Cross-cutting business readiness and security (CP-02 through CP-11)

- Product truthfulness: selected business industry, scoped qualification fields, owner-approved script changes/versioning and rollback; call-outcome learning suggests revisions without automatic model retraining or uncontrolled prompt replacement. Compare old/new campaigns only among contacts eligible for follow-up.
- System Health & Audit Center with correlated identifiers from campaign → call leg → provider → transcript → lead/appointment → financial ledger → UI notification; staff support diagnostics with redaction and least privilege.
- Every P0/P1 acceptance covers RBAC/tenant isolation, server-side plan feature flags, protected secrets, encrypted transport, recording access/retention/deletion, session revocation, audit durability, tamper-resistant callbacks, consumer consent and appropriate sector restrictions.
- Operational acceptance: monitored workers and queues, configurable backups, verified **restore on disposable infrastructure**, alert delivery, secure release/rollback, disaster and incident runbooks, emergency stop and actual support ownership. Failed external actions must not be represented as successful.
- Commercial path: clear onboarding for independent businesses (potentially one VPS per paying client), lawful number provisioning by approved provider, customer subscription/invoice/tax policy **only after owner decision**, per-client cost/margin reconciliation, support, offboarding/export/deletion. Do not add payments or new vendor stacks without approval.
- Provider dependency awareness: carrier/SIP eligibility and costs; LiveKit/Google quota and version; Deepgram/DeepSeek/Cartesia language and price; cloud/provider outage recovery. External dependencies cannot be declared COMPLETE from UI or local tests.

### 11.7 Required source-to-plan reconciliation report BEFORE any coding

For each CP phase and its original PKG/G/ISS entries, the incoming agent must return **one evidence table**:

`CP | PKG | G/ISS | master requirement | current module/route | status with source+test evidence | actual missing or defective behavior | risk to protected code/data/calls | smallest correction or test | test/live evidence needed | model/reviewer | owner approval required`

Use these mutually distinct statuses: `VERIFIED LIVE`, `TESTED OFFLINE`, `IMPLEMENTED—UNVERIFIED`, `CONFIRMED DEFECT`, `MISSING`, `BLOCKED EXTERNAL`, `EVIDENCE UNAVAILABLE`, `DEFERRED`, `PROPOSED`. Not previously witnessed ≠ missing; mere existence ≠ working. No speculative P0 or invented critical requirement. Reuse historic proofs; reopen only when actual code contradicts old evidence.

**Release gates (proposed reinforcement):**

- **First authorized browser call:** CP-00 correction reviewed, spending controls and safe worker setup, explicit owner approval, disposable fixture only, transcript/cleanup/usage evidence.
- **First paying inbound pilot:** CP-01..CP-06 required acceptance, appropriate verified inward calling eligibility, financial guard, CRM, owner UX, monitoring, restoration and compliant notice; full outbound remains disabled.
- **First outbound campaign:** CP-07/CP-08/CP-09-number-safety specific protections, carrier/recipient lawful consent, authorization, DNC/opt-out, rate limits and small controlled test all PASS **before any bulk campaign**. Sequential CP numbering does NOT authorize campaign activation ahead of prerequisites.
- **Three-mode release:** each engine real-call accepted, immutable pin/switch and segmented billing, safety and regression, client understanding and owner sign-off.
- **30K/day claim:** provider written CPS/channel quota, real sustained staged measurements, authenticated owner visibility, concurrency, safe recovery, operational incident response and explicit approval. Synthetic proof not enough.

**Change discipline:** This section is a safety/acceptance addendum, not permission to alter current source, mutate providers, run calls, or use a different architecture. First perform read-only reconciliation and propose the smallest owner-approved amendments to existing sections/packages only. Preserve all Git changes and unknown running operations. Require stronger-model independent review for financial, concurrent, SIP, data isolation and deployment changes, and human carrier/legal/UX validation where applicable. Do not execute any phase automatically.
