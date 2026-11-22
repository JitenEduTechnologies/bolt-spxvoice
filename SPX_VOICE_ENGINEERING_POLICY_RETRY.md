# SPX VOICE — PERMANENT ENGINEERING, TESTING & PRODUCTION RELEASE POLICY

> **Project:** SPX Voice — multi-tenant AI calling SaaS  
> **Document type:** Permanent engineering governance and phase-execution policy  
> **Version:** 1.0 — proposed for owner approval  
> **Prepared:** 2026-11-16  
> **Intended project location:** `D:\spx-voice-mvp\SPX_VOICE_PERMANENT_ENGINEERING_POLICY.md`  
> **Active development workspace:** `D:\spx-voice-mvp`  
> **Protected original:** `F:\spxv1\spx-voice` — **DO NOT ACCESS, SCAN, READ, WRITE OR MODIFY**  
> **Handoff:** `AGENT_HANDOFF.md`  
> **Phase checkpoints:** `docs/phase-checkpoints/`  
> **Release evidence:** `C:\Users\JITEN\spx-voice-releases\`

**Status and authority.** This is a *proposed* standing policy until the owner approves its placement and adoption. Reading it is **not authorization** to implement, edit files, execute tests with side effects, modify Git history, operate Docker, change databases, use paid telephony, access production, or deploy. An AI agent must never change or weaken this policy without explicit owner approval. This file guides decisions; it does not automatically enforce controls. Use actual permissions, isolation, tests and review to enforce them.

**Mission.** Build a reliable, secure, recoverable, commercially usable AI voice SaaS while preserving accepted functionality, preventing cross-agent conflicts, reducing redundant AI usage, and making every result traceable to the exact source and environment tested. Specifically prevent a recurrence of dashboard breakage caused by stale Docker backend references after container changes, as well as VOICE-01 wrong-source testing and repeated review loops.

**Core contract:** **Inspect → Scope → Authorize → Implement → Self-repair → Verify the exact code → Independently review → Stage and validate → Obtain owner approval → Release → Monitor → Checkpoint.** Not every phase requires a production release; never confuse an offline milestone with a production deployment.

---

## 1. NON-NEGOTIABLE RULES AND PRECEDENCE

1. **Protect the original.** Never open, search, enumerate, copy, diff, back up, or edit `F:\spxv1\spx-voice`, including through automated broad directory scans. If an instruction would require it, stop and request a different source.
2. **Respect explicit authorization.** No agent may infer permission for runtime or production changes merely because a phase is assigned.
3. **Preserve current work.** Never overwrite uncommitted or untracked work, replace newer corrections with older patches, or destroy existing verified functionality.
4. **Use one implementation owner per overlapping component.** No concurrent edits or runtime operations by multiple agents without a documented handoff.
5. **Verify facts on disk and in the intended environment.** Past chat reports and agent summaries are leads, not proof of current state.
6. **Test the candidate that will be reviewed/released.** A passing test importing different code is invalid evidence.
7. **Expose failures and unknowns.** Never manipulate tests, logs, classifications or dashboards to manufacture a PASS.
8. **Separate states.** Offline verified, staging verified, runtime accepted, owner accepted and production deployed are distinct.
9. **Minimize scope and token waste.** Focused checks are preferable to repeated whole-repository audits, except when a demonstrated systemic issue requires expansion.
10. **Never silently change the commercial product contract.** No unapproved engine substitution, tenant crossover, unsafe call behavior, billing changes, or bypassing consent/opt-out safeguards.

**Source-of-truth hierarchy for different questions:**

- **Permissions:** Explicit current owner approval and task boundaries govern what may be done. Conflicting or ambiguous permissions require a stop before the conflicting action.
- **What is actually running/changed:** Current inspected source, Git status, container/service state and observed evidence take priority over old notes.
- **What a phase is meant to deliver:** The current *owner-approved phase master* and accepted change requests; do not infer CP requirements from their numbers.
- **What was previously accepted:** The last approved checkpoint, its immutable release evidence and `AGENT_HANDOFF.md`, reconciled against the current working tree.
- **How to execute safely:** This policy. If a task appears to conflict with a safety requirement, identify the conflict and request a decision rather than silently overriding it.

If the approved master, handoff and actual source disagree, report **which statement conflicts with which evidence**, and do not fabricate a single authoritative version.

---

## 2. AGENT OWNERSHIP AND HANDOFF

| Role | Default responsibility | Authority boundary |
|---|---|---|
| **DeepSeek** | Focused, owner-approved implementation and bounded in-scope self-repair | Does not self-certify final acceptance or operate production without approval |
| **Codex** | Independent code/security/regression review | Does not silently implement its own review findings in the implementation workspace |
| **Claude Code** | Specifically assigned dashboard/browser tests, environment, staging and release engineering | Docker/process/browser/production actions still require the relevant approval |
| **Cline** | Explicitly bounded, non-overlapping tasks | No default write access to another agent's scope |
| **Owner** | Selects scope, resolves collisions, authorizes consequential actions and accepts phases/releases | Technical PASS remains evidence-based, not a substitute for owner approval |

These are **defaults**, not claims of exclusive capabilities. The owner may designate a different agent for a task.

**Before work begins, declare:** phase ID; objective; named implementation owner and reviewer; permitted directories/files; protected paths; Git branch/revision or candidate root; other agents' active scopes; running services or watched bind mounts; safe test boundary; expected outputs; and approval-needed operations.

**Concurrency rule:** Never let two agents write the same file, branch, generated artifact, shared database or runtime configuration simultaneously. Separate working copies/worktrees and non-overlapping test resources are preferred. A new agent must inspect `git status` and active ownership rather than assuming prior changes are committed or deployed.

**Handoff rule:** Record the exact last accepted source identity, open changes, unverified claims, environment state, test provenance, unresolved blocker and next authorized task. An agent taking over must acknowledge that handoff *before* writing. Never kill or repurpose another agent's processes simply to remove apparent conflicts.

**Browser automation:** Preserve existing Playwright/Selenium/browser automation and its evidence. For unexpected repeated Chrome/Voice Console windows, first identify the verified launching process and distinguish intended test infrastructure from unwanted auto-launch behavior. Do not delete tests, stop supporting services or kill browser drivers without the appropriate approval.

---

## 3. READ-ONLY PRE-FLIGHT — BEFORE ANY EDIT

Perform only the minimum relevant inspection:

- Read this policy, the **latest relevant** `AGENT_HANDOFF.md` section, approved phase master and latest applicable checkpoint; identify the exact owner-approved task.
- Inspect current Git branch, revision, staged/unstaged/untracked changes, existing patch application state and any file collisions. Do **not** run destructive Git commands.
- Inspect the affected code, package/lockfile versions, actual entry points, relevant existing tests and prior trusted evidence. Avoid rereading unchanged files.
- Determine whether running services watch source mounts (`uvicorn --reload`, Next.js dev mode, file watchers, hot reload). An innocent file write can trigger a restart or active call effects.
- For tasks touching services, inspect only the relevant Compose services, networks, mounts, ports, image identifiers, health checks, environment variable **names** and dependent clients. **Never print secrets or full connection strings.**
- Check for active tests, sessions, campaigns, browser automation, background jobs and other agents that could be affected.
- Define candidate root, affected files, approval boundary, rollback approach and concrete tests **before** implementation.

**Pre-flight output (short):** `PHASE | CURRENT SOURCE | CLEAN/DIRTY | OWNER | ALLOWED SCOPE | RUNNING/WATCHED STATE | EXISTING EVIDENCE | BLOCKERS | APPROVALS REQUIRED`.

If a pre-flight check is impossible, mark it **UNKNOWN**, not PASS. If the workspace or current owner cannot be verified, do not perform conflicting writes. No permission is implied to access the protected original.

---

## 4. AUTHORIZATION MATRIX — RESOLVE AMBIGUITY ONCE

| Operation | Allowed from policy-reading alone? | When allowed |
|---|---|---|
| Read-only inspection inside approved workspace and non-sensitive evidence | **Yes**, within authorized access | Narrowly scoped; no protected-original access |
| Write a proposed plan/prompt in the conversation | **Yes** | Label unverified assumptions |
| Modify source in an **isolated candidate** | **No** | After explicit approval of phase, implementation owner and scope; verify isolation |
| Perform bounded in-scope repairs and safe offline tests | **No** | Included in an approved implementation task **only** when they do not mutate shared services/data, consume paid resources or trigger watched-runtime effects |
| Write to active watched mounts or shared workspaces | **No** | Explicit approval after identifying reload and conflict risks |
| Edit/commit phase checkpoint, handoff or standing policy | **No** | Explicit documentation-only checkpoint approval, designated writer and collision check; separate approval for policy revisions |
| Stage, commit, merge, revert, reset, clean, force-push, stash | **No** | Explicit Git operation approval and preservation/rollback plan; destructive operations remain exceptional |
| Restart/create/remove containers, change ports, networks, environment, images, credentials or services | **No** | Explicit owner approval for exact operation and impact |
| Modify databases, run migrations, touch customer data, backups/restores | **No** | Explicit owner approval, validated backup and migration/rollback plan |
| Run real calls, acquire DIDs, spend provider credits, touch live campaigns | **No** | Explicit owner approval, compliance/cost limits and test scope |
| Deploy, cut over, change production config or rollback production | **No** | Explicit release-specific owner approval and gates below |

**Once the owner approves a focused implementation**, its designated owner should finish that approved work—including up to three bounded **in-scope** repair cycles and safe isolated regression tests—without repeatedly asking permission. Approval **does not** extend to different files, architecture, production effects, paid services, migrations, cross-agent conflicts or irreversible actions. Stop and report those boundaries.

**Default when a phase ID is provided without explicit execution authority:** PLAN-ONLY. Inspect safely and return one tailored, copy-paste-ready implementation prompt. Do not modify project files.

---

## 5. PHASE SCOPE AND ACCEPTANCE DEFINITIONS

**Known VOICE workstream (descriptive, subject to the approved phase master):**

| Phase | Intended scope | Non-negotiable distinction |
|---|---|---|
| **VOICE-01** | Opening behavior, interruption/lifecycle ownership, shutdown, finalization and usage | Prior offline test/build PASS does **not** equal live runtime or owner acceptance |
| **VOICE-02** | Intelligent listening, silence and end-of-turn decisions, response latency, conversational pauses, interruption recovery | Reuse existing state/silence/interruption modules before adding replacements |
| **VOICE-03** | Natural voice behavior, supported language/voice configuration and Telugu–Hindi–English code-switching | Provider capabilities must be verified before claiming support |
| **VOICE-P** | Premium streaming STT → DeepSeek → Cartesia | Costs, latency, failure recovery and voice behavior must be actually measured |
| **Cross-engine** | Budget, Standard and Premium integration consistency | Never conflate an engine's own tests with cross-engine acceptance |
| **CP-01…CP-11** | Defined **only** by the existing owner-approved CP master | Do not invent CP-02, CP-03 or CP-05 requirements from names |

Always distinguish **already implemented**, **verified**, **defective**, **missing**, **deferred**, and **unknown**. In-scope changes should be the smallest maintainable correction to demonstrated gaps. For a regression in an accepted phase: classify as **critical safety regression**, **noncritical defect**, or **future enhancement**; block unsafe continuation when necessary, but never restart the entire phase without evidence.

**One phase's acceptance contract must state:** behavior/invariants; entry prerequisites; allowed components; forbidden scope; dependencies; deterministic tests; relevant failure paths; whether real audio/browser/staging is required; exact evidence needed; review owner; and whether deployment is deliberately **out of scope**.

---

## 6. IMPLEMENTATION AND BOUNDED SELF-REPAIR

1. **Understand:** Map the actual call path, source and existing contracts from relevant files; do not design from chat recollections alone.
2. **Plan:** State minimal file/function changes, public API/data-contract impact, backward compatibility, test matrix and risks. No imaginary file paths/functions.
3. **Implement:** One assigned agent, one isolated candidate where feasible, narrowly scoped diffs; preserve tests and infrastructure.
4. **Self-repair:** On a failing in-scope test, retain logs, reproduce, identify root cause, correct once, rerun affected tests, and perform relevant regression checks.
5. **Bound:** Up to **three root-cause-oriented correction cycles** under the same approval. If still failing, blocked by uncertainty, or requiring scope expansion, stop and provide evidence plus a proposed next action. Do not endlessly re-prompt or randomly change configurations.
6. **Review:** Send one final candidate artifact/diff and meaningful test provenance to an independent reviewer.
7. **Close:** Only after required evidence and owner acceptance; a release is a separate approval gate.

**Never:** delete, suppress, skip, mock away, `@ts-ignore`, broadly catch-and-ignore, or weaken meaningful failures to obtain green checks. A defective test may be corrected **only with a demonstrated rationale**, preserved behavioral coverage and independent review. Do not replace working architecture or add dependencies without necessity.

---

## 7. CANDIDATE PROVENANCE — PROVE YOU TESTED THE RIGHT CODE

A **PASS** is not trustworthy unless it is tied to the implementation that will be reviewed or released.

**For every isolated candidate:**

- Identify **candidate directory**, Git revision/patch ID, relevant changed-file hashes and dependency lockfile state.
- Identify actual Python interpreter/virtual environment, package versions and `PYTHONPATH`; verify imports for **application and test modules** using resolved `__file__` paths or equivalent. Ensure none unexpectedly resolves to the live repo or a stale installed package.
- Verify the selected test runner, collection root, import isolation and actual tests executed; record command, counts, skipped/xfailed tests, errors and results.
- For TypeScript/Next.js, verify the exact workspace, resolved dependency versions, generated/built route output and relevant type/build results. Build success is not browser success.
- For Docker, verify built **image identity/digest**, configuration, running image, Compose project/network and entrypoint; a mutable tag or healthy container alone proves neither correct source nor app health.
- After the **final** code change, rerun impacted checks; prior PASS evidence applies only if its source and environment are demonstrably unchanged.
- Bind independent review to the **exact candidate hash/patch**. New modifications invalidate the affected portion of that review and require targeted re-verification.

**Failure to establish provenance ⇒ `BLOCKED: SOURCE NOT VERIFIED`, regardless of exit code.** Never report tests against the isolated candidate if they actually imported the active repository.

---

## 8. ENVIRONMENT, DOCKER AND PROCESS SAFETY

**Known development routing fact:** `http://api:8000` is the **documented internal UI-to-API address for the existing Docker development network**, **not** a universal hostname for host browsers, staging or production. Reverify it against the actual running Compose configuration before relying on it.

