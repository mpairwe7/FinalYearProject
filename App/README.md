# 🇺🇬 URA Chatbot - Application Directory

This directory contains all application components for the URA Chatbot project.

## Design docs

Deeper contracts live in `docs/`. Read these before changing the
behaviour they describe.

| Doc | Covers |
| --- | --- |
| [`docs/mcp-architecture.md`](docs/mcp-architecture.md) | MCP spec 2026-07-28 surface, transports, tool authorization |
| [`docs/tax-rate-tables.md`](docs/tax-rate-tables.md) | Effective-dated rate tables, provenance, adding a fiscal year |
| [`docs/agentic-loop.md`](docs/agentic-loop.md) | Per-turn tool budgets, duplicate suppression, observation compaction |
| [`docs/tax-education.md`](docs/tax-education.md) | Fading scaffolding, retrieval practice, keeping worked examples tied to the rate tables |
| [`docs/context-aware-escalation.md`](docs/context-aware-escalation.md) | Transcript handoff, sentiment, delivery, queue integrity, privacy constraints |
| [`docs/ws_chat_protocol.md`](docs/ws_chat_protocol.md) | `/v2/chat/stream` WebSocket frames and the agentic event surface |

## Directory Structure

```
App/
├── app.py              # Main Gradio app (HF Spaces deployment)
├── classifier.py       # Legacy classifier interface
├── requirements.txt    # Python dependencies
├── README.md          # This file
├── backend/           # FastAPI backend API
│   ├── app/
│   │   ├── main.py          # API routes + SSE streaming + speech + eval endpoints
│   │   ├── models.py        # Pydantic v2 request/response models
│   │   ├── speech_service.py # SpeechModel (ASR/Whisper + TTS/Piper + MT + Sunbird fallback)
│   │   ├── service.py       # ChatModel (RAG orchestrator + agentic routing)
│   │   ├── llm.py           # Qwen3-8B vLLM + local generation + tool-calling
│   │   ├── flags.py         # Feature flag registry (45 flags, cohort rollout)
│   │   ├── agents/          # Supervisor router + agent graph runtime
│   │   ├── tools/           # 19 tools (calculators, rates, calendar, RAG, education, empathy, escalate)
│   │   ├── workflows/       # YAML-driven slot-filling engine + TIN registration
│   │   ├── memory/          # Episodic + semantic + working memory
│   │   ├── guardrails.py    # OWASP LLM Top 10 guards + abstention
│   │   ├── retriever.py     # Hybrid retriever (Qdrant + BM25 + reranking)
│   │   ├── native_voice/    # Phase 28: streaming ASR/TTS, speculative prefetch, query planner
│   │   ├── vision/          # Phase 28: Qwen2-VL encoder, OCR, document classifier
│   │   ├── voice_stream_v2.py # V2 voice session (dual-path + vision)
│   │   ├── voice_ws_v2.py   # V2 WebSocket handler (/v2/voice/chat/stream)
│   │   └── ...              # cache, query, indexer, resilience, tracing, analytics, database
│   └── requirements.txt
└── frontend/          # Next.js 16 PWA frontend
    ├── src/
    │   ├── app/             # Pages: chat, 404, error, analytics, evaluation
    │   ├── components/      # 23 components (10 charts + 13 UI: Chat, Voice, Markdown, etc.)
    │   ├── services/        # voiceService.ts (AudioRecorder + playback + API)
    │   ├── hooks/           # useSpeech.ts (React Query hooks)
    │   └── store/           # Zustand 5 + analytics (consent-gated)
    ├── public/            # PWA: manifest, SW, icons, robots.txt
    └── package.json
```

## Components

### 1. Gradio App (`app.py`)

Modern chat interface for Hugging Face Spaces deployment.

**Features:**
- 💬 Natural language chat interface
- 🎨 Modern dark theme matching frontend design
- 📱 Responsive layout with sidebar
- 🏷️ AI-powered query classification
- 📚 Knowledge base integration

**Run locally:**
```bash
cd App
pip install -r requirements.txt
python app.py
```

### 2. Backend API (`backend/`)

FastAPI REST API with a production-hardened agentic RAG pipeline, durable
conversation threading, guided workflows, human handoff, and Qwen3-backed
generation over either local Transformers or vLLM.

**Core Endpoints:**
- `GET /health` — Liveness probe
- `GET /ready` — Readiness probe (model + Qdrant status)
- `POST /v1/chat` — Synchronous chat with full RAG pipeline
- `POST /v1/chat/stream` — SSE streaming chat (progressive token delivery)
- `POST /classify` — Text classification
- `POST /classify/batch` — Batch classification
- `POST /v1/index` — Trigger document re-indexing (auth required)
- `POST /v1/feedback` — Submit feedback (thumbs up/down)
- `GET /v1/feedback/summary` — Feedback analytics
- `POST /v1/analytics/event` — Track client-side events
- `GET /v1/analytics/dashboard` — Comprehensive dashboard
- `GET /metrics` — Prometheus-compatible metrics

**Speech Endpoints (2026):**
- `POST /v1/voice/chat` — Compound voice pipeline: audio -> ASR -> MT -> LLM -> MT -> TTS -> audio+text
- `WS /v1/voice/chat/stream` — V1 streaming voice (sentence-chunked TTS, VAD, barge-in)
- `WS /v2/voice/chat/stream` — V2 native voice-to-voice (token-level TTS, speculative prefetch, vision)
- `POST /v1/asr` — Server-side ASR (Whisper via transformers, raw PCM -> transcript)
- `POST /v1/tts` — Text-to-speech synthesis (Edge Neural TTS, text -> base64 WAV)
- `POST /v1/translate` — Machine translation (English <-> Luganda)
- `GET /v1/speech/health` — Speech pipeline readiness check

**Anonymous usage policy (May 2026):**

The public assistant is intentionally usable without logging in. Public routes
use `optional_user()`: a valid bearer token personalizes/scopes the request, but
missing auth becomes `role=public`, `tenant_id=default`. Invalid bearer tokens
still return `401`.

| Surface | Anonymous? | Auth/consent behavior |
|---------|------------|-----------------------|
| `POST /v1/chat`, `POST /v1/chat/stream` | Yes | Optional bearer token; anonymous turns still get RAG/workflows and citations |
| `GET /v1/speech/health`, `POST /v1/tts`, `POST /v1/translate` | Yes | Optional bearer token |
| `POST /v1/asr`, `POST /v1/voice/chat`, voice WebSocket, `POST /v1/voice/vision/chat` | Yes | Requires explicit voice consent: `X-Voice-Consent: true` for HTTP or `voice_consent_accepted=true` in the WebSocket `session_start` frame |
| `/v1/me/*`, feedback writes/summaries, analytics dashboards, metrics, evaluation exports, offline bundle APIs | No | Require a verified user or staff/admin role depending on route |
| `ura_account_profile`, `ura_action_proposal` MCP tools | No | Require verified taxpayer/staff role, consent, confirmation/idempotency for actions, and configured URA API credentials |

The frontend stores a bearer token under `NEXT_PUBLIC_AUTH_TOKEN_STORAGE_KEY`
(default `ura_auth_token`) when a login flow is added, but no token is required
for normal chat usage.

**Evaluation Endpoints (2026):**
- `GET /v1/evaluation/results` — Pre-computed IEEE metrics (RAG, safety, calibration, benchmark, tokenizer)

**Current request pipeline:**
1. **Auth + request context** — fail-closed private/admin guards, optional RS256/JWKS OIDC verification, durable `conversation_id` thread handling
2. **Supervisor routing** — routes turns into greetings, standard RAG, guided workflows, clarification, or human escalation. A message carrying a money amount **and** a calculator intent reaches the calculators whatever the word order (`"VAT on 500000"`, `"take-home pay on a 2M salary"`), so a numeric tax question is not answered from the model's memory
3. **Hybrid Retrieval** — Qdrant dense + BM25 sparse RRF + cross-encoder reranking + circuit breaker. `top_k` is a ceiling, not a quota: passages the reranker scored as irrelevant are dropped rather than padded into the prompt, since a weak passage beside a strong one is what lets a model synthesise a claim neither supports. Acronym expansion keeps **both** surface forms (`WHT` → `Withholding Tax (WHT)`) so the sparse side still matches documents that write it short
4. **LLM Generation** — `Qwen/Qwen3-8B` via local Transformers or vLLM HTTP
5. **Streaming delivery** — progressive SSE with chunk-aware sanitization, optional `revision` event, and keepalive pings
6. **Query intelligence** — rewriting (abbreviations, spelling, coreference), semantic cache, optional consented memory, multi-turn continuity
7. **Response governance** — OWASP LLM Top 10 guards, corrective RAG, `response_judge` (soft citation check + faithfulness gating), claim verification (percentage **and** money-amount contradiction against the cited passage), structured `handoff`, calibrated escalation
8. **Emotional intelligence** — `assess_emotional_tone` classifies frustration / anxiety / urgency / confusion / hardship and returns the acknowledgement, tone hint and handoff signal the reply should use
9. **Context-aware escalation** — an escalated ticket carries the whole conversation (both sides, untruncated), the taxpayer's sentiment at the point of transfer, and whether the officer should be briefed first; queued urgent-first then longest-waiting, de-duplicated per conversation, and announced over a webhook that carries triage metadata but never the transcript. See [`docs/context-aware-escalation.md`](docs/context-aware-escalation.md)
10. **Taxpayer education** — `explain_tax_concept` teaches a concept instead of only answering about it: fading scaffolding (worked → completion problem → transfer question), a check question whose answer is withheld until asked for, and every figure computed from the effective-dated rate tables
11. **Observability** — OpenTelemetry per-stage spans, Prometheus metrics, analytics dashboard, live smoke + deploy preflight gates

### Backend Architecture & Request Flows

For a local-only document/PDF analysis and report-evaluation setup, use
[docs/local-document-evaluation.md](docs/local-document-evaluation.md). It
keeps OCR on a private Docker network and disables remote inference/fallback
paths for that focused workflow; the dated implementation record is in
[docs/traceability/document-pdf-evaluation-2026-07-21.md](docs/traceability/document-pdf-evaluation-2026-07-21.md).

For local Qdrant FAQ retrieval, see
[docs/local-faq-retrieval.md](docs/local-faq-retrieval.md). The intended
corpus is CSV plus teacher-QA JSONL under `App/Data`; PDFs are the upstream
source for those QA pairs, while evaluation JSONL stays out of live retrieval.

#### RAG Pipeline Flow (10-stage)

Source: `service.py::generate()` (lines 992-1677). Every `/v1/chat` and
`/v1/chat/stream` request traverses this pipeline:

```
                          +------------------+
                          |  User Message    |
                          +--------+---------+
                                   |
                    +--------------v--------------+
                    |  0. Conversation History     |
                    |  (db.get_recent_turns, n=5)  |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  0b. Query Rewrite           |
                    |  (spelling, abbreviations,   |
                    |   coreference resolution)    |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  0c. Language Detection       |
                    |  (lg/sw/nyn/ach heuristics)  |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  1. InputGuard (OWASP LLM01) |
                    |  - injection patterns        |
                    |  - illegal intent detection  |
                    |  - length validation          |
                    +---+----------+-----------+---+
                        |          |           |
                    BLOCKED    HARMLESS    FLAGGED
                    (403)          |        (log+continue)
                                   |
                    +--------------v--------------+
                    |  1b. Workflow Router          |
                    |  WorkflowRegistry.match()    |
                    +---+---------+----------------+
                        |         |
                    MATCHED    NO MATCH
                    (guided)       |
                        |          |
                    +--------------v--------------+
                    |  1c. Semantic Cache Lookup    |
                    |  (cosine >= 0.92 threshold)  |
                    +---+---------+----------------+
                        |         |
                    HIT        MISS
                    (return)       |
                                   |
                    +--------------v--------------+
                    |  1a2. Greeting Detection      |
                    |  (≤3 words, always active)    |
                    +---+---------+----------------+
                        |         |
                    GREETING   NOT GREETING
                    (warm       |
                     reply)     |
                                |
                    +-----------v------------------+
                    |  1d. Supervisor Router        |
                    |  (rule-based, < 1ms)         |
                    +--+---+---+---+---+-----------+
                       |   |   |   |   |
                     RAG TOOLS SPEC CLARIFY ESCALATE
                       |   |   |   |       |
                       |   +---+   |    (ticket)
                       |   |       |
                       v   v       v
                    +--------------v--------------+
                    |  2. Hybrid Retrieval          |
                    |  Qdrant dense (bge-m3 1024d)  |
                    |  + BM25 sparse + RRF fusion   |
                    |  + near-duplicate collapse    |
                    |  + cross-encoder reranking    |
                    |  + context floor (drop tail)  |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  3. Corrective RAG            |
                    |  + FAQ keyword blend (top 2)  |
                    |  + clarification check        |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  4. Abstention Check          |
                    |  (no hits or confidence low)  |
                    +---+---------+----------------+
                        |         |
                    ABSTAIN    PROCEED
                    (refuse)       |
                                   |
                    +--------------v--------------+
                    |  5. LLM Generation            |
                    |  Qwen3-8B (local or vLLM)     |
                    |  - agentic path (tools) OR    |
                    |  - standard RAG synthesis     |
                    |  - 45s deadline + breaker     |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  6. OutputGuard               |
                    |  - PII redaction              |
                    |  - XSS sanitization           |
                    |  - prompt leakage detection   |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  7. Grounding Verification    |
                    |  - faithfulness scoring       |
                    |  - self-reflection (optional) |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  8. Response Judge             |
                    |  (approve / revise / escalate)|
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    |  9. Escalation (if needed)    |
                    |  - handoff packet             |
                    |  - ticket creation            |
                    +--------------+--------------+
                                   |
                    +--------------v--------------+
                    | 10. Post-processing            |
                    |  - semantic cache store       |
                    |  - memory persist (consented) |
                    |  - audit ledger append        |
                    +--------------+--------------+
                                   |
                          +--------v---------+
                          |  ChatResponse    |
                          +------------------+
```

#### Agentic Tool-Calling Flow

When the supervisor routes a query to `TOOLS`, `TAX_SPECIALIST`, or
`CUSTOMS_SPECIALIST`, the agentic path activates:

```
Supervisor.classify(query)
  --> AgentRoute.TOOLS / TAX_SPECIALIST / CUSTOMS_SPECIALIST
  --> force_agentic=True, force_tool_whitelist from supervisor

_call_llm_agentic(max_iterations=3, deadline=90s)
  --> tool schemas narrowed by Tool RAG when FLAG_TOOL_RAG is on
  --> llm_module.generate_with_tools(messages, tool_schemas)
      Loop (bounded by iterations AND by spend):
        LLM proposes tool_call(name, args)
        --> ToolCallBudget.admit(name, args)  # ceilings + duplicate memo
        --> ToolRegistry.call(name, args)     # validated I/O
        --> compact_observation(result)       # valid JSON, salience-ordered
        --> next iteration
      Exit: no more tool_calls OR max_iterations hit
  --> circuit breaker wraps entire loop
  --> returns {text, tool_calls, iterations, truncated, tool_budget}
```

