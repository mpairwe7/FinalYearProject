# URA Chatbot — Production Deployment Guide

Covers the full stack: **FastAPI API**, **Next.js frontend**, **Qdrant vector DB**, and **Flutter mobile app**.

---

## 1. Prerequisites

| Requirement | Minimum version |
|---|---|
| Docker Engine + Compose v2 | 24.0+ / 2.24+ |
| Domain name (A/AAAA record) | e.g. `ura-chatbot.example.com` |
| TLS certificate | Let's Encrypt or organisational CA |
| `.env` file | Copy from `.env.example`, fill secrets |
| Trivy | Latest (scans run via `trivy.yaml` config) |

Secrets that **must** be set before first deploy:

```bash
HF_TOKEN=hf_...              # Hugging Face model download
INDEX_API_KEY=<random-64>    # Protects /v1/index endpoint
```

Generate `INDEX_API_KEY`:

```bash
openssl rand -hex 32
```

---

## 2. Docker Compose Production

The project ships a single `docker-compose.yml` at the repo root with four services: `qdrant`, `api`, `frontend`, and `api-dev` (dev profile, ignored in production).

### Build and start

```bash
# Build images
docker compose build api frontend

# Start production stack (excludes api-dev, trainer profiles)
docker compose up -d qdrant api frontend
```

### Production overrides

Create `docker-compose.prod.yml` alongside the existing file to tighten settings:

```yaml
# docker-compose.prod.yml
services:
  api:
    environment:
      - APP_ENV=production
      - WORKERS=4
      - LOG_LEVEL=warning
      - OTEL_ENABLED=true
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 2G
    logging:
      driver: json-file
      options:
        max-size: "50m"
        max-file: "5"

  frontend:
    environment:
      - NODE_ENV=production

  qdrant:
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 2G
```

Launch with overrides:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

### Image scanning before deploy

```bash
trivy image --config trivy.yaml landwind/ura-chatbot-api:latest
trivy image --config trivy.yaml landwind/ura-chatbot-frontend:latest
```

---

## 3. Environment Configuration

All variables are documented in `.env.example`. Critical production values:

```dotenv
# --- Core ---
APP_ENV=production
LOG_LEVEL=warning
WORKERS=4
PORT=8000

# --- Security ---
INDEX_API_KEY=<your-64-char-hex>
CORS_ORIGINS=https://ura-chatbot.example.com
RATE_LIMIT=30/minute

# --- Qdrant ---
QDRANT_URL=http://qdrant:6333          # Inside Docker network (default)
# QDRANT_URL=http://localhost:16333    # From host, if ports are remapped
QDRANT_COLLECTION=ura_knowledge_base
DENSE_MODEL=BAAI/bge-m3
DENSE_DIM=1024                         # Must match the indexed collection

# --- Frontend ---
NEXT_PUBLIC_API_URL=https://ura-chatbot.example.com/api
FRONTEND_PORT=3000

# --- Observability ---
OTEL_ENABLED=true
OTEL_SERVICE_NAME=ura-chatbot-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317

# --- Privacy ---
STORE_RAW_PROMPTS=false
CONVERSATION_TTL_DAYS=7

# --- Models ---
HF_TOKEN=hf_...
HF_MODEL_REPO=mpairweLandwind/ura-chatbot
LLM_MODEL=Qwen/Qwen3-8B
LLM_LOAD_IN_4BIT=true
LORA_ADAPTER_LG=/app/adapters/luganda-lora
LORA_ADAPTER_SW=/app/adapters/sw-lora
LORA_ADAPTER_NYN=/app/adapters/nyn-lora
LORA_ADAPTER_ACH=/app/adapters/ach-lora

# --- Speech ---
SPEECH_ENABLED=true
WHISPER_DEVICE=cpu
WHISPER_ADAPTER_LG=/app/adapters/whisper-lg
WHISPER_ADAPTER_SW=/app/adapters/whisper-sw
WHISPER_ADAPTER_NYN=/app/adapters/whisper-nyn
```

> Never commit `.env` to version control. The repo `.gitignore` already excludes it.

### Model and adapter mounts

The App compose stack mounts LoRA artifacts read-only from the repository:

```yaml
services:
  api:
    volumes:
      - ../fine-tuning/adapters:/app/adapters:ro
```

For GPU-constrained hosts, keep `LLM_LOAD_IN_4BIT=true`. The local Transformers path loads Qwen3-8B with BitsAndBytes NF4 quantization and then attaches the per-locale PEFT adapters for `lg`, `sw`, `nyn`, and `ach`. Whisper ASR should remain on CPU with `WHISPER_DEVICE=cpu` unless the deployment has a separate GPU budget for ASR.