**Avoid the known dashboard failure:** Never replace a stable service alias with a temporary preflight/container hostname. Before any **authorized** recreate/remove/reconfigure action:

1. Inventory affected Compose project, services, networks, aliases, DNS, ports, dependent consumers and actual image IDs.
2. Distinguish browser-facing URLs, container-to-container URLs, build-time variables, runtime variables and Next.js server/client exposure.
3. Compare environment variable **names**, origin and required presence without leaking values. Check container mounts, command, health probes, dependencies and persistent volumes.
4. Check active calls, workers, queues, tests and browser drivers; plan drains or safe isolation as needed.
5. Record an exact change plan, downtime expectation/risk, rollback source/config and acceptance checks; obtain explicit owner approval.
6. After the approved change, verify DNS **from the dependent container**, API reachability, authenticated routes, backend persistence, error logs and the affected end-to-end user journey.

Do not regard `docker ps`, an HTTP 200 from an unrelated route, or a successful build as sufficient proof. Do not delete containers/volumes, kill processes, change secrets or modify shared infrastructure as an unsolicited fix.

**Watched mounts:** If `uvicorn --reload`, hot-reload or another watcher may react to a write, treat that write as a possible runtime operation. Use an isolated working copy **outside watched bind mounts** and ensure the test itself does not contact shared provider endpoints, databases or ports without permission.