Iteration count bounds how often the model generates, not how much each
generation spends — see [`docs/agentic-loop.md`](docs/agentic-loop.md)
for the per-turn ceilings, duplicate-call suppression and observation
compaction that bound the rest.

**Tool inventory** (19 tools in `backend/app/tools/`). Each declares an
MCP `namespace`, risk tier, required consent scopes and annotation hints;
see [`docs/mcp-architecture.md`](docs/mcp-architecture.md).

| Tool | Module | Namespace | Description |
|------|--------|-----------|-------------|
| `calculate_vat` | `calculators.py` | `tax_calculator` | VAT: add to net or extract from gross |
| `check_vat_registration` | `calculators.py` | `tax_calculator` | Compulsory-registration test against the turnover threshold |
| `calculate_paye` | `calculators.py` | `tax_calculator` | PAYE from progressive bands, with per-band working |
| `calculate_corporation_tax` | `calculators.py` | `tax_calculator` | Corporate income tax |
| `calculate_capital_gains` | `calculators.py` | `tax_calculator` | Capital gains tax |
| `calculate_customs_duty` | `calculators.py` | `tax_calculator` | Duty + VAT + used-clothing environmental levy |
| `calculate_rental_tax` | `calculators.py` | `tax_calculator` | Rental income tax (individual / company) |
| `calculate_withholding` | `calculators.py` | `tax_calculator` | WHT incl. royalties, entertainers, betting, foreign interest |
| `lookup_rate` | `rates.py` | `rates` | One rate or threshold, effective-dated |
| `list_available_rates` | `rates.py` | `rates` | Every rate for a fiscal year |
| `compare_tax_years` | `rates.py` | `rates` | What changed between two fiscal years |
| `get_current_date` | `calendar.py` | `calendar` | Current date for deadline logic |
| `get_next_deadlines` | `calendar.py` | `calendar` | Upcoming tax filing deadlines |
| `search_ura_knowledge_base` | `rag_tool.py` | `rag` | Semantic search (wraps hybrid retriever) |
| `assess_emotional_tone` | `empathy.py` | `empathy` | Classify a message as frustration/anxiety/urgency/confusion/hardship and return tone guidance |
| `explain_tax_concept` | `education.py` | `education` | Teach a concept: scaffolded explanation, worked example computed from the live rate tables, misconceptions, check question |

| `escalate_to_human` | `escalate.py` | `core` | Create escalation ticket from tool loop |
| `ura_account_profile` | `ura_account.py` | `ura_account` | Authenticated taxpayer profile (fail-closed) |
| `ura_action_proposal` | `ura_actions.py` | `ura_actions` | Confirmed, idempotent URA action (fail-closed) |

`explain_tax_concept` teaches rather than answers — see
[`docs/tax-education.md`](docs/tax-education.md) for the scaffolding model
and how its worked examples stay tied to the rate tables.

Every calculator takes an optional `fiscal_year` **or** `as_of` date and
resolves the rate table in force for that period. Rates live as versioned
JSON in `backend/app/tax/data/`, with statutory basis, sources and a
confirmed/provisional status attached to each result — see
[`docs/tax-rate-tables.md`](docs/tax-rate-tables.md).

#### Guided Workflow Engine Flow

YAML-driven durable workflows in `backend/app/workflows/`. Triggered by
natural-language phrases, they guide users through multi-step processes:

```
User message
  --> WorkflowRegistry.match_trigger(message)
      (checks trigger_phrases in all registered workflows)
  --> MATCHED? --> create_session(workflow_id)
  --> advance(session, user_input)
      FOR each step:
        Display step.question to user
        Receive user input
        Validate via slots.py (enum, regex, boolean, text)
        Store value in session.slots
        IF step has when condition --> evaluate (skip if false)
        IF step has tool_call --> ToolRegistry.call inline
      Return WorkflowTurn(question, is_complete, slot_name)
  --> db.upsert_workflow_session (durable persistence across turns)
  --> ON completion: generate summary from collected slots
```

**Registered workflows** (5 YAML flows in `workflows/flows/`):

| Workflow | Trigger Examples | Slots Collected |
|----------|-----------------|-----------------|
| `tin_registration` | "register for a TIN", "get a TIN" | taxpayer_type, legal_name, NIN/company_reg, phone, email |
| `return_filing` | "file a return", "tax return" | tax_type, filing_period, required_docs |
| `payment_assistance` | "how to pay", "payment" | payment_method, amount, account_details |
| `customs_clearance` | "customs", "import", "export" | shipment_type, declaration_number, goods, CIF value |
| `objection_or_dispute` | "objection", "dispute", "appeal" | assessment_ref, grounds, supporting_docs |

#### Speech Pipeline Flow (Local-First Architecture)

Full compound voice pipeline (`POST /v1/voice/chat`).

**Design principle:** Local models are the primary inference path for
offline capability and lower latency. Sunbird cloud API is the final
fallback when all local backends are unavailable.

```
Audio Input (PCM16, 16 kHz)
  |
  v
ASR Fallback Chain (local-first):
  [1] Whisper + LoRA adapter (fine-tuned per-language: lg, sw, nyn)
      Base: openai/whisper-small | Adapter: artifacts/speech/asr/whisper_lg/final
      LoRA config: r=16, alpha=32, targets: q/k/v/out/fc1/fc2 projections
  [2] Local Sherpa ASR (ONNX, if model available)
  [3] faster-whisper CTranslate2 int8 (offline, auto-downloads ~150MB)
  [4] Sunbird cloud API (fallback, native Luganda STT)
  |
  v
Language Detection --> detected_lang
  |
  v
IF detected_lang != "en":
  MT (source --> en):
    [1] Qwen3-8B prompted translation (uses already-loaded LLM, no extra model)
    [2] Local MADLAD-400-3b + LoRA (artifacts/mt/madlad_ura_lgen/final)
    [3] Sunbird cloud (NLLB translation API, fallback)
  |
  v
ChatModel.generate(en_query) --> RAG Pipeline (Qwen3-8B) --> en_reply
  |
  v
IF target_lang != "en":
  MT (en --> target):
    [1] Qwen3-8B prompted translation (repetition_penalty=1.3, max_tokens=256)
    [2] Local MADLAD-400-3b + LoRA
    [3] Sunbird cloud (fallback)
  |
  v
TTS Fallback Chain (local-first):
  [1] Local Sherpa/Piper TTS (offline, if model available)
  [2] edge-tts Microsoft neural voices (free, needs internet)
  [3] Sunbird cloud TTS (native Luganda speaker voices, fallback)
  |
  v
Audio Output (WAV base64) + Text Reply
```

Each subsystem (ASR, TTS, MT) has its own `CircuitBreaker` (3-failure
threshold, 15s reset, 120s max) and bounded `ThreadPoolExecutor`
(`SPEECH_MAX_CONCURRENCY=4`). Hard deadline: `SPEECH_DEADLINE_S=120`.
Failures in speech never block the text API (503 graceful degradation).

**Model artifacts used in production:**

| Component | Base Model | LoRA Adapter | Config |
|-----------|-----------|-------------|--------|
| ASR | `openai/whisper-small` (MIT) | `artifacts/speech/asr/whisper_lg/final` | r=16, alpha=32, PEFT |
| MT | `google/madlad400-3b-mt` (Apache-2.0) | `artifacts/mt/madlad_ura_lgen/final` | r=8, alpha=16, SEQ_2_SEQ_LM |
| LLM | `Qwen/Qwen3-8B` (Apache-2.0) | `luganda-lora`, `sw-lora`, `nyn-lora`, `ach-lora` | BitsAndBytes NF4 4-bit when `LLM_LOAD_IN_4BIT=true`; multi-adapter PEFT routing |
| TTS | edge-tts / Sherpa Piper | N/A | en_US-lessac / luganda-vits-v1 |

#### Streaming Voice Chat Flow (Phase 23)

Real-time duplex voice via WebSocket (`WS /v1/voice/chat/stream`):

```
Client (PCM16 chunks, ~20ms)
  |
  v
WebSocket Handler (voice_ws.py)
  |
  v
VoiceSession (voice_stream.py):
  |
  ├─ Energy-based VAD (hysteresis, configurable thresholds)
  |    └─ Emits vad_state events on speech/silence transitions
  |
  ├─ Utterance complete? ──▶ ASR (existing fallback chain)
  |                           └─▶ transcript_final event
  |
  ├─ Accent Detection (accent_detector.py, < 50ms)
  |    └─ Routes to accent-specific Whisper LoRA adapter
  |
  ├─ [MT lg→en] ──▶ LLM RAG ──▶ [MT en→lg]
  |
  ├─ Sentence-Chunked TTS:
  |    ├─ Split reply on sentence boundaries
  |    ├─ Synthesize each via existing TTS fallback chain
  |    ├─ Yield audio_start + binary audio chunks + audio_end
  |    └─ Check _cancelled between sentences (barge-in support)
  |
  └─ Barge-in: client sends {"type":"barge_in"}
       └─ Sets _cancelled event, aborts TTS, clears audio buffer
```

**New modules:**

| Module | File | Purpose |
|--------|------|---------|
| `voice_stream.py` | `backend/app/` | VADConfig, VoiceSession, sentence splitting |
| `voice_ws.py` | `backend/app/` | WebSocket handler, Prometheus metrics |
| `voice_consent.py` | `backend/app/` | Voice consent, audit log, retention policy |
| `offline_rag.py` | `backend/app/` | FAISS offline retrieval fallback |
| `accent_detector.py` | `backend/app/` | Prosodic accent classifier |

**New frontend:**

| Module | File | Purpose |
|--------|------|---------|
| `VoiceChat.tsx` | `frontend/src/components/` | Full-screen voice-first mobile interface. **No longer mounted** — its only entry point was a header mic that duplicated the composer's; still on disk, see PROJECT_SETUP "Speaking to the assistant" |
| `voiceWebSocket.ts` | `frontend/src/services/` | WebSocket client (auto-reconnect, binary frames) |
| `useVoiceStore.ts` | `frontend/src/store/` | Zustand store for voice state |
| `useVoiceWebSocket.ts` | `frontend/src/hooks/` | React hook wiring WS events to store |
| `CameraCapture.tsx` | `frontend/src/components/` | Document scanning for voice+vision mode |
| `audio-worklet-processor.js` | `frontend/public/` | AudioWorklet for streaming PCM16 capture |

**Feature flags:** `FLAG_VOICE_STREAMING`, `FLAG_VOICE_CONSENT`

**Latency targets:** < 800ms p95 simple queries, < 1.2s p95 full RAG.

**Privacy:** Raw audio never stored by default (SHA-256 hash only). Configurable retention via `VOICE_RAW_AUDIO_TTL_H`.

#### Native Voice-to-Voice + Voice+Vision (Phase 28)

V2 streaming engine at `WS /v2/voice/chat/stream` with dual-path routing,
token-level TTS, speculative retrieval, and parallel vision encoding.
Target: **p95 < 600ms** end-to-end (fast path) and **< 800ms** (grounded path).

```
Client (PCM16 chunks + optional JPEG camera frames)
  |
  v
V2 WebSocket Handler (voice_ws_v2.py)
  |
  v
VoiceSessionV2 (voice_stream_v2.py):
  |
  +-- Energy VAD (same as V1)
  |     +-- Emits vad_state events
  |
  +-- Streaming ASR (streaming_asr.py):
  |     +-- Sliding-window partial hypotheses
  |     +-- Token stability tracking
  |     +-- Emits partial_transcript events
  |
  +-- Speculative Prefetch (speculative_prefetch.py):
  |     +-- Starts RAG retrieval on stable ASR prefix (>= 4 tokens)
  |     +-- If final query matches prefix, reuse cached hits (100-300ms saved)
  |
  +-- [Parallel] Vision Encoder (vision/encoder.py):
  |     +-- Qwen2-VL-2B document understanding
  |     +-- EasyOCR text extraction
  |     +-- URA document classification
  |     +-- Emits vision_result event
  |
  +-- Query Planner (query_planner.py):
  |     +-- FAST path: greeting / cache hit / acknowledgement (< 400ms)
  |     +-- GROUNDED path: full RAG pipeline (< 800ms)
  |     +-- VISION path: image context + RAG
  |     +-- ESCALATE path: human handoff
  |
  +-- LLM Generation (existing service.py 21-phase pipeline)
  |
  +-- Token-Level Streaming TTS (streaming_tts.py):
        +-- CosyVoice2-0.5B flow-matching codec (primary)
        +-- First audio in 150-250ms (vs 400-800ms sentence-chunked)
        +-- WAXAL Luganda speaker embeddings for voice cloning
        +-- Falls back to Piper/edge-tts/Sunbird when unavailable
```

**New V2 modules (Phase 28):**

| Module | File | Purpose |
|--------|------|---------|
| `native_voice/streaming_tts.py` | `backend/app/` | CosyVoice2 token-level TTS with Piper fallback |
| `native_voice/streaming_asr.py` | `backend/app/` | Sliding-window partial ASR hypotheses |
| `native_voice/speculative_prefetch.py` | `backend/app/` | Background RAG retrieval on partial ASR prefix |
| `native_voice/query_planner.py` | `backend/app/` | Fast/grounded/vision/escalate path routing |
| `native_voice/voice_codec.py` | `backend/app/` | PCM/WAV/Opus conversion + audio utilities |
| `vision/encoder.py` | `backend/app/` | Qwen2-VL-2B document understanding + OCR |
| `vision/ocr.py` | `backend/app/` | EasyOCR wrapper + URA field extraction (TIN, UGX, dates) |
| `vision/document_classifier.py` | `backend/app/` | Rule-based URA document type classifier |
| `voice_stream_v2.py` | `backend/app/` | V2 session with dual-path routing + vision |
| `voice_ws_v2.py` | `backend/app/` | V2 WebSocket handler + 8 Prometheus metrics |

**V2 WebSocket protocol extensions (additive over V1):**

| Direction | Message | New in V2 |
|-----------|---------|-----------|
| Client -> Server | `{type: "image_frame"}` + binary JPEG | Yes |
| Server -> Client | `{type: "partial_transcript", text, stable_prefix}` | Yes |
| Server -> Client | `{type: "vision_result", ocr_text, doc_type, summary}` | Yes |
| Server -> Client | `{type: "session_ready", capabilities: {...}}` | Extended |
| Server -> Client | `{type: "latency_report", voice_path, speculative_prefetch_used}` | Extended |

**Feature flags:** `FLAG_NATIVE_VOICE`, `FLAG_STREAMING_TTS_V2`, `FLAG_VOICE_VISION_V2`, `FLAG_SPECULATIVE_PREFETCH`

**Backward compatibility:** V1 WebSocket at `/v1/voice/chat/stream` is unchanged. V2 falls back to V1 behaviour when native models are unavailable or flags are off.

#### Escalation and Handoff Flow

Escalation triggers when any of these conditions are met:

