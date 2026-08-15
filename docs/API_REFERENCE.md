# API Reference Documentation

> **API Version**: 1.4.0

## Overview

The URA Chatbot provides a FastAPI-based REST API for tax-related question classification and answering.

**Base URL**: `http://localhost:8887` (local) or `https://api.ura-chatbot.com` (production)

## Authentication

The API is open for chat and classification endpoints. The indexing endpoint requires bearer token authentication when `INDEX_API_KEY` is configured:

```http
Authorization: Bearer <INDEX_API_KEY>
```

Session tracking uses the `X-Session-ID` header (optional, client-provided).

## Endpoints

### Liveness Probe

Check if the API process is running. Does **not** depend on model availability.

```http
GET /health
```

**Response**
```json
{
  "status": "alive",
  "version": "1.0.0"
}
```

---

### Readiness Probe

Confirms the model is loaded and the service can handle requests. Returns **503** if the model is unavailable. Includes retrieval mode status (`hybrid` when Qdrant is connected, `keyword` fallback).

```http
GET /ready
```

**Response**
```json
{
  "status": "ready",
  "version": "1.1.0",
  "model_loaded": true,
  "tags_loaded": 41,
  "retrieval_mode": "hybrid"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `ready` (Qdrant connected) or `degraded` (keyword fallback) |
| `retrieval_mode` | string | `hybrid` (Qdrant dense+BM25) or `keyword` (CSV overlap fallback) |

---

### Classify Text

Classify a tax-related question into a category/tag.

```http
POST /classify
```

**Request Body**
```json
{
  "text": "How do I register for TIN?",
  "top_k": 3
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | Yes | The question to classify |
| `top_k` | integer | No | Number of top predictions (default: 1) |

**Response**
```json
{
  "predictions": [
    {
      "tag": "tin_registration",
      "confidence": 0.92,
      "label": "TIN Registration"
    },
    {
      "tag": "taxpayer_registration",
      "confidence": 0.05,
      "label": "Taxpayer Registration"
    }
  ],
  "processing_time_ms": 45
}
```

**cURL Example**
```bash
curl -X POST http://localhost:8887/classify \
  -H "Content-Type: application/json" \
  -d '{"text": "How do I register for TIN?", "top_k": 3}'
```

---

### Chat

Send a message and receive a grounded, cited answer via the hybrid RAG pipeline. Includes OWASP LLM Top 10 guardrails (prompt injection detection, PII redaction, grounding verification, calibrated abstention, and human escalation).

```http
POST /v1/chat
```

**Request Body**
```json
{
  "message": "What is the VAT rate in Uganda?",
  "conversation_id": "conv_123",
  "top_k": 4,
  "locale": "en"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | User's question (max 2000 chars) |
| `conversation_id` | string | No | For conversation continuity |
| `top_k` | integer | No | Number of passages to retrieve (1–10, default 4) |
| `locale` | string | No | ISO 639-1 locale, e.g. `en`, `lg-UG` (default `en`) |

**Response**
```json
{
  "reply": "The standard VAT rate in Uganda is 18%. Some goods and services are zero-rated or exempt.",
  "sources": ["ura_vat_faqs.csv"],
  "citations": [
    {
      "ref": "[1]",
      "source": "ura_vat_faqs.csv",
      "page": "",
      "section": "VAT",
      "passage": "The standard VAT rate in Uganda is 18%..."
    }
  ],
  "faithfulness_score": 0.92,
  "retrieval_mode": "hybrid",
  "model": "ura-qwen2.5-3b-instruct",
  "conversation_id": "conv_123",
  "locale": "en",
  "escalation_required": false,
  "escalation_reason": ""
}
```

| Field | Type | Description |
|-------|------|-------------|
| `reply` | string | Generated answer (PII-redacted, XSS-sanitized) |
| `sources` | string[] | Unique source file names |
| `citations` | Citation[] | Passage-level references with source, page, section, excerpt |
| `faithfulness_score` | float\|null | 0–1 grounding score (null if blocked/abstained) |
| `retrieval_mode` | string | `hybrid`, `keyword`, `blocked`, or `abstained` |
| `locale` | string | Locale used for response |
| `escalation_required` | boolean | Whether human review is recommended |
| `escalation_reason` | string | Reason(s) for escalation (e.g. `low_faithfulness=0.12; no_retrieval_results`) |

**Retrieval Modes**
| Mode | Meaning |
|------|---------|
| `hybrid` | Qdrant dense + BM25 sparse RRF fusion + cross-encoder reranking |
| `keyword` | Fallback keyword-overlap search (Qdrant unavailable) |
| `blocked` | Input rejected by guardrails (prompt injection detected) |
| `abstained` | Confidence too low to answer reliably |

**cURL Example**
```bash
curl -X POST http://localhost:8887/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: my-session-123" \
  -d '{"message": "What is VAT rate in Uganda?", "locale": "en"}'
```

---

### Chat (SSE Streaming)

Stream tokens progressively via Server-Sent Events. Same guardrails, retrieval, and LLM pipeline as `/v1/chat` but tokens arrive incrementally.

```http
POST /v1/chat/stream
```

**Request Body** — identical to `/v1/chat`.

**SSE Event Types**

| Event | Data | Description |
|-------|------|-------------|
| `metadata` | JSON | Sources, citations, retrieval mode, locale (sent first) |
| `token` | string | Generated text chunk (sanitized, XSS-safe) |
| `grounding` | JSON | `faithfulness_score`, `escalation_required`, `escalation_reason` |
| `done` | empty | Stream complete |
| `error` | string | Error message |

**cURL Example**
```bash
curl -N -X POST http://localhost:8887/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: my-session-123" \
  -d '{"message": "What is VAT rate in Uganda?", "locale": "en"}'
```

**JavaScript Example**
```javascript
const response = await fetch("/v1/chat/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ message: "What is VAT?", locale: "en" }),
});
const reader = response.body.getReader();
const decoder = new TextDecoder();
let currentEventType = "";

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  const text = decoder.decode(value, { stream: true });
  for (const line of text.split("\n")) {
    if (line.startsWith("event: ")) currentEventType = line.slice(7);
    else if (line.startsWith("data: ")) {
      const data = line.slice(6);
      if (currentEventType === "token") console.log(data);
      else if (currentEventType === "metadata") console.log(JSON.parse(data));
      else if (currentEventType === "grounding") console.log(JSON.parse(data));
    }
  }
}
reader.releaseLock();
```

**Rate Limiting**: Same limit as `/v1/chat` (default: 30/minute per IP, configurable via `RATE_LIMIT` env var).

---

### Trigger Re-Indexing

Rebuild the Qdrant vector index from all PDFs and FAQ CSVs. Requires `Authorization: Bearer <INDEX_API_KEY>` when configured (OWASP LLM10 — unbounded consumption protection).

```http
POST /v1/index
```

**Headers**
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | When `INDEX_API_KEY` is set | `Bearer <INDEX_API_KEY>` |

**Response**
```json
{
  "collection": "ura_knowledge_base",
  "total_documents": 1250,
  "total_upserted": 1250,
  "pdf_documents": 800,
  "csv_documents": 450,
  "retrieval_mode": "hybrid"
}
```

**cURL Example**
```bash
curl -X POST http://localhost:8887/v1/index \
  -H "Authorization: Bearer my-secret-key"
