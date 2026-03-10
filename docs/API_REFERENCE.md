# API Reference Documentation

## Overview

The URA Chatbot provides a FastAPI-based REST API for tax-related question classification and answering.

**Base URL**: `http://localhost:8000` (local) or `https://api.ura-chatbot.com` (production)

## Authentication

Currently, the API is open. Future versions will implement:
- API Key authentication
- OAuth 2.0 / JWT tokens

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
curl -X POST http://localhost:8000/classify \
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
  "model": "ura-gemma-2-9b",
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
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: my-session-123" \
  -d '{"message": "What is VAT rate in Uganda?", "locale": "en"}'
```

---

### Trigger Re-Indexing

Rebuild the Qdrant vector index from all PDFs and FAQ CSVs.

```http
POST /v1/index
```

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
    model: str = "ura-gemma-2-9b"
    conversation_id: str | None = None
    locale: str = "en"
    escalation_required: bool = False
    escalation_reason: str = ""
```

---

## SDK Examples

### Python
```python
import requests

API_URL = "http://localhost:8000"

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
const API_URL = "http://localhost:8000";

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

---

## OpenAPI/Swagger

Interactive API documentation is available at:
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **OpenAPI JSON**: `http://localhost:8000/openapi.json`

---

## Deployment

### Docker
```bash
docker run -p 8000:8000 landwind/ura-chatbot-api:latest
```

### Environment Variables
| Variable | Description | Default |
|----------|-------------|---------|
| `PORT` | Server port | `8000` |
| `WORKERS` | Uvicorn workers | `2` |
| `LOG_LEVEL` | Logging level | `info` |
| `HF_MODEL_REPO` | Model repository | `mpairweLandwind/ura-chatbot` |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000` |
| `DATA_DIR` | Path to FAQ CSV directory | `Data/dataset` |
| `PDF_DIR` | Path to PDF documents | `Data/pdfs` |
| **Qdrant (Hybrid Retrieval)** | | |
| `QDRANT_URL` | Qdrant server URL | `http://localhost:6333` |
| `QDRANT_COLLECTION` | Collection name | `ura_knowledge_base` |
| `DENSE_MODEL` | Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| `RERANKER_MODEL` | Cross-encoder reranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| `RERANK_ENABLED` | Enable cross-encoder reranking | `true` |
| **OWASP Guardrails** | | |
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

### v1.1.0 (2026-03-10)
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