**Environment inventory:** Maintain versioned, non-secret documentation of services, dependency direction, aliases, exposed ports, images, health checks, ownership, configuration keys, expected URL roles and rollback prerequisites. Never store `.env` values or credentials in evidence.

---

## 9. VOICE AND TELEPHONY PRODUCT INVARIANTS

**Engines and cost boundaries:**

- **Budget:** Prerecorded IVR + DTMF. Do not silently start STT, TTS, LLM or charge AI usage until an authorized activation transition.
- **Standard:** Gemini Live speech-to-speech using a configuration actually supported by the installed SDK/provider.
- **Premium:** Compatible streaming STT → DeepSeek → Cartesia TTS, with validated streaming behavior and costs.
- No covert engine downgrade/upgrade, cross-engine response stitching or hidden expensive fallback. If a configured mode is unavailable, use a documented authorized fail/stop policy.

**Every call must have:** isolated organization/business/campaign/call/session IDs; immutable published configuration snapshot; authorized caller ID and provider; pinned engine/model/voice/language/limits; bounded retries; ordered/deduplicated lifecycle events; transcript/recording permission and access controls; recoverable finalization; idempotent usage/billing; and coherent dashboard state. Handle at-least-once events, duplicate webhooks, late events, disconnect races and worker crashes explicitly where the phase touches them.

