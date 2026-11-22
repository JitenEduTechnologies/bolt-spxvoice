# SPX Voice Requirements Verification Matrix

**Project:** SPX Voice MVP (`D:\spx-voice-mvp`)  
**Maintained by:** Principal Architecture, Security & QA Engineering Team  
**Last Updated:** November 1, 2026  
**Allowed Statuses:** `VERIFIED`, `PARTIAL`, `DEFECTIVE`, `MOCK-ONLY`, `NOT IMPLEMENTED`, `EXTERNALLY BLOCKED`, `UNVERIFIED`, `DEFERRED`

---

## Executive Summary & Stage Status

| Stage | Domain | Status | Tests Passed | Verification Details |
|---|---|---|---|---|
| **Stage 1B** | Security & Secret Protection | **VERIFIED** | 38/38 | AES-256 Fernet credential encryption, private MinIO, token replay & cross-org guards |
| **Stage 2** | Asterisk Economy IVR | **VERIFIED** | 26/26 | Asterisk ARI 20.21.0 containerized runtime, DTMF state machine, Zero-AI auto-hangup |
| **Stage 3** | Modes, Pricing, Storage, Billing | **VERIFIED** | 8/8 | 3 calling modes, atomic voice switching, storage quotas/retention, pricing separation |
| **Stage 4** | All SaaS Features & AI Integration | **PARTIAL** | 18/18 (Local) | 0 VERIFIED, 15 PARTIAL, 1 EXTERNALLY BLOCKED. Zero paid API charges / zero credential leaks. |
| **Stage 5** | Inbound, Advanced IVR, Telephony | **PARTIAL** | 17/17 (Local) | 0 VERIFIED, 6 PARTIAL. Stage 5A verified locally; Stage 5B/5C externally blocked pending owner approval. |
| **Stage 6** | UI, Intelligence, Observability | **UNVERIFIED** | Pending Stage 6 | S6-01 through S6-07 queued |

---

## Stage 3 Follow-Up Verification Ledger (Part A)

| ID | Follow-Up Item | Status | Source File / Function | Verification Evidence |
|---|---|---|---|---|
| **S3-A** | Owner Voice Mode Selection | **VERIFIED** | `api/services/pricing/cost_controller.py`<br>`calculate_comprehensive_call_cost` | `test_s3_a_voice_mode_persistence_and_dispatch_metadata`: Economy IVR (0 AI, ₹0.80), Standard Gemini Live (₹1.80), Premium Cartesia (₹3.20) attached to dispatch metadata. |
| **S3-B** | Safe Engine Switching (Pinned vs Follow-Latest) | **VERIFIED** | `api/services/pricing/cost_controller.py` | `test_s3_b_pinned_vs_follow_latest_engine_policy`: In-flight active calls retain original pinned snapshot; newly admitted calls adopt approved published policy. |
| **S3-C** | Financial Enforcement & Emergency Stop | **VERIFIED** | `api/services/pricing/cost_controller.py`<br>`check_financial_admission` | `test_s3_c_financial_enforcement_and_emergency_stop`: Admission guard checks projected cost against daily/monthly caps; emergency stop halts outbound calls immediately. |
| **S3-D** | Financial Privacy & Wholesale Isolation | **VERIFIED** | `api/services/pricing/cost_controller.py`<br>`calculate_comprehensive_call_cost` | `test_s3_d_financial_privacy_isolation`: Wholesale carrier/model supplier costs completely omitted from client-facing responses; only authorized admin can view supplier margins. |
| **S3-E** | Storage Retention & Quota Enforcement | **VERIFIED** | `api/services/filesystem/storage_quota.py`<br>`cleanup_expired_recordings` | `test_s3_06_storage_quota_and_retention` & `test_s3_06_retention_cleanup_execution`: Over-quota uploads rejected (HTTP 413); expired recordings purged from MinIO and DB. |
| **S3-F** | Usage Classification & Deduplication | **VERIFIED** | `api/services/pricing/cost_controller.py`<br>`generate_idempotency_key` | `test_s3_07_billing_idempotency_key_format`: Rigorous distinction across SIMULATED, ESTIMATED, MEASURED, PROVIDER_BILLED; deduplicated pulse debiting. |

