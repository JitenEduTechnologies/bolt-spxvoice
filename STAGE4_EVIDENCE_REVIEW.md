# SPX Voice — Stage 4 Independent Evidence Review

**Document Path:** `D:\spx-voice-mvp\STAGE4_EVIDENCE_REVIEW.md`  
**Review Date:** November 1, 2026  
**Auditor Role:** Independent Principal QA Auditor, Security Engineer and SaaS Release Engineer  
**Audit Target:** Stage 4 Complete SaaS Implementation in `D:\spx-voice-mvp`  
**Protected Baseline:** `F:\spxv1\spx-voice` (Untouched)  

---

## 1. Executive Verdict & Summary

### **STAGE 4 VERDICT: REQUIRES CORRECTION**

The Stage 4 engineering effort has achieved substantial, genuine milestones:
- **Frontend Production Build:** Next.js 15.5.14 production compilation **PASSED** with 35/35 pages compiled and 0 TypeScript errors. The UI container is live and serving HTTP 200 on port 3020.
- **Backend Test Suite:** 108 total tests **PASSED** in 23.27s (18 new Stage 4 tests, 90 full-suite regressions), covering Asterisk ARI, private MinIO, Fernet credential encryption, pricing tariffs, and domain controllers.
- **Zero Provider Charges / Zero Leaks:** No outbound HTTP requests to paid AI providers occurred; wholesale margins remain strictly isolated from business users.

**Reason for "REQUIRES CORRECTION":**  
The Stage 4 completion report claimed **15 VERIFIED** and **1 LOCAL-INTEGRATION** out of 16 requirements. Under independent QA inspection, this overstates the verified boundary:
1. **Interactive UI Verification:** The Next.js build and HTTP 200 reachability are verified, but full interactive browser testing (clicking dashboard buttons, submitting forms) was not executed via headless browser.
2. **Campaign Scale Verification:** The 20,000-contact ingestion test (`test_s4_11`) and 500-queued-calls concurrency test (`test_s4_12`) verified memory algorithms and governor logic, not 20,000 rows inserted into PostgreSQL or 500 active ARQ worker jobs.
3. **AI Voice Pipelines:** No active WebSocket sessions or audio streaming occurred with Gemini Live (`generativelanguage.googleapis.com`) or Cartesia (`api.cartesia.ai`). This must remain classified as **EXTERNALLY BLOCKED** for live audio.

The register must be reconciled to **5 VERIFIED**, **10 PARTIAL**, **1 EXTERNALLY BLOCKED**, and **0 DEFECTIVE**.

---

## 2. Test Integrity Analysis (`test_stage4_complete_saas.py`)

### 2.1 File Metrics
- **Location:** `api/tests/test_stage4_complete_saas.py`
- **Total `def test_` functions:** **18** (matches reported count)
- **Total `assert` statements:** **66**
- **Git History / Diff Availability:** `D:\spx-voice-mvp` is an unpacked working directory (not an active git repository). Pre-edit intermediate versions were reconstructed directly from the conversation transcript logs (steps 5606–5619).

### 2.2 Forensic Inspection of Test Edits
During test authoring, three initial failures occurred due to schema/signature mismatches. The changes between drafts were:
1. `test_scenario_a_school_fee_verification_and_due_enquiry`:
   - *Initial:* Attempted to call `identity_verifier.get_allowed_verification_factors("schools")` which did not exist on the controller.
   - *Correction:* Called `identity_verifier.evaluate_access(domain="schools", ...)`.
   - *Integrity Evaluation:* **No assertions were weakened.** The test asserts both positive access (`verified is True`) and negative access rejection (`unverified is False` with privacy refusal message).
2. `test_scenario_d_ecommerce_order_lookup_and_support_ticket`:
   - *Initial:* Same non-existent method call.
   - *Correction:* Aligned to `identity_verifier.evaluate_access(domain="ecommerce", ...)`.
   - *Integrity Evaluation:* **Legitimate signature fix.**
3. `test_s4_07_indian_festival_and_relative_callback_parser`:
   - *Initial:* Passed `reference_dt=now`.
   - *Correction:* Passed `reference_now=now` matching `parse_indian_relative_callback_time` signature.
   - *Integrity Evaluation:* **Legitimate argument alignment.** Both `dt_tomorrow.hour == 10` and `dt_diwali.month == 11` assertions remained intact.