**Campaign mode change (mandatory):** Running campaigns **cannot** switch Budget/Standard/Premium in place. `PAUSE/STOP dispatch → verify the dispatch barrier → edit → validate → publish → explicitly RESUME`. Previously dispatched/ringing/active calls keep their original pinned config. Only **not-yet-dispatched** calls after authorized resume use the new published version.

**Voice acceptance, when relevant to the phase:** Natural initial greeting without suppression, caller barge-in, echo cancellation boundaries, end-of-turn/silence detection, interruption recovery, latency, multilingual pronunciation/code-switching, intent/numeric capture, safe transfer/termination, provider error handling, recording and transcript attribution, inbound/outbound parity, concurrency and cleanup. Do not claim a provider/language/telephony feature works merely because configuration accepts its name.

**Compliance and safety:** Verify consent, applicable calling hours/telemarketing regulations, DNC/opt-out propagation, lawful recording/retention, provider-authorized DIDs/caller ID, complaint handling and spending/abuse ceilings before real calls. Do not rotate numbers to evade spam controls. Paid calls and customer contact always need explicit authorization.

---

## 10. SECURITY, DATA AND BILLING INVARIANTS

Apply security checks **where affected**, and complete a broader production gate before onboarding customers:

- Tenant-scoped authorization **at the data/query layer**, not just hidden UI controls; least-privilege RBAC and business-owner vs superuser boundaries.
- Credential and API-key isolation, redaction in logs/test fixtures, safe secret storage and revocation; never upload production secrets into patches, prompts or release bundles.
- Session/authentication integrity, webhook signatures/replay protection, input validation, prompt-injection boundaries for untrusted caller text, rate limiting and abuse protection.
- Minimized data collection; explicit recording and transcript permissions, encrypted storage/transport where applicable, retention/deletion rules and auditable staff access.
- Durable event and workflow state; idempotent mutations, reconciliation after missed events, retries with bounds/backoff, backpressure, per-tenant rate/concurrency controls and emergency stop.
- Provider usage accounting with units, provider/model attribution, price-version assumptions, per-tenant spend caps, duplicate-charge prevention, reconciliation and explainable invoice calculations.
- Database backups with **tested restoration**, migration compatibility and safe rollback limitations. A backup file existing is not proof of recovery readiness.