```
1. faithfulness_score < GROUNDING_THRESHOLD (0.3)
2. Supervisor.classify() --> AgentRoute.ESCALATE
3. response_judge.decision == "escalate"
4. escalate_to_human tool called from agentic loop

  --> _build_handoff_packet():
      - topic: classified from query content
      - priority: normal / high (based on signals)
      - sentiment: taxpayer's state at the point of transfer
      - transfer_style: warm (brief the officer first) or cold
      - turns_before_handoff: how long they had been going
      - required_details: context-specific info needed
      - contact_channels: phone, WhatsApp, web portal
      - recent_context: short preview for the queue list
      - source_list: retrieved passages with metadata

  --> _maybe_create_ticket() (if FLAG_TICKET_QUEUE=true):
      - reuses an open ticket for the same conversation if one exists
      - snapshots the FULL conversation onto the ticket (both sides,
        untruncated) — not a join, because `conversations` is purged
        after CONVERSATION_TTL_DAYS while a ticket has no TTL
      - db.create_ticket(..., transcript=..., user_id=...)
      - notify_ticket_created() posts a webhook: triage metadata only,
        never the transcript
      - on failure: handoff.ticket_persisted=false + delivery_warning,
        logged as ESCALATION LOST
      - ticket_id[:8] surfaced in ChatResponse

  See docs/context-aware-escalation.md for the full contract.

Admin endpoints for ticket management:
  GET  /v1/admin/tickets?status=open&priority=urgent&limit=50
       queue view — urgent first, then longest-waiting; no transcript
  GET  /v1/admin/tickets/stats?days=30
  GET  /v1/admin/tickets/{id}
       detail view — includes the full conversation transcript and viewers
  POST /v1/admin/tickets/{id}/presence
  GET  /v1/admin/flags
  PATCH /v1/admin/flags/{name}?enabled=
  PATCH /v1/admin/tickets/{id}  (status/assignee/note/priority)
```

#### Authentication Flow

```
Request with Authorization: Bearer <token>
  |
  v
Dev mode (APP_ENV=development, AUTH_ALG=HS256):
  Verify with AUTH_DEV_SECRET (shared secret)

Production mode (APP_ENV=production, AUTH_ALG=RS256):
  Fetch JWKS from OIDC_JWKS_URL (cached OIDC_JWKS_CACHE_TTL_S=3600)
  Lookup key by kid claim (re-fetch on miss)
  Verify signature + exp/nbf temporal claims
  Verify OIDC_ISSUER + OIDC_AUDIENCE (if set)
  |
  v
AuthContext(user_id, tenant_id, roles, consents, claims)
  |
  +-- optional_user()   --> public assistant routes; anonymous if no token
  +-- current_user()    --> auth-required aware; 401 if FLAG_AUTH_REQUIRED=true and missing token
  +-- require_user()    --> mandatory (401 if missing, used on /v1/me/*)
  +-- require_role(*)   --> role-gated (403 if wrong role, used on /v1/admin/*)
```

When `FLAG_AUTH_REQUIRED=true`, routes using `current_user()`,
`require_user()`, or `require_role()` require authentication. Public assistant
routes use `optional_user()` so anonymous chat remains available in production.
When `FLAG_MULTI_TENANT=true`, authenticated rows are scoped by `tenant_id`;
anonymous public turns use the default public tenant context and cannot access
URA account/action tools.

#### Memory System Flow

Consent-gated personalization (Phase 16, `FLAG_MEMORY_ENABLED`):

```
generate() --> _load_personalization_state(user_id)
  |
  v
Check UDPA consent: user.has_purpose("personalization")?
  NO  --> skip memory entirely
  YES --> get_memory_service().read_all(user_id)
          |
          +-- Working Memory  (30 min TTL):
          |     last_topic, last_agent_role, last_conversation_id
          |
          +-- Episodic Memory (90 day TTL):
          |     conversation summaries (2 most recent)
          |
          +-- Semantic Memory (indefinite):
                extracted user facts (taxpayer_type, industry, language)
  |
  v
Inject into LLM prompt as personalization_context
Prefill workflow slots (e.g., taxpayer_type from memory)
  |
  v
After generate():
  absorb_conversation() --> extractor + decay
  |
  v
DELETE /v1/me --> cascades erasure across all 3 memory tiers
```

#### Audit Ledger Flow

Hash-chained compliance logging (Phase 21, `FLAG_AUDIT_LEDGER`):

```
Every generate() return site --> _audit_turn()
  |
  v
Payload construction (no raw PII):
  query_sha256, reply_sha256, retrieval_mode, num_sources,
  faithfulness_score, escalation_required, model, locale,
  input_tokens, output_tokens, tool_calls, agent_route, ticket_id
  |
  v
AuditLedger.append(event_type, payload, tenant_id, user_id):
  seq = monotonic counter
  payload_hash = sha256(sorted-json(payload))
  prev_hash = last row's row_hash (or GENESIS_HASH = "0"*64)
  row_hash = sha256(prev_hash + payload_hash)
  INSERT INTO audit_events
  |
  v
Merkle Anchoring (batch):
  compute_merkle_root(batch of payload_hashes)
  Bitcoin-style: pairs of sha256, odd-level duplicates last
  INSERT INTO audit_anchors(merkle_root, first_seq, last_seq)

Verification:
  verify_chain(tenant_id) --> rewalk all rows, recompute hashes
  --> VerificationReport {valid, rows_checked, breaks[]}
```

**Run locally (with full speech pipeline):**
```bash
cd App/backend
uv pip install -r requirements.txt

# Full production startup with local LoRA adapters and speech:
WHISPER_ADAPTER_LG="../../artifacts/speech/asr/whisper_lg/final" \
WHISPER_ADAPTER_SW="../../fine-tuning/adapters/whisper-sw" \
WHISPER_ADAPTER_NYN="../../fine-tuning/adapters/whisper-nyn" \
WHISPER_DEVICE=cpu \
SPEECH_ENABLED=true \
SPEECH_MT_BACKEND=prompted \
SPEECH_MAX_CONCURRENCY=4 \
SPEECH_DEADLINE_S=120 \
LLM_MODEL="Qwen/Qwen3-8B" \
LLM_BACKEND=local \
LLM_DEVICE=auto \
LLM_LOAD_IN_4BIT=true \
LLM_TORCH_DTYPE=bfloat16 \
LORA_ADAPTER_LG="../../fine-tuning/adapters/luganda-lora" \
LORA_ADAPTER_SW="../../fine-tuning/adapters/sw-lora" \
LORA_ADAPTER_NYN="../../fine-tuning/adapters/nyn-lora" \
LORA_ADAPTER_ACH="../../fine-tuning/adapters/ach-lora" \
SUNBIRD_API_TOKEN="<your-sunbird-jwt>" \
CORS_ORIGINS="http://localhost:3300" \
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8887 --reload
```

**Minimal startup (text-only, no speech):**
```bash
SPEECH_ENABLED=false uvicorn app.main:app --reload --port 8887
```

**Key environment variables for speech/voice/translation:**

| Variable | Default | Description |
|----------|---------|-------------|
| `SPEECH_ENABLED` | `true` | Master toggle for entire speech pipeline |
| `SPEECH_ASR_BACKEND` | `auto` | `auto\|sherpa\|transformers\|mock` |
| `SPEECH_TTS_BACKEND` | `auto` | `auto\|sherpa\|piper\|mock` |
| `SPEECH_MT_BACKEND` | `prompted` | `prompted` (Qwen3) / `auto` (MADLAD+LoRA) / `mock` |
| `SPEECH_MAX_CONCURRENCY` | `4` | Speech executor thread pool size |
| `SPEECH_DEADLINE_S` | `120` | Hard wall-clock timeout per speech inference |
| `FLAG_VOICE_CONSENT` | `false` (`true` in production defaults) | Enforce explicit voice consent before ASR/voice processing |
| `WHISPER_ADAPTER_LG` | (empty) | Path to Luganda Whisper LoRA adapter |
| `WHISPER_ADAPTER_SW` | (empty) | Path to Swahili Whisper LoRA adapter |
| `WHISPER_ADAPTER_NYN` | (empty) | Path to Runyankole Whisper LoRA adapter |
| `SUNBIRD_API_TOKEN` | (empty) | JWT token for Sunbird cloud API fallback |
| `LLM_MODEL` | `Qwen/Qwen3-8B` | HuggingFace model ID for LLM |
| `LLM_TORCH_DTYPE` | `auto` | `bfloat16` / `float16` / `float32` |
| `LLM_LOAD_IN_4BIT` | `false` | Enable BitsAndBytes NF4 4-bit Qwen loading to reduce GPU memory use |
| `FLAG_NATIVE_VOICE` | `false` | Enable Phase 28 native voice-to-voice engine (V2 WebSocket) |
| `FLAG_STREAMING_TTS_V2` | `false` | Token-level streaming TTS via CosyVoice2 (falls back to Piper) |
| `FLAG_VOICE_VISION_V2` | `false` | V2 voice+vision: parallel ASR + Qwen2-VL document understanding |
| `FLAG_SPECULATIVE_PREFETCH` | `false` | Start RAG retrieval on partial ASR stable prefix |
| `COSYVOICE_MODEL` | `CosyVoice2-0.5B` | CosyVoice2 model identifier for streaming TTS |
| `COSYVOICE_DEVICE` | `cuda:0` | Device for CosyVoice2 inference |
| `VISION_MODEL` | `Qwen/Qwen2-VL-2B-Instruct` | Vision-language model for document understanding |
| `VISION_DEVICE` | `cuda:0` | Device for vision model inference |
| `OCR_BACKEND` | `auto` | `auto` / `service` / `easyocr` / `disabled`; see [`docs/local-ocr.md`](docs/local-ocr.md) |
| `OCR_SERVICE_URL` | (empty) | Local OCR sidecar URL; `auto` uses it when set |
| `OCR_SERVICE_MAX_CONCURRENT` | `2` | Concurrent OCR-sidecar calls made by the API |
| `OCR_SERVICE_TIMEOUT_SECONDS` | `6` | Per-page sidecar deadline; keep below document OCR budget |
| `OCR_SERVICE_MAX_BYTES` | `12582912` | Sidecar request-body cap |
| `OCR_SERVICE_MAX_PIXELS` | `20000000` | Sidecar Pillow decode cap |
| `OCR_INFERENCE_MAX_CONCURRENT` | `1` | Sidecar in-process OCR concurrency |
| `DOCUMENT_MAX_BYTES` | `10485760` | Maximum uploaded source file size (10 MiB) |
| `DOCUMENT_MAX_PDF_XREFS` | `20000` | Query-time PDF object-count cap |
| `DOCUMENT_MAX_PDF_PAGE_EDGE_PT` | `14400` | Max page width/height in points (200 in) |
| `DOCUMENT_MAX_IMAGE_PIXELS` | `20000000` | Pillow decode cap for uploaded images |
| `DOCUMENT_OCR_TIMEOUT_SECONDS` | `20` | Wall-clock budget for OCR across a scanned PDF |
| `DOCUMENT_MAX_PDF_RENDER_PIXELS` | `12000000` | Per-page raster cap before OCR |
| `DOCUMENT_MAX_ARCHIVE_ENTRIES` | `1000` | Max ZIP entries in an Office upload |
| `DOCUMENT_MAX_ARCHIVE_UNCOMPRESSED_BYTES` | `52428800` | Max uncompressed Office ZIP size |
| `DOCUMENT_MAX_ARCHIVE_COMPRESSION_RATIO` | `100` | Max per-entry compression ratio |
| `PDF_CORPUS_MAX_XREFS` | `250000` | Index-time handbook xref cap |
| `EXPORT_RATE_LIMIT` | `10/minute` | Rate limit for PDF exports |
| `TAX_RATES_REQUIRE_CONFIRMED` | `true` in production, else `false` | Fail closed instead of quoting a rate table still marked `provisional`; see [`docs/tax-rate-tables.md`](docs/tax-rate-tables.md) |
| `MCP_SERVER_URL_<NAMESPACE>` | (empty) | Bind an MCP namespace to a deployed server (e.g. `MCP_SERVER_URL_TAX_CALCULATOR`); unset namespaces stay in-process |
| `MCP_SERVER_TOKEN_<NAMESPACE>` | (empty) | Bearer token for that server, sent as a header — never in the URL |

See [`docs/document-processing.md`](docs/document-processing.md) for document
upload limits, binding requirements, provenance fields, and proxy deployment
requirements. See [`docs/mcp-architecture.md`](docs/mcp-architecture.md) for
MCP routing, authorization and the `mcp_tax_calculator` server, and
[`docs/tax-rate-tables.md`](docs/tax-rate-tables.md) for how fiscal rates
are versioned, dated and sourced.

### 3. Frontend (`frontend/`)

Next.js 16 + React 19 PWA with SSE streaming, voice modal, and IEEE evaluation dashboards.

**Features:**
- SSE streaming with `ReadableStream` + `requestAnimationFrame` batched rendering
- Same-origin `/api/*` proxy via `INTERNAL_API_URL` (default `http://127.0.0.1:8887`)
- Markdown rendering (bold, lists, code, citations, links) + Mermaid diagram support
- Grok-inspired glassmorphism dark design (2,283 lines CSS)
- Copy button + timestamps on every message
- Installable PWA (manifest, service worker, offline caching)
- GDPR consent banner (gates analytics tracking)
- Branded 404 page + analytics error boundary
- Zustand 5 state management with durable multi-conversation persistence
- Staff analytics panel with escalation queue / ticket visibility

**Accounts and settings:**
- `/signin` and `/signup` are the same OAuth 2.1 + PKCE redirect; sign-up adds
  the registration hints (`prompt=create`, Auth0 `screen_hint=signup`) and both
  return through `/signin/callback` (`src/lib/oidcFlow.ts`)
- Reachable from the assistant itself: header pair while signed out, sidebar
  account block (Grok-style, with the account menu and Settings), landing note
- Nothing is gated — chat, documents and voice all work signed out; an account
  adds the server-side tax profile and staff tools
- `useIdentity` treats "has a token" and "the backend accepts it" as separate
  facts, so a stale session shows as stale rather than as an empty dashboard
- Settings dialog (`src/components/settings/`) — General (theme, language),
  Voice (narration + voice), Tax profile (`/v1/me/profile`), Privacy & data
  (analytics consent, consent receipts, export/erase, local history), Account

**Voice capabilities (2026):**
- Full-screen voice modal (Grok-style pulse rings + waveform + transcript)
- Voice selection per language (24 speakers: 3 English edge-tts neural + 8
  Luganda, 5 Acholi, 5 Runyankole, 2 Swahili native Sunbird speakers, each with
  its own in-panel preview), served by `GET /v1/speech/voices`
- Real TTS via edge-tts Microsoft neural / Sunbird native voices (fallback)
- ASR via Whisper+LoRA (fine-tuned Luganda, local-first) + Sunbird cloud fallback
- Translation via Qwen3-8B prompted MT (local) + Sunbird NLLB (fallback)
- Listen button on every reply + auto-narrate toggle
- Complete voice chat compound pipeline (ASR -> MT -> LLM -> MT -> TTS)
- AudioWorklet-based streaming with ScriptProcessorNode fallback

**Evaluation dashboards (IEEE-standard):**
- RAG quality radar chart (10 metrics)
- Calibration reliability curve + coverage-accuracy plot
- Safety refusal rates by attack category
- Benchmark throughput by prompt class
- Tokenizer fertility comparison (EN vs LG)
- Confusion matrix heatmap
- Quality gates pass/fail table

**Accessibility (WCAG 2.1 AA):**
- Skip-to-content link, ARIA labels, keyboard navigation
- 44px minimum touch targets on mobile
- `@prefers-reduced-motion` disables all animations
- Focus-visible rings, semantic HTML, aria-live regions

