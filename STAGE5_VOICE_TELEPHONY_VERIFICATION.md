# SPX VOICE — STAGE 5A FINAL EVIDENCE CORRECTION & RECONCILIATION REPORT

**Document Path:** `D:\spx-voice-mvp\STAGE5_VOICE_TELEPHONY_VERIFICATION.md`  
**Date:** November 1, 2026  
**Auditor / Engineering Role:** Principal QA Auditor, SIP/Telephony Engineer, Realtime Voice Engineer, Docker Integration Engineer, SaaS Release Engineer  
**Active Project:** `D:\spx-voice-mvp`  
**Protected Original:** `F:\spxv1\spx-voice` (Untouched, Zero Modifications)  

---

## 1. MANDATORY SELF-AUDIT DISCLOSURE

> **VERBATIM DISCLOSURE:**  
> *"This run was performed by the same agent context that produced the prior Stage 4 implementation, evidence reports and Stage 5A completion report."*

This is an auditor-style self-review and evidence reconciliation, not an independent third-party certification. To guard against confirmation bias, every claim below is anchored to executable commands, packet captures, process PIDs, and honest evidence boundaries.

### Bias-Sensitive Classifications for Owner Review
1. **S4-01 / S4-02 / S4-03 Re-downgrade:** Previously claimed as "VERIFIED" based on `test_browser_acceptance.py`. However, `test_browser_acceptance.py` used HTTP API requests via `urllib.request`, not genuine headless browser DOM automation (Playwright/Puppeteer clicking buttons, submitting forms, taking screenshots). All three are corrected to **PARTIAL**.
2. **S5-04 / S5-05 / S5-06 Re-downgrade:** Previously claimed as "VERIFIED" based on local state-machine and mock tests. Under original master acceptance criteria, genuine spoken voice quality (Telugu/Hindi/English accents), recovery from live cloud provider 503/429 outages, and real carrier billing invoices belong to Stage 5B/5C. All six S5 requirements are corrected to **PARTIAL**.
3. **Full Pytest Execution vs Focused Suites:** Focused test suites (65 tests) pass cleanly. When running the entire 976-test suite, execution stalls in `test_run_pipeline_text_greeting.py` / `test_tts_endframe_with_audio_write_failure.py` due to Pipecat `WorkerRunner` awaiting `EndFrame` without a global harness timeout.

---

## 2. GATE 0: STAGE 4 BASELINE RECONCILIATION

Forensic audit of all eight candidate requirements:

| ID | Requirement | Original Acceptance Criteria | Tested Layer & Executed Evidence | Untested / Blocked Scope | Final Classification |
|---|---|---|---|---|---|
| **S4-01** | Universal Onboarding | Multi-step interactive wizard, preset loading, dynamic schema, DB persistence, ReactFlow graph compilation. | Unit test `test_s4_01_universal_business_onboarding_persistence` (graph validation); HTTP POST `/api/v1/business/agent-training/draft` DB persistence. | Headless browser driver (Playwright DOM navigation, button clicks, visual regression). | **PARTIAL** |
| **S4-02** | Staff, Roles & Permissions | Server-side RBAC, secret masking, wholesale rate isolation, dynamic token revocation. | In-memory matrix `test_s4_02_rbac_server_side_permission_matrix`; live API HTTP 403 on `/api/v1/superuser/tenants`. | Dynamic session revocation on role change; client React DOM component masking. | **PARTIAL** |
| **S4-03** | Complete Professional Dashboard | Production build, WCAG AA accessibility, responsive viewport, interactive controls, live DOM rendering. | Next.js 15.5.14 build (35/35 pages, 0 TS errors); HTTP 200 on port 3020; viewport meta tags verified. | Interactive browser driver DOM automation, Toast alerts, client state transitions. | **PARTIAL** |
| **S4-07** | CRM, Appointments, Callbacks | Lead scoring (0-100), intent extraction, calendar scheduling, Indian festival/relative time parsing, SMS/WhatsApp escalation. | `test_s4_07_indian_festival_and_relative_callback_parser` (tomorrow 10 AM, Diwali Nov 8 2026); `test_scenario_b` lead extraction. | Live external SMS/WhatsApp dispatch (Twilio/Gupshup); Google Calendar sync. | **PARTIAL** |
| **S4-10** | Telephony Call Lifecycle | Inbound/outbound call lifecycle, ARI channel control, DTMF capture, clean teardown, CDR tracking. | Real Asterisk 20.21.0 container (`test_live_asterisk_ari_e2e.py` 4/4 passed); Stasis channel origination, DTMF, HTTP 204 hangup. | Live carrier PSTN telecom network delivery across physical mobile switches. | **PARTIAL** |
| **S4-13** | Phone Numbers & Smart DID | Outbound Caller ID pool rotation, health monitoring, failure cooldown quarantine, returning-customer continuity. | In-memory pool selection `test_s4_13_smart_did_selection_and_cooldown` (healthy picked, quarantined skipped). | Live carrier CLI presentation across Indian PSTN/TRAI DLT headers. | **PARTIAL** |
| **S4-14** | Post-Call Idempotency | Collision-resistant idempotency keys preventing duplicate bookings, callbacks, or charges under concurrent retries. | Deterministic SHA-256 generation `test_s4_14_action_idempotency_key_generation` (`key1 == key2`, `key1 != key3`). | High-concurrency multi-worker distributed database advisory lock contention in PostgreSQL. | **PARTIAL** |
| **S4-15** | Logging & Operational Monitoring | Health endpoints, searchable correlation IDs, tenant audit trail, secret redaction in logs, metrics export. | Secret masking `test_23_api_responses_and_logs_contain_no_plaintext_secrets_or_ciphertext`; `/api/v1/health` HTTP 200. | Immutable database audit trail across all tenant entities; OpenTelemetry metric exporter. | **PARTIAL** |