### Shared-host port remapping

On multi-tenant servers where default ports (6333, 6379, 3000) are
already claimed by other services, use env vars to remap:

```bash
# docker-compose.yml already supports these overrides:
QDRANT_PORT=16333        # external → internal 6333
QDRANT_GRPC_PORT=16334   # external → internal 6334

# Start Qdrant with remapped ports
QDRANT_PORT=16333 QDRANT_GRPC_PORT=16334 docker compose up -d qdrant

# Point the backend at the remapped port (when running outside Docker)
QDRANT_URL=http://localhost:16333 \
REDIS_URL=redis://localhost:16379/0 \
uvicorn app.main:app --host 127.0.0.1 --port 18000
```

Similarly, the Next.js frontend falls back to port 13100 if 3000 is
occupied:

```bash
bun run next dev -p 13100 -H 127.0.0.1
```

The frontend's `next.config.mjs` rewrites `/api/*` to the backend's
`INTERNAL_API_URL` (default `http://127.0.0.1:18000`), so no
client-side code changes are needed.

---

## 4. TLS / Reverse Proxy

### Option A — Caddy (auto HTTPS with Let's Encrypt)

```Caddyfile
ura-chatbot.example.com {
    # API — proxied under /api prefix
    handle /api/* {
        uri strip_prefix /api
        reverse_proxy api:8000
    }

    # Prometheus metrics — internal only
    handle /metrics {
        respond 403
    }

    # Frontend — everything else
    handle {
        reverse_proxy frontend:3000
    }

    header {
        Strict-Transport-Security "max-age=63072000; includeSubDomains"
        X-Content-Type-Options    nosniff
        X-Frame-Options           DENY
    }
}
```

### Option B — nginx

```nginx
upstream api_backend  { server api:8000; }
upstream frontend_app { server frontend:3000; }

server {
    listen 443 ssl http2;
    server_name ura-chatbot.example.com;

    ssl_certificate     /etc/letsencrypt/live/ura-chatbot.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ura-chatbot.example.com/privkey.pem;

    # API
    location /api/ {
        rewrite ^/api/(.*)$ /$1 break;
        proxy_pass http://api_backend;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE streaming support (/v1/chat/stream)
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # WebSocket streaming voice chat (Phase 23)
    location /v1/voice/chat/stream {
        proxy_pass http://backend_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 600s;  # Long-lived voice sessions
        proxy_send_timeout 600s;
    }

    # Block external access to metrics
    location = /metrics { return 403; }

    # Frontend
    location / {
        proxy_pass http://frontend_app;
        proxy_set_header Host $host;
    }
}
```

---

## 5. Health Checks & Probes

The API exposes two endpoints (see `App/backend/app/main.py`):

| Endpoint | Type | Behaviour |
|---|---|---|
| `GET /health` | **Liveness** | Returns `{"status":"alive","version":"1.3.0"}` — always 200 if the process is up. |
| `GET /ready` | **Readiness** | Returns 200 with `status: "ready"` when both the ChatModel and Qdrant retriever are healthy. Returns 503 if the model failed to load, or `status: "degraded"` if Qdrant is unreachable (falls back to keyword search). |

Docker Compose already configures a healthcheck on the `api` service hitting `/health`. For Kubernetes or external uptime monitors, probe `/ready` instead.

Example external check:

```bash
curl -sf https://ura-chatbot.example.com/api/ready | jq .status
```

### Anonymous public smoke

The public assistant is intentionally usable without login. Verify both public access and protected-route denial after every deploy:

```bash
curl -sS https://ura-chatbot.example.com/api/health

curl -sS -X POST https://ura-chatbot.example.com/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Session-ID: anonymous-smoke" \
  -d '{"message":"How do I register for a TIN?","locale":"en"}'

curl -sS https://ura-chatbot.example.com/api/v1/speech/health

curl -i https://ura-chatbot.example.com/api/v1/admin/tickets/stats
```

The admin/ticket request should return an auth failure, while chat and speech health should succeed anonymously.

---

## 6. Scaling

### Horizontal API scaling

The API is stateless (model loaded per-worker, analytics written to a shared SQLite volume). Scale with:

```bash
docker compose up -d --scale api=3
```

Place the reverse proxy in front and load-balance across the replicas. When scaling beyond a single host, switch the analytics database from SQLite to PostgreSQL and mount a shared filesystem or object store for model artifacts.

### Qdrant replication