Classify security, data-loss, cross-tenant, unauthorized paid-call and billing-integrity regressions as **release-blocking** until resolved or explicitly removed from the release scope with a safe containment decision. No agent may grant itself an exception to a safety invariant.

---

## 11. PHASE-APPROPRIATE TESTING AND REAL USER JOURNEYS

Choose the **smallest sufficient** test matrix, plus relevant negative paths and regressions. Never automatically run the entire suite after every minor edit; expand only if shared contracts or failure evidence justify it.

| Type | What it can establish | What it cannot establish alone |
|---|---|---|
| Unit / deterministic mocks | Local logic, boundaries, expected state transitions | Real providers, audio quality, deployed integration |
| Isolated integration | API/database/event contract and recovery under controlled dependencies | Real telecom or production config |
| Type-check/build | Compile and bundle correctness | Authenticated flows, real-time UI or live audio |
| Authenticated browser E2E | Real user navigation, saved settings, errors and readback | Carrier capacity or real call behavior |
| Staging voice/audio test | Observable speech, interruption, persistence and teardown in the tested stack | Production scale or all carriers |
| Approved production smoke/monitoring | Actual release behavior under tightly bounded conditions | Zero defects or guaranteed future capacity |

**Dashboard acceptance, when affected:**

- Login/session expiration and appropriate **superuser, administrator and business-owner** authorization; forbidden actions are blocked by the API, not only the UI.
- BUILD, ADMIN, workflow and agent navigation; loading/skeleton, error, empty, retry, refresh, reconnect and browser back/forward behavior.
- `Save → validate → publish → readback` through the actual frontend, API and database, including the published version used for the next eligible call.
- Workflow settings and business configuration compatibility, field validation, UI/API schema/version agreement and per-tenant data separation.
- Actual network/console evidence for unexpected errors; no hidden 401/403/5xx, uncaught exception, stale API origin or incorrect data. Document **expected** negative-path errors separately.
- Restore browser automation and eliminate unwanted repeated browser launches by **root-cause identification**, not by deleting the test suite.

**Voice acceptance, when affected:** use controlled real-audio, cancellation, turn-taking, barge-in, language, disconnect, failure-retry, concurrent-call and billing-readback tests appropriate to scope. Real telephony, paid usage, recordings and production accounts require prior approval.

**Always:** use isolated fixtures, distinguish mocked from live tests, verify source provenance, protect customer data, and report `PASS`, `FAIL`, `BLOCKED` or `NOT TESTED` with commands/observations. Do not claim all dashboard or voice behaviors were verified if only a subset ran.

---

## 12. INDEPENDENT REVIEW AND DEFECT TRIAGE

**Default:** One focused independent review of the final candidate, relevant tests, source provenance and affected security/billing/regression surfaces. The reviewer should not be the implementer of the candidate being certified.

For each real finding provide **severity**, file/line or reproducible evidence, expected vs observed behavior, impact and the minimal correction. Distinguish:

- **P0 / release blocker:** data loss, unauthorized call/charge, tenant isolation breach, unsafe runtime behavior, serious security defect, wrong-source test evidence or other unacceptable release risk.
- **P1 / acceptance blocker:** required in-scope behavior fails or lacks mandated evidence.
- **P2 / deferred:** valid noncritical issue with an explicitly tracked owner, impact and next phase; not silently called fixed.
- **Unverified claim:** needs reproduction or evidence; not automatically a defect or PASS.

If rejected: repair only affected code/tests, rerun impacted checks, and obtain **targeted re-review**. Reopen broader investigation only if the new evidence indicates a shared/systemic defect. Reviewer approval attaches to the **exact reviewed artifact** and cannot be transplanted to a newer patch.

**STOP immediately** for unexplained tests against the wrong source, accidental live resource access, competing writers, credentials leaked to output, cross-tenant data, unexpected paid calls, unauthorized runtime changes or evidence of destructive operations. Preserve logs and ask for the narrow next authorization.

---