---

## 3. Provider SDK & Mock Inspection

### 3.1 Imports in `test_stage4_complete_saas.py`
- `unittest.mock`: `AsyncMock`, `MagicMock`, `patch` are imported on line 13.
- `google-generativeai` / `google.genai`: **NOT REFERENCED**
- `cartesia`: **NOT REFERENCED**
- `deepgram`: **NOT REFERENCED**
- `livekit`: **NOT REFERENCED**
- `pipecat`: **NOT REFERENCED**

### 3.2 Provider Call Evidence
Docker logs and system caches were inspected for outbound HTTPS requests to:
- `generativelanguage.googleapis.com`: **0 calls**
- `api.cartesia.ai`: **0 calls**
- `api.deepgram.com`: **0 calls**
- `api.deepseek.com`: **0 calls**
- `livekit.cloud`: **0 calls**

**Finding:** All AI pipeline tests in `test_stage4_complete_saas.py` verified configuration schemas and cost tariff calculations in memory. No paid API calls or live streaming audio occurred.

---

## 4. Campaigns & Concurrency Verification

### 4.1 20,000-Contact Ingestion Claim (`test_s4_11`)
- **Numeric Literals Found:** `range(500)` (generates 500 DND numbers), `range(20000)` (generates 20,000 synthetic contact records), `1000` (duplicate frequency), `15000` (phone pool modulo).
- **Assertions:** `assert len(duplicates_seen) <= 15000`, `assert dnd_filtered > 0`, `assert len(valid_contacts) + dnd_filtered == len(duplicates_seen)`.
- **Finding:** The test mathematically proves E.164 normalization, deduplication, and DND list scrubbing on 20,000 records in memory. It does **not** insert 20,000 rows into PostgreSQL. Throughput under 20,000 DB records remains **UNVERIFIED**.

### 4.2 500-vs-2 Concurrency Governor Claim (`test_s4_12`)
- **Numeric Literals Found:** `max_concurrent_slots = 2`, `range(500)` (creates 500 call IDs).
- **Assertions:** `assert admitted == 2`, `assert held_in_queue == 498`, `assert currently_active <= max_concurrent_slots`.
- **Finding:** Governor math and admission logic are verified. Database-level concurrency locking is separately verified in `test_campaign_call_dispatcher.py` (`TestProcessBatchConcurrency`). Full load testing of 500 concurrent live SIP calls remains **EXTERNALLY BLOCKED**.

---

## 5. Prior-Stage Evidence Ledger (75 Reused Tests)

The 75 passing tests cited from Stages 1A, 1B, 2, and 3 were verified from execution logs:
1. **Stage 1A Security (`api/tests/test_stage1a_security.py`):** 23 tests (`test_1_anonymous_read_denied` through `test_23_api_responses_and_logs_contain_no_plaintext_secrets_or_ciphertext`).
2. **Stage 1B Security Fixes (`api/tests/test_stage1b_blocker_fixes.py`):** 11 tests (`test_crypto_env_single_and_alias_support` through `test_signed_url_post_and_put_route_behavior`).
3. **Stage 2 Asterisk Economy IVR (`api/tests/test_economy_ivr_prototype.py`):** 22 tests (`test_01_economy_ivr_schema_validation` through `test_22_economy_ivr_configuration_api_get_and_post`).
4. **Stage 2 Commercial IVR (`api/tests/test_commercial_ivr_and_costs.py`):** 7 tests (`test_ivr_xml_generation_disabled/enabled`, `test_ivr_dtmf_routing_ai_and_human`, `test_cost_ceilings_*`, `test_budget_admission_guard`).
5. **Stage 2 Real Asterisk ARI E2E (`api/tests/test_live_asterisk_ari_e2e.py`):** 4 tests (`test_01_live_asterisk_ari_info`, `test_02_live_asterisk_channel_originate_and_hangup`, `test_03_live_asterisk_channel_dtmf_delivery`, `test_04_live_asterisk_ari_connection_wrapper`).
6. **Stage 3 Modes, Pricing & Storage (`api/tests/test_stage3_modes_pricing_storage.py`):** 8 tests (`test_s3_01` through `test_s3_07`).

**Total:** 23 + 11 + 22 + 7 + 4 + 8 = **75 tests verified**.

---

## 6. Frontend Verification Status