**Key files:**
- `src/components/VoiceModal.tsx` — Voice recording UI (pulse rings + waveform)
- `src/components/Markdown.tsx` — Zero-dep markdown renderer with citation pills
- `src/components/MermaidDiagram.tsx` — Lazy Mermaid diagram renderer
- `src/components/ConsentBanner.tsx` — GDPR consent banner
- `src/components/AccountRail.tsx` — Sidebar account block (auth entry points)
- `src/components/settings/SettingsDialog.tsx` — Tabbed settings + its sections
- `src/lib/oidcFlow.ts` — Shared PKCE authorize request (sign-in and sign-up)
- `src/hooks/useIdentity.ts` — Token + `/v1/me` identity, as one state machine
- `src/services/accountApi.ts` — `/v1/me` family (profile, consents, export, erase)
- `src/app/analytics/evaluation/page.tsx` — IEEE evaluation dashboard
- `src/services/voiceService.ts` — AudioRecorder, playback, speech API wrappers
- `src/store/useChatStore.ts` — Multi-conversation Zustand state

**Run locally:**
```bash
cd App/frontend
bun install
INTERNAL_API_URL=http://127.0.0.1:8887 bun run dev --port 3300
```

**Frontend configuration for voice/speech:**

The frontend uses a same-origin `/api/*` proxy (`next.config.mjs`) to forward
all API calls to the backend. The browser never talks directly to the backend.

| Config | Location | Value |
|--------|----------|-------|
| API proxy target | `.env.local` `INTERNAL_API_URL` | `http://127.0.0.1:8887` |
| CSP `connect-src` | `next.config.mjs` | `'self'` (+ `ws:` in dev) |
| Permissions-Policy | `next.config.mjs` | `microphone=(self)` allows mic |
| Audio capture | `voiceService.ts` | PCM16 LE @ 16 kHz, MediaRecorder |
| Fetch timeout | `voiceService.ts` | 30s (60s for compound voice chat) |
| Allowed dev origins | `next.config.mjs` | `127.0.0.1`, `localhost`, LAN IP |

## Live Smoke Checks

Repeatable live verification for the backend and frontend proxy layer:

```bash
cd App
chmod +x scripts/live_smoke.sh
BACKEND_URL=http://127.0.0.1:8887 \
FRONTEND_URL=http://127.0.0.1:13000 \
./scripts/live_smoke.sh
```

Or from the frontend package:

```bash
cd App/frontend
bun run smoke:live
```

What it checks:
- `GET /health`
- `GET /ready`
- `POST /v1/chat`
- `POST /v1/chat/stream`
- `GET /`
- `GET /api/v1/speech/health`
- `POST /api/v1/chat`
- `POST /api/v1/chat/stream`

Optional env vars:
- `BACKEND_URL` — backend base URL, default `http://127.0.0.1:8887`
- `FRONTEND_URL` — frontend base URL, default `http://127.0.0.1:13000`
- `SMOKE_TIMEOUT_SECONDS` — per-request timeout, default `60`
- `SMOKE_RUN_ID` — override the generated conversation/test run suffix

## Deploy Preflight

Use the deploy preflight wrapper when you need the stack to block on
readiness plus a full live smoke gate:

```bash
cd App
chmod +x scripts/deploy_preflight.sh
BACKEND_URL=http://127.0.0.1:8887 \
FRONTEND_URL=http://127.0.0.1:13000 \
./scripts/deploy_preflight.sh
```

What it does:
- waits for backend `GET /health`
- waits for backend `GET /ready`
- waits for frontend `GET /`
- waits for frontend `GET /api/v1/speech/health`
- runs the full `scripts/live_smoke.sh` suite only after those URLs are reachable

Useful env vars:
- `PREFLIGHT_WAIT_TIMEOUT_SECONDS` — total readiness wait budget, default `180`
- `PREFLIGHT_POLL_INTERVAL_SECONDS` — polling cadence, default `2`

Convenience command from the frontend package:

```bash
cd App/frontend
bun run preflight:deploy
```

## Deployment

### Hugging Face Spaces

1. Copy `app.py` and `requirements.txt` to your Space
2. Create a Space `README.md` from the relevant App runtime notes
3. Ensure model files are in the HF Model repository

### Docker

```bash
# Build
docker build -t ura-chatbot .

# Run
docker run -p 7860:7860 ura-chatbot
```

### Docker (Frontend)

The frontend is containerised and deployed via Docker Hub (see `App/frontend/Dockerfile`).

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HF_MODEL_REPO` | Hugging Face model repository | `mpairweLandwind/ura-chatbot` |
| `HF_TOKEN` | Hugging Face API token | - |
| `API_URL` | Backend API URL | `http://localhost:8887` |
| **LLM Generation** | | |
| `LLM_MODEL` | HuggingFace model ID | `Qwen/Qwen3-8B` |
| `LLM_MODEL_REVISION` | Pin to a specific HF commit SHA (SLSA provenance) | _unset_ |
| `LLM_TRUST_REMOTE_CODE` | Allow model-defined Python (OWASP LLM03 off by default) | `false` |
| `LLM_CONTEXT_WINDOW` | Hard cap on prompt tokens (tokenizer-aware trimming) | `8192` |
| `LLM_ENABLED` | Enable LLM generation | `true` |
| `LLM_DEVICE` | Inference device (`auto`/`cpu`/`cuda`) | `auto` |
| `LLM_TORCH_DTYPE` | Tensor dtype | `auto` |
| `LLM_LOAD_IN_4BIT` | Enable BitsAndBytes NF4 4-bit loading for local Qwen | `false` |
| `LLM_TEMPERATURE` | Generation temperature | `0.2` |
| `LLM_MAX_TOKENS` | Max new tokens; compose uses `512` for complete procedural answers | `512` |
| `LLM_DEADLINE_SECONDS` | Hard wall-clock deadline per LLM call | `45` |
| `LLM_TOTAL_BUDGET_SECONDS` | Hard wall-clock cap on the *whole* local→Gemini→Workers AI chain for one request (checked before each hop, not mid-call) — keeps one degraded request from tying up a worker for the sum of every hop's own timeout | `70` |
| `LLM_MAX_CONCURRENCY` | Bounded LLM thread-pool size | `2` |
| `LLM_STRUCTURED_OUTPUT` | Emit JSON `{answer, citations, abstain}` | `false` |
| **Self-Reflection (Self-RAG)** | | |
| `SELF_REFLECT_ENABLED` | Regenerate once when faithfulness is weak | `false` |
| `SELF_REFLECT_THRESHOLD` | Faithfulness below this triggers a reflect pass | `0.4` |
| **Semantic Cache** | | |
| `CACHE_ENABLED` | Enable semantic cache | `true` |
| `CACHE_THRESHOLD` | Cosine similarity threshold | `0.92` |
| `CACHE_TTL_SECONDS` | Cache entry TTL | `3600` |
| **Corrective RAG** | | |
| `CORRECTIVE_RAG_ENABLED` | Enable corrective re-retrieval | `true` |
| `CORRECTIVE_RAG_THRESHOLD` | Min avg score before re-retrieve | `0.3` |
| **Rate Limiting** | | |
| `RATE_LIMIT` | Chat endpoint rate limit | `30/minute` |
| `SLOWAPI_STORAGE_URI` | Set to `redis://host:6379` for multi-replica rate limits | _in-process_ |
| **Retrieval** | | |
| `QDRANT_URL` | Qdrant server URL | `http://localhost:6333` |
| `DENSE_MODEL` | Embedding model | `BAAI/bge-m3` |
| **Contradiction grounding** | | |
| `ENTAILMENT_MODEL` | 3-way NLI cross-encoder, labels `[contradiction, entailment, neutral]`. Unset falls back to numeric-only, which misses semantic reversals ("optional" vs "compulsory", "annually" vs "monthly") | `cross-encoder/nli-deberta-v3-small` |
| `ENTAILMENT_CONTRADICTION_MIN` | Min contradiction probability to flag a claim | `0.6` |
| `RETRIEVER_CONTEXT_FLOOR` | Calibrated relevance below which a passage is not passed to the model. Must stay below `ABSTENTION_THRESHOLD_NORM` — this trims a result set, it does not decide whether to answer | `0.20` |
| `RETRIEVER_CONTEXT_RELATIVE_DROP` | Also drop passages this far below the best hit | `0.45` |
| `RETRIEVER_DEDUPE_THRESHOLD` | Shingle-Jaccard above which two candidates are near-duplicates | `0.9` |
| `RETRIEVER_RERANK_CHARS` | Passage characters fed to the cross-encoder | `1200` |
| `RETRIEVER_QUERY_CACHE_SIZE` | Query embeddings kept for reuse | `256` |
| **Observability** | | |
| `OTEL_ENABLED` | Enable OpenTelemetry tracing | `false` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint | `http://localhost:4317` |
| **Analytics Backend (Phase 8)** | | |
| `ANALYTICS_BACKEND` | `sqlite` (default) or `postgres` | `sqlite` |
| `POSTGRES_DSN` | psycopg DSN when using postgres | _unset_ |
| `POSTGRES_POOL_MIN` / `_MAX` | Pool bounds | `1` / `10` |
| **vLLM HTTP backend (Phase 9)** | | |
| `LLM_BACKEND` | `local` (HF Transformers) or `vllm` (HTTP) | `local` |
| `VLLM_BASE_URL` | OpenAI-compatible endpoint | `http://vllm:8001/v1` |
| `VLLM_API_KEY` | Bearer token for vLLM | `not-needed` |
| `VLLM_HTTP_TIMEOUT` | Seconds per vLLM call | `60` |
| **Caddy TLS ingress (Phase 9)** | | |
| `CADDY_DOMAIN` | Public domain for Let's Encrypt | _unset_ |
| `CADDY_ACME_EMAIL` | Contact email for ACME | _unset_ |
| **Continuous evaluation (Phase 10)** | | |
| `EVAL_SAMPLE_SIZE` | Rows per eval run | `50` |
| `EVAL_FAITHFULNESS_MIN` | SLO gate for faithfulness | `0.7` |
| `EVAL_ANSWER_REL_MIN` | SLO gate for answer relevancy | `0.6` |
| `EVAL_CONTEXT_PREC_MIN` | SLO gate for context precision | `0.6` |
| **Feature flags (Phase 11)** | | |
| `FLAG_SELF_REFLECT` | Regenerate when faithfulness is low | `false` |
| `FLAG_STRUCTURED_OUTPUT` | JSON-mode output | `false` |
| `FLAG_CORRECTIVE_RAG` | Corrective re-retrieval | `true` |
| `FLAG_SEMANTIC_CACHE` | Semantic query cache | `true` |
| `FLAG_QUERY_REWRITE` | Spell / abbrev / coreference | `true` |
| `FLAG_RERANKER` | Cross-encoder reranking | `true` |
| **Agentic flags (Phase 14–18)** | | |
| `FLAG_TOOL_USE` | Allow registered tools through the bounded agentic loop | `false` |
| `FLAG_AGENTIC_MODE` | Enable supervisor routing for tools / specialists (EN golden-set gate ≥ 0.95) | `true` |
| `FLAG_TICKET_QUEUE` | Persist escalations to the `tickets` table | `false` |
| `ESCALATION_WEBHOOK_URL` | POST target notified when an escalation ticket is created; unset disables delivery | _(unset)_ |
| `ESCALATION_WEBHOOK_TOKEN` | Bearer token for that webhook, sent as a header | _(unset)_ |
| `ESCALATION_WEBHOOK_TIMEOUT` | Webhook timeout in seconds | `5` |
| `ESCALATION_WEBHOOK_MIN_PRIORITY` | Lowest ticket priority worth notifying | `normal` |
| `ESCALATION_TEAM_<TOPIC>` | Override the team that owns a handoff topic, e.g. `ESCALATION_TEAM_CUSTOMS=border-ops` | per-topic defaults |
| `FLAG_TOOL_RAG` | Expose top-k tool schemas plus rails. A scored miss keeps rails only. Default off; `FLAG_TOOL_RAG_PERCENT` for a canary | `false` |
| `TOOL_RAG_TOP_K` | Tools selected when `FLAG_TOOL_RAG` is on (rails are always added) | `5` |
| `TOOL_MAX_CALLS_PER_TURN` | Total tool dispatches allowed in one turn | `8` |
| `TOOL_MAX_CALLS_PER_ITERATION` | Fan-out ceiling for a single generation round | `4` |
| `TOOL_MAX_CALLS_PER_TOOL` | How often one tool may run in a turn | `3` |
| `FLAG_AUTH_REQUIRED` | Reject unauthenticated private `/v1/*` routes | `false` |
| `FLAG_WORKFLOWS` | YAML-driven durable multi-step workflow guides | `true` |
| `FLAG_HANDOFF_SUMMARIES` | Structured human handoff packets + escalation metadata | `true` |
| `FLAG_MEMORY_ENABLED` | Inject consented personal memory facts into prompts | `false` |
| **Voice-first flags (Phase 23)** | | |
| `FLAG_VOICE_STREAMING` | Enable WebSocket streaming voice chat (VAD + barge-in) | `false` |
| `FLAG_VOICE_CONSENT` | Enforce voice-specific consent checks before audio processing | `false` |
| **Voice-first config (Phase 23)** | | |
| `VOICE_VAD_ENERGY_THRESHOLD` | RMS energy threshold for VAD speech detection | `0.015` |
| `VOICE_VAD_SILENCE_MS` | Silence duration (ms) before utterance end | `600` |
| `VOICE_VAD_MIN_SPEECH_MS` | Minimum speech duration (ms) to process | `250` |
| `VOICE_VAD_MAX_UTTERANCE_S` | Maximum utterance duration (seconds) | `30.0` |
| `VOICE_RAW_AUDIO_TTL_H` | Raw audio retention (hours), 0 = never store | `24` |
| `VOICE_TRANSCRIPT_TTL_DAYS` | Transcript retention (days) | `90` |
| `VOICE_STORE_RAW_AUDIO` | Store raw audio to disk (for debugging) | `false` |
| `ACCENT_CONFIDENCE_THRESHOLD` | Min confidence for accent-specific adapter | `0.7` |
| `WHISPER_ADAPTER_EN_UG_CENTRAL` | Path to Ugandan Central English LoRA adapter | _unset_ |
| `WHISPER_ADAPTER_EN_UG_EASTERN` | Path to Ugandan Eastern English LoRA adapter | _unset_ |
| `WHISPER_ADAPTER_CODE_SWITCH` | Path to code-switching LoRA adapter | _unset_ |
| `NEXT_PUBLIC_WS_URL` | WebSocket URL for voice streaming (prod) | _derived from host_ |
| **Host / HuggingFace** | | |
| `HF_HOME` | Writable HF cache location | `~/.cache/huggingface` |
| `INTERNAL_API_URL` | Next.js rewrite target (server-side) | `http://127.0.0.1:8887` |
| `NEXT_PUBLIC_API_URL` | Browser-side API URL (bake-time) | `/api` |
| **Authentication (OIDC)** | | |
| `AUTH_ALG` | JWT algorithm (`HS256` dev / `RS256` production) | `HS256` |
| `AUTH_DEV_SECRET` | Dev shared secret (MUST change for production) | `dev-insecure-change-me` |
| `OIDC_ISSUER` | Token issuer URL (checked if set) | _unset_ |
| `OIDC_AUDIENCE` | Token audience | `ura-chatbot` |
| `OIDC_JWKS_URL` | JWKS endpoint (required for RS256) | _unset_ |
| `OIDC_JWKS_CACHE_TTL_S` | JWKS cache TTL in seconds | `3600` |
| `OIDC_JWKS_TIMEOUT_S` | JWKS fetch timeout | `5` |
| `APP_ENV` | `development` or `production` (gates startup validation) | `development` |
| **LoRA Adapters** | | |
| `LORA_ADAPTER_PATH` | Single-language LoRA adapter path (backward-compat) | _unset_ |
| `LORA_ADAPTER_LG` | Luganda LoRA adapter path | _unset_ |
| `LORA_ADAPTER_SW` | Swahili LoRA adapter path | _unset_ |
| `LORA_ADAPTER_NYN` | Runyankole LoRA adapter path | _unset_ |
| `LORA_ADAPTER_ACH` | Acholi LoRA adapter path | _unset_ |
| **Speech Pipeline (local-first)** | | |
| `SPEECH_ENABLED` | Enable speech subsystem on startup | `true` |
| `SPEECH_ASR_BACKEND` | ASR backend (`auto`/`sherpa`/`transformers`/`mock`) | `auto` |
| `SPEECH_TTS_BACKEND` | TTS backend (`auto`/`sherpa`/`piper`/`mock`) | `auto` |
| `SPEECH_MT_BACKEND` | MT backend: `prompted` (Qwen3) / `auto` (MADLAD) / `mock` | `prompted` |
| `SPEECH_DEADLINE_S` | Hard deadline per speech call (seconds) | `120` |
| `SPEECH_MAX_CONCURRENCY` | Bounded speech thread-pool size | `4` |
| `SPEECH_EN_VOICE` | English TTS voice name | `en_US-lessac-medium` |
| `SPEECH_LG_VOICE` | Luganda TTS voice name | `luganda-vits-v1` |
| `WHISPER_ADAPTER_PATH` | Whisper LoRA adapter for Luganda ASR (legacy) | _unset_ |
| `WHISPER_ADAPTER_LG` | Whisper+LoRA adapter (Luganda) — primary ASR backend | `artifacts/speech/asr/whisper_lg/final` |
| `WHISPER_ADAPTER_SW` | Whisper+LoRA adapter (Swahili) | _unset_ |
| `WHISPER_ADAPTER_NYN` | Whisper+LoRA adapter (Runyankole) | _unset_ |
| `WHISPER_DEVICE` | Whisper device: `cpu`, `auto`, or `cuda[:index]`; production compose pins CPU | `cpu` |
| `SUNBIRD_API_TOKEN` | Sunbird AI cloud API JWT (cloud fallback for ASR/TTS/MT) | _unset_ |
| **Audit Ledger (Phase 21)** | | |
| `FLAG_AUDIT_LEDGER` | Enable hash-chained audit event logging | `false` |
| `FLAG_VOICE_ENABLED` | Gate mobile on-device voice UI and scoped analytics | `false` |