## 13. IMMUTABLE ARTIFACTS, STAGING AND RELEASE GATES

**Standard lifecycle:**

`Source baseline → Approved scoped change → Focused tests → Provenance check → Independent review → Immutable candidate build → Isolated staging → Required browser/voice runtime acceptance → Security/billing/recovery gate → Owner release approval → Controlled deployment → Observability and rollback readiness → Post-release checkpoint`

**Never deploy solely because the code compiles**, or use an old `:3030` preflight snapshot / `:3020` development server to represent the final production candidate. Staging and production must have separately managed secrets, credentials, databases and provider safety controls. Production builds should be immutable/versioned and not use development source auto-reload.

**Pre-release manifest (required if deploying):**

- Exact source Git revision/patch and image/build hashes; dependency locks; artifact provenance and independent reviewer verdict.
- Environment-specific config inventory with no secrets; database/API contract and migration compatibility.
- Safe startup, health/readiness, service dependencies, published-config readback and authenticated customer journeys.
- Active-call and campaign impact, worker drain, pinned-call preservation and downtime/failure boundaries.
- Required monitoring, alerts, cost limits, incident owner, rollback steps and restoration caveats (especially irreversible schema/data migrations).
- Written **release-specific owner authorization** including target environment and risk/impact.

**Deploy only with approved operational steps.** Record when the release begins, how to detect an unhealthy rollout, who may stop/rollback, and which evidence proves it is serving the intended artifact. Verify post-release calls/events/billing only if specifically authorized. Never silently rebuild/replace containers or touch live customers to satisfy a test.

**Rollback is not assumed safe:** verify image/config compatibility, schema backward compatibility, queued event semantics, pinned in-progress calls, and backup/restoration ability. If a rollback cannot safely restore the prior state, report that limitation **before** release.

---

## 14. CAPACITY TARGET AND BUSINESS-GRADE OPERATIONS

**Goal: 30,000 authorized call attempts/day** — a design target, **not** a proven throughput figure or authorization to contact numbers. Do not claim it is met from source review, synthetic unit tests or one successful call.

Capacity acceptance requires measured peak call-attempt rate/CPS, connect rate, simultaneous active calls, worker/CPU/memory/network utilization, provider/carrier-approved throughput, queue depth, disconnect/retry behavior, latency percentiles, cost per minute/call, failure recovery and tenant isolation. Test ramp-up and backpressure under a specifically approved, compliant load plan. Avoid dialing unsolicited numbers to demonstrate scale.

**Before real customer onboarding:** evidence must cover auth/RBAC, data separation, monitoring/alerts, error budgets and incident handling, backups with restoration tests, migrations, deployment/rollback, provider capacity/compliance, billing reconciliation, spending caps, opt-outs, recording access/retention, operational dashboard, recovery after worker/queue/database failures and support/escalation ownership. Missing items remain **not verified** until tested.

No unsupported promises of zero downtime, zero defect rates, perfect latency, unrestricted DID use or guaranteed numbers-per-day throughput.

---

## 15. PHASE STATES, CHECKPOINTS AND EVIDENCE RETENTION

Track phase and release status separately. Use these terms literally:

| State | Meaning |
|---|---|
| `NOT STARTED` | No authorized implementation begun |
| `PLANNED` | Scope/prompt defined; source not changed |
| `IMPLEMENTING` | Approved scoped work underway |
| `OFFLINE VERIFIED` | Candidate-source provenance, required offline checks PASS |
| `REVIEW APPROVED` | Independent reviewer approves exact candidate and checked scope |
| `STAGING VERIFIED` | Required staged integration/runtime acceptance PASS |
| `RUNTIME ACCEPTED` | The explicitly required real user journeys passed in the named environment |
| `OWNER ACCEPTED` | Owner explicitly accepts the required phase criteria |
| `PRODUCTION DEPLOYED` | Separate release authorized and verified in production |
| `BLOCKED` | Required permission, evidence or criterion missing/failed |

**Do not mark `COMPLETE`** without required phase criteria and owner acceptance. A phase may be `OWNER ACCEPTED` for **offline deliverables only**, clearly saying that staging, live calling or deployment remains pending. A completed test suite is not a production claim.

**Three durable evidence layers (only write after approval):**