For high-availability retrieval, run a multi-node Qdrant cluster:

```yaml
# qdrant-cluster.yml (example)
services:
  qdrant-0:
    image: qdrant/qdrant:v1.17.1
    environment:
      - QDRANT__CLUSTER__ENABLED=true
    command: ./qdrant --uri http://qdrant-0:6335
  qdrant-1:
    image: qdrant/qdrant:v1.17.1
    environment:
      - QDRANT__CLUSTER__ENABLED=true
    command: ./qdrant --uri http://qdrant-1:6335 --bootstrap http://qdrant-0:6335
```

Set replication factor on the collection:

```bash
curl -X PATCH http://qdrant-0:6333/collections/ura_knowledge_base \
  -H 'Content-Type: application/json' \
  -d '{"replication_factor": 2}'
```

### Frontend scaling

The Next.js standalone frontend is also stateless. Scale with `--scale frontend=N` and load-balance via the reverse proxy.

---

## 7. Backup & Recovery

### Qdrant snapshots

```bash
# Create snapshot
curl -X POST http://localhost:6333/collections/ura_knowledge_base/snapshots

# List snapshots
curl http://localhost:6333/collections/ura_knowledge_base/snapshots

# Download snapshot
curl -o qdrant-backup.snapshot \
  http://localhost:6333/collections/ura_knowledge_base/snapshots/<snapshot-name>

# Restore (on a fresh node)
curl -X PUT http://localhost:6333/collections/ura_knowledge_base/snapshots/recover \
  -H 'Content-Type: application/json' \
  -d '{"location": "file:///qdrant/storage/snapshots/ura_knowledge_base/<snapshot-name>"}'
```

### SQLite analytics database

The analytics DB is stored in the `analytics_data` Docker volume (mounted at `/app/data_store` inside the container).

```bash
# Backup
docker cp ura-chatbot-api:/app/data_store/analytics.db ./backups/analytics-$(date +%F).db

# Or from the host volume
docker run --rm -v analytics_data:/data -v $(pwd)/backups:/backup \
  alpine cp /data/analytics.db /backup/analytics-$(date +%F).db
```

Schedule daily backups via cron:

```cron
0 3 * * * docker cp ura-chatbot-api:/app/data_store/analytics.db /backups/analytics-$(date +\%F).db
```

### Model artifacts

Model weights are pulled from Hugging Face Hub (`HF_MODEL_REPO=mpairweLandwind/ura-chatbot`) and cached in the `./artifacts/models` bind mount. Back this directory up alongside Qdrant snapshots for a full disaster recovery set.

---

## 8. Monitoring

### OpenTelemetry

The API ships with full OTel support (see `App/backend/app/tracing.py`). Enable it:

```dotenv
OTEL_ENABLED=true
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
```

Traced spans include:
- `rag.pipeline` (parent) with per-stage children: `embed`, `search`, `rerank`, `generate`, `guardrails`
- `gen_ai.client.token.usage` counter
- `gen_ai.retrieval.duration` histogram

### Prometheus metrics

The `/metrics` endpoint (see `App/backend/app/analytics.py`) exposes Prometheus text format:

```
http_requests_total{method="POST",path="/v1/chat",status="200"} 4821
http_request_duration_ms{quantile="0.95",method="POST",path="/v1/chat"} 1423.12
chat_requests_total 4821
chat_response_time_ms{quantile="0.95"} 1423.12
faithfulness_score{quantile="0.95"} 0.82
escalation_required_total 37
feedback_total{rating="up"} 312
feedback_total{rating="down"} 41
```

Add a Prometheus scrape job:

```yaml
# prometheus.yml
scrape_configs:
  - job_name: ura-chatbot-api
    scrape_interval: 15s
    static_configs:
      - targets: ["api:8000"]
    metrics_path: /metrics
```

### Grafana dashboard recommendations

| Panel | Query |
|---|---|
| Request rate | `rate(http_requests_total[5m])` |
| p95 latency | `http_request_duration_ms{quantile="0.95"}` |
| Error rate | `rate(http_errors_total[5m]) / rate(http_requests_total[5m])` |
| Chat throughput | `rate(chat_requests_total[5m])` |
| Faithfulness p95 | `faithfulness_score{quantile="0.95"}` |
| Escalation rate | `rate(escalation_required_total[5m])` |
| Feedback ratio | `feedback_total{rating="up"} / (feedback_total{rating="up"} + feedback_total{rating="down"})` |

---

## 9. Mobile App Distribution

The Flutter app lives at `MobileApp/ura_chatbot/` (version `1.1.0+1` per `pubspec.yaml`).