### Production Configuration Summary (2026)

**Fallback chain architecture (local-first):**

All speech subsystems prioritize local inference models over cloud APIs.
Sunbird AI cloud is the final fallback when local backends are unavailable.

```
ASR: Whisper+LoRA (local) -> Sherpa (local) -> faster-whisper (local) -> Sunbird (cloud)
TTS: Sherpa/Piper (local) -> edge-tts (internet) -> Sunbird (cloud)
MT:  Qwen3-8B prompted (local) -> MADLAD+LoRA (local) -> Sunbird (cloud)
LLM: Qwen3-8B (local) — always local, no cloud fallback
```

**Security headers (production):**

| Header | Backend (`main.py`) | Frontend (`next.config.mjs`) |
|--------|--------------------|-----------------------------|
| `Permissions-Policy` | `microphone=(self)` | `microphone=(self)` |
| `Content-Security-Policy` | N/A (API only) | `connect-src 'self'` |
| `X-Frame-Options` | `DENY` | `DENY` |
| `HSTS` | `max-age=63072000` | `max-age=63072000` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` | `strict-origin-when-cross-origin` |

**Port allocation:**

| Service | Port | Required |
|---------|------|----------|
| Backend (FastAPI) | 8887 | yes |
| Frontend (Next.js) | 3300 | yes |
| Qdrant | 6333 | yes |
| Redis | 6379 | optional (rate limiting) |

**Tested endpoints (2026-04-29):**

| Endpoint | Backend | Latency | Status |
|----------|---------|---------|--------|
| `GET /health` | FastAPI | <10ms | working |
| `GET /v1/speech/health` | SpeechModel | <10ms | ready |
| `POST /v1/asr` | `whisper_peft` (Whisper+LoRA) | ~14s | working |
| `POST /v1/tts` | `mock` / `edge_tts` | <1s | working |
| `POST /v1/translate` (en->lg) | `prompted_qwen3` | ~6s | working |
| `POST /v1/translate` (lg->en) | `prompted_qwen3` | ~17s | working |
| `POST /v1/chat` | Qwen3-8B RAG | ~20s | working |
| Frontend `/api/*` proxy | Next.js rewrite | +<5ms overhead | working |

### 2026 production upgrade notes

This directory was hardened in a phased upgrade to close the remaining
OWASP LLM Top 10 (2025) items and align with OpenTelemetry GenAI
semantic conventions (stable 2025). See the section below for what
changed and why.

**Phase 1 — OWASP closure**

- `trust_remote_code=False` by default in `backend/app/llm.py` (LLM03).
- New env `LLM_MODEL_REVISION` pins the HF commit SHA for reproducible
  builds and SLSA provenance.
- Retrieved passages are scrubbed for injection phrases before being
  handed to the LLM (`guardrails.scan_retrieved_text`, LLM01 indirect).
- Passages are wrapped in hash-derived spotlight markers
  `<passage id="p1-<sha8>">…</passage>` so the model cannot be tricked
  into treating passage text as instructions.
- `OutputGuard.check_prompt_leakage` redacts verbatim system-prompt
  signature phrases from responses (LLM07).
- `_build_messages` uses tokenizer-aware trimming with a configurable
  `LLM_CONTEXT_WINDOW` budget rather than a fixed 1500-character slice.

**Phase 2 — OTel GenAI 2025 alignment**

- Token accounting now uses the real Qwen tokenizer
  (`llm.count_tokens`) rather than `len(text.split())`.
- Span attributes follow the 2025 stable semconv:
  `gen_ai.operation.name`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, `rag.retrieval.num_results`.
- `X-Request-ID` is propagated into the RAG pipeline span as
  `http.request.id` for frontend↔backend correlation.
- New `trace_llm_operation` helper wraps individual LLM calls.

**Phase 3 — Resilience**

- `backend/app/resilience.py` hosts a shared `CircuitBreaker`; both
  Qdrant (retrieval) and the LLM get their own breaker.
- LLM calls run on a bounded `ThreadPoolExecutor` with a hard
  `LLM_DEADLINE_SECONDS` wall-clock deadline.
- On timeout / breaker-open / exception the pipeline gracefully
  falls back to the best-matching FAQ answer.
- The local→Gemini→Workers AI fallback chain a request may walk shares one
  `LLM_TOTAL_BUDGET_SECONDS` wall-clock budget across every hop, checked
  before starting each new hop (not mid-call). Each hop already has its own
  timeout (`LLM_DEADLINE_SECONDS` locally, `CF_HTTP_TIMEOUT` per cloud call),
  but nothing previously capped their *sum* — a single degraded request
  could otherwise hold a worker for up to ~165s (45s local + 30s Gemini +
  30s × 3 Workers AI models). On a small worker pool, a handful of
  concurrent slow requests exhausts every worker and queues every new
  request behind them until it also times out.

**Phase 4 — Structured outputs + self-reflection**

- `LLM_STRUCTURED_OUTPUT=true` switches the model to JSON-mode
  output `{answer, citations, abstain}`. Citations are validated
  against the actual retrieved passage numbers — the model cannot
  fabricate references.
- `SELF_REFLECT_ENABLED=true` turns on a single-pass Self-RAG
  reflection: when faithfulness falls below `SELF_REFLECT_THRESHOLD`,
  the pipeline regenerates the answer once with instructions to
  verify every claim against the retrieved contexts.

**Phase 5 — Frontend modernization**

- `frontend/src/app/error.tsx` — App Router error boundary with
  analytics beacon.
- `frontend/src/app/loading.tsx` — App Router Suspense fallback.
- `FeedbackButtons.tsx` adopts React 19 `useOptimistic` +
  `useTransition` so thumbs-up/down flip instantly even on slow
  networks, and roll back automatically on network failure.

**Frontend framework upgrade — Next.js 16.2.3 + React 19.2**

- `frontend/package.json` pins `next@16.2.3`, `react@19.2.0`,
  `react-dom@19.2.0`, `eslint-config-next@16.2.3`; dev deps include
  `@eslint/eslintrc@3.2.0` for flat-config compat shim.
- The removed `next lint` command is replaced with `bun run eslint .`
  in the `lint` script — Next.js 16 no longer ships lint.
- New `frontend/eslint.config.mjs` — flat-config ESLint extending
  `next/core-web-vitals` and `next/typescript` via `FlatCompat`.
- `frontend/Dockerfile` runtime image pinned to `node:20.18-slim`
  (Next.js 16 requires Node.js ≥ 20.9).
- Gap #16 fix — `backend/app/service.py::stream_llm_tokens` routes
  the SSE streaming path through the shared `_LLM_CIRCUIT` breaker
  with the same success/failure accounting as the sync path.
  `backend/app/main.py::chat_stream` calls it instead of
  `llm_module.generate_stream` directly, closing the last resilience
  gap.

**Zero-impact Next.js 16 breaking changes** (verified unused here):
`middleware.ts` → `proxy.ts`, async `params`/`searchParams`/`cookies()`/
`headers()`/`draftMode()`, AMP, `serverRuntimeConfig`,
`publicRuntimeConfig`, `experimental.ppr`, `experimental.dynamicIO`,
`next/legacy/image`, `images.domains`, parallel routes `default.js`.

**Phase 7 — Redis: distributed rate limit + semantic cache**

- `backend/app/main.py` — `Limiter(storage_uri=SLOWAPI_STORAGE_URI)`
  is used when set, so the per-IP rate bucket is shared across
  workers and replicas instead of being per-process memory.
- `backend/app/cache.py` — new `RedisSemanticCache` class +
  `create_cache()` factory.  `CACHE_BACKEND=redis` stores embeddings
  as base64 numpy blobs in Redis hashes with TTL.  Falls back to
  the in-process `SemanticCache` automatically if Redis is unreachable.
- `backend/requirements.txt` — drops the `slowapi[redis]` extra
  (which pinned `redis<4`) in favour of the `slowapi` base package
  plus `limits[redis]>=3.13` (slowapi's internal storage layer), so
  modern `redis>=5` is usable for both the cache and the rate limiter.
- `docker-compose.yml` — first-class `redis:7.4-alpine` service
  (LRU-evicting, 512 MB cap) with healthcheck.  The api service
  `depends_on` it and gets `REDIS_URL`, `SLOWAPI_STORAGE_URI` and
  `CACHE_BACKEND` wired in by default.

**Phase 8 — Postgres analytics backend (opt-in)**

- New `backend/app/postgres.py` — psycopg3 + `psycopg_pool`
  implementation mirroring the functions listed in the dispatch block
  at the bottom of `database.py` (34 of 37 public functions). Identity,
  consent, profiles, workflow sessions and tickets are all mirrored.
  **Still SQLite-only:** `export_user_data`, `delete_user_cascade` and
  `export_eval_samples`, because they cascade through memory and audit
  tables that are not mirrored yet — see the note below. Schema is
  identical for what is mirrored.
- `TestBackendParity` asserts every dispatched name exists in both
  modules and takes the same arguments; `TestTicketColumnParity`
  compares the declared columns against the declared SELECT lists; and
  `TestBackendsAgree` runs the same identity/consent/workflow sequence
  against both backends and compares the results (needs `POSTGRES_DSN`).
  A stale claim here is what let tickets, conversation logging and the
  whole consent surface silently diverge.

- **Backend-agnostic query seam.** `database.query_one`, `query_all`,
  `execute` and `execute_script` run on whichever backend is live —
  callers write `?` placeholders and get dicts back; the Postgres path
  rewrites the placeholders and reuses the pool. Modules that own their
  own tables use this instead of reaching for a raw connection, so one
  implementation of each query serves both backends.

  **No module bypasses it.** The audit ledger, semantic and episodic
  memory and the voice audit log all run on the seam and are verified
  against a real Postgres — the ledger including tamper detection.
  `test_backend_shim.py` fails if any module under `app/` reaches for
  `_get_connection()` again.

  **Every public function reaches the production backend.** A function
  gets there one of two ways: the dispatch block re-binds it to a
  Postgres mirror, or it is written against the seam and runs on both
  unchanged. `export_user_data`, `delete_user_cascade` and
  `export_eval_samples` take the second route — one implementation, no
  mirror to drift. `test_backend_shim.py` fails if any public function
  does neither.

UDPA subject access and erasure are verified end to end on Postgres:
export returns the user, profile, consents, conversations and tickets;
erasure removes all of them; and a post-erasure export comes back
empty.
- `backend/app/database.py` — dispatch block at the bottom re-binds
  the public names to `postgres.*` when `ANALYTICS_BACKEND=postgres`.
  SQLite remains the zero-config default for single-node deploys;
  Postgres is the correct choice for multi-replica deploys.
- `docker-compose.yml` — `postgres:16.6-alpine` behind
  `--profile postgres` with healthcheck and named volume.

**Phase 9 — vLLM runtime + Caddy TLS ingress**

- `backend/app/llm.py` — new `LLM_BACKEND=local|vllm` switch plus
  `_vllm_generate()` and `_vllm_generate_stream()` that call the
  OpenAI-compatible `/v1/chat/completions` endpoint that vLLM exposes.
  When `LLM_BACKEND=vllm`, no local HF model is loaded and the
  sidecar is the sole inference runtime.
- `docker-compose.yml` — `vllm/vllm-openai:v0.6.6` service behind
  `--profile vllm` with `deploy.resources.reservations.devices` for
  GPU passthrough and a `/v1/models` healthcheck.
- `docker-compose.yml` — `caddy:2.8-alpine` reverse proxy behind
  `--profile tls` with HTTP/2 + HTTP/3 and auto Let's Encrypt.
- New `Caddyfile` — HSTS, CSP, security headers, SSE-safe reverse
  proxy to `api:8000`, frontend to `frontend:3000`, JSON access logs.

**Phase 10 — Continuous evaluation (Ragas-compatible)**

- New `backend/app/evaluation.py` — `run_evaluation()` collects
  samples via the existing `db.export_review_feedback()` stream and
  computes faithfulness / answer_relevancy / context_precision.
  Uses the real Ragas library when `ragas` + `datasets` are
  installed, falls back to built-in heuristics otherwise.
- New `POST /v1/evaluate` admin endpoint in `main.py`, gated by the
  existing `INDEX_API_KEY` Bearer token.  Writes each metric into
  the in-process Prometheus store (`ura_eval_metric{name=...}`) so
  Grafana can chart evaluation scores alongside request metrics.
- CLI entry point — `python -m App.backend.app.evaluation`.  Emits
  JSON + a Prometheus text-exposition file and exits non-zero on
  regression so CI jobs can gate on it.

**Phase 11 — SLO alert rules, feature flags, embeddings, Gradio 5**

- New `monitoring/prometheus-rules.yaml` — 10 alerts across 4 groups
  (availability, latency, AI quality, resources) with SLO targets:
  p95 latency < 3 s, error rate < 1 %, faithfulness ≥ 0.5, Qdrant
  availability ≥ 99.9 %, LLM breaker never OPEN > 5 min.  Includes
  `UraFaithfulnessLow`, `UraEvalRegression`, `UraEscalationSpike`.
- New `backend/app/flags.py` — tiny env-backed feature flag registry
  with 7 canonical flags.  `flags.is_enabled("self_reflect")`
  resolves in this order: in-memory override → `FLAG_<NAME>` env
  → registry default.  `service.py` uses this alongside the legacy
  `SELF_REFLECT_ENABLED` env for back-compat.
- `backend/app/retriever.py` + `backend/app/indexer.py` —
  `DENSE_MODEL` default upgraded to `BAAI/bge-m3` (1024-dim,
  multilingual, MTEB state-of-art for free models).  **Re-index
  required** when switching collections.  See the "Host gotchas"
  section below for driver constraints.
- `App/requirements.txt` — Gradio bumped to `>=5.0.0,<6.0.0`
  (SSR, improved streaming, better accessibility).

**Phase 12 — `app.py` unified with the hardened ChatModel**

- `App/app.py` — new `_load_chat_model()` lazy singleton attempts
  `from App.backend.app.service import ChatModel`.  When successful,
  `generate_response()` delegates to the full Phase 1-11 pipeline
  (hybrid retrieval, spotlight guardrails, self-reflect, circuit
  breakers, etc.) and the Gradio UI renders its replies.  When the
  backend isn't installed (HF Spaces), the legacy classifier +
  keyword fallback runs unchanged — no deployment regressions.

**Feedback URL fix — same-origin via Next.js rewrite**

Symptom: the frontend couldn't submit feedback because every fetch
was going to `http://localhost:8000` — the Dockerfile default baked
into the client JS bundle at build time (Next.js inlines
`NEXT_PUBLIC_*` env vars during `next build`, not at runtime).

Fix applied in `frontend/next.config.mjs`:

- New `rewrites()` block proxies `/api/:path*` →
  `${INTERNAL_API_URL}/:path*` (default `http://127.0.0.1:8887`).
- CSP `connect-src` simplified to `'self'` — the browser never
  crosses origins, so no CORS and no baked host:port.
- Frontend rebuilt with `NEXT_PUBLIC_API_URL=/api` (relative).

Result: the browser does `fetch("/api/v1/feedback", …)`, Next.js
proxies it to the backend over the internal network, and the same
bundle works identically on localhost, behind Caddy, over SSH port
forwarding, and in Docker Compose — with no client-side rebuild
when the backend moves.

**Phase 13 — 2026 UI redesign (glassmorphism + Grok-inspired)**

- `frontend/src/app/globals.css` completely rewritten:
  - Animated gradient-mesh background (violet → blue → cyan blobs
    drifting on a 32 s loop).
  - Glass panels with 20-28 px backdrop-filter + subtle 1 px borders
    and gradient border glow on the primary chat shell.
  - Grok-style message layout: 36 px glass avatars, tight typography,
    violet-gradient "user" bubbles, subtle glass "assistant" bubbles
    with 8-bit noise overlay to prevent banding.
  - Pill citations inside a `<details>` with collapsible source list
    and inline faithfulness indicator (`Well grounded` / `Verify with URA`).
  - Segmented locale switch (English / Luganda) with sliding active pill.
  - Floating composer with focus-glow border, custom send/mic buttons,
    gradient primary action.
  - Custom scrollbar with gradient thumb; `prefers-reduced-motion`
    respected throughout.
  - Responsive breakpoint at 720 px collapses the hero and tightens
    padding for mobile.
- `frontend/src/app/layout.tsx` — font switched from `Space_Grotesk`
  to **Geist + Geist_Mono** from `next/font/google`, exposed as
  `--font-geist-sans` / `--font-geist-mono` CSS variables.  Added
  OpenGraph metadata and a `Viewport` export.
- `frontend/src/app/page.tsx` — refactored to use the new CSS classes
  (`locale-switch`, `composer`, `chip-grid`, `bubble-role`,
  `citations`, `panel-note`, `escalation-banner`, `grounding-ok` /
  `grounding-warn`).  Every inline `style={{...}}` block removed.
  Logic (Zustand store, SSE reader, speech recognition, feedback
  buttons) untouched.

## Host-level gotchas we hit (and how to avoid them)

These are not code bugs; they're environment traps other operators
will likely hit too.  All documented so future deploys can skip the
diagnosis phase.

1. **HF cache directory owned by root** — if `~/.cache/huggingface`
   was created by a previous `sudo` run, the unprivileged user can't
   write to it and model downloads fail with a confusing
   `PermissionError at .../models--*`.  Fix: set `HF_HOME` to a
   writable path (`~/hf-cache`) or `chown -R` the existing dir.
2. **torch CUDA runtime vs. NVIDIA driver mismatch** — `uv pip
   install torch` now pulls `torch==2.11+cu130` by default, which
   requires NVIDIA driver ≥ 540.  Hosts with driver ≤ 535 (CUDA 12.2)
   must pin an older CUDA build, e.g.:
   ```bash
   uv pip install --index-url https://download.pytorch.org/whl/cu121 \
     "torch==2.5.1" "torchvision==0.20.1"
   ```
3. **BGE-M3 blocked by CVE-2025-32434** — BGE-M3 ships a legacy
   `pytorch_model.bin`, and transformers refuses `torch.load()` on
   it unless `torch >= 2.6`.  torch 2.6+ only has cu124/cu126 wheels,
   which need driver ≥ 550.  Workarounds for driver ≤ 535:
   - Use a safetensors embedding model instead (e.g.
     `sentence-transformers/all-MiniLM-L6-v2`, 384-dim, or
     `intfloat/multilingual-e5-base`, 768-dim).  Set `DENSE_DIM`
     accordingly and re-index.
   - Or upgrade the host NVIDIA driver to ≥ 550 and reinstall torch.
4. **`NEXT_PUBLIC_*` env vars are inlined at build time** — setting
   them before `next start` does nothing.  Use the new Next.js
   rewrite pattern above to avoid ever baking a host:port into the
   client bundle.
5. **`next start` warns when `output: "standalone"`** — the warning
   is cosmetic for dev, but in production use
   `node .next/standalone/server.js` (already the Dockerfile path).
6. **Qdrant client-server version drift warning** —
   `qdrant-client 1.17.1` vs `qdrant/qdrant:1.13.3` prints a
   harmless compat warning on boot; pin both or bump together.
7. **slowapi `[redis]` extra pins redis<4** — install
   `slowapi>=0.1.9` bare and `limits[redis]>=3.13` (slowapi's
   internal storage layer) for modern `redis>=5` compatibility.

## Development

### Prerequisites

- Python 3.11+
- Node.js 20.9+ (Next.js 16 minimum) / Bun ≥ 1.1
- Trained model files in `Model/` directory

### Quick Start (2026 stack)

**Option A — minimal smoke test (SQLite, in-process cache + rate limit,
CPU-only, no build):**

```bash
# 1. One-time: create a Python venv and install backend deps
uv venv .venv --python 3.11
VIRTUAL_ENV=$PWD/.venv uv pip install -r App/backend/requirements.txt

# 2. One-time: install frontend deps
curl -fsSL https://bun.sh/install | bash     # skip if bun already installed
cd App/frontend && bun install && cd -

# 3. Start Qdrant + Redis in containers (fast — pre-built images)
docker compose up -d qdrant redis

# 4. Index the FAQs into Qdrant (uses sentence-transformers)
QDRANT_URL=http://127.0.0.1:6333 \
  .venv/bin/python -m App.backend.app.indexer --recreate --csvs-only

# 5. Run the FastAPI backend
LLM_ENABLED=false QDRANT_URL=http://127.0.0.1:6333 REDIS_URL=redis://127.0.0.1:6379/0 \
  CACHE_BACKEND=redis SLOWAPI_STORAGE_URI=redis://127.0.0.1:6379/1 \
  .venv/bin/python -m uvicorn App.backend.app.main:app --port 8887

# 6. In another terminal, run the frontend
cd App/frontend
NEXT_PUBLIC_API_URL=/api INTERNAL_API_URL=http://127.0.0.1:8887 bun run build
INTERNAL_API_URL=http://127.0.0.1:8887 PORT=13000 HOSTNAME=0.0.0.0 node .next/standalone/server.js

# 7. Optional: block on readiness + full live smoke verification
cd ..
./scripts/deploy_preflight.sh
```

Open http://localhost:13000 in a browser.  Feedback, citations, and
SSE streaming are all proxied through the `/api` rewrite so no ports
need to be exposed beyond 13000.

**Option B — one-command Docker stack with local Qwen3-8B:**

```bash
# From App/
docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml up -d --build
```

This starts Redis, Qdrant, vLLM (`Qwen/Qwen3-8B`), the FastAPI backend,
and the Next.js frontend together.  The local override serves Qwen through
vLLM on GPU 4 by default at `http://localhost:8011/v1`, points the backend at
`http://vllm:8001/v1`, exposes the backend on `http://localhost:8083`,
and exposes the frontend on `http://localhost:3032`.

To use a different free GPU, set `QWEN_GPU_ID`:

```bash
QWEN_GPU_ID=7 docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml up -d --build
```

To use another Qwen model variant by Hugging Face ID, set `QWEN_MODEL`.
The model must be available to vLLM, either downloadable from Hugging Face
or already present in `${HOME}/.cache/huggingface`:

```bash
QWEN_MODEL=Qwen/Qwen2.5-3B-Instruct QWEN_GPU_ID=4 \
  docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml up -d --build
```

For fully offline startup with a model that is already in the Hugging Face
cache, add `HF_HUB_OFFLINE=1`:

```bash
HF_HUB_OFFLINE=1 QWEN_MODEL=Qwen/Qwen2.5-3B-Instruct QWEN_GPU_ID=4 \
  docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml up -d --build
```

To use a manually downloaded model directory on the local PC, point
`QWEN_LOCAL_MODEL_DIR` at the host directory and set `QWEN_MODEL` to the
container mount path `/models/local-qwen`. The host directory should contain
the model files vLLM expects, such as `config.json`, tokenizer files, and
the model weights (`*.safetensors`):

```bash
QWEN_LOCAL_MODEL_DIR=/home/$USER/models/Qwen2.5-3B-Instruct \
QWEN_MODEL=/models/local-qwen \
QWEN_GPU_ID=4 \
  docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml up -d --build
```

Useful follow-up commands:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml ps
docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml logs -f api vllm
docker compose -f docker-compose.yml -f docker-compose.local-qwen.yml down
```

Smoke checks:

```bash
curl -sS http://127.0.0.1:8011/v1/models
curl -sS http://127.0.0.1:8083/health
curl -sS http://127.0.0.1:8083/ready
```

**Option C — manual vLLM inference (Qwen3-8B) with full voice pipeline:**

```bash
# 1. Start vLLM on a free GPU
docker run -d --name ura-vllm --gpus '"device=7"' --ipc=host \
  -p 8011:8001 -v ~/.cache/huggingface:/root/.cache/huggingface \
  vllm/vllm-openai:v0.8.5 \
  --model Qwen/Qwen3-8B --port 8001 --max-model-len 8192 \
  --enable-auto-tool-choice --tool-call-parser hermes

# 2. Start the backend (embeddings on the mapped CUDA device, LLM via vLLM HTTP)
cd App/backend
CUDA_VISIBLE_DEVICES=4 LLM_BACKEND=vllm VLLM_BASE_URL=http://localhost:8011/v1 \
  RETRIEVER_DENSE_DEVICE=cuda:0 RERANKER_DEVICE=cpu \
  FLAG_TOOL_USE=true FLAG_AGENTIC_MODE=true \
  PYTHONPATH=/path/to/FinalYearProject:/path/to/FinalYearProject/App/backend \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8009

# 3. Start the frontend (in another terminal)
cd App/frontend
INTERNAL_API_URL=http://127.0.0.1:8009 bun run dev --port 8010
```

Open http://localhost:8010. All chat, voice (ASR + TTS), agentic
tool-calling, and IEEE evaluation dashboard are proxied through
the Next.js `/api` rewrite.

**Option C — full production stack with Postgres + vLLM + Caddy TLS:**

```bash
# Put CADDY_DOMAIN / CADDY_ACME_EMAIL / POSTGRES_PASSWORD in .env
docker compose --profile postgres --profile vllm --profile tls up -d
```

Requires ports 80/443 free, a public domain pointing at the host,
and NVIDIA Container Toolkit with a GPU to spare.

### Legacy HF Spaces entry point

```bash
pip install -r requirements.txt
python app.py     # Gradio 5 on http://localhost:7860
```

When `App.backend.app.service` is importable, `app.py` delegates to
the full hardened `ChatModel` pipeline automatically.  On HF Spaces
where only the minimal classifier is shipped, the legacy keyword
fallback runs unchanged.

## Phase 14 A-D — Agentic workflows (feat/agentic-workflows)

The `feat/agentic-workflows` branch adds a **supervisor-specialist
agent runtime** on top of the Phase 1-13 RAG pipeline.  Every new
capability is gated behind a feature flag that defaults OFF —
merging the branch is a no-op on the existing request path until
an operator flips a flag.

**Phase A — Tool framework + 11 starter tools** (`App/backend/app/tools/`)
- `Tool` ABC + `ToolRegistry` with risk-tier filtering
- Tools: `calculate_vat`, `calculate_paye`, `calculate_corporation_tax`,
  `calculate_capital_gains`, `calculate_customs_duty` (all deterministic,
  reading effective-dated rate tables), `get_current_date`,
  `get_next_deadlines`, `lookup_rate`, `list_available_rates`,
  `search_ura_knowledge_base` (wraps the existing hybrid retriever),
  `escalate_to_human`
- Auto-registration via `__init__.py` import hook

**Phase B — Qwen3 tool-calling loop** (`App/backend/app/llm.py`)
- `generate_with_tools()` — bounded (`max_iterations=3`) tool-call
  loop using Qwen's native `apply_chat_template(tools=...)` format
- `_parse_tool_calls()` handles parallel calls, string-encoded
  arguments, and malformed blocks (silent skip)
- `_call_llm_agentic()` in `service.py` routes the loop through the
  shared `_LLM_CIRCUIT` breaker with 2× the sync deadline

**Phase C — Supervisor router + state machine** (`App/backend/app/agents/`)
- `Supervisor.classify()` — rule-based router, <1 ms per call
- 7 routes: `RAG` (default), `TOOLS`, `TAX_SPECIALIST`,
  `CUSTOMS_SPECIALIST`, `CLARIFY` (early-return prompt),
  `ESCALATE` (human handoff), `BLOCKED`
- `AgentRoute` enum (stable string values, logged to analytics)
- `AgentState` TypedDict threaded through the pipeline

**Phase D — Ticket queue + admin endpoints** (`App/backend/app/database.py`,
`main.py`, `tools/escalate.py`)
- New `tickets` table with status/priority/assignee/staff_note
- `escalate_to_human` tool creates tickets from within the LLM loop
- Supervisor `ESCALATE` route creates tickets when `FLAG_TICKET_QUEUE=true`
- Admin REST endpoints:
  - `GET /v1/admin/tickets?status=open&priority=urgent&limit=50` — queue
    view, ordered urgent-first then longest-waiting; omits the transcript
  - `GET /v1/admin/tickets/stats?days=30`
  - `GET /v1/admin/tickets/{id}` — detail view, includes the full
    conversation transcript captured when the ticket was raised, plus
    live `viewers`
  - `POST /v1/admin/tickets/{id}/presence` — collision heartbeat
  - `GET /v1/admin/tickets/sla?days=30` — medians plus population
    first-response / next-reply breach counts
  - `GET /v1/admin/flags` / `PATCH /v1/admin/flags/{name}` — replica
    registry; toggles are in-process and ephemeral
  - `PATCH /v1/admin/tickets/{id}` (status / assignee / note / priority /
    `officer_reply`). `officer_reply` is delivered to the **taxpayer** on
    their next turn; `staff_note` stays internal
- Staff UI at `/admin/tickets` (Next.js) — the queue in backend order
  (urgent first, then longest-waiting), the full transcript per ticket,
  live arrival via `WS /v1/admin/tickets/stream`, canned replies,
  assignment lock, and separate controls for the taxpayer-facing reply
  and the internal note
- Flags console at `/admin/flags`
- `ticket_id` surfaced in `ChatResponse` so the frontend can show
  "ticket 1234abcd" to the user

**Tests** — 153 pytest tests in `tests/agents/`, fully offline
(in-memory SQLite, no GPU/Qwen/Qdrant), **passing in 2.57 s**:

```bash
.venv/bin/python -m pytest tests/agents/ -q
```

For the full architecture — tool inventory with risk tiers,
supervisor routing table, feature flag matrix, operational
runbook, safety model, and the 3-step recipe for adding a new
tool — see:

- [`docs/AGENT_ARCHITECTURE.md`](../docs/AGENT_ARCHITECTURE.md)

## Phase 15–20 — Production hardening + UI redesign (April 2026)

**Phase 15 — Qwen3-8B fine-tuning config + vLLM backend**

- `ml/scripts/fine_tune_gemma.py` — Added `web_qwen3_8b` and `web_qwen3_4b`
  presets to `MODEL_CONFIGS`, plus `_messages_to_qwen_text()` (ChatML template)
  and `format_for_qwen()` for Qwen3-specific training data formatting.
  Model type detection recognises "qwen" and routes to the correct template.
- `App/backend/app/service.py` — Model name derived dynamically from
  `LLM_MODEL` env var instead of hardcoded `"ura-qwen2.5-3b-instruct"`.
- `.env.example` — Added `LLM_BACKEND`, `VLLM_BASE_URL`, `VLLM_API_KEY`,
  `VLLM_HTTP_TIMEOUT`, and all `FLAG_*` env vars.

**Phase 16 — Agentic mode activation + vLLM tool-calling**

- `App/backend/app/llm.py` — New `_vllm_generate_with_tools()` function
  implements the full OpenAI-compatible tool-calling loop over vLLM HTTP.
  Dispatches tool calls via `ToolRegistry.call()`, feeds results back,
  bounded by `max_iterations`. Added `_strip_thinking()` to remove
  Qwen3's `<think>` reasoning blocks from all output paths.
- `App/backend/app/flags.py` — `tool_use` stays off by default.
  `agentic_mode` defaults **on** after the English routing golden set
  holds ≥ 0.95 (`agentic_mode_gate`). Tool-calling still activates only
  when the supervisor routes to TOOLS/SPECIALIST (`force_agentic`) or
  when a deployment turns `FLAG_TOOL_USE` on.
- `App/backend/app/service.py` — Fixed `use_agentic` logic: now only
  `force_agentic` (supervisor decision), not `FLAG_TOOL_USE` alone,
  triggers the tool-calling path. This prevents double-search degradation
  on simple factual queries.

**Phase 17 — YAML workflow engine (guided task flows)**

- `App/backend/app/workflows/` — New package with 4 modules:
  - `slots.py` — Slot validators (enum, regex, boolean, text)
  - `loader.py` — YAML → `WorkflowDefinition` dataclass parser
  - `registry.py` — `WorkflowRegistry` + `WorkflowSession` runtime with
    conditional step evaluation and trigger phrase matching
  - Workflow flows now include `tin_registration`, `return_filing`,
    `objection_or_dispute`, `payment_assistance`, and
    `customs_clearance`
- `App/backend/app/flags.py` — `FLAG_WORKFLOWS` now defaults to `true`
  so high-intent task queries can be routed into guided flows by default

**Phase 18 — UI redesign (Grok-inspired, production-grade)**

*New components:*
- `VoiceModal.tsx` (202 lines) — Full-screen Grok-inspired voice recording
  modal with animated pulse rings (3-layer CSS animation), real-time
  waveform visualisation (Web Audio AnalyserNode → canvas), live transcript
  display, and Cancel (red) / Send (green) action buttons.
- `VoiceSettings.tsx` (148 lines) — Voice persona selection modal with
  5 voice options (3 English + 1 default + 1 Luganda), preview play
  buttons via TTS API, and "Active" badge on selected voice.
- `Markdown.tsx` (288 lines) — Zero-dependency markdown renderer
  supporting bold, italic, inline code, headings (h2-h4), lists, ordered
  lists, blockquotes, code blocks, horizontal rules, links (XSS-safe:
  only `https?://` allowed), and `[1]` citation reference pills
  (rendered as violet superscript badges).
- `MermaidDiagram.tsx` (140 lines) — Lazy-loaded (`React.lazy` + `Suspense`)
  Mermaid 11 diagram renderer with dark theme matching design tokens.
  SVG output sanitised (strips `<script>`, `on*` handlers, `<foreignObject>`).
- `ConsentBanner.tsx` (57 lines) — GDPR/UDPA analytics consent banner
  with Accept/Decline buttons, persists to localStorage.

*Enhanced components:*
- `ChatMessage.tsx` — Added `CopyButton` (clipboard copy with "Copied"
  checkmark feedback), `MessageTime` timestamps on every message, split
  action bar into left (copy + listen) and right (feedback + time).
  Assistant messages now render through `<Markdown />` instead of plain text.
- `Icons.tsx` — Added `GearIcon`, `CopyIcon`, `CheckIcon` (17 total).
- `ConversationRail.tsx` — `RelativeTime` component defers to client-only
  rendering via `useEffect` to prevent SSR hydration mismatch.

*New pages:*
- `not-found.tsx` — Branded 404 page with gradient heading + back link.
- `analytics/error.tsx` — Segment-level error boundary for analytics routes.
- `analytics/evaluation/page.tsx` — IEEE-standard evaluation dashboard
  with 5 sections: RAG quality radar, calibration reliability/coverage
  curves, safety refusal bar chart, benchmark throughput chart, tokenizer
  fertility comparison, confusion matrix heatmap, quality gates table.

*CSS additions (650+ lines):*
- Voice modal: pulse rings, waveform canvas, transcript panel, action buttons
- Voice settings: voice option cards, preview play, active badge
- Markdown typography: headings, lists, code blocks, blockquotes, citation pills
- Mermaid diagram container: dark panel, responsive SVG, loading/error states
- Consent banner: fixed bottom, responsive (stacks on mobile)
- Message enhancements: copy button, timestamps, split action bar
- Mobile: composer fixed dock, 44px touch targets (WCAG 2.1 AA)
- `@prefers-reduced-motion` disables all animations

**Phase 19 — Voice pipeline fix (ASR + TTS end-to-end)**

- **Root cause:** `PYTHONPATH` excluded the project root, so
  `from ml.scripts.asr.infer_asr import AsrTranscriber` raised
  `ModuleNotFoundError`. All speech services (ASR, TTS, MT, lang-ID)
  failed silently on startup.
- **ASR fix:** `ml/scripts/asr/infer_asr.py` — Transformers backend
  now falls back to `openai/whisper-small` from HuggingFace when the
  local model path doesn't exist (instead of returning `None`).
- **TTS fix:** `ml/scripts/tts/infer_tts.py` — New `_synth_edge()`
  backend using Microsoft Edge Neural TTS (0.4s latency, neural voice
  quality). Added `edge` to backend priority chain: sherpa → edge →
  piper → mock. Uses `imageio-ffmpeg` for MP3→WAV decode.
- **Speech service:** `App/backend/app/speech_service.py` — Simplified
  `_do_synthesize()` with clean priority chain instead of double-call pattern.
- **Dependencies:** Installed `edge-tts@7.2.8`, `imageio-ffmpeg@0.6.0`.
- **LLM prompt:** Added rule 12 to SYSTEM_PROMPT instructing Qwen3-8B
  to include mermaid diagrams for multi-step processes.

**Phase 20 — Security hardening + gap closure**

- **SSE robustness:** `main.py` — Added `request.is_disconnected()` check
  in SSE token loop + 15-second keepalive ping to prevent proxy timeouts.
- **XSS prevention:** Markdown links block `javascript:`, `data:`,
  `vbscript:` protocols. Mermaid SVG output sanitised before `innerHTML`.
- **GDPR compliance:** Analytics `trackEvent()` respects consent stored
  in `ura_analytics_consent` localStorage key.
- **Backend evaluation API:** New `GET /v1/evaluation/results` endpoint
  serves all pre-computed Results/ JSON files as a consolidated bundle
  for the IEEE evaluation dashboard.
- **PWA:** `manifest.json`, service worker (cache-first assets,
  network-first pages), app icons (SVG), `robots.txt`, Apple Web App
  metadata, service worker registration in layout.

**Phase 21 — Audit ledger + hash-chained compliance log**

- **Audit ledger:** Hash-chained append-only `audit_events` table with
  tamper-evident integrity. Every `generate()` outcome (blocked, cached,
  clarified, escalated, abstained, happy-path) appends an event via
  `_audit_turn()` in `service.py`. Payloads store SHA-256 hashes of
  queries and replies (no raw PII), plus metadata: retrieval_mode,
  num_sources, faithfulness_score, escalation_required, model, locale,
  input_tokens, output_tokens, tool_calls, agent_route, ticket_id.
- **Hash chain:** Each row stores `row_hash = sha256(prev_hash +
  payload_hash)`. The first row uses `GENESIS_HASH = "0" * 64`.
  Tampering with any row breaks the chain for all subsequent rows.
- **Merkle anchoring:** `compute_merkle_root()` in `audit/merkle.py`
  computes Bitcoin-style Merkle roots over batches of payload hashes.
  Roots stored in `audit_anchors` table for batch integrity proofs.
- **Verification:** `verify_chain(tenant_id)` in `audit/verifier.py`
  rewalks all rows and recomputes every hash. Returns a
  `VerificationReport` with `valid`, `rows_checked`, `breaks[]`.
- **Schema:** `audit_events` (event_id TEXT PK, event_type, tenant_id,
  user_id, payload JSON, ts REAL, seq INTEGER, prev_hash, payload_hash,
  row_hash). `audit_anchors` (anchor_id TEXT PK, tenant_id, first_seq,
  last_seq, merkle_root, created_at).
- **Feature flag:** `FLAG_AUDIT_LEDGER` (default false) gates all writes.
  Failures are swallowed — a broken audit DB never blocks a user response.
- **UDPA erasure:** Right-to-erasure writes a tombstone event so the
  hash chain remains verifiable while PII is removed.

## Current Runtime State (May 2026)

- **Model default:** `Qwen/Qwen3-8B` is the default model in code, API responses, and tracing metadata.
- **Fast Qwen runtime:** local/ngrok compose routes generation through the host `ura-vllm` service at `http://host.docker.internal:8011/v1` (`LLM_BACKEND=vllm`) instead of the slower serialized Transformers path.
- **Response budget:** compose sets `LLM_MAX_TOKENS=512` for vLLM chat calls to allow complete procedural answers. Qwen3 thinking mode is suppressed via `/no_think` system prompt tag and `chat_template_kwargs: {"enable_thinking": False}` in vLLM requests, so the full token budget goes to answer content.
- **4-bit Qwen fallback:** local Transformers can still load Qwen with BitsAndBytes NF4 when `LLM_BACKEND=local` and `LLM_LOAD_IN_4BIT=true`.
- **GPU split:** compose maps the API container to host GPU 2 (`NVIDIA_VISIBLE_DEVICES=2`) so `RETRIEVER_DENSE_DEVICE=cuda:0` uses that free mapped GPU; reranking runs on CPU (`RERANKER_DEVICE=cpu`) to avoid GPU 0 startup stalls; vLLM serves Qwen on GPU 7; Whisper remains on CPU.
- **Locale LoRA routing:** production compose mounts `../fine-tuning/adapters:/app/adapters:ro` and exposes `LORA_ADAPTER_LG`, `LORA_ADAPTER_SW`, `LORA_ADAPTER_NYN`, and `LORA_ADAPTER_ACH`; multi-adapter mode uses PEFT `set_adapter()` instead of merging.
- **Whisper GPU isolation:** Whisper adapters for `lg`, `sw`, and `nyn` are mounted from `/app/adapters`, with `WHISPER_DEVICE=cpu` so ASR does not compete with Qwen or retrieval GPU workloads.
- **Redis cache:** compose runs `redis:7.4-alpine`; the API uses `CACHE_BACKEND=redis`, `REDIS_URL=redis://redis:6379/0`, and `SLOWAPI_STORAGE_URI=redis://redis:6379/1`.
- **Anonymous public assistant:** `/v1/chat`, `/v1/chat/stream`, speech health, TTS, translation, and consented voice processing work without login. Anonymous requests use `role=public` and cannot access account/action tools.
- **Private/admin auth:** `/v1/me/*`, admin/ticket, feedback governance, analytics dashboards, metrics, evaluation exports, offline bundles, and URA account/action surfaces fail closed behind verified bearer tokens and/or staff roles.
- **Voice consent:** anonymous voice/ASR requests must send `X-Voice-Consent: true`; streaming voice sends `voice_consent_accepted=true` in `session_start`.
- **Durable thread identity:** `conversation_id` is a stable thread key across turns; it is no longer regenerated per reply.
- **Guided workflows:** the backend ships five guided flows out of the box: `tin_registration`, `return_filing`, `objection_or_dispute`, `payment_assistance`, and `customs_clearance`.
- **Workflow routing:** generic informational questions such as “How do I register for a TIN?” stay in RAG and return a direct answer; guided workflows start only when the user explicitly asks to start/proceed/continue or already has an active workflow.
- **Response cleanup:** generated and cached replies pass through the same output guard, so stale Redis entries cannot leak passage-by-passage model reasoning into the UI.
- **Procedure answer quality:** common TIN registration and return-filing how-to prompts bypass free-form LLM synthesis and return vetted FAQ-backed procedural answers, with high-priority FAQ hits and query-ranked deterministic revisions as fallback.
- **Response governance:** `ChatResponse` now carries `agent_role`, `workflow`, `handoff`, `response_judge`, `next_actions`, and `ticket_id` where applicable.
- **Streaming behavior:** `/v1/chat/stream` and `/api/v1/chat/stream` stream progressively, sanitize chunked output before emission, and support a `revision` event when the `response_judge` replaces a provisional answer.
- **Consent + personalization:** frontend analytics are consent-gated, and memory-backed personalization only activates when the deployment enables it and the user has granted consent.
- **Operational verification:** `scripts/live_smoke.sh` provides repeatable live endpoint verification, and `scripts/deploy_preflight.sh` gates deployments on readiness plus the full smoke suite.
- **Mobile chat UX:** the frontend keeps the input composer docked at the bottom with safe-area/keyboard offsets, auto-scrolls new assistant turns when the user is near the latest message, preserves manual scroll position when reviewing earlier messages, and shows a scroll-to-latest button.

**Verified ngrok anonymous smoke flow:**

```bash
curl -sS https://struttingly-nongeological-briella.ngrok-free.dev/api/health

curl -sS -X POST https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: anonymous-smoke" \
  -d '{"message":"How do I register for a TIN?","locale":"en"}'

curl -sS https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/speech/health

# Should remain protected:
curl -i https://struttingly-nongeological-briella.ngrok-free.dev/api/v1/admin/tickets/stats
```

**Reference local stack (smoke / preflight defaults):**

| Service | Port | GPU | Description |
|---------|------|-----|-------------|
| Backend (FastAPI) | 8083 container / 8887 local dev | API retrieval GPU mapping + CPU reranker | RAG retrieval, workflows, auth, speech orchestration |
| Frontend (Next.js 16) | 3032 Compose / 13000 standalone | — | PWA + `/api` proxy + consent + analytics queue |
| vLLM | 8011 | `QWEN_GPU_ID` via `docker-compose.local-qwen.yml` (default GPU 4) | Qwen/Qwen3-8B + tool-calling via OpenAI-compatible API |
| Qdrant | 6333 | CPU | dense + sparse retrieval index |
| Redis | internal 6379 | CPU | semantic cache and distributed rate-limit storage |
| ngrok | public HTTPS -> 3032 | — | silent background tunnel to the frontend; no GPU is used by ngrok |

Current latency note: if `LLM_BACKEND=local`, generation runs through the
single-process HF Transformers path and is serialized for LoRA adapter safety.
For responsive local demos, keep `ura-vllm` running on a free GPU and use
`LLM_BACKEND=vllm`. If a direct factual response approaches 30 seconds through
the frontend/ngrok path, check the API stage timings first; the usual cause is
`llm_generate`, not Redis.

## Roadmap: from FAQ chatbot to personalized tax assistant

Phases 1–13 above + Phase 14 A-D ship a hardened RAG chatbot with
an agentic runtime already in place.  For the broader journey to a
**personalized, multi-user** tax assistant with identity,
long-term memory, document uploads, workflow engines, and
proactive notifications, see the companion roadmap:

- [`docs/GAPS_AND_AGENTIC_ROADMAP.md`](../docs/GAPS_AND_AGENTIC_ROADMAP.md)

That document inventories **34 concrete gaps** across identity,
memory, actions, knowledge, agentic reasoning, evaluation, and
operations; proposes a **supervisor-specialist agent architecture**
with **12 MCP tool servers** (Phase 14 A-D ships the first 3 of
those with 11 concrete tools + the supervisor); and sketches
**Phases 14–20** of the project.  Phase 14 A-D covers the
"Tool-calling foundation + supervisor" slice.

## Links

- [GitHub Repository](https://github.com/mpairweLandwind/FinalYearProject)
- [Hugging Face Space](https://huggingface.co/spaces/mpairweLandwind/ura-chatbot)
- [URA Official Website](https://www.ura.go.ug)
- [Gap Analysis & Agentic Roadmap](../docs/GAPS_AND_AGENTIC_ROADMAP.md)


### Python API
```python
from classifier import predict_tag

result = predict_tag("How do I pay VAT?")
print(f"Tag: {result['tag']}")
print(f"Confidence: {result['confidence']:.2%}")
```

## Legacy HF Spaces Surface

For the lightweight Hugging Face Spaces deployment path, the legacy entry
surface remains:

```text
App/
├── app.py
├── classifier.py
└── requirements.txt
```

## ML Training Pipeline

The project includes a comprehensive MLOps pipeline for data preparation,
model training, evaluation, and deployment. Configuration lives in
`ml/configs/training_config.yaml`; orchestration runs via GitHub Actions
(`.github/workflows/ci-ml-pipeline.yml`).

### Data Sources

| Source | Location | Description |
|--------|----------|-------------|
| CSV FAQs | `Data/dataset/ura_*_faqs.csv` | 41 structured Q&A files covering all URA tax categories |
| PDF Handbooks | `Data/pdfs/` | 45+ URA guidance documents (VAT, TIN, customs, EFRIS, sector guides) |
| Web Crawl | `Data/crawl/` | Daily crawled content from ura.go.ug (HTML pages + discovered PDFs) |
| Luganda Parallel | `Data/TTT/` | Luganda-English sentence pairs for translation training |
| Teacher QA | `Data/teacher_qa/` | LLM-generated QA from domain expert prompts |
| Online Corpora | `Data/online_corpora/` | OPUS, JW300 for translation model training |
| Audio | `Data/lgaudio/`, `Data/speech/` | Luganda speech clips for ASR/TTS training |
| Evaluation | `Data/eval/` | `rag_eval.jsonl` (EN), `rag_eval_lg.jsonl` (LG), `redteam_corpus.jsonl` |

### Data Augmentation Pipeline (4-stage)

Orchestrated by `ml/scripts/data_augmentation.py`:

```
Stage 1 — Ingest
  Load every enabled source as validated TrainingExamples (Pydantic schema).
  Sources: CSV_FAQ, PDF_CORPUS, PDF_QA, TEACHER_QA, LUGANDA_PARALLEL,
           LUGANDA_QA, REFUSAL, RETRIEVAL, WEB_CRAWL, ONLINE_CORPUS.

Stage 2 — Normalize
  Unicode NFKC + ftfy text repair + PII redaction (emails, phones, TINs).
  Applied inside each source loader before validation.

Stage 3 — Quality
  Token-aware length filtering (8-2048 tokens), near-duplicate removal
  (cosine threshold 0.85), per-source caps (e.g. luganda_parallel=5000,
  pdf_corpus=2000), and FineWeb-Edu style quality classification.

Stage 4 — Format
  Stratified split (train/val/test) → output:
    train.messages.jsonl   (TRL-ready OpenAI ChatML format)
    val.messages.jsonl
    test.messages.jsonl
    train.parquet / val.parquet / test.parquet
    manifest.json          (provenance: git SHA + content hashes + stats)
    DATA_CARD.md           (human-readable metadata)
```

```bash
# Run the full augmentation pipeline
uv run python -m ml.scripts.data_augmentation \
  --output-dir artifacts/training_data \
  --quality-threshold 0.45 \
  --near-dup-threshold 0.85
```

### Web Crawler Pipeline

Automated via `ml/scripts/data_aug/crawler.py` and scheduled daily at
04:00 UTC (`.github/workflows/scheduled-crawl.yml`):

```
1. CDX Discovery     — query Wayback Machine CDX API for all archived ura.go.ug URLs
2. Direct Fetch      — live HTTP requests to ura.go.ug pages
3. Wayback Fetch     — exact-timestamp snapshots (fallback if direct fails)
4. Content Extract   — BeautifulSoup parsing + table extraction (structured + markdown)
5. Deduplication     — content-hash dedup to skip already-crawled pages
6. Deep-Link BFS     — follow PDFs and internal links discovered in page content
7. PDF Download      — new PDFs saved to Data/pdfs/
8. State Persist     — crawl_state.json updated and committed (max 200 pages/run)
```

### Model Fine-Tuning

**Qwen3-8B QLoRA (Luganda):**

| Parameter | Value |
|-----------|-------|
| Base model | Qwen/Qwen3-8B |
| Method | QLoRA via Unsloth/PEFT |
| LoRA rank (r) | 32 |
| LoRA alpha | 64 |
| Target modules | 7 (q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj) |
| Training data | 3,800 Luganda ChatML pairs |
| Epochs | 3 |
| Trainer | SFTTrainer (TRL) |
| Output | safetensors adapter (~349 MB), merged at inference via `LORA_ADAPTER_PATH` |

**Whisper-small LoRA (Luganda ASR):**

| Parameter | Value |
|-----------|-------|
| Base model | openai/whisper-small |
| Method | LoRA via PEFT |
| LoRA rank (r) | 16 |
| LoRA alpha | 32 |
| Target modules | q_proj, v_proj |
| Training data | 2,478 FLEURS Luganda speech clips |
| Epochs | 3 |
| Trainer | Seq2SeqTrainer |
| Output | safetensors adapter (~7 MB), merged at inference via `WHISPER_ADAPTER_LG` |

**Additional presets** in `ml/scripts/fine_tune_gemma.py`:

| Preset | Model | LoRA r | Epochs | Use Case |
|--------|-------|--------|--------|----------|
| `web_high_accuracy` | Gemma-2-2B-it | 16 | 3 | High-accuracy web deployment |
| `mobile_gemma_2b` | Gemma-2-2B-it | 8 | 5 | Mobile on-device (GGUF) |
| `mobile_offline` | Llama-3.2-1B | 8 | 5 | Lightweight offline mobile |
| `background_t5` | flan-t5-small | 8 | 10 | Background classification |

```bash
# Fine-tune Qwen3-8B with QLoRA
uv run python -m ml.scripts.fine_tune_gemma \
  --preset web_qwen3_8b \
  --data artifacts/training_data/train.messages.jsonl

# Fine-tune Whisper for Luganda ASR
uv run python -m fine-tuning.scripts.04_finetune_whisper_luganda
```

### Evaluation Pipeline

The evaluation system runs across 5 dimensions via dedicated scripts in
`ml/pipelines/`:

| Script | Metrics | Thresholds |
|--------|---------|------------|
| `evaluate.py` | Accuracy, F1 (macro/weighted), precision, recall, latency (p50/p95/p99) | accuracy >= 0.85, F1 >= 0.75 |
| `evaluate_rag.py` | Faithfulness, answer relevancy, context precision/recall, groundedness, citation accuracy | faithfulness >= 0.6, answer_relevancy >= 0.65 |
| `evaluate_speech.py` | WER, CER, RTF by language (EN/LG/SW/NYN/ACH) | WER_en <= 0.15, WER_lg <= 0.25 |
| `evaluate_mt.py` | BLEU, chrF, length ratio, hallucination rate by direction | BLEU_en_lg >= 15.0, hallucination <= 0.05 |
| `evaluate_safety.py` | Red-team refusal rate, CoT leak detection | refusal >= 0.90, CoT_leak <= 0.05 |
| `evaluate_tts.py` | Roundtrip intelligibility, RTF, speaker consistency | intelligibility >= 0.80 |
| `calibrate.py` | Expected Calibration Error (ECE), Brier score | ECE <= 0.25 |
| `audit_tokenizer.py` | Luganda fertility (tokens/word), vocabulary coverage | lg_over_en_fertility <= 1.8 |

### Production Quality Gates

Latest results from `Results/rag_quality_gates.json` (9/9 passed, score 1.0):

| Gate | Actual | Target | Status |
|------|--------|--------|--------|
| Answer Rate (%) | 100.0 | 80 | PASS |
| Avg Faithfulness | 0.93 | 0.70 | PASS |
| CoT Leak Rate (%) | 0.0 | 5 | PASS |
| Red Team Block Rate (%) | 80.0 | 80 | PASS |
| P50 Latency (s) | 2.415 | 30 | PASS |
| P90 Latency (s) | 46.233 | 60 | PASS |
| TTS Available | 1.0 | 1.0 | PASS |
| ASR Available | 1.0 | 1.0 | PASS |
| MT Available | 1.0 | 1.0 | PASS |

### CI/CD Pipeline (8-stage GitHub Actions)

Defined in `.github/workflows/ci-ml-pipeline.yml`:

| Stage | Job | Description |
|-------|-----|-------------|
| 1 | `lint-and-test` | Ruff lint + format check + pytest (current coverage ratchet >= 35%) |
| 1a | `reproducibility` | `uv pip compile` → requirements.lock with hashes |
| 1b | `governance-check` | NIST AI RMF, ISO/IEC 42001, OWASP LLM Top 10 compliance |
| 1c | `data-aug-smoke` | Schema validation, PII redaction tests, pipeline dry-run (< 2 min) |
| 2 | `data-validation` | Great Expectations patterns for CSV quality (missing ratios, duplicates) |
| 3 | `prepare-training-data` | Full data augmentation pipeline (ingest → normalize → quality → format) |
| 4 | `train` | Model training dry-run on CI (full GPU training via Kaggle) |
| 5 | `evaluate` | Classifier metrics |
| 5b | `evaluate-rag` | RAGAS faithfulness, relevancy, precision, recall |
| 5c | `mobile-export` | GGUF Q4_K_M quantization validation |
| 5d | `production-gates` | Tokenizer audit, safety eval, calibration, synthetic benchmark, model card |
| 5e | `adapter-eval` | Multilingual LoRA quality gates (ROUGE-L, BLEU-1 per language) |
| 6 | `push-model` | Push to Hugging Face Hub (`mpairweLandwind/ura-chatbot`) |
| 7 | `build-push-docker` | Docker build + Trivy security scan + push |
| 8 | `deploy-backend` | Backend deployment |

Additional scheduled workflows:
- `scheduled-crawl.yml` — daily 04:00 UTC crawl of ura.go.ug
- `scheduled-retrain.yml` — triggered on `Data/` changes
- `kaggle-training.yml` — GPU/TPU training on Kaggle (multi-stage)

### Mobile Export

| Parameter | Value |
|-----------|-------|
| Base model | google/gemma-3n-E2B-it |
| Quantization | Q4_K_M (GGUF) |
| Inference engine | MediaPipe LLM Inference API |
| Max bundle size | 1,800 MB |
| Min Android SDK | 24 |
| Min iOS version | 16.0 |

```bash
# Export mobile model
uv run python -m ml.scripts.export_mobile \
  --config ml/configs/training_config.yaml
```

### Document Indexing (RAG Knowledge Base)

```bash
# Full reindex (PDFs + CSVs → Qdrant)
uv run python -m App.backend.app.indexer --recreate

# FAQ CSVs only
uv run python -m App.backend.app.indexer --csvs-only

# PDFs only
uv run python -m App.backend.app.indexer --pdfs-only
```

## Additional Links

- **Repository**: [github.com/mpairweLandwind/FinalYearProject](https://github.com/mpairweLandwind/FinalYearProject)
- **Documentation**: [MLOps Pipeline Guide](../docs/MLOPS_PIPELINE.md)

## License

MIT License - See repository for details.