1. **Handoff:** `D:\spx-voice-mvp\AGENT_HANDOFF.md` — concise latest status, actual Git/runtime state, latest accepted candidate, remaining issues, next owner-approved action. Preserve history and coordinate one writer.
2. **Phase checkpoint:** `D:\spx-voice-mvp\docs\phase-checkpoints\<PHASE-ID>.md` — scope/criteria; changes and rationale; exact affected files; source identity; commands/counts/results/provenance; independent review; runtime evidence; owner decision; known limitations; follow-up and rollback. Check for an existing checkpoint before creating/updating it; do not overwrite older history.
3. **Release evidence:** `C:\Users\JITEN\spx-voice-releases\<PHASE-ID>\<candidate-id>\` — immutable candidate/source patch, checksums, manifest, redacted logs, review verdict and rollback prerequisites. Do not include credentials, customer audio, transcripts or personally identifying data without specific retention authorization.

A checkpoint may cite external evidence by path/hash; do not endlessly duplicate large logs. Record the **actual** Git state; an uncommitted patch is not a commit. Document where the reviewed changes actually live and whether they were applied to the active workspace, built, deployed or merely prepared.

**Checkpoint policy:** After independent review, obtain approval for a **documentation-only** checkpoint. Assign one writer; confirm no concurrent changes; append/update carefully; verify the checkpoint and handoff agree. Do not use documentation approval as permission to alter source, Git history or production. Any standing-policy edits require **separate explicit approval**.

**Agent discovery after adoption:** Placing this file at the repository root does **not** guarantee every coding agent will automatically read it. After the read-only audit and owner approval, inspect existing agent-instruction mechanisms (for example `AGENTS.md`, `CLAUDE.md` or editor-specific rules) and propose the **smallest non-conflicting reference** to this policy. Do not assume those files exist, overwrite their existing rules, or install a global instruction without separate approval. For each new phase, explicitly instruct the assigned agent to read this policy and the relevant phase master.

---

## 16. OWNER TIME, CREDIT AND TOKEN DISCIPLINE

**Enforce beyond prompts:** When separately approved, translate critical rules into practical controls: protected-path exclusions, scoped repository permissions, CI checks for source provenance and targeted regression tests, secret scanning, image/Compose configuration validation, independent review protection and release approval gates. These controls require their **own reviewed implementation**; their existence must never be assumed because this policy says they should exist.

- One focused implementation task per coding prompt. Separate planner, implementer and independent reviewer responsibilities.
- Read relevant changed files and earlier evidence, not the whole repository on every turn.
- Reuse valid test evidence only when source, test configuration and environment are unchanged.
- Run focused tests first; broader tests when the change affects shared contracts or failures justify it.
- Do not introduce new frameworks, broad refactors, repeated architecture documents or speculative repairs by default.
- Combine root-cause diagnosis and approved in-scope self-repair into one bounded implementation cycle.
- Escalate consequential permission boundaries, not routine debugging details. Present the technically recommended option and risk in plain language.
- Keep routine reporting concise, but never omit provenance, security/billing failures or acceptance gaps merely to save tokens.

---

## 17. REQUIRED OPERATING OUTPUTS

### A. On loading this policy without a specific authorized task

Reply **only** with: `Policy read. No changes made. Awaiting an authorized phase/task.` Do not execute work to acknowledge it.

### B. On receiving a phase ID without implementation authorization

Produce **one** phase-specific, copy-paste-ready prompt containing:

1. **Phase/objective:** exact approved intended outcome and exclusions.
2. **Current state:** implemented/verified/missing/defective/unknown, with evidence and source identity.
3. **Authorized implementation proposal:** minimal affected files/functions **only when observed**; otherwise clearly mark `TO VERIFY IN REPO`.
4. **Tests and acceptance:** candidate-provenance proof, functional tests, failure paths, relevant real UI/audio and clear PASS/BLOCKED criteria.
5. **Permissions:** allowed isolated operations versus owner-approval gates; protected original, watched mounts and live services.
6. **Self-repair:** bounded cycles, root-cause logging and stop conditions.
7. **Independent review:** exact patch identity and changed-scope re-review rule.
8. **Evidence/checkpoint:** what gets saved, where, by whom and only after approval.
9. **Next phase:** successor **from the approved master**, not a guessed number.

Do not imply repository access that was not actually available. If no repo access, provide a **conditional** prompt instructing the coding agent to verify current file names and dependencies before changes.

### C. After an authorized implementation/review

Use this short decision report:

```text
PHASE / TASK:
STATUS: PASS | FAIL | BLOCKED | NOT TESTED
CANDIDATE: root; revision/patch; hash; imported source verified? YES/NO
CHANGES: exact files + purpose (brief)
EVIDENCE: tests, counts, relevant browser/audio/runtime results, environment
INDEPENDENT REVIEW: reviewer; artifact hash; verdict; unresolved findings
RUNTIME / DEPLOYMENT: unchanged | staged | owner-approved released; environment
SAFETY: tenant, billing, telephony, secrets, Docker/active-call impact
CHECKPOINT: not approved | saved [location] | pending
NEXT ACTION: one precise recommended step + permissions needed
```

`PASS` must identify **which gate** passed. For mixed results, report the unmet gate and do not summarize the overall phase as PASS.

---

## 18. MANDATORY CHECKLISTS

### Pre-implementation gate

- [ ] Exact phase master, handoff and relevant checkpoint inspected; conflicts reported.
- [ ] Protected original excluded from all reads/scans/writes.
- [ ] Git status, candidate root, relevant source/dependency versions identified.
- [ ] Implementation owner, reviewer, scope and other agents checked.
- [ ] Watched mounts, running calls/tests and Docker dependencies checked if relevant.
- [ ] Data, payment, provider and runtime side effects identified.
- [ ] Specific owner approval obtained for the operation being attempted.

### Candidate acceptance gate

- [ ] Affected implementation and key regressions pass **against the candidate**.
- [ ] Source module/build/image provenance and hashes recorded.
- [ ] Negative/failure/recovery tests relevant to the change addressed.
- [ ] Auth/tenant/billing/voice and dashboard contracts checked **where affected**.
- [ ] Final diff and exact candidate independently reviewed; findings dispositioned.
- [ ] Offline evidence is not mislabeled as live/staging/production acceptance.
- [ ] Unknowns, skips and deferred work explicitly listed.

### Release and checkpoint gate (only when applicable/authorized)

- [ ] Required staging browser/audio and service-boundary tests pass.
- [ ] Security, cost/usage, data, opt-out and service checks pass at the required scope.
- [ ] Release artifact is immutable and matches reviewed candidate.
- [ ] Backup/restoration and **compatible** rollback capabilities verified.
- [ ] Owner has approved this exact target release and impact.
- [ ] Post-release monitoring, smoke checks and incident/rollback responsibility assigned.
- [ ] Documentation-only checkpoint separately authorized and correctly saved.
- [ ] `AGENT_HANDOFF.md`, phase checkpoint and release evidence identify the same state.

---

## APPENDIX A — COPY-PASTE PROMPT: READ-ONLY POLICY ADOPTION AUDIT

Use this **before** asking an agent to install or enforce this policy in the working repository. This is a **review**, not a modification request.

```text
READ-ONLY TASK — SPX VOICE ENGINEERING POLICY ADOPTION AUDIT