- **Build Status:** Next.js 15.5.14 production build compiled in 117s.
- **Type Checking:** `Checking validity of types...` passed with **0 errors**.
- **Static Page Generation:** 35/35 pages generated successfully.
- **Shared Bundle:** 102 kB First Load JS.
- **HTTP Probe:** `http://localhost:3020/auth/login` returned HTTP 200 (34,492 bytes).
- **Interactive Browser Testing:** **UNVERIFIED**. Headless browser automation (Playwright/Puppeteer) was not executed.

---

## 7. Reconciled Stage 4 Requirements Matrix

| ID | Requirement | Reconciled Status | Audited Findings & Gaps |
|---|---|---|---|
| **S4-01** | Universal Onboarding | **PARTIAL** | Preset compilation to ReactFlow verified. Interactive multi-step wizard browser journey unverified. |
| **S4-02** | Staff Roles & Permissions | **PARTIAL** | Role permission matrix verified; wholesale margin isolation verified. Active session revocation unverified. |
| **S4-03** | Complete Professional Dashboard | **PARTIAL** | Production build and HTTP 200 reachability verified. Interactive UI button click tests unverified. |
| **S4-04** | Agent Training & Dynamic Fields | **PARTIAL** | Custom field schema and prompt generation verified. Live audio extraction deferred to Stage 5. |
| **S4-05** | All Industry Workflows | **PARTIAL** | 5 domain scenarios verified in code. Real external third-party API sync unverified. |
| **S4-06** | External Business Data Safeguards | **PARTIAL** | Identity verification policy and anti-hallucination fail-closed logic verified. 10k-row CSV worker unverified. |
| **S4-07** | CRM, Appointments, Callbacks | **VERIFIED** | Lead scoring, intent extraction, Asia/Kolkata festival/relative time parsing verified. |
| **S4-08** | Objection Intelligence | **PARTIAL** | Objection detection and Action Inbox queuing verified. UI response versioning review unverified. |
| **S4-09** | Campaign Learning | **PARTIAL** | Draft/publish isolation verified. Automated A/B splitting engine deferred. |
| **S4-10** | Call Lifecycle & Event Idempotency | **VERIFIED** | Real Asterisk ARI Stasis channel origination, event reconciliation, and disconnect cleanup verified. |
| **S4-11** | 20k-Contact Campaign Engine | **PARTIAL** | In-memory 20k normalization and DND scrub verified. Large-scale DB queue persistence unverified. |
| **S4-12** | Concurrency & Capacity | **PARTIAL** | Database concurrency locking and governor logic verified. 500 active RTP streams unverified. |
| **S4-13** | Phone Numbers & Smart DID | **VERIFIED** | Pool selection, failure cooldown, and pool exhaustion event logging verified. |
| **S4-14** | Post-Call Idempotency | **VERIFIED** | SHA-256 collision-resistant action idempotency keys verified. |
| **S4-15** | Logging & Operational Monitoring | **VERIFIED** | Health endpoints (`/api/v1/health` HTTP 200), correlation IDs, and secret masking verified. |
| **S4-16** | Gemini Live & Cartesia Pipeline | **EXTERNALLY BLOCKED** | Local config and tariff accounting verified; live provider audio streaming blocked pending credentials in Stage 5. |

### Summary Counts:
- **VERIFIED:** 5
- **PARTIAL:** 10
- **DEFECTIVE:** 0
- **MOCK-ONLY:** 0
- **NOT IMPLEMENTED:** 0
- **EXTERNALLY BLOCKED:** 1
- **UNVERIFIED:** 0
- **DEFERRED:** 0
- **TOTAL:** **16**

---

## 8. Minimal Targeted Remediation List

To achieve clean production readiness before live carrier activation:
1. **Reconcile Register:** Update `SPX_VOICE_REQUIREMENTS_VERIFICATION.md` to reflect the 5 VERIFIED, 10 PARTIAL, 1 EXTERNALLY BLOCKED counts.
2. **Headless Browser Smoke Test:** Run a lightweight Playwright/Puppeteer script verifying login, onboarding navigation, and agent card saving on `localhost:3020`.
3. **Stage 5 Carrier Gate:** Retain S4-16 as EXTERNALLY BLOCKED until live VoiceLink/LiveKit SIP credentials and Gemini Live API keys are authorized.

