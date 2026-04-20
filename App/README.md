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
│   │   ├── main.py          # API routes + SSE streaming + speech + eval endpoints
│   │   ├── models.py        # Pydantic v2 request/response models
│   │   ├── speech_service.py # SpeechModel (ASR + MT + TTS, edge-tts backend)
│   │   ├── service.py       # ChatModel (RAG orchestrator + agentic routing)
│   │   ├── llm.py           # Qwen3-8B vLLM + local generation + tool-calling
│   │   ├── flags.py         # Feature flag registry (17 flags)
│   │   ├── agents/          # Supervisor router + agent graph runtime
│   │   ├── tools/           # 11 tools (calculators, rates, calendar, RAG, escalate)
│   │   ├── workflows/       # YAML-driven slot-filling engine + TIN registration
│   │   ├── memory/          # Episodic + semantic + working memory
│   │   ├── guardrails.py    # OWASP LLM Top 10 guards + abstention
│   │   ├── retriever.py     # Hybrid retriever (Qdrant + BM25 + reranking)
│   │   └── ...              # cache, query, indexer, resilience, tracing, analytics, database
│   └── requirements.txt
└── frontend/          # Next.js 16 PWA frontend
    ├── src/
    │   ├── app/             # Pages: chat, 404, error, analytics, evaluation
    │   ├── components/      # 12 components (Chat, Voice, Markdown, Mermaid, Consent, etc.)
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

**Speech Endpoints (2026):**
- `POST /v1/voice/chat` — Compound voice pipeline: audio -> ASR -> MT -> LLM -> MT -> TTS -> audio+text
- `POST /v1/asr` — Server-side ASR (Whisper via transformers, raw PCM -> transcript)
- `POST /v1/tts` — Text-to-speech synthesis (Edge Neural TTS, text -> base64 WAV)
- `POST /v1/translate` — Machine translation (English <-> Luganda)
- `GET /v1/speech/health` — Speech pipeline readiness check

**Evaluation Endpoints (2026):**
- `GET /v1/evaluation/results` — Pre-computed IEEE metrics (RAG, safety, calibration, benchmark, tokenizer)

**RAG Pipeline (6 Phases):**
1. **Hybrid Retrieval** — Qdrant dense + BM25 sparse RRF + cross-encoder reranking + circuit breaker
2. **LLM Generation** — Qwen3-8B local inference (sync + streaming)
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

Next.js 16 + React 19 PWA with SSE streaming, voice modal, and IEEE evaluation dashboards.

**Features:**
- SSE streaming with `ReadableStream` + `requestAnimationFrame` batched rendering
- Markdown rendering (bold, lists, code, citations, links) + Mermaid diagram support
- Grok-inspired glassmorphism dark design (2,283 lines CSS)
- Copy button + timestamps on every message
- Installable PWA (manifest, service worker, offline caching)
- GDPR consent banner (gates analytics tracking)
- Branded 404 page + analytics error boundary
- Zustand 5 state management with multi-conversation persistence

**Voice capabilities (2026):**
- Full-screen voice modal (Grok-style pulse rings + waveform + transcript)
- Voice persona selection (5 voices: 3 English + 1 default + 1 Luganda)
- Real TTS via Microsoft Edge Neural (0.4s latency)
- ASR via Whisper (HuggingFace transformers backend)
- Listen button on every reply + auto-narrate toggle
- Complete voice chat compound pipeline (ASR → LLM → TTS)

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
- `src/app/analytics/evaluation/page.tsx` — IEEE evaluation dashboard
- `src/services/voiceService.ts` — AudioRecorder, playback, speech API wrappers
- `src/store/useChatStore.ts` — Multi-conversation Zustand state

**Run locally:**
```bash
cd App/frontend
bun install
INTERNAL_API_URL=http://127.0.0.1:8887 bun run dev --port 3300
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
| `API_URL` | Backend API URL | `http://localhost:8887` |
| **LLM Generation** | | |
| `LLM_MODEL` | HuggingFace model ID | `Qwen/Qwen3-8B` |
| `LLM_MODEL_REVISION` | Pin to a specific HF commit SHA (SLSA provenance) | _unset_ |
| `LLM_TRUST_REMOTE_CODE` | Allow model-defined Python (OWASP LLM03 off by default) | `false` |
| `LLM_CONTEXT_WINDOW` | Hard cap on prompt tokens (tokenizer-aware trimming) | `6144` |
| `LLM_ENABLED` | Enable LLM generation | `true` |
| `LLM_DEVICE` | Inference device (`auto`/`cpu`/`cuda`) | `auto` |
| `LLM_TORCH_DTYPE` | Tensor dtype | `auto` |
| `LLM_TEMPERATURE` | Generation temperature | `0.2` |
| `LLM_MAX_TOKENS` | Max new tokens | `512` |
| `LLM_DEADLINE_SECONDS` | Hard wall-clock deadline per LLM call | `45` |
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
| `FLAG_TOOL_USE` | Qwen3 native tool-calling via vLLM OpenAI API | `true` |
| `FLAG_AGENTIC_MODE` | Supervisor classifier routes every request | `true` |
| `FLAG_TICKET_QUEUE` | Persist escalations to the `tickets` table | `false` |
| `FLAG_WORKFLOWS` | YAML-driven multi-step slot-filling workflows | `false` |
| **Host / HuggingFace** | | |
| `HF_HOME` | Writable HF cache location | `~/.cache/huggingface` |
| `INTERNAL_API_URL` | Next.js rewrite target (server-side) | `http://127.0.0.1:8887` |
| `NEXT_PUBLIC_API_URL` | Browser-side API URL (bake-time) | `/api` |

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