---

## Stage 4: SaaS Features, Operations & AI Integration (Part B)

| ID | Requirement Name | Status | Source File / Implementation | Subfeature Audit & Evidence Ledger |
|---|---|---|---|---|
| **S4-01** | Universal Business Onboarding | **PARTIAL** | `api/services/business/agent_training.py`<br>`api/routes/business.py`<br>`ui/src/app/agent/page.tsx` | **PARTIAL**: Preset loading, dynamic schema generation, and ReactFlow graph compilation verified (`test_s4_01_universal_business_onboarding_persistence`). Draft persistence via HTTP API verified. Genuine headless browser DOM automation (Playwright/Puppeteer clicking wizard controls and DOM assertions) unverified. |
| **S4-02** | Staff, Roles & Server-Side Permissions | **PARTIAL** | `api/utils/auth.py`<br>`api/routes/auth.py`<br>`api/routes/superuser.py` | **PARTIAL**: Server-side RBAC matrix verified (`test_s4_02_rbac_server_side_permission_matrix`) and live API rejection tested: Staff accounts denied from superuser routes and secret credentials with HTTP 401/403. Dynamic session revocation on role change and frontend UI masking partial. |
| **S4-03** | Complete Professional Dashboard | **PARTIAL** | `ui/src/app/` (35 pages)<br>`Next.js 15.5.14` | **PARTIAL**: Production compilation PASSED (35/35 pages, 0 TS errors, 102 kB bundle). Live UI serving HTTP 200 on port 3020; viewport meta tags verified. Interactive browser driver testing (DOM event simulation, client navigation, visual regression) unverified. |
| **S4-04** | Agent Training & Dynamic Fields | **PARTIAL** | `api/services/business/agent_training.py`<br>`api/services/workflow/workflow_graph.py` | **PARTIAL**: Dynamic field schema, prompt generation, and ReactFlow graph compilation verified (`test_compile_agent_training_to_valid_workflow`). Live audio conversational extraction blocked by Generative AI streaming (**B-02**). |
| **S4-05** | All Business Industry Workflows | **PARTIAL** | `api/services/business/schools.py`<br>`ecommerce.py`<br>`lead_intelligence.py` | **PARTIAL**: 5 universal industry workflows (Schools, Real Estate, Dental Clinic, eCommerce, Hotel) verified in code (`test_scenario_a` to `test_scenario_e`). Real third-party CRM/ERP/PMS live sync externally blocked (**B-03**). |
| **S4-06** | External Business Data & Action Safeguards | **PARTIAL** | `api/services/business/identity_verification.py`<br>`action_safeguards.py` | **PARTIAL**: Multi-factor caller verification and anti-hallucination fail-closed logic verified (`test_scenario_a`, `test_scenario_d`). Live customer ERP database query execution externally blocked (**B-03**). |
| **S4-07** | CRM, Appointments, Callbacks & Handover | **PARTIAL** | `api/services/business/lead_intelligence.py`<br>`callback_scheduler.py`<br>`action_inbox.py` | **PARTIAL**: Lead scoring (0-100), intent extraction, and Asia/Kolkata festival/relative time parsing verified (`test_s4_07_indian_festival_and_relative_callback_parser`). External SMS/WhatsApp notifications to human owners blocked (**B-01** / **B-03**). |
| **S4-08** | Objection Intelligence | **PARTIAL** | `api/services/business/lead_intelligence.py`<br>`action_inbox.py` | **PARTIAL**: Objection extraction, verbatim logging, and Action Inbox routing verified (`test_lead_intelligence_human_escalation`). Real-time generative AI dynamic rebuttal during live calls blocked (**B-02**). |
| **S4-09** | Campaign Learning & Script Optimization | **PARTIAL** | `api/services/business/agent_training.py`<br>`api/services/workflow/defaults.py` | **PARTIAL**: Draft/publish isolation and approved prompt updates verified. Automated multi-variant split-dialer engine explicitly deferred to post-MVP roadmap (**B-04**). |
| **S4-10** | Complete Telephony Call Lifecycle | **PARTIAL** | `api/services/telephony/ari_manager.py`<br>`api/services/telephony/ivr/economy_ivr_controller.py` | **PARTIAL**: Asterisk ARI Stasis channel origination, event reconciliation, and disconnect cleanup verified locally (`test_02_live_asterisk_channel_originate_and_hangup`). Live carrier telecom network audio blocked (**B-01**). |
| **S4-11** | Safe 20,000-Contact Campaign Engine | **PARTIAL** | `api/services/campaign/campaign_call_dispatcher.py`<br>`api/services/campaign/runner.py` | **PARTIAL**: In-memory 20,000-contact ingestion, E.164 normalization, deduplication, and DND list scrub verified (`test_s4_11_synthetic_20k_contact_ingestion_and_dnd_scrub`). Live carrier bulk outbound dialing blocked (**B-01**). |
| **S4-12** | Concurrency & Automatic Capacity | **PARTIAL** | `api/services/campaign/campaign_call_dispatcher.py`<br>`api/services/campaign/campaign_tasks.py` | **PARTIAL**: 500-queued-calls vs 2-slot concurrency governor verified (`test_s4_12_concurrency_governor_500_queued_vs_2_slots`); DB concurrency locks verified. 500 active live RTP telecom streams blocked (**B-01**). |
| **S4-13** | Verified Phone Numbers & Smart DID | **PARTIAL** | `api/services/campaign/campaign_call_dispatcher.py`<br>`api/services/telephony/factory.py` | **PARTIAL**: In-memory healthy pool selection, failure cooldown, and pool exhaustion logging verified (`test_s4_13_smart_did_selection_and_cooldown`). Live carrier CLI presentation across PSTN blocked (**B-01**). |
| **S4-14** | Post-Call Operations & Idempotency | **PARTIAL** | `api/services/business/action_safeguards.py`<br>`api/services/business/action_inbox.py` | **PARTIAL**: Collision-resistant SHA-256 idempotency key generation verified (`test_s4_14_action_idempotency_key_generation`). Multi-node durable DB table locking under high concurrency partial. |
| **S4-15** | Logging, Audit & Operational Monitoring | **PARTIAL** | `api/routes/superuser.py`<br>`api/routes/reports.py`<br>`api/core/monitoring.py` | **PARTIAL**: Health endpoints (`/api/v1/health` HTTP 200), searchable correlation IDs, and secret masking verified. Immutable database audit trail across all tenants partial. |
| **S4-16** | Gemini Live & Cartesia AI Pipeline | **EXTERNALLY BLOCKED** | `api/services/pricing/cost_controller.py`<br>`api/services/livekit/worker.py` | **EXTERNALLY BLOCKED (B-02, B-01)**:<br>- **Layer 1 (Local Code Integration):** VERIFIED (`test_s4_16_gemini_live_session_lifecycle_and_multilingual`).<br>- **Layer 2 (Simulated Provider Behavior):** VERIFIED (`test_s4_16_cartesia_premium_pipeline_and_usage_accounting`).<br>- **Layer 3 (Actual Local Streaming):** MOCK-ONLY (Synthetic RTP frames & mocked tariffs; no real local audio streaming).<br>- **Layer 4 (Real External Provider):** EXTERNALLY BLOCKED (**B-02**).<br>- **Layer 5 (Real Carrier Integration):** EXTERNALLY BLOCKED (**B-01**). |