Workspace: D:\spx-voice-mvp
Protected original: F:\spxv1\spx-voice — DO NOT ACCESS, READ, SCAN OR MODIFY.
Proposed policy: SPX_VOICE_PERMANENT_ENGINEERING_POLICY.md (only if present in the active workspace).

1. Read the proposed policy and relevant existing AGENT_HANDOFF.md, approved phase master, applicable checkpoint and repository guidance/agent instruction files. Confirm their real paths; do not invent them.
2. Inspect only the necessary read-only Git state, service definitions, active watched mounts and existing test/release evidence. Do not read secrets or start/stop/recreate any service. Do not run tests in this audit unless explicitly authorized.
3. Double-check: conflicting instructions; phase naming/scope; agent ownership; protected path exclusion; approval boundaries; Git and watched-mount safety; Docker api:8000 claim; source/test provenance; voice-mode pinning and campaign pause/publish/resume; independent review; phase-state semantics; checkpoint destinations; release safety; data/security/billing/compliance; token efficiency.
4. Label every assertion VERIFIED, CONFLICTING, UNVERIFIED or NOT APPLICABLE. Cite exact inspected file paths/sections and observed evidence; do not claim production readiness based on the policy.
5. Return: (A) ADOPT / ADOPT WITH CORRECTIONS / DO NOT ADOPT; (B) up to 10 prioritized issues with severity and exact section; (C) a minimal proposed text-only correction for each real issue; (D) what is currently true about VOICE-01 and next-phase readiness; (E) the one recommended next approval.
6. Make NO edits: no file writes, Git operations, dependency installs, browser launches, Docker changes, database access, paid calls, checkpoints, deployment or policy amendments. Do not touch the protected original.

Stop if the policy file cannot be found. Report its absence and the intended placement. Do not claim to have inspected it.
```

## APPENDIX B — PHASE STARTER (AFTER AUDIT AND APPROVAL)

```text
Apply SPX_VOICE_PERMANENT_ENGINEERING_POLICY.md.
Current phase: <EXACT APPROVED PHASE ID>.
Task mode: PLAN ONLY (read-only, no source/runtime changes).
Use the actual approved phase master, latest handoff, relevant checkpoint and current repository evidence. Verify before proposing edits.
Return one scoped implementation prompt with preserved functionality, candidate-source verification, tests, bounded self-repair, independent review, release/permission gates and checkpoint evidence.
Do not touch the protected original. Do not assume prior offline PASS equals runtime or production acceptance.
```

---

**Final standing instruction:** **Build the minimum correct change; test the actual candidate; preserve tenant/call/billing safety; independently verify; release only with approval; record the exact truth; then advance.** Policy acknowledgment alone must never initiate work.
