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

Confirms the model is loaded and the service can handle requests. Returns **503** if the model is unavailable.

```http
GET /ready
```

**Response**
```json
{
  "status": "ready",
  "version": "1.0.0",
  "model_loaded": true,
  "tags_loaded": 41
}
```

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

Send a message and receive an AI-generated response.

```http
POST /v1/chat
```

**Request Body**
```json
{
  "message": "What is the VAT rate in Uganda?",
  "conversation_id": "conv_123",
  "top_k": 4
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `message` | string | Yes | User's question (max 2000 chars) |
| `conversation_id` | string | No | For conversation continuity |
| `top_k` | integer | No | Number of passages to retrieve (1–10, default 4) |

**Response**
```json
{
  "reply": "The standard VAT rate in Uganda is 18%. Some goods and services are zero-rated or exempt.",
  "sources": ["ura_vat_faqs.csv"],
  "model": "ura-gemma-2-9b",
  "conversation_id": "conv_123"
}
```

**cURL Example**
```bash
curl -X POST http://localhost:8000/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is VAT rate in Uganda?"}'
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
    conversation_id: Optional[str] = None
    top_k: int = Field(4, ge=1, le=10)
```

### ChatResponse
```python
class ChatResponse(BaseModel):
    reply: str
    sources: List[str] = Field(default_factory=list)
    model: str = "ura-gemma-2-9b"
    conversation_id: Optional[str] = None
```

---

## SDK Examples

### Python
```python
import requests

API_URL = "http://localhost:8000"

def classify(text: str) -> dict:
    response = requests.post(
        f"{API_URL}/classify",
        json={"text": text}
    )
    return response.json()

def chat(message: str) -> dict:
    response = requests.post(
        f"{API_URL}/v1/chat",
        json={"message": message}
    )
    return response.json()

# Usage
result = classify("How do I register for TIN?")
print(result["predictions"][0]["tag"])
```

### JavaScript
```javascript
const API_URL = "http://localhost:8000";

async function classify(text) {
  const response = await fetch(`${API_URL}/classify`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text })
  });
  return response.json();
}

async function chat(message) {
  const response = await fetch(`${API_URL}/v1/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message })
  });
  return response.json();
}

// Usage
const result = await classify("How do I register for TIN?");
console.log(result.predictions[0].tag);
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
| `PORT` | Server port | 8000 |
| `WORKERS` | Uvicorn workers | 2 |
| `LOG_LEVEL` | Logging level | info |
| `HF_MODEL_REPO` | Model repository | mpairweLandwind/ura-chatbot |
| `CORS_ORIGINS` | Comma-separated allowed origins | http://localhost:3000 |
| `DATA_DIR` | Path to FAQ CSV directory | Data/dataset |

---

## Changelog

### v1.0.0 (2024-12-28)
- Initial API release
- Classification endpoint
- Chat endpoint
- Batch classification
- Health check