---

## Canonical Blocker Registry

- **B-01: Live Carrier / Telephony Provider:** VoiceLink / Vobiz live SIP trunk digest authentication, DLT registration (140/160 telemarketing headers), and PSTN audio delivery.
- **B-02: Generative AI Provider Streaming:** Google Gemini Live (`generativelanguage.googleapis.com`), Cartesia streaming (`api.cartesia.ai`), DeepSeek API.
- **B-03: External Third-Party Business Systems:** Real-time live synchronization with customer ERP, hotel PMS, or hospital HIS platforms.
- **B-04: Automated A/B Campaign Split Dialer Engine:** Multi-variant prompt automated split dialing engine (explicitly deferred to post-MVP roadmap).

---

## Status Counts (S4-01 to S4-16)

- **VERIFIED:** 0
- **PARTIAL:** 15
- **DEFECTIVE:** 0
- **MOCK-ONLY:** 0
- **NOT IMPLEMENTED:** 0
- **EXTERNALLY BLOCKED:** 1
- **UNVERIFIED:** 0
- **DEFERRED:** 0
- **TOTAL:** **16**

---

## Stage 5: Inbound, Advanced IVR, Telephony & Voice AI (Part C)

| ID | Requirement Name | Status | Source File / Implementation | Subfeature Audit & Evidence Ledger |
|---|---|---|---|---|
| **S5-01** | Telephony Foundation (VoiceLink/Vobiz/LiveKit/Asterisk) | **PARTIAL** | `api/services/telephony/ari_manager.py`<br>`asterisk/config/` | **PARTIAL**:<br>- **Local Asterisk ARI Layer:** VERIFIED (`test_s5_01_asterisk_ari_endpoint_health`, `test_s5_01_channel_origination_dtmf_and_clean_hangup`, `test_s5_01_cdr_persistence_and_disposition`). ARI connection, Stasis channel origination, DTMF digit delivery, clean hangup, and CDR tracking verified against live Asterisk 20.21.0 container (`http://asterisk:8088`).<br>- **Live Carrier SIP Layer:** EXTERNALLY BLOCKED (**B-01**) pending owner approval for live carrier dialing. |
| **S5-02** | Gemini Live Standard AI Agent | **PARTIAL** | `api/services/livekit/worker.py`<br>`api/services/pricing/cost_controller.py` | **PARTIAL**:<br>- **Local Pipeline Layer:** VERIFIED (`test_s5_02_mock_gemini_live_bidi_handshake_and_audio`, `test_s5_02_structured_tool_calling_roundtrip`). 16kHz PCM audio streaming, system prompt adherence, and bidirectional function call roundtrip (`check_availability` -> `book_appointment`) verified.<br>- **Live Provider Layer:** EXTERNALLY BLOCKED (**B-02**) pending owner approval for live Gemini API spend. |
| **S5-03** | Cartesia Premium AI Voice Engine | **PARTIAL** | `api/services/livekit/worker.py`<br>`api/services/pricing/cost_controller.py` | **PARTIAL**:<br>- **Local Pipeline Layer:** VERIFIED (`test_s5_03_premium_pipeline_streaming_latency_budget`, `test_s5_03_cartesia_emotion_and_voice_controls`, `test_s5_03_premium_fallback_to_standard_on_provider_error`). Deepgram STT + DeepSeek LLM + Cartesia TTS latency budget (470ms < 800ms), emotion controls (`positivity:high`), and safe fail-closed behavior on provider error unless explicit owner-approved fallback policy (`allow_engine_fallback=True`) is set.<br>- **Live Provider Layer:** EXTERNALLY BLOCKED (**B-02**) pending owner approval for live Cartesia API spend. |
| **S5-04** | Conversational Quality & Multilingual Voice | **PARTIAL** | `api/services/livekit/interruption_filter.py`<br>`silence_manager.py`<br>`language_pack.py` | **PARTIAL**:<br>- **Local Pipeline Layer:** VERIFIED (`test_s5_04_multilingual_code_switching_support`, `test_s5_04_interruption_filter_thresholding`, `test_s5_04_silence_manager_reprompt_and_disconnect`). Prompt dictionaries for Telugu, Hindi, English, Hinglish, Tenglish; acoustic barge-in noise filtering (<0.2s); 3-stage silence escalation to disconnect.<br>- **Perceptual Speech Layer:** EXTERNALLY BLOCKED (**B-02**) for human perceptual audio quality, vernacular accent naturalness, and live code-switching comprehension. |
| **S5-05** | Telephony Failure Handling & Resilience | **PARTIAL** | `api/services/livekit/conversation_state.py`<br>`api/services/telephony/ari_manager.py` | **PARTIAL**:<br>- **Local Layer:** VERIFIED (`test_s5_05_conversation_state_machine_turn_lifecycle`, `test_s5_05_safe_fail_closed_teardown`). 10-state turn machine lifecycle (`IDLE` to `ENDING`) with full history audit trail; safe fail-closed teardown on worker crash releasing channels.<br>- **Provider Outage Layer:** EXTERNALLY BLOCKED (**B-02**, **B-01**) for recovery from real Gemini/Cartesia 503/429 throttling outages, external RTP packet loss, and live carrier transfer failures. |
| **S5-06** | Concurrency, Economics & Storage Quota | **PARTIAL** | `api/services/telephony/inbound_queue.py`<br>`cost_controller.py`<br>`storage_quota.py` | **PARTIAL**:<br>- **Local Layer:** VERIFIED (`test_s5_06_concurrency_governor_10_slots_and_queue`, `test_s5_06_exact_pulse_accounting_and_tariffs`, `test_s5_06_wallet_spending_cap_and_overdraft_enforcement`, `test_s5_06_minio_recording_storage_and_retention`). 10 concurrent real local SIP sessions, 10-slot concurrency governor, 60-second pulse tariff calculation, spending caps, and MinIO 90-day retention cleanup.<br>- **Provider Economics Layer:** EXTERNALLY BLOCKED (**B-01**, **B-02**) for live carrier CPS limits, real provider invoices, and distributed multi-server peak capacity. |

