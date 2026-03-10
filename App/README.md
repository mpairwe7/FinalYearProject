# 🇺🇬 URA Chatbot - Application Directory

This directory contains all application components for the URA Chatbot project.

## Directory Structure

```
App/
├── app.py              # Main Gradio app (HF Spaces deployment)
├── classifier.py       # Legacy classifier interface
├── requirements.txt    # Python dependencies
├── README.md          # This file
├── README_HF.md       # Hugging Face Spaces README
├── backend/           # FastAPI backend API
│   ├── app/
│   │   ├── main.py          # API routes + SSE streaming + rate limiting
│   │   ├── models.py        # Pydantic v2 request/response models
│   │   ├── service.py       # ChatModel (6-phase RAG orchestrator)
│   │   ├── llm.py           # Qwen2.5-3B-Instruct local generation
│   │   ├── query.py         # Query rewriting pipeline
│   │   ├── cache.py         # Semantic response cache
│   │   ├── corrective_rag.py # Corrective re-retrieval + clarification
│   │   ├── guardrails.py    # OWASP LLM Top 10 guards
│   │   ├── retriever.py     # Hybrid retriever + circuit breaker
│   │   ├── indexer.py       # PDF/CSV → Qdrant indexing
│   │   ├── tracing.py       # OpenTelemetry GenAI spans
│   │   ├── analytics.py     # Prometheus metrics middleware
│   │   └── database.py      # SQLite WAL store
│   └── requirements.txt
└── frontend/          # Next.js 15 web frontend
    ├── src/
    │   ├── app/       # Next.js pages (SSE streaming chat)
    │   └── store/     # Zustand 5 state management
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

FastAPI v0.111 REST API with 6-phase advanced RAG pipeline and local LLM inference.

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

**RAG Pipeline (6 Phases):**
1. **Hybrid Retrieval** — Qdrant dense + BM25 sparse RRF + cross-encoder reranking + circuit breaker
2. **LLM Generation** — Qwen2.5-3B-Instruct local inference (sync + streaming)
3. **SSE Streaming** — `TextIteratorStreamer` with per-token OutputGuard sanitization
4. **Query Intelligence** — Rewriting (abbreviations, spelling, coreference), semantic cache, multi-turn memory
5. **Observability** — OpenTelemetry per-stage spans, Prometheus metrics, analytics dashboard
6. **Safety** — OWASP LLM Top 10 guardrails, corrective RAG, calibrated abstention, human escalation

**Run locally:**
```bash
cd App/backend
uv pip install -r requirements.txt
uvicorn app.main:app --reload
```

### 3. Frontend (`frontend/`)

Next.js 15 + React 19 web application with SSE streaming support.

**Features:**
- SSE streaming with `ReadableStream` reader + sync fallback
- `requestAnimationFrame` batched token rendering
- Speech recognition support
- Glassmorphism design
- Zustand 5 state management with `updateLastTurn()` for streaming
- ARIA-accessible locale selection

**Run locally:**
```bash
cd App/frontend
bun install
bun dev
```

## Deployment

### Hugging Face Spaces

1. Copy `app.py`, `requirements.txt`, and `README_HF.md` to your Space
2. Rename `README_HF.md` to `README.md`
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
| `API_URL` | Backend API URL | `http://localhost:8000` |
| **LLM Generation** | | |
| `LLM_MODEL` | HuggingFace model ID | `Qwen/Qwen2.5-3B-Instruct` |
| `LLM_ENABLED` | Enable LLM generation | `true` |
| `LLM_DEVICE` | Inference device (`auto`/`cpu`/`cuda`) | `auto` |
| `LLM_TORCH_DTYPE` | Tensor dtype | `auto` |
| `LLM_TEMPERATURE` | Generation temperature | `0.2` |
| `LLM_MAX_TOKENS` | Max new tokens | `512` |
| **Semantic Cache** | | |
| `CACHE_ENABLED` | Enable semantic cache | `true` |
| `CACHE_THRESHOLD` | Cosine similarity threshold | `0.92` |
| `CACHE_TTL_SECONDS` | Cache entry TTL | `3600` |
| **Corrective RAG** | | |
| `CORRECTIVE_RAG_ENABLED` | Enable corrective re-retrieval | `true` |
| `CORRECTIVE_RAG_THRESHOLD` | Min avg score before re-retrieve | `0.3` |
| **Rate Limiting** | | |
| `RATE_LIMIT` | Chat endpoint rate limit | `30/minute` |
| **Retrieval** | | |
| `QDRANT_URL` | Qdrant server URL | `http://localhost:6333` |
| `DENSE_MODEL` | Embedding model | `sentence-transformers/all-MiniLM-L6-v2` |
| **Observability** | | |
| `OTEL_ENABLED` | Enable OpenTelemetry tracing | `false` |

## Development

### Prerequisites

- Python 3.11+
- Node.js 18+ / Bun
- Trained model files in `Model/` directory

### Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run Gradio app
python app.py

# App will be available at http://localhost:7860
```

## Links

- [GitHub Repository](https://github.com/mpairweLandwind/FinalYearProject)
- [Hugging Face Space](https://huggingface.co/spaces/mpairweLandwind/ura-chatbot)
- [URA Official Website](https://www.ura.go.ug)


### Python API
```python
from classifier import predict_tag

result = predict_tag("How do I pay VAT?")
print(f"Tag: {result['tag']}")
print(f"Confidence: {result['confidence']:.2%}")
```

## Project Structure

```
App/
├── classifier.py      # Main Gradio application
├── README.md          # This file (HF Space metadata)
└── requirements.txt   # Python dependencies
```

## Training

The model is trained on URA FAQ datasets. To retrain:

```bash
python ml/pipelines/train.py --config ml/configs/training_config.yaml
```

## Links

- **Repository**: [github.com/mpairweLandwind/FinalYearProject](https://github.com/mpairweLandwind/FinalYearProject)
- **Documentation**: [MLOps Pipeline Guide](../docs/MLOPS_PIPELINE.md)

## License

MIT License - See repository for details.