```

---

### Submit Feedback

Submit thumbs-up/down feedback on a chatbot response.

```http
POST /v1/feedback
```

**Request Body**
```json
{
  "message_id": "msg_abc123",
  "rating": "up",
  "comment": "Very helpful answer",
  "session_id": "session_xyz",
  "user_query": "What is the VAT rate?",
  "bot_reply": "The standard VAT rate in Uganda is 18%."
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message_id` | string | Yes | ID of the bot message being rated |
| `rating` | string | Yes | `up` or `down` |
| `comment` | string | No | Optional text feedback (max 1000 chars) |
| `session_id` | string | No | Client session identifier |
| `user_query` | string | No | Original question (PII-redacted before storage) |
| `bot_reply` | string | No | Bot response that was rated (PII-redacted before storage) |

**Response**
```json
{
  "id": "fb_12345",
  "message_id": "msg_abc123",
  "rating": "up",
  "created_at": 1710072000.0
}
```

---

### Update Feedback Comment

Add a follow-up comment to existing feedback (avoids duplicate entries).

```http
PATCH /v1/feedback/{message_id}/comment
```

**Request Body**
```json
{
  "comment": "Actually, I had a follow-up question about exemptions."
}
```

**Response**
```json
{
  "status": "ok",
  "message_id": "msg_abc123"
}
```

---

### Feedback Summary

Aggregated feedback statistics for the specified period.

```http
GET /v1/feedback/summary?days=30
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | integer | No | Period in days (1–365, default 30) |

**Response**
```json
{
  "period_days": 30,
  "total": 150,
  "thumbs_up": 120,
  "thumbs_down": 30,
  "satisfaction_pct": 80.0,
  "recent": []
}
```

---

### Track Analytics Event

Track a client-side analytics event (e.g., page views, button clicks).

```http
POST /v1/analytics/event
```

**Request Body**
```json
{
  "event_type": "page_view",
  "event_data": {"page": "/chat"},
  "session_id": "session_xyz"
}
```

**Response**
```json
{
  "status": "ok"
}
```

---

### Analytics Dashboard

Comprehensive analytics dashboard data including uptime, request counters, session stats, conversation stats, and feedback summary.

```http
GET /v1/analytics/dashboard?days=30
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | integer | No | Period in days (1–365, default 30) |

**Response**
```json
{
  "uptime_seconds": 86400.5,
  "requests": {
    "counters": {},
    "latency": {}
  },
  "chat": {
    "event_counts": {}
  },
  "sessions": {},
  "conversations": {},
  "feedback": {}
}
```

---

### Prometheus Metrics

Prometheus-compatible metrics endpoint for scraping by monitoring systems.

```http
GET /metrics
```

**Response** (`text/plain; version=0.0.4`)
```
# HELP requests_total Total HTTP requests
# TYPE requests_total counter
requests_total{method="POST",path="/v1/chat"} 1234
...
```

---

### Get Tags

List all available classification tags.

```http
GET /tags
```

**Response**
```json
{
  "tags": [
    {
      "id": "vat",
      "name": "Value Added Tax",
      "description": "Questions about VAT registration, rates, and filing"
    },
    {
      "id": "tin_registration",
      "name": "TIN Registration",
      "description": "Questions about obtaining a Tax Identification Number"
    }
  ],
  "total": 41
}
```

---

### Get FAQ

Retrieve FAQ content for a specific tag.

```http
GET /faq/{tag}
```

**Parameters**
| Parameter | Type | Description |
|-----------|------|-------------|
| `tag` | string | The tag/category ID |

**Response**
```json
{
  "tag": "vat",
  "faqs": [
    {
      "question": "What is VAT?",
      "answer": "VAT (Value Added Tax) is a consumption tax...",
      "source": "ura_vat_faqs.csv"
    }
  ],
  "total": 25
}
```

---

### Batch Classify

Classify multiple texts in a single request.

```http
POST /classify/batch
```

**Request Body**
```json
{
  "texts": [
    "How do I register for TIN?",
    "What is the VAT rate?",
    "How to file annual returns?"
  ]
}
```

**Response**
```json
{
  "results": [
    {
      "text": "How do I register for TIN?",
      "tag": "tin_registration",
      "confidence": 0.92
    },
    {
      "text": "What is the VAT rate?",
      "tag": "vat",
      "confidence": 0.95
    },
    {
      "text": "How to file annual returns?",
      "tag": "annual_returns",
      "confidence": 0.88
    }
  ],
  "processing_time_ms": 120
}
```

---

## Speech Endpoints (2026)

Full bilingual speech pipeline for Luganda and English. Requires
`SPEECH_ENABLED=true` on the backend. All speech endpoints return **503**
when the speech pipeline is disabled or failed to initialise.

Rate limit: `30/minute` per IP (configurable via `RATE_LIMIT` env var).

---

### Speech Health Check

```http
GET /v1/speech/health
```

**Response**
```json
{
  "status": "ready",
  "enabled": true,
  "asr_backend": "auto",
  "tts_backend": "auto",
  "mt_backend": "auto"
}
```

Always returns 200 (even when speech is unavailable). Check `status` field.

---

### Transcribe Audio (ASR)

```http
POST /v1/asr?sample_rate=16000&language=en
Content-Type: application/octet-stream

<raw PCM16 little-endian bytes, mono channel>
```

| Query Param | Type | Default | Validation |
|---|---|---|---|
| `sample_rate` | int | 16000 | 8000-48000 |
| `language` | string | (auto-detect) | ISO 639-1, e.g. `en`, `lg` |

**Limits:** Max 16 MiB audio body (~2 min at 16 kHz int16).

**Response**
```json
{
  "text": "What is the VAT rate?",
  "language": "en",
  "duration_s": 2.1,
  "latency_s": 0.85,
  "rtf": 0.4,
  "backend": "mock",
  "error": null
}
```

---

### Synthesize Audio (TTS)

```http
POST /v1/tts
Content-Type: application/json
```

**Request**
```json
{
  "text": "The current VAT rate is 18 percent.",
  "language": "en",
  "voice": "en_US-lessac-medium",
  "streaming": false
}
```

| Field | Type | Default | Validation |
|---|---|---|---|
| `text` | string | (required) | 1-4000 chars |
| `language` | string | `"en"` | ISO 639-1 |
| `voice` | string | (auto by language) | `[a-zA-Z0-9_-]{1,64}` — see below |
| `streaming` | bool | `false` | Reserved for future use |

`voice` names a speaker **for that language**. For English it is an edge-tts
voice name (`en-US-AriaNeural`); for the Ugandan languages it is a Sunbird
catalog tag (`waxal_lug_0004`), validated against `language` — a tag belonging
to another language is replaced with that language's default rather than
forwarded, since orpheus would otherwise reject it or synthesise the wrong
language. English is validated the same way, against the edge voices this
deployment offers; a name outside that list falls back to `SPEECH_EN_EDGE_VOICE`.
A Sunbird tag is never forwarded to edge-tts — edge has no such speaker and the
call would return no audio, losing the fallback. List the choices with
`GET /v1/speech/voices`; the response's `voice` field reports the speaker
actually used, so a caller can tell a honoured pick from a fallback.

---

### List Narration Voices

```http
GET /v1/speech/voices
```

Serves the per-language catalog a client should build its picker from, rather
than hardcoding one: only the backend knows whether this deployment can reach
Sunbird, and an unreachable voice would be offered and then silently replaced by
an English fallback.

**Response**
```json
{
  "sunbird_configured": true,
  "voices": {
    "en": [
      {"id": "en-US-AriaNeural", "provider": "edge_tts", "native": false, "default": true, "available": true}
    ],
    "lg": [
      {"id": "salt_lug_0001", "provider": "sunbird", "native": true, "default": true, "available": true},
      {"id": "waxal_lug_0004", "provider": "sunbird", "native": true, "default": false, "available": true}
    ]
  }
}
```

| Field | Meaning |
|---|---|
| `default` | The speaker used when a caller names none. Exactly one per language — the head of the list, which is what the synthesis chain reaches first. |
| `native` | A native speaker of that language. False for English's Sunbird voice: Sunbird has no native English speaker, and that tag is the last resort edge-tts exists to avoid. |
| `available` | False when this deployment cannot reach the provider (e.g. Sunbird voices with no API token). |

**Response**
```json
{
  "sample_rate": 22050,
  "num_samples": 46305,
  "duration_s": 2.1,
  "latency_s": 0.42,
  "backend": "mock",
  "voice": "en_US-lessac-medium",
  "audio_base64": "UklGR...",
  "error": null
}
```

The `audio_base64` field contains a base64-encoded WAV file (PCM16 mono).

---

### Translate Text (MT)

```http
POST /v1/translate
Content-Type: application/json
```

**Request**
```json
{
  "text": "What is income tax?",
  "source_lang": "en",
  "target_lang": "lg"
}
```

| Field | Type | Default | Validation |
|---|---|---|---|
| `text` | string | (required) | 1-4000 chars |
| `source_lang` | string | `"en"` | ISO 639-1 |
| `target_lang` | string | `"lg"` | ISO 639-1 |

Same-language passthrough: if `source_lang == target_lang`, returns the
original text with `backend: "passthrough"` and `latency_s: 0.0`.

**Response**
```json
{
  "text": "Omusolo gw'ensimbi ki?",
  "source_lang": "en",
  "target_lang": "lg",
  "latency_s": 0.31,
  "backend": "mock",
  "error": null
}
```

---

### Voice Chat (Compound Pipeline)

Full round-trip: audio in -> ASR -> [MT] -> LLM -> [MT] -> TTS -> audio out.
This is the primary endpoint for voice mode in the web client.

```http
POST /v1/voice/chat?language=en&sample_rate=16000&tts_enabled=true&top_k=4
Content-Type: application/octet-stream
X-Session-ID: <session-id>

<raw PCM16 little-endian bytes, mono channel>
```

| Query Param | Type | Default | Validation |
|---|---|---|---|
| `language` | string | `"en"` | ISO 639-1 (`en` or `lg`) |
| `sample_rate` | int | `16000` | 8000-48000 |
| `tts_enabled` | bool | `true` | Whether to synthesize reply audio |
| `top_k` | int | `4` | 1-10 (RAG retrieval depth) |
| `voice` | string | (auto by language) | `[a-zA-Z0-9_-]{1,64}` |
| `conversation_id` | string | (none) | `[a-zA-Z0-9_-]{1,64}` |

**Limits:** Max 16 MiB audio body.

**Response**
```json
{
  "transcript": "What is the VAT rate?",
  "transcript_language": "en",
  "reply": "The current VAT rate in Uganda is 18%.",
  "reply_audio_base64": "UklGR...",
  "sample_rate": 22050,
  "duration_s": 3.2,
  "sources": ["URA_VAT_Guide.pdf"],
  "citations": [
    {
      "ref": "[1]",
      "source": "URA_VAT_Guide.pdf",
      "page": "3",
      "section": "VAT Rates",
      "passage": "The standard VAT rate is 18%..."
    }
  ],
  "faithfulness_score": 0.92,
  "retrieval_mode": "hybrid",
  "asr_latency_s": 0.85,
  "mt_latency_s": 0.0,
  "llm_latency_s": 1.2,
  "tts_latency_s": 0.42,
  "total_latency_s": 2.47,
  "asr_backend": "mock",
  "tts_backend": "mock",
  "mt_backend": "",
  "error": null
}
```

**Error handling:** The `error` field is `null` on success. On partial
failure (e.g. MT unavailable but LLM still works), it contains a
semicolon-separated list of per-stage errors such as
`"MT(lg->en): unavailable; TTS: circuit open"`. The `transcript` and
`reply` fields are still populated on partial failure.

**Metrics tracked:** `speech_asr_total`, `speech_mt_total`, `speech_tts_total`
(counters), `speech_voice_chat_latency_s` (histogram), plus per-stage
latency histograms and error counters.

---

### Streaming Voice Chat (Phase 23 — WebSocket)

```
WebSocket /v1/voice/chat/stream
```

Real-time duplex voice chat with VAD (Voice Activity Detection) and
barge-in support. Gated by `FLAG_VOICE_STREAMING=true`.

**Protocol:**

1. Client opens WebSocket, sends a JSON config frame:
```json
{
  "type": "session_start",
  "language": "en",
  "voice": null,
  "conversation_id": null,
  "sample_rate": 16000,
  "vad_sensitivity": "medium",
  "tts_enabled": true,
  "top_k": 4
}
```
2. Server responds with `{"type": "session_ready", "session_id": "..."}`.
3. Client streams binary PCM16 LE mono audio frames (recommended 20ms = 640 bytes at 16kHz).
4. Server runs energy-based VAD with hysteresis — emits `vad_state` on transitions.
5. On utterance end: ASR → [MT lg→en] → LLM (RAG) → [MT en→lg] → sentence-chunked TTS.
6. Server streams back: `transcript_final`, `reply_text` (per sentence), binary TTS audio chunks, `reply_meta`, `latency_report`.
7. Client can send `{"type": "barge_in"}` at any time to interrupt TTS playback.
8. Clean close via `{"type": "session_end"}`.

**Server → Client events:**

| Event | Format | Description |
|-------|--------|-------------|
| `session_ready` | JSON | Session ID assigned |
| `vad_state` | JSON | `{speaking: bool}` — VAD state changes |
| `transcript_final` | JSON | Final ASR transcript with language, latency, backend |
| `audio_start` | JSON | `{sample_rate: int}` — precedes TTS binary frames |
| *(binary)* | Binary | TTS audio chunks (PCM16 LE or WAV) |
| `audio_end` | JSON | TTS playback complete |
| `reply_text` | JSON | Text of each TTS sentence with chunk_index |
| `mt_degraded` | JSON | `{direction: "lg-en"\|"en-lg", detail}` — translation unavailable after retry; the pipeline continues honestly (inbound: original text + real locale to the LLM; outbound: English reply spoken by the English voice) |
| `tts_degraded` | JSON | `{detail}` — every sentence failed to synthesize; the turn is text-only |
| `reply_meta` | JSON | Sources, citations, faithfulness_score, conversation_id, reply_language, mt_degraded[] |
| `latency_report` | JSON | Per-stage timing: asr_ms, mt_ms, llm_ms, tts_first_chunk_ms, total_ms |
| `error` | JSON | `{detail, recoverable, stage?}` — recoverable errors keep connection open; the LLM stage is bounded by `VOICE_LLM_DEADLINE_S` (default 45s) |

**Latency targets:** < 800ms p95 for simple queries, < 1.2s p95 for full RAG.

**Metrics tracked:** `voice_ws_connections_total`, `voice_ws_active_connections` (gauge),
`voice_ws_session_duration_seconds`, `voice_stream_asr_latency_seconds`,
`voice_stream_tts_first_chunk_seconds`, `voice_stream_total_latency_seconds`,
`voice_barge_in_total`, `voice_vad_utterances_total`.

---

### Voice Audit Log (Phase 23 — Admin)

```http
GET /v1/admin/voice_audit?days=30&limit=100
```

Voice-specific audit trail for regulatory compliance (NDPA 2019).
Requires admin auth.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `user_id` | string | No | Filter by user |
| `session_id` | string | No | Filter by voice session |
| `days` | int | No | Look-back period (default 30) |
| `limit` | int | No | Max entries (default 100, max 500) |

**Response**
```json
{
  "entries": [
    {
      "id": "uuid",
      "user_id": "user123",
      "session_id": "ws-session-uuid",
      "event_type": "recording_end",
      "metadata": {"duration_s": 3.2, "language": "en"},
      "audio_hash": "sha256...",
      "tenant_id": "default",
      "created_at": 1714300000.0
    }
  ],
  "stats": {
    "period_days": 30,
    "total_events": 142,
    "unique_sessions": 38,
    "unique_users": 12,
    "events_by_type": {"session_start": 38, "recording_end": 38, ...}
  }
}
```

**Event types:** `recording_start`, `recording_end`, `transcript_stored`,
`audio_deleted`, `consent_checked`, `consent_denied`, `retention_cleanup`,
`session_start`, `session_end`, `barge_in`.

---

### Admin Ticket Queue (Phase 14-D)

CRUD endpoints for the escalation ticket queue. All admin endpoints require operator-level auth.

---

#### List Escalation Tickets

```http
GET /v1/admin/tickets?status=open&limit=20&offset=0
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `status` | string | No | Filter by status (`open`, `in_progress`, `resolved`, `closed`) |
| `limit` | integer | No | Page size (default 20) |
| `offset` | integer | No | Pagination offset (default 0) |

**Response**
```json
{
  "tickets": [],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

---

#### Aggregate Ticket Statistics

```http
GET /v1/admin/tickets/stats?days=30
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `days` | integer | No | Period in days (default 30) |

**Response**
```json
{
  "total": 0,
  "by_status": {},
  "by_priority": {},
  "avg_resolution_hours": 0.0
}
```

---

#### Fetch Single Ticket

```http
GET /v1/admin/tickets/{ticket_id}
```

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `ticket_id` | string | Yes | Ticket identifier |

**Response**: Full ticket object with conversation context, assignee, status, priority, and staff notes.

---

#### Update Ticket

```http
PATCH /v1/admin/tickets/{ticket_id}
```

**Request Body**
```json
{
  "status": "in_progress",
  "assignee": "staff@ura.go.ug",
  "staff_note": "Investigating TIN mismatch.",
  "priority": "high"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `status` | string | No | New status (`open`, `in_progress`, `resolved`, `closed`) |
| `assignee` | string | No | Staff email or identifier |
| `staff_note` | string | No | Internal note (max 2000 chars) |
| `priority` | string | No | `low`, `medium`, `high`, `critical` |

**Response**: Updated ticket object.

---

### Identity & Profile (Phase 14)

User identity, profile preferences, and UDPA 2019 data-subject rights. All endpoints under `/v1/me` require JWT authentication unless otherwise noted.

---

#### Get Auth Context

```http
GET /v1/me
```

Returns the current authentication context. Works for both anonymous and authenticated users.

**Response**
```json
{
  "authenticated": false,
  "sub": null,
  "roles": ["anonymous"]
}
```

---

#### Get User Profile

```http
GET /v1/me/profile
```

**Requires auth.** Returns the user's profile preferences.

**Response**
```json
{
  "sub": "user-123",
  "taxpayer_type": "individual",
  "industry": "retail",
  "primary_language": "en",
  "detail_level": "standard"
}
```

---

#### Update User Profile

```http
PUT /v1/me/profile
```

**Request Body**
```json
{
  "taxpayer_type": "business",
  "industry": "agriculture",
  "primary_language": "lg",
  "detail_level": "detailed"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `taxpayer_type` | string | No | `individual`, `business`, `ngo` |
| `industry` | string | No | Industry sector |
| `primary_language` | string | No | ISO 639-1 locale |
| `detail_level` | string | No | `brief`, `standard`, `detailed` |

**Response**: Updated profile object.

---

#### List Consent Receipts

```http
GET /v1/me/consents
```

**Requires auth.** Returns active consent receipts.

**Response**
```json
{
  "consents": [
    {
      "purpose": "personalization",
      "legal_basis": "consent",
      "granted_at": "2026-04-01T10:00:00Z"
    }
  ]
}
```

---

#### Grant Consent

```http
POST /v1/me/consents/grant
```

**Request Body**
```json
{
  "purposes": ["personalization", "analytics"],
  "legal_basis": "consent"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `purposes` | string[] | Yes | List of purposes to consent to |
| `legal_basis` | string | Yes | Legal basis (`consent`, `legitimate_interest`, `contract`) |

**Response**: Created consent receipt(s).

---

#### Withdraw Consent

```http
POST /v1/me/consents/withdraw
```

**Request Body**
```json
{
  "purposes": ["personalization"]
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `purposes` | string[] | Yes | List of purposes to withdraw consent for |

**Response**: Confirmation with updated consent state.

---

#### Data Portability Export

```http
GET /v1/me/export
```

**Requires auth.** UDPA 2019 data-portability export. Returns a ZIP archive containing the user's conversations, feedback, profile, and consent records.

**Response**: `application/zip` binary stream.

---

#### Right to Erasure

```http
DELETE /v1/me
```

**Requires auth.** UDPA 2019 right-to-erasure. Permanently deletes the user's profile, conversations, feedback, and consent records.

**Response**
```json
{
  "status": "erased",
  "sub": "user-123"
}
```

---

### Export (Phase 14)

Document export endpoints for generating branded PDF and image artifacts.

---

#### Export Conversation as PDF

```http
POST /v1/export/conversation
```

**Request Body**
```json
{
  "conversation_id": "conv_123"
}
```

**Response**: `application/pdf` binary stream (branded URA PDF).

---

#### Export Tax Summary as PDF

```http
POST /v1/export/tax-summary
```

**Request Body**
```json
{
  "conversation_id": "conv_123"
}
```

**Response**: `application/pdf` binary stream (branded tax calculation summary).

---

#### Export Artifacts (IEEE-standard Figures/Tables)

```http
POST /v1/export/artifacts
```

Generates IEEE-standard PNG figures and tables for project reports.

**Response**: `application/zip` binary stream containing PNG artifacts.

---

### Document Attachments

Chat attachment endpoints: upload a document for analysis, ground chat
answers on it, and download a branded PDF analysis report. Documents are
held in ephemeral container storage shared across the app's worker
processes (TTL ~2 h, dies with the container) and are bound to the
uploading `X-Session-ID`; they are never written to the analytics DB.

---

#### Analyze Uploaded Document

```http
POST /v1/documents/analyze
```

`multipart/form-data` with a single `file` part. Supported: PDF, DOCX,
XLSX/XLSM, CSV, TXT, and images (PNG/JPEG/WebP/BMP/TIFF, OCR best-effort).
Max 10 MB. Extracts text and tables, classifies the document against the
URA taxonomy (receipt, tin_card, assessment, customs_declaration,
filing_form, invoice, generic), and pulls TINs, UGX amounts, dates, and
reference numbers.

**Response** `200`
```json
{
  "document_id": "3f2a…(32 hex)",
  "filename": "receipt.pdf",
  "kind": "pdf",
  "doc_type": "receipt",
  "confidence": 0.92,
  "fields": {"tins": ["1001234567"], "amounts": ["UGX 1,250,000"], "dates": [], "references": []},
  "tables": [],
  "text_preview": "…",
  "summary": "Payment receipt (92% classification confidence). …",
  "warnings": [],
  "expires_in_seconds": 7200
}
```

Errors: `413` over size limit, `415` unsupported type, `422` empty/missing file.

To ground a chat turn on the document, pass the id in the chat request:
`{"message": "…", "attachment_ids": ["<document_id>"]}` (max 3, both
`/v1/chat` and `/v1/chat/stream`). Attachment turns bypass the semantic
cache, clarification, and abstention; the extracted content is injected as
top-priority grounded passages (same prompt-injection scrubbing as
retrieved passages) and appears in `sources` as `attached:<filename>`.

---

#### Download Document Analysis Report

```http
GET /v1/documents/{document_id}/report
```

Requires the same `X-Session-ID` the document was uploaded with.

**Response**: `application/pdf` binary stream (branded analysis report:
classification, extracted fields, table totals, summary, content excerpt).
`404` when the document is unknown, expired, or session-mismatched.

---

### Evaluation

RAG quality evaluation endpoints. The evaluate endpoint is operator-only.

---

#### Run RAG Evaluation

```http
POST /v1/evaluate
```

Run the RAG evaluation harness against the current knowledge base. Requires `Authorization: Bearer <INDEX_API_KEY>`.

**Headers**
| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | Yes | `Bearer <INDEX_API_KEY>` |

**Response**
```json
{
  "status": "complete",
  "metrics": {
    "faithfulness": 0.0,
    "answer_relevancy": 0.0,
    "context_precision": 0.0,
    "context_recall": 0.0
  }
}
```

---

#### Get Evaluation Results

```http
GET /v1/evaluation/results
```

Serve pre-computed evaluation metrics from the most recent evaluation run.

**Response**
```json
{
  "timestamp": "2026-04-28T12:00:00Z",
  "metrics": {},
  "quality_gates": {}
}
```

---

## Error Responses

All errors follow this format:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Text field is required",
    "details": {
      "field": "text",
      "constraint": "required"
    }
  }
}
```

### Error Codes

| Code | HTTP Status | Description |
|------|-------------|-------------|
| `VALIDATION_ERROR` | 400 | Invalid request parameters |
| `NOT_FOUND` | 404 | Resource not found |
| `MODEL_ERROR` | 500 | Model inference failed |
| `INTERNAL_ERROR` | 500 | Server error |

---

## Data Models

### ClassificationRequest
```python
class ClassificationRequest(BaseModel):
    text: str
    top_k: int = 1
```

### ClassificationResponse
```python
class ClassificationResponse(BaseModel):
    predictions: List[Prediction]
    processing_time_ms: float
```

### ChatRequest
```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: str | None = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    top_k: int = Field(4, ge=1, le=10)
    locale: str = Field("en", pattern=r"^[a-z]{2}(-[A-Z]{2})?$")
```

### Citation
```python
class Citation(BaseModel):
    ref: str          # e.g. "[1]"
    source: str       # source file name
    page: str = ""    # page number (PDFs)
    section: str = "" # section or heading title
    passage: str = "" # relevant passage excerpt (max 500 chars)
```

### ChatResponse
```python
class ChatResponse(BaseModel):
    reply: str
    sources: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    faithfulness_score: float | None = None
    retrieval_mode: str = "keyword"  # hybrid | keyword | blocked | abstained
    model: str = "ura-qwen2.5-3b-instruct"
    conversation_id: str | None = None
    locale: str = "en"
    escalation_required: bool = False
    escalation_reason: str = ""
```

### FeedbackRequest
```python
class FeedbackRequest(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=128)
    rating: str = Field(..., pattern=r"^(up|down)$")
    comment: str = Field("", max_length=1000)
    session_id: str | None = Field(None, max_length=128)
    user_query: str = Field("", max_length=2000)
    bot_reply: str = Field("", max_length=5000)
```

### FeedbackResponse
```python
class FeedbackResponse(BaseModel):
    id: str
    message_id: str
    rating: str
    created_at: float
```

### FeedbackSummary
```python
class FeedbackSummary(BaseModel):
    period_days: int
    total: int
    thumbs_up: int
    thumbs_down: int
    satisfaction_pct: float
    recent: list[dict]
```

### AnalyticsEvent
```python
class AnalyticsEvent(BaseModel):
    event_type: str = Field(..., min_length=1, max_length=100)
    event_data: dict = Field(default_factory=dict)
    session_id: str | None = Field(None, max_length=128)
```

### AnalyticsDashboard
```python
class AnalyticsDashboard(BaseModel):
    uptime_seconds: float
    requests: dict
    chat: dict
    sessions: dict
    conversations: dict
    feedback: dict
```

### HealthResponse
```python
class HealthResponse(BaseModel):
    status: str        # "ready" or "degraded"
    version: str
    model_loaded: bool
    tags_loaded: int = 0
    retrieval_mode: str = "keyword"  # hybrid | keyword
```

### SynthesizeRequest
```python
class SynthesizeRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    voice: str | None = Field(None, pattern=r"^[a-zA-Z0-9_\-]{1,64}$")
    language: str = Field("en", pattern=r"^[a-z]{2}$")
    streaming: bool = False
```

### SynthesizeResponse
```python
class SynthesizeResponse(BaseModel):
    sample_rate: int
    num_samples: int
    duration_s: float
    latency_s: float
    backend: str
    voice: str
    audio_base64: str = ""   # base64-encoded WAV bytes
    error: str | None = None
```

### TranscribeResponse
```python
class TranscribeResponse(BaseModel):
    text: str
    language: str | None = None
    duration_s: float | None = None
    latency_s: float | None = None
    rtf: float | None = None
    backend: str = "unknown"
    error: str | None = None
```

### TranslateRequest
```python
class TranslateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    source_lang: str = Field("en", pattern=r"^[a-z]{2}$")
    target_lang: str = Field("lg", pattern=r"^[a-z]{2}$")
```

### TranslateResponse
```python
class TranslateResponse(BaseModel):
    text: str
    source_lang: str
    target_lang: str
    latency_s: float
    backend: str
    error: str | None = None
```

### VoiceChatResponse
```python
class VoiceChatResponse(BaseModel):
    transcript: str = ""
    transcript_language: str | None = None
    reply: str = ""
    reply_audio_base64: str = ""  # base64-encoded WAV
    sample_rate: int = 0
    duration_s: float = 0.0
    sources: list[str] = []
    citations: list[Citation] = []
    faithfulness_score: float | None = None
    retrieval_mode: str = "keyword"
    asr_latency_s: float = 0.0
    mt_latency_s: float = 0.0
    llm_latency_s: float = 0.0
    tts_latency_s: float = 0.0
    total_latency_s: float = 0.0
    asr_backend: str = ""
    tts_backend: str = ""
    mt_backend: str = ""
    error: str | None = None
```

### SpeechHealthResponse
```python
class SpeechHealthResponse(BaseModel):
    status: str       # "ready" or "unavailable"
    enabled: bool
    asr_backend: str
    tts_backend: str
    mt_backend: str
```

---

## SDK Examples

### Python
```python
import requests

API_URL = "http://localhost:8887"

def chat(message: str, locale: str = "en") -> dict:
    response = requests.post(
        f"{API_URL}/v1/chat",
        json={"message": message, "locale": locale},
        headers={"X-Session-ID": "my-session"},
    )
    data = response.json()

    # Display answer with citations
    print(data["reply"])
    print(f"Retrieval: {data['retrieval_mode']}, Faithfulness: {data['faithfulness_score']}")

    for cit in data.get("citations", []):
        print(f"  {cit['ref']} {cit['source']} p.{cit.get('page','')} — {cit.get('section','')}")

    if data.get("escalation_required"):
        print(f"⚠ Escalation: {data['escalation_reason']}")

    return data

# Usage
result = chat("What is the VAT rate in Uganda?")
```

### JavaScript / TypeScript
```typescript
const API_URL = "http://localhost:8887";

interface Citation {
  ref: string; source: string; page?: string; section?: string; passage?: string;
}

interface ChatResponse {
  reply: string;
  sources: string[];
  citations: Citation[];
  faithfulness_score: number | null;
  retrieval_mode: "hybrid" | "keyword" | "blocked" | "abstained";
  locale: string;
  escalation_required: boolean;
  escalation_reason: string;
}

async function chat(message: string, locale = "en"): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Session-ID": "my-session" },
    body: JSON.stringify({ message, locale }),
  });
  return res.json();
}