### Stage 4 Reconciled Status Counts (Total = 16)
- **VERIFIED:** 0
- **PARTIAL:** 15 (S4-01 through S4-15)
- **DEFECTIVE:** 0
- **MOCK-ONLY:** 0
- **NOT IMPLEMENTED:** 0
- **EXTERNALLY BLOCKED:** 1 (S4-16)
- **UNVERIFIED:** 0
- **DEFERRED:** 0
- **TOTAL:** **16**

---

## 3. GATE 1: REAL LOCAL SIP/RTP AUDIO VERIFICATION

### Signaling Evidence (Docker Network `172.22.0.6` <-> `172.22.0.8:5060`)
- **SIP INVITE:** Sent from `172.22.0.6:58028` to `sip:9999@asterisk:5060` with SDP offering `audio RTP/AVP 0` (PCMU/8000).
- **SIP 100 Trying:** Received from Asterisk 20.21.0.
- **SIP 200 OK:** Received from Asterisk negotiating codec `PCMU/8000` on remote RTP port `172.22.0.8:21800` (To-Tag: `tag-echo-1`).
- **SIP ACK:** Dispatched to complete the 3-way SIP handshake.
- **SIP BYE:** Dispatched at call termination; received `SIP/2.0 200 OK` from Asterisk.

### Media Evidence (Bidirectional Non-Silent RTP Exchange)
- **RTP Transmission:** 25 RTP packets (4,000 bytes) containing a mathematically generated 400Hz sine wave tone encoded in G.711 mu-law (PCMU) transmitted from client to Asterisk RTP port 21800.
- **RTP Reception:** Asterisk `app_echo.so` received the media and reflected 25 real RTP packets back to client socket.
- **Audio Sample Decoding:** 4,000 raw linear PCM samples decoded from received mu-law payload.
- **Signal Analysis:** Peak Amplitude = **7,932**, RMS Amplitude = **5,290.1**. Non-silent audio mathematically confirmed (`RMS > 1000`).

### LiveKit Worker Bridge
- **Status:** **PARTIAL / EXTERNALLY BLOCKED**.
- Asterisk local audio is verified. However, no local `livekit` container is present in the MVP Docker compose stack; full LiveKit agent audio bridging requires either live LiveKit credentials or a containerized LiveKit server.

---

## 4. GATE 2: TEN CONCURRENT LOCAL SIP SESSIONS

- **Executed Command:** Python multi-threaded SIP generator establishing 10 concurrent SIP calls to `sip:9999@asterisk:5060`.
- **Concurrent Active State:** **10/10 sessions simultaneously established** on Asterisk.
- **Media Negotiation:** All 10 channels negotiated `PCMU/8000` and independent RTP port pairs.
- **Resource Cleanup:** All 10 sessions cleanly received `BYE 200 OK` confirmations; 0 leaked channels.
- **Admission Logic:** Tested 10 active slots + 5 queued slots + 16th call rejected as `BUSY` via `InboundQueueManager`.
- **Unresolved Limit:** Carrier Calls-Per-Second (CPS) across live PSTN trunks remains EXTERNALLY BLOCKED pending carrier provisioning.

---

## 5. GATE 3: STAGE 5 CORRECTED REQUIREMENTS MATRIX (S5-01 TO S5-06)

Evaluated against **original master acceptance criteria** (not artificially reduced Stage 5A local criteria):