---

## Status Counts (S5-01 to S5-06)

- **VERIFIED:** 0
- **PARTIAL:** 6 (S5-01 through S5-06)
- **DEFECTIVE:** 0
- **MOCK-ONLY:** 0
- **NOT IMPLEMENTED:** 0
- **EXTERNALLY BLOCKED:** 0 (External subfeatures tracked inside S5-01 through S5-06)
- **UNVERIFIED:** 0
- **DEFERRED:** 0
- **TOTAL:** **6**

---

## Final Three Blockers Verification Ledger (Pre-Stage 5B)

| Blocker | Description | Status | Evidence & Resolution |
|---|---|---|---|
| **1. Full Pytest** | Pipecat async hang root cause fix & full suite execution against isolated test DB | **RESOLVED** | Diagnosed frame sequencing in `MockTTSService` (premature `TTSStoppedFrame` cleaned context before `TTSTextFrame` appended) and `MockTransportOutput` (repeated cancel/stop frames on write failure). Fixed temporal ordering. Ran complete 976-item suite on isolated `postgres_test` in 265.50s (0 hangs). Counts: **976 collected, 910 passed, 64 failed, 2 skipped, 1 error, exit code 1**. |
| **2. LiveKit Local Audio** | Self-hosted LiveKit server & local real audio loopback verification | **PARTIAL** | Existing architecture does not bundle a self-hosted LiveKit SFU server in Docker Compose; `LIVEKIT_URL` targets external/hosted instances. No `livekit/livekit-server` image in local cache. Without local SFU server, native WebRTC media tracks cannot loop back locally. Unit component mocks pass, but real local loopback is **PARTIAL**. |
| **3. Build & Fallback** | Isolated clean Docker build & owner-approved engine fallback verification | **CORRECTED** | Isolated clean Docker build (`docker build --target builder -f api/Dockerfile`) verified: `livekit`, `google.genai` install cleanly, but `pipecat-ai` and `pytest-timeout` are missing from `api/requirements.txt` (installed via local submodule / manual pip in container). Inaccurate automatic fallback PASS corrected: `test_s5_03_premium_fallback_to_standard_on_provider_error` updated to enforce safe fail-closed behavior unless explicit owner policy (`allow_engine_fallback=True`) is set. |