// Usage
const data = await chat("How do I register for TIN?");
console.log(data.reply);
data.citations.forEach(c => console.log(`${c.ref} ${c.source} ${c.section ?? ""}`));
```

### Python — Voice Chat
```python
import requests

API_URL = "http://localhost:8887"

def voice_chat(audio_path: str, language: str = "en") -> dict:
    """Send a WAV file through the compound voice pipeline."""
    import wave, struct

    # Read WAV and extract raw PCM16 bytes
    with wave.open(audio_path, "rb") as w:
        pcm_bytes = w.readframes(w.getnframes())
        sample_rate = w.getframerate()

    response = requests.post(
        f"{API_URL}/v1/voice/chat",
        params={"language": language, "sample_rate": sample_rate, "tts_enabled": "true"},
        headers={"Content-Type": "application/octet-stream", "X-Session-ID": "my-session"},
        data=pcm_bytes,
    )
    data = response.json()
    print(f"Transcript: {data['transcript']}")
    print(f"Reply: {data['reply']}")
    print(f"Latency: ASR={data['asr_latency_s']}s MT={data['mt_latency_s']}s "
          f"LLM={data['llm_latency_s']}s TTS={data['tts_latency_s']}s "
          f"Total={data['total_latency_s']}s")
    return data

# Usage
result = voice_chat("question.wav", language="lg")
```

### JavaScript — TTS Playback
```typescript
const API_URL = "http://localhost:8887";