- New `backend/app/postgres.py` — full psycopg3 + `psycopg_pool`
  implementation mirroring every public function in `database.py`.
  Schema is intentionally identical.
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
  `${INTERNAL_API_URL}/:path*` (default `http://127.0.0.1:18000`).
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
  .venv/bin/python -m uvicorn App.backend.app.main:app --port 18000

# 6. In another terminal, run the frontend
cd App/frontend
NEXT_PUBLIC_API_URL=/api INTERNAL_API_URL=http://127.0.0.1:18000 bun run build
NEXT_PUBLIC_API_URL=/api INTERNAL_API_URL=http://127.0.0.1:18000 bun run next start -p 13000
```

Open http://localhost:13000 in a browser.  Feedback, citations, and
SSE streaming are all proxied through the `/api` rewrite so no ports
need to be exposed beyond 13000.

**Option B — vLLM inference (Qwen3-8B) with full voice pipeline:**

```bash
# 1. Start vLLM on a free GPU
docker run -d --name ura-vllm --gpus '"device=7"' --ipc=host \
  -p 8011:8001 -v ~/.cache/huggingface:/root/.cache/huggingface \
  vllm/vllm-openai:v0.8.5 \
  --model Qwen/Qwen3-8B --port 8001 --max-model-len 8192 \
  --enable-auto-tool-choice --tool-call-parser hermes

# 2. Start the backend (embeddings on GPU 4, LLM via vLLM HTTP)
cd App/backend
CUDA_VISIBLE_DEVICES=4 LLM_BACKEND=vllm VLLM_BASE_URL=http://localhost:8011/v1 \
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
  `calculate_capital_gains`, `calculate_customs_duty` (all deterministic
  FY2025-26 rate tables), `get_current_date`, `get_next_deadlines`,
  `lookup_rate`, `list_available_rates`, `search_ura_knowledge_base`
  (wraps the existing hybrid retriever), `escalate_to_human`
- Auto-registration via `__init__.py` import hook

**Phase B — Qwen2.5 tool-calling loop** (`App/backend/app/llm.py`)
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
  - `GET /v1/admin/tickets?status=open&limit=50`
  - `GET /v1/admin/tickets/stats?days=30`
  - `GET /v1/admin/tickets/{id}`
  - `PATCH /v1/admin/tickets/{id}` (status / assignee / note / priority)
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
- `App/backend/app/flags.py` — `tool_use` and `agentic_mode` defaults
  changed to `true`. Agentic tool-calling only activates when the
  supervisor explicitly routes to TOOLS/SPECIALIST — standard RAG queries
  bypass the tool loop entirely for performance.
- `App/backend/app/service.py` — Fixed `use_agentic` logic: now only
  `force_agentic` (supervisor decision), not `FLAG_TOOL_USE` alone,
  triggers the tool-calling path. This prevents double-search degradation
  on simple factual queries.

**Phase 17 — YAML workflow engine (TIN registration PoC)**

- `App/backend/app/workflows/` — New package with 4 modules:
  - `slots.py` — Slot validators (enum, regex, boolean, text)
  - `loader.py` — YAML → `WorkflowDefinition` dataclass parser
  - `registry.py` — `WorkflowRegistry` + `WorkflowSession` runtime with
    conditional step evaluation and trigger phrase matching
  - `flows/tin_registration.yaml` — 9-step TIN registration flow with
    conditional branching (individual/company/NGO), NIN regex validation,
    phone/email validation, and confirmation gate
- `App/backend/app/flags.py` — New `FLAG_WORKFLOWS` flag (default false)

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

**Services (current running configuration):**

| Service | Port | GPU | Description |
|---------|------|-----|-------------|
| Backend (FastAPI) | 8009 | GPU 4 (embeddings) | RAG + agentic + speech |
| Frontend (Next.js 16) | 8010 | — | PWA + consent + voice modal |
| vLLM | 8011 | GPU 7 | Qwen/Qwen3-8B + tool-calling |
| Qdrant | 6333 | CPU | 4,526 vectors (dense + sparse) |

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