| ID | Requirement Name | Status | Verified Local Subfeatures | Unverified / Blocked Scope | Required Next-Stage Test |
|---|---|---|---|---|---|
| **S5-01** | Telephony Foundation | **PARTIAL** | Asterisk ARI Stasis channel control, 10-session SIP signaling, bidirectional RTP loopback, CDR logging. | Live VoiceLink / Vobiz SIP trunk digest auth; PSTN audio delivery; DLT 140/160 telemarketing headers. | Stage 5C (Live Carrier Dialing) |
| **S5-02** | Gemini Live Standard AI Agent | **PARTIAL** | Mock WebSocket bidi S2S streaming (16kHz PCM); system prompt adherence; tool roundtrip (`check_availability` -> `book_appointment`). | Live Google Gemini Live WebSocket speech-to-speech audio streaming; live voice switching; real token usage. | Stage 5B (Real AI Provider) |
| **S5-03** | Cartesia Premium AI Voice Engine | **PARTIAL** | Streaming STT+LLM+TTS latency budget (470ms < 800ms); emotion controls (`positivity:high`); fallback to Standard on provider error. | Live Cartesia TTS API synthesis quality, natural prosody, latency under real internet jitter; DeepSeek API streaming. | Stage 5B (Real AI Provider) |
| **S5-04** | Conversational Quality & Multilingual | **PARTIAL** | Multilingual prompt dictionaries (Telugu, Hindi, English, Hinglish, Tenglish); acoustic noise filtering (<0.2s); 3-stage silence escalation. | Real human perceptual voice quality, Indian vernacular accent naturalness, live dynamic code-switching comprehension. | Stage 5B (Real AI Provider) |
| **S5-05** | Failure Handling & Resilience | **PARTIAL** | 10-state turn machine lifecycle (`IDLE` to `ENDING`); fail-closed channel termination on local worker crash with zero orphaned channels. | Recovery from real Gemini 503/429 throttling outages; external RTP packet loss; live PSTN warm transfer failure. | Stage 5B & 5C |
| **S5-06** | Capacity, Economics & Storage | **PARTIAL** | 10-slot concurrency governor; 10 real concurrent SIP sessions; 60s pulse tariffs; spending caps; MinIO 90-day retention cleanup. | Carrier CPS limits; real provider invoices; provider-billed reconciliation; multi-node distributed peak load capacity. | Stage 5B & 5C |

### Stage 5 Reconciled Status Counts (Total = 6)
- **VERIFIED:** 0
- **PARTIAL:** 6 (S5-01 through S5-06)
- **DEFECTIVE:** 0
- **MOCK-ONLY:** 0
- **NOT IMPLEMENTED:** 0
- **EXTERNALLY BLOCKED:** 0 (Tracked within partial subfeatures)
- **UNVERIFIED:** 0
- **DEFERRED:** 0
- **TOTAL:** **6**

---

## 6. GATE 4: FULL PYTEST EXECUTION & TEST PROCESS SAFETY

- **Collection Result:** **976 items collected, 0 collection errors** (eliminated from 26 collection errors previously).
- **Process Inspection:** Verified PID `9421` inside `spx-voice-mvp-api-1` running `python -m pytest /app/api/tests -q --tb=short`.
- **Database Isolation:** Confirmed connected to `postgres_test` (26 tables migrated, zero production records modified).
- **Execution & Stall Diagnosis:**
  - Full suite ran sequentially from `_pre_correction_snapshots/` through `api/tests/test_tts_endframe_with_audio_write_failure.py`.
  - Process stalled in `do_epoll_wait` during `test_run_pipeline_text_greeting.py` / `test_tts_endframe_with_audio_write_failure.py`.
  - Root Cause: Pipecat `WorkerRunner` awaits `EndFrame` indefinitely when simulated transport write failure prevents `_bot_stopped_speaking()` from being called. The test harness lacks a global `pytest-timeout` plugin.
  - Process Termination: Safe SIGTERM dispatched via Python `os.kill(9421, signal.SIGTERM)`; PID 9421 terminated cleanly without orphaned processes.
- **Exit Code:** 1 (partial execution before stall termination).

---

## 7. GATE 5: REPRODUCIBLE DEPENDENCIES & CLEAN BUILD AUDIT

- **Root Cause of Missing Fixtures:** `api/Dockerfile` line 19 actively prunes directories named `tests` or `test` during multi-stage build optimization, stripping `pipecat.tests` from `/root/.local/lib/python3.12/site-packages/`. Furthermore, `dograh_sdk` is an internal repository package, not on PyPI.
- **Reproducible Solution Implemented:**
  - Fixtures permanently stored in repository at `api/tests/_fixtures/dograh_sdk` and `api/tests/_fixtures/pipecat`.
  - In `api/tests/conftest.py`, added dynamic `sys.path` and `pipecat.__path__` registration hooks.
  - Verified: `import dograh_sdk` and `from pipecat.tests import MockLLMService` now resolve directly from `api/tests/_fixtures/` without modifying site-packages. Rebuilding containers preserves all test fixtures.

---

## 8. GATE 6: ENGINE FALLBACK, COSTS & BILLING SAFETY

- **Engine Switching:** Tested Economy IVR (₹0.80), Standard Gemini Live (₹1.80), Premium Cartesia (₹3.20). Pinned calls preserve original engine; new calls adopt updated policy.
- **Safe Fallback:** Provider error during Cartesia TTS automatically downgrades to Standard (cheaper, never silently switches to a more expensive provider).
- **Tariff Honesty:** Tariffs explicitly classified as software baseline rates (`CostClassification.MEASURED` / `SIMULATED`).
- **Financial Isolation:** Wholesale carrier and AI supplier margins strictly hidden from client-facing responses (`is_admin=False`).
- **Admission Controls:** Daily/monthly spending caps and emergency kill-switch verified; duplicate billing blocked via SHA-256 idempotency keys.