async function speakReply(text: string, language = "en"): Promise<void> {
  const res = await fetch(`${API_URL}/v1/tts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, language }),
  });
  const data = await res.json();
  if (!data.audio_base64) return;

  const binary = atob(data.audio_base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);

  const ctx = new AudioContext();
  const buffer = await ctx.decodeAudioData(bytes.buffer.slice(0));
  const source = ctx.createBufferSource();
  source.buffer = buffer;
  source.connect(ctx.destination);
  source.start(0);
}

// Usage
await speakReply("The VAT rate is 18 percent.", "en");
```

---

## OpenAPI/Swagger

Interactive API documentation is available at:
- **Swagger UI**: `http://localhost:8887/docs`
- **ReDoc**: `http://localhost:8887/redoc`
- **OpenAPI JSON**: `http://localhost:8887/openapi.json`

---

## Deployment

### Docker
```bash
docker run -p 8887:8887 landwind/ura-chatbot-api:latest
```

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | `8887` |
| `WORKERS` | Uvicorn workers | `2` |
| `LOG_LEVEL` | Logging level | `info` |
| `HF_MODEL_REPO` | Model repository | `mpairweLandwind/ura-chatbot` |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000` |
| `DATA_DIR` | Path to FAQ CSV directory | `Data/dataset` |
| `PDF_DIR` | Path to PDF documents | `Data/pdfs` |
| **Qdrant (Hybrid Retrieval)** | | |
| `QDRANT_URL` | Qdrant server URL | `http://localhost:6333` |
| `QDRANT_COLLECTION` | Collection name | `ura_knowledge_base` |
| `DENSE_MODEL` | Embedding model | `BAAI/bge-m3` |
| `RERANKER_MODEL` | Cross-encoder reranker | `mixedbread-ai/mxbai-rerank-base-v2` |
| `RERANK_ENABLED` | Enable cross-encoder reranking | `true` |
| **LLM Generation (Qwen3-8B)** | | |
| `LLM_MODEL` | HuggingFace model ID | `Qwen/Qwen3-8B` |
| `LLM_ENABLED` | Enable LLM generation (`false` = FAQ lookup fallback) | `true` |
| `LLM_DEVICE` | Device for inference (`auto`, `cpu`, `cuda`) | `auto` |
| `LLM_TORCH_DTYPE` | Tensor dtype (`float16`, `bfloat16`, `float32`, `auto`) | `auto` |
| `LLM_TEMPERATURE` | Generation temperature | `0.2` |
| `LLM_MAX_TOKENS` | Max new tokens per response | `512` |
| **Semantic Cache** | | |
| `CACHE_ENABLED` | Enable semantic response cache | `true` |
| `CACHE_THRESHOLD` | Cosine similarity threshold for cache hit | `0.92` |
| `CACHE_TTL_SECONDS` | Cache entry expiry | `3600` |
| `CACHE_MAX_SIZE` | Max cached entries | `1000` |
| **Corrective RAG** | | |
| `CORRECTIVE_RAG_ENABLED` | Enable corrective re-retrieval | `true` |
| `CORRECTIVE_RAG_THRESHOLD` | Min avg reranker score before re-retrieve | `0.3` |
| **Rate Limiting** | | |
| `RATE_LIMIT` | Rate limit for chat endpoints | `30/minute` |
| **OWASP Guardrails** | | |
| `INDEX_API_KEY` | Bearer token for `/v1/index` (empty = auth disabled) | `` |
| `MAX_INPUT_LENGTH` | Maximum input characters | `2000` |
| `GROUNDING_THRESHOLD` | Faithfulness threshold for disclaimer | `0.3` |
| `ABSTENTION_THRESHOLD` | Score below which to refuse answering | `0.15` |
| `ESCALATION_THRESHOLD` | Faithfulness below which to flag for human review | `0.25` |
| **Privacy** | | |
| `STORE_RAW_PROMPTS` | Store unredacted prompts (false = PII redacted) | `false` |
| `CONVERSATION_TTL_DAYS` | Days to retain conversation logs | `7` |
| `FEEDBACK_TTL_DAYS` | Days to retain feedback | `90` |
| **Observability** | | |
| `OTEL_ENABLED` | Enable OpenTelemetry tracing | `false` |
| `OTEL_SERVICE_NAME` | OTel service name | `ura-chatbot-api` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTel collector endpoint | `http://localhost:4317` |
| **Authentication** | | |
| `AUTH_ALG` | JWT signing algorithm | `HS256` |
| `AUTH_DEV_SECRET` | HMAC secret for dev/test JWTs | |
| `OIDC_ISSUER` | OIDC token issuer URL | |
| `OIDC_AUDIENCE` | Expected JWT audience claim | `ura-chatbot` |
| `OIDC_JWKS_URL` | JWKS endpoint for RS256 key rotation | |
| **Speech Pipeline** | | |
| `SPEECH_ENABLED` | Enable speech pipeline (ASR/TTS/MT) | `true` |
| `SPEECH_ASR_BACKEND` | ASR backend selection | `auto` |
| `SPEECH_TTS_BACKEND` | TTS backend selection | `auto` |
| `SPEECH_MT_BACKEND` | MT backend selection | `prompted` |
| `SPEECH_DEADLINE_S` | Max wall-clock time per speech inference | `20` |
| `SPEECH_MAX_CONCURRENCY` | Thread pool workers for speech | `2` |
| `SPEECH_TTS_CACHE_SIZE` | LRU entries for repeated-phrase TTS (0 disables) | `64` |
| `VOICE_LLM_DEADLINE_S` | Hard ceiling on the LLM stage of a voice turn | `45` |
| `VOICE_CHAT_BUDGET_S` | Time budget for batch `/v1/voice/chat`; once spent, reply-TTS is skipped (`tts_skipped=true`) so the text reply beats the gateway timeout and the client narrates via `/v1/tts` | `50` |
| `SPEECH_CLOUD_DEADLINE_S` | Hard ceiling per cloud speech-tier call (Sunbird/edge-tts/Workers AI, all of ASR/TTS/MT); a hung upstream fails that tier and falls through instead of 504ing the request | `40` |
| `SPEECH_CLOUD_MAX_CONCURRENCY` | Worker threads for bounded cloud speech calls | `4` |
| `SPEECH_EN_VOICE` | Default English TTS voice | `en_US-lessac-medium` |
| `SPEECH_LG_VOICE` | Default Luganda TTS voice | `luganda-vits-v1` |
| **Sunbird AI** | | |
| `SUNBIRD_API_URL` | Sunbird AI cloud API base URL | `https://api.sunbird.ai` |
| `SUNBIRD_API_TOKEN` | Bearer token for Sunbird AI services | |
| `SUNBIRD_RETRIES` | Attempts per Sunbird account before failover (429/5xx/timeouts only) | `2` |
| **Feature Flags** | | |
| `FLAG_TOOL_USE` | Enable LLM tool-calling loop | `false` |
| `FLAG_AGENTIC_MODE` | Enable agentic supervisor routing | `false` |
| `FLAG_WORKFLOWS` | Enable YAML-driven guided workflows | `true` |
| `FLAG_AUTH_REQUIRED` | Require JWT authentication on protected endpoints | `false` |
| `FLAG_AUDIT_LEDGER` | Enable hash-chained audit ledger | `false` |
| `FLAG_MEMORY_ENABLED` | Enable consent-gated personalization memory | `false` |
| `FLAG_VOICE_ENABLED` | Enable voice chat endpoints | `false` |
| `FLAG_VOICE_STREAMING` | Enable WebSocket streaming voice chat with VAD + barge-in | `false` |
| `FLAG_VOICE_CONSENT` | Enforce voice-specific consent checks before audio processing | `false` |
| **Analytics DB** | | |
| `ANALYTICS_BACKEND` | Analytics storage backend (`sqlite` or `postgres`) | `sqlite` |
| `POSTGRES_DSN` | PostgreSQL connection string for analytics | |
| **vLLM** | | |
| `LLM_BACKEND` | LLM dispatch backend (`local` or `vllm`) | `local` |
| `VLLM_BASE_URL` | vLLM OpenAI-compatible API base URL | `http://vllm:8001/v1` |

---

## Security Headers

All responses include hardened security headers (OWASP, NIST SSDF):

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Strict-Transport-Security` | `max-age=63072000; includeSubDomains` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `camera=(), microphone=(), geolocation=()` |
| `X-Request-ID` | Validated or server-generated UUID |

---

## Changelog

### v1.3.0 (2026-04-28) — Agentic RAG + Speech + Identity
- **Agentic Routing**: Supervisor classifier routes to RAG, TOOLS, SPECIALIST, CLARIFY, or ESCALATE
- **Tool-Calling**: Bounded LLM tool-calling loop with 6 tool modules (calculators, rates, calendar, KB search, escalation)
- **Guided Workflows**: YAML-driven slot-filling workflows (TIN registration, filing, payment, customs, objection)
- **Speech Pipeline**: ASR (Whisper + LoRA), TTS (Piper), MT (prompted/ONNX), Sunbird AI cloud fallback
- **Identity & Auth**: JWT (HS256/RS256 OIDC), RBAC (5 roles), consent management (UDPA 2019)
- **Admin Tickets**: CRUD endpoints for escalation ticket queue with priority/status/assignee
- **Data Export**: Branded PDF export for conversations and tax summaries
- **Feature Flags**: 18 env-backed flags for progressive rollout
- **PostgreSQL**: Optional analytics backend (drop-in for SQLite)
- **Audit Ledger**: Hash-chained immutable event log with Merkle tree proofs
- **Memory**: Consent-gated personalization (semantic facts, episodic summaries, working state)
- **vLLM Backend**: OpenAI-compatible HTTP dispatch for high-throughput serving

### v1.2.0 (2026-03-10) — Advanced RAG (6-Phase)
- **LLM Generation**: Qwen3-8B local inference replacing FAQ lookup (sync + SSE streaming)
- **SSE Streaming**: `POST /v1/chat/stream` with `metadata`, `token`, `grounding`, `done`, `error` event types
- **Query Rewriting**: Abbreviation expansion (15+ URA terms), spell correction, coreference resolution from history
- **Semantic Cache**: Cosine similarity matching with configurable threshold/TTL/max-size
- **Corrective RAG**: Automatic re-retrieval with expanded query when initial quality is low
- **Clarification Detection**: Ask for more details on genuinely ambiguous single-word queries
- **Multi-turn Memory**: 5-turn sliding window from SQLite conversation history
- **Circuit Breaker**: Thread-safe Qdrant circuit breaker with exponential backoff (10s→300s)
- **Rate Limiting**: `slowapi` with configurable per-IP limits on chat endpoints
- **OutputGuard on SSE**: PII redaction and XSS sanitization applied to streaming tokens
- **Per-stage Tracing**: OpenTelemetry spans with automatic timing for each RAG stage

### v1.4.0 (2026-04-29) — Quantization, Offline RAG & Voice-First Mobile

**New Endpoints:**
- `GET /v1/models/quantized` — List available quantized model variants (GGUF, AWQ, GPTQ, ONNX) with sizes, checksums, and faithfulness scores. Feature-flagged: `FLAG_QUANTIZATION`.
- `GET /v1/offline/status` — Offline bundle availability, version, sync status. Feature-flagged: `FLAG_OFFLINE_RAG`.
- `POST /v1/offline/sync` — Hash-based delta sync. Client sends chunk hashes, server returns only changed chunks. Target: < 12s on 3G. Feature-flagged: `FLAG_OFFLINE_SYNC`.
- `GET /v1/offline/bundle` — Download compressed offline RAG bundle (< 150 MB). SHA-256 verified. Feature-flagged: `FLAG_OFFLINE_BUNDLE_API`.
- `POST /v1/voice/vision/chat` — Voice + camera compound endpoint. Accepts multipart audio + image. Performs ASR + OCR + LLM + TTS in parallel. Feature-flagged: `FLAG_VOICE_VISION`.
- `GET /v1/admin/offline_stats` — Admin dashboard for offline usage: bundle downloads, sync completions, active offline devices, avg sync duration.

**New Feature Flags (11 total):**
- Phase 24: `quantization`, `speculative_decoding`, `prefix_caching`
- Phase 25: `offline_rag`, `offline_sync`, `offline_bundle_api`
- Phase 26: `mobile_bundle_check`, `on_device_search`
- Phase 27: `voice_first_mobile`, `voice_vision`, `offline_voice`

**New Backend Modules:**
- `offline_rag.py` — Production rewrite with versioned bundles, SHA-256 integrity, chunk hashes, ONNX embedder optimization
- `offline_sync.py` — Delta sync engine with hash-based chunk diffing, sync event logging
- `offline_bundle.py` — Bundle builder (FAISS + passages + embedder + manifest) + bundle manager for serving

**New Scripts:**
- `scripts/quantize_models.py` — Automated GGUF/AWQ/GPTQ/ONNX quantization pipeline
- `scripts/quantization_quality_gate.py` — CI quality gate: faithfulness ≤ 4% drop, WER ≤ 3% increase, bundle size limits
- `scripts/export_mobile_bundle.py` — Mobile bundle assembler with 800 MB hard limit enforcement

**New CI Workflow:**
- `.github/workflows/quantization.yml` — 5-job pipeline: validate → quantize (GGUF + AWQ parallel) → quality gate → publish

**Frontend Components:**
- `VoiceFirstChat.tsx` — Full-screen voice-first interface with animated orb, waveform, barge-in, offline indicators
- `VoiceVisionMode.tsx` — Voice + camera document scanning mode
- Zustand stores updated with offline mode, sync state, voice-first preferences

**Infrastructure:**
- `docker-compose.yml` — New `vllm-quantized` service (AWQ, prefix caching, continuous batching)
- `Dockerfile` — Added offline/quantized artifact directories
- Offline + quantized volume mounts on API service

### v1.1.0 (2026-03-08)
- Hybrid retrieval: Qdrant dense + BM25 sparse + RRF fusion + cross-encoder reranking
- Passage-level citations with source, page, section, excerpt
- Runtime faithfulness scoring (grounding verification)
- OWASP LLM Top 10 guardrails (prompt injection, PII redaction, XSS sanitization)
- Calibrated abstention (refuses when confidence too low)
- Human escalation flagging
- OpenTelemetry GenAI tracing (opt-in)
- Document re-indexing endpoint (`POST /v1/index`)
- Locale support (`en`, `lg-UG`)
- Privacy-by-design: PII redaction before storage, configurable TTLs
- Governance compliance gate in CI/CD

### v1.0.0 (2024-12-28)
- Initial API release
- Classification endpoint
- Chat endpoint
- Batch classification
- Health check
