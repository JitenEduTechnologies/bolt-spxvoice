# SPX VOICE — STAGE 5 MASTER DIRECTIVE
## Real Voice, Telephony and Resilience Verification

**Document Path:** `D:\spx-voice-mvp\STAGE5_MASTER_DIRECTIVE.md`  
**Status:** Approved Master Directive  
**Date:** November 1, 2026  

---

## 1. Approved Scope & Requirement Structure

The approved original master directive defines **six Stage 5 requirements** (S5-01 through S5-06). Do not invent S5-07 through S5-16.

| ID | Requirement Name | Scope | Execution Tier |
|---|---|---|---|
| **S5-01** | VoiceLink, Vobiz, LiveKit & Real Telephony | SIP trunking, Asterisk ARI/PJSIP, LiveKit media bridging, channel lifecycle, audio path, CDR | 5A (Local loopback/Asterisk/ARI/Worker) → 5C (Live VoiceLink/Vobiz carrier) |
| **S5-02** | Gemini Live Standard Voice | Google Gemini Live native speech-to-speech, real-time audio turns, prompt adherence, voice switching | 5A (Local mock WebSocket provider) → 5B (Real Gemini Live API) |
| **S5-03** | Premium Streaming Pipeline | Compatible streaming STT + DeepSeek reasoning + Cartesia speech synthesis | 5A (Local mock HTTP/WS adapters) → 5B (Real DeepSeek/Cartesia API) |
| **S5-04** | Conversational Quality & Multilingual | Spoken comprehension, pronunciation, multilingual code-switching (English, Telugu, Hindi, Hinglish, Tenglish), interruptions | 5A (Text & synthetic audio prompt harness) → 5B (Spoken human listening evaluation) |
| **S5-05** | Failure Handling & Resilience | Network drops, silence detection/recovery, model timeout, worker crash recovery, safe fail-closed fallback | 5A (Simulated disconnects & faults) → 5B/5C (Provider & trunk timeout resilience) |
| **S5-06** | Capacity, Concurrency & Economics | Concurrent session admission, governor limits, Redis locking, tariff tracking, budget caps, MinIO retention | 5A (Local 10-call concurrent load & pulse math) → 5C (Carrier CPS & billing reconciliation) |

---

## 2. Stage Breakdown & Execution Boundaries

- **Stage 5A (Local Voice & Telephony Verification):**
  - **AUTHORIZED BY DEFAULT**: Local Asterisk ARI, local SIP/RTP media loopback, LiveKit local agent dispatch, mock Gemini Live WebSocket server, mock Cartesia/DeepSeek pipeline, CRM & booking local tools in PostgreSQL, multilingual prompt harness, 10-call concurrency governor, MinIO recording lifecycle.
  - **Zero paid API charges / zero real telephone calls / zero customer messaging.**

- **Stage 5B (Real AI Voice Provider Acceptance):**
  - **REQUIRES EXPLICIT OWNER APPROVAL**: Live WebSocket connections to Google Gemini Live (`generativelanguage.googleapis.com`) and Cartesia (`api.cartesia.ai`).
  - Evaluates speech latency, Telugu/Hindi spoken accuracy, human listening evaluation, provider billing reconciliation under approved spending caps.

- **Stage 5C (Real Telephony Acceptance):**
  - **REQUIRES EXPLICIT OWNER APPROVAL**: Live SIP digest authentication with VoiceLink / Vobiz, DLT telemarketing headers (140/160 prefixes), inbound DID routing, single-call outbound test dialing, CDR tariff reconciliation.
  - WhatsApp reminders are outside Stage 5C implementation.

---

## 3. Mandatory Evidence Traceability

Every subfeature across 5A, 5B, and 5C must record:
1. Exact source file & function.
2. Targeted test command & execution exit code.
3. Media layer classification (`LOCAL-AUDIO`, `MOCK-PROVIDER`, `UNIT`, `EXTERNALLY BLOCKED`).
4. Database state / API evidence.
5. Screenshot / audio artifact path.

