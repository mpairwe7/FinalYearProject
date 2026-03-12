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
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=ura_knowledge_base

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
```

> Never commit `.env` to version control. The repo `.gitignore` already excludes it.

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
| `GET /health` | **Liveness** | Returns `{"status":"alive","version":"1.2.0"}` — always 200 if the process is up. |
| `GET /ready` | **Readiness** | Returns 200 with `status: "ready"` when both the ChatModel and Qdrant retriever are healthy. Returns 503 if the model failed to load, or `status: "degraded"` if Qdrant is unreachable (falls back to keyword search). |

Docker Compose already configures a healthcheck on the `api` service hitting `/health`. For Kubernetes or external uptime monitors, probe `/ready` instead.

Example external check:

```bash
curl -sf https://ura-chatbot.example.com/api/ready | jq .status
```

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
    image: qdrant/qdrant:v1.13.3
    environment:
      - QDRANT__CLUSTER__ENABLED=true
    command: ./qdrant --uri http://qdrant-0:6335
  qdrant-1:
    image: qdrant/qdrant:v1.13.3
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
- [ ] Qdrant collection exists and has documents (`curl http://qdrant:6333/collections/ura_knowledge_base`)
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