### Android (Google Play Store)

```bash
cd MobileApp/ura_chatbot

# Build release APK
flutter build apk --release

# Build release App Bundle (preferred for Play Store)
flutter build appbundle --release
```

Upload `build/app/outputs/bundle/release/app-release.aab` to the Google Play Console. Configure the API base URL via the app's environment/config before building.

### iOS (Apple App Store)

```bash
cd MobileApp/ura_chatbot

flutter build ipa --release
```

Open `build/ios/archive/Runner.xcarchive` in Xcode, then distribute via App Store Connect.

### Apple Guideline 5.1.2(i) — AI Data Disclosure

Apple requires apps that use AI/ML features to disclose:

- What data is sent to external servers (user questions are sent to the URA Chatbot API).
- Whether conversations are stored (controlled by `STORE_RAW_PROMPTS` and `CONVERSATION_TTL_DAYS`).
- Whether data is used for model training (it is **not** — the production API does not feed user data back into training).

Include this disclosure in the App Store Connect privacy section and in the app's privacy policy. The app uses PII redaction before storage (see `ChatModel.redact_for_storage` in `App/backend/app/service.py`).

---

## 10. SLO Targets

| SLI | Target | Measurement |
|---|---|---|
| **Availability** | 99.9% (43 min downtime/month) | `1 - (http_errors_total{status=~"5.."} / http_requests_total)` |
| **Latency (p95)** | < 2 seconds | `http_request_duration_ms{quantile="0.95",path="/v1/chat"}` |
| **Voice Latency (p95)** | < 1.2 seconds | `voice_stream_total_latency_seconds{quantile="0.95"}` |
| **Voice TTS First Byte (p95)** | < 800 ms | `voice_stream_tts_first_chunk_seconds{quantile="0.95"}` |
| **Error rate** | < 1% | `rate(http_errors_total[5m]) / rate(http_requests_total[5m])` |
| **Faithfulness (p50)** | > 0.7 | `faithfulness_score{quantile="0.5"}` |

Set Prometheus alerting rules to fire when any SLO is breached over a 5-minute window.

---

## 11. Rollback Procedure

### Docker image rollback

```bash
# List available image tags
docker images landwind/ura-chatbot-api --format "{{.Tag}}\t{{.CreatedAt}}"

# Roll back to a known-good tag
IMAGE_TAG=v1.1.0 docker compose up -d api

# Or pull and pin a specific digest
docker compose pull api
docker compose up -d api
```

### Model rollback from Hugging Face Hub

```bash
# List model revisions
huggingface-cli repo info mpairweLandwind/ura-chatbot --revision main

# Pin to a specific commit
HF_MODEL_REPO=mpairweLandwind/ura-chatbot
HF_REVISION=abc123def  # known-good commit hash

# Restart API with pinned revision (set HF_REVISION in .env)
docker compose restart api
```

### Qdrant index rollback

Restore from a prior snapshot (see Section 7) if a bad re-index corrupts results:

```bash
# Stop API to prevent writes
docker compose stop api

# Restore snapshot
curl -X PUT http://localhost:6333/collections/ura_knowledge_base/snapshots/recover \
  -H 'Content-Type: application/json' \
  -d '{"location": "file:///qdrant/storage/snapshots/ura_knowledge_base/<good-snapshot>"}'

# Restart API
docker compose start api
```

---

## 12. Production Checklist

Run through every item before go-live:

- [ ] `.env` created from `.env.example` with all production values
- [ ] `APP_ENV=production` set
- [ ] `INDEX_API_KEY` set to a strong random value
- [ ] `CORS_ORIGINS` restricted to production domain only
- [ ] `STORE_RAW_PROMPTS=false` (privacy compliance)
- [ ] `OTEL_ENABLED=true` and collector endpoint configured
- [ ] TLS termination configured (Caddy or nginx)
- [ ] `docker compose build` succeeds cleanly
- [ ] `trivy image --config trivy.yaml <image>` shows zero CRITICAL/HIGH findings
- [ ] `GET /health` returns 200
- [ ] `GET /ready` returns `"status": "ready"` (not `"degraded"`)
- [ ] `GET /metrics` returns Prometheus text output
- [ ] Qdrant collection exists and has documents (`curl http://qdrant:6333/collections/ura_knowledge_base` or `http://localhost:16333/...` on shared hosts)
- [ ] Qdrant container health shows `healthy` (not `unhealthy` — check Docker healthcheck config if stuck)
- [ ] `DENSE_MODEL` / `DENSE_DIM` env vars match the indexed collection's vector dimensions (384 for MiniLM, 1024 for bge-m3)
- [ ] Rate limiting active (`RATE_LIMIT=30/minute`)
- [ ] Log rotation configured (Docker `json-file` driver with `max-size`)
- [ ] Backup cron job scheduled for analytics DB and Qdrant snapshots
- [ ] Prometheus scrape target added and Grafana dashboards imported
- [ ] DNS A/AAAA record points to production host
- [ ] Mobile app builds (`flutter build appbundle --release`, `flutter build ipa --release`)
- [ ] Apple 5.1.2(i) AI data disclosure completed in App Store Connect
- [ ] Google Play data safety section filled
- [ ] Rollback procedure tested with a known-good image tag
- [ ] SLO alerting rules loaded in Prometheus/Alertmanager
- [ ] `python scripts/validate_env.py --env production` passes with 0 errors
- [ ] `AUTH_DEV_SECRET` rotated from default value
- [ ] `LLM_TRUST_REMOTE_CODE=false` (OWASP LLM03 supply chain)
- [ ] `LLM_MODEL_REVISION` pinned to a specific commit SHA (SLSA v1.2)
- [ ] `LLM_LOAD_IN_4BIT=true` when running Qwen3-8B local Transformers on shared GPUs
- [ ] Qwen LoRA adapters mounted read-only at `/app/adapters`
- [ ] `WHISPER_DEVICE=cpu` unless a separate ASR GPU is intentionally allocated
- [ ] Container images signed with cosign (`cosign verify` passes)
- [ ] SLSA provenance attestation generated via `container-sign-provenance.yml`
- [ ] Frontend Vitest tests pass (`cd App/frontend && bun run test`)
- [ ] Frontend Lighthouse accessibility score >= 90
- [ ] AI red team evaluation passes (`python scripts/ai_red_team.py` >= 90% block rate)
- [ ] Bias & fairness audit passes (`python scripts/bias_fairness_audit.py` >= 70% parity)
- [ ] Incident response simulation passes (`python scripts/incident_response_sim.py`)
- [ ] Disaster recovery test passes (`bash scripts/dr_test.sh`)
- [ ] k6 load test validates SLOs (`k6 run tests/load/k6-chat-slo.js`)
- [ ] Monitoring stack operational (`docker compose --profile monitoring up`)
- [ ] Grafana dashboard shows live metrics
- [ ] Model Card reviewed and up-to-date (`docs/MODEL_CARD.md`)
- [ ] Privacy Impact Assessment completed (`docs/capstone/PIA.md`)

### v1.4.0 Additions — Quantization, Offline RAG & Voice-First

**Quantization Pipeline:**
- [ ] Quantization CI workflow tested (`python scripts/quantize_models.py --dry-run`)
- [ ] Quality gate passes (`python scripts/quantization_quality_gate.py`)
- [ ] Quantized models published to artifacts/ or HuggingFace Hub
- [ ] `FLAG_QUANTIZATION=true` set for quantized model serving

**Offline RAG:**
- [ ] Offline bundle built (`python scripts/export_offline_bundle.py`)
- [ ] Bundle size verified ≤ 150 MB compressed
- [ ] Bundle integrity SHA-256 checksums verified
- [ ] `FLAG_OFFLINE_RAG=true`, `FLAG_OFFLINE_SYNC=true`, `FLAG_OFFLINE_BUNDLE_API=true`
- [ ] Delta sync tested on 3G-equivalent network (< 12s for typical daily changes)
- [ ] `artifacts/offline/` volume mounted read-only on API container

**Mobile Bundle:**
- [ ] Mobile bundle assembled (`python scripts/export_mobile_bundle.py --validate-only`)
- [ ] Total size verified ≤ 800 MB
- [ ] Flutter app tested offline on mid-range Android (4GB RAM)
- [ ] On-device vector search latency < 180ms p95

**Voice-First Mobile:**
- [ ] Voice-first UI tested on target devices (Android 10+, 4GB RAM)
- [ ] Barge-in success rate measured ≥ 92%
- [ ] Voice + vision endpoint tested (`/v1/voice/vision/chat`)
- [ ] `FLAG_VOICE_FIRST_MOBILE=true`, `FLAG_VOICE_VISION=true`

**Quantized Serving (Optional GPU):**
- [ ] `docker compose --profile vllm-quantized up` starts successfully
- [ ] AWQ model loads within 180s start period
- [ ] p95 latency measured ≤ 1.8s for full RAG pipeline
- [ ] Memory usage measured ≥ 38% reduction from bfloat16 baseline
