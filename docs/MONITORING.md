# Monitoring & Observability Guide

Production observability for the URA Chatbot FastAPI backend, covering
distributed tracing, metrics, log aggregation, and LLM-specific monitoring.

---

## 1. Architecture Overview

```
                        +-------------+
   FastAPI App          |   Jaeger /  |
  +--------------+      |   Tempo     |
  | tracing.py   |----->| (OTLP gRPC)|      +----------+
  | (OTel SDK)   |      +-------------+      | Grafana  |
  +--------------+                            | Dashboards|
  | analytics.py |----> /metrics -----------> | <--------+
  | (Prometheus  |      (text exposition)     +----------+
  |  middleware)  |                                 ^
  +--------------+      +-------------+            |
  | structlog    |----->| Loki / ELK  |------------+
  | JSON logs    |      +-------------+
  +--------------+
```

| Signal  | Producer                     | Collector        | Storage/UI       |
|---------|------------------------------|------------------|------------------|
| Traces  | `tracing.py` (OpenTelemetry) | OTLP gRPC :4317  | Jaeger / Tempo   |
| Metrics | `analytics.py` (Prometheus)  | Prometheus scrape | Prometheus + Grafana |
| Logs    | `structlog` JSON             | Promtail / Filebeat | Loki / Elasticsearch |

---

## 2. Enabling Observability

### Environment variables (`.env`)

```bash
# OpenTelemetry (opt-in)
OTEL_ENABLED=true
OTEL_SERVICE_NAME=ura-chatbot-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317

# Logging
LOG_LEVEL=info          # production: info, debug only in dev
```

`tracing.py` reads these on startup inside the FastAPI lifespan handler
(`main.py` -> `init_tracing()`). When `OTEL_ENABLED=false` (default), all
tracing and metric helpers are no-ops with zero overhead.

### Required packages

```
opentelemetry-api
opentelemetry-sdk
opentelemetry-exporter-otlp-proto-grpc
```

These are optional dependencies; the app functions without them (`ImportError`
is caught gracefully in `tracing.py`).

---

## 3. Key Metrics

All metrics are emitted by the `MetricsStore` singleton in
`App/backend/app/analytics.py` and exposed at `GET /metrics`.

### HTTP layer (AnalyticsMiddleware)

| Metric                       | Type      | Labels                       | Description                     |
|------------------------------|-----------|------------------------------|---------------------------------|
| `http_requests_total`        | counter   | `method`, `path`, `status`   | Total HTTP requests             |
| `http_request_duration_ms`   | summary   | `method`, `path`             | Request latency histogram       |
| `http_errors_total`          | counter   | `method`, `path`, `status`   | Requests returning >= 400       |

### Chat / RAG layer

| Metric                       | Type      | Labels       | Description                             |
|------------------------------|-----------|--------------|-----------------------------------------|
| `chat_requests_total`        | counter   | --           | Total `/v1/chat` requests               |
| `chat_response_time_ms`      | summary   | --           | End-to-end chat latency                 |
| `retrieval_mode_total`       | counter   | `mode`       | Distribution: `hybrid`, `keyword`, etc. |
| `faithfulness_score`         | summary   | --           | Grounding faithfulness (0-1)            |
| `escalation_total`           | counter   | --           | Human-escalation events                 |
| `escalation_required_total`  | counter   | --           | Escalation flagged in middleware         |
| `feedback_total`             | counter   | `rating`     | User feedback (`up` / `down`)           |
| `classification_errors_total`| counter   | --           | Classifier failures during chat         |

### OpenTelemetry metrics (via `tracing.py`, exported to OTLP)

| Metric                           | Type      | Attributes              |
|----------------------------------|-----------|-------------------------|
| `gen_ai.client.token.usage`      | counter   | `gen_ai.token.type`     |
| `gen_ai.retrieval.duration`      | histogram | --                      |
| `gen_ai.retrieval.results`       | counter   | --                      |

---

## 4. Prometheus Setup

### Scrape config (`prometheus.yml`)

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "ura-chatbot-api"
    metrics_path: /metrics
    static_configs:
      - targets: ["api:8000"]
        labels:
          env: "production"
          service: "ura-chatbot"

  - job_name: "qdrant"
    metrics_path: /metrics
    static_configs:
      - targets: ["qdrant:6333"]
```

### Docker Compose addition

```yaml
services:
  prometheus:
    image: prom/prometheus:v2.53.0
    container_name: ura-prometheus
    volumes:
      - ./infra/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    ports:
      - "9090:9090"
    networks:
      - ura-network

  grafana:
    image: grafana/grafana:11.1.0
    container_name: ura-grafana
    ports:
      - "3001:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=admin
    volumes:
      - grafana_data:/var/lib/grafana
    networks:
      - ura-network

volumes:
  grafana_data:
```

### Verifying the endpoint

```bash
curl -s http://localhost:8000/metrics | head -20
```

Expected output (Prometheus text exposition format):

```
# TYPE http_requests_total counter
http_requests_total{method="GET",path="/health",status="200"} 42
# TYPE http_request_duration_ms summary
http_request_duration_ms{quantile="0.5",method="POST",path="/v1/chat"} 145.3200
http_request_duration_ms{quantile="0.95",method="POST",path="/v1/chat"} 892.1100
```

---

## 5. Grafana Dashboards

### Recommended panels

| Panel                 | Query (PromQL)                                                                     |
|-----------------------|------------------------------------------------------------------------------------|
| Request rate          | `rate(http_requests_total[5m])`                                                    |
| Error rate (%)        | `rate(http_errors_total[5m]) / rate(http_requests_total[5m]) * 100`                |
| p95 latency           | `http_request_duration_ms{quantile="0.95",path="/v1/chat"}`                        |
| Chat throughput       | `rate(chat_requests_total[5m])`                                                    |
| Cache hit ratio       | `rate(retrieval_mode_total{mode="cache"}[5m]) / rate(chat_requests_total[5m])`     |
| Faithfulness dist.    | `faithfulness_score{quantile="0.5"}` (median, p95, p99)                            |
| Escalation rate       | `rate(escalation_total[5m])`                                                       |
| Retrieval mode split  | `rate(retrieval_mode_total[5m])` grouped by `mode`                                 |
| Token usage           | `rate(gen_ai_client_token_usage[5m])` by `gen_ai.token.type`                       |
| Qdrant query latency  | `gen_ai_retrieval_duration` (histogram from OTLP)                                  |
| Feedback ratio        | `rate(feedback_total{rating="up"}[1h]) / rate(feedback_total[1h])`                 |

### Dashboard layout (3 rows)

1. **Service Health** -- Request rate, error rate, p95 latency, uptime
2. **RAG Pipeline** -- Retrieval mode split, Qdrant latency, cache hit ratio, faithfulness
3. **LLM & Safety** -- Token usage, escalation rate, guardrail triggers, feedback ratio

---

## 6. Alerting Rules

Save as `alert_rules.yml` and load in Prometheus via `rule_files:`.

```yaml
groups:
  - name: ura_chatbot_alerts
    rules:

      # Error rate > 1% sustained for 5 minutes
      - alert: HighErrorRate
        expr: |
          (
            rate(http_errors_total[5m])
            / rate(http_requests_total[5m])
          ) > 0.01
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Error rate above 1% for 5 minutes"
          description: "Current error rate: {{ $value | humanizePercentage }}"

      # p95 chat latency > 2 seconds for 5 minutes
      - alert: HighChatLatency
        expr: |
          http_request_duration_ms{quantile="0.95",path="/v1/chat"} > 2000
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "p95 chat latency exceeds 2 seconds"

      # Availability < 99.9% over 1 hour
      - alert: LowAvailability
        expr: |
          (
            1 - (
              rate(http_errors_total{status=~"5.."}[1h])
              / rate(http_requests_total[1h])
            )
          ) < 0.999
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "Service availability below 99.9% SLO"

      # Disk usage > 80% (node_exporter required)
      - alert: HighDiskUsage
        expr: |
          (node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}) < 0.2
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Root disk usage above 80%"

      # Escalation rate spike
      - alert: HighEscalationRate
        expr: |
          rate(escalation_total[15m]) > 0.1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Sustained high human-escalation rate"
```

---

## 7. SLO Definitions

| SLO              | Target  | Window   | Metric basis                                                  |
|-------------------|---------|----------|---------------------------------------------------------------|
| Availability      | 99.9%   | 30 days  | `1 - (5xx responses / total requests)`                        |
| Latency (chat)    | p95 < 2s| 30 days  | `http_request_duration_ms{quantile="0.95",path="/v1/chat"}`   |
| Error budget      | 0.1%    | 30 days  | Max 43.2 minutes of downtime / month                          |
| Faithfulness      | median > 0.6 | 7 days | `faithfulness_score{quantile="0.5"}`                      |

### Error budget burn-rate alerting

```
Short window (1h):  burn rate > 14.4x  -> page
Medium window (6h): burn rate >  6x    -> ticket
Long window (3d):   burn rate >  1x    -> review
```

---

## 8. LLM-Specific Observability

### Token usage tracking

`tracing.py` -> `record_token_usage()` emits `gen_ai.client.token.usage` with
`gen_ai.token.type` = `input` | `output`. Track daily token consumption to
manage costs and detect anomalous query patterns.

```python
# In service.py, after LLM generation:
from .tracing import record_token_usage
record_token_usage(prompt_tokens=est_prompt, completion_tokens=est_completion)
```

### Guardrail trigger rates

Metric: `classification_errors_total`, `escalation_total`, plus structured log
events from `guardrails.py` (`InputGuard.check()` and `OutputGuard` methods).

Key signals:
- **Prompt injection blocks**: grep logs for `"injection_detected": true`
- **Abstention rate**: `retrieval_mode_total{mode="abstained"}` / total chats
- **PII redaction events**: structured log with `event=pii_redacted`

### Faithfulness score distribution

The `faithfulness_score` summary metric (emitted by `AnalyticsMiddleware`) gives
p50/p95/p99. A drop in median faithfulness below 0.5 likely signals:
- Stale or corrupted Qdrant index
- Embedding model drift
- New query patterns outside training distribution

### Escalation rate

`escalation_total` fires when `OutputGuard.should_escalate()` returns `True`
(faithfulness below `ESCALATION_THRESHOLD` or zero retrieval hits). Target: < 5%
of chat sessions.

---

## 9. Log Aggregation

### Structured JSON logging

Configure structlog (or stdlib) to emit JSON lines:

```python
import logging, json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        return json.dumps(log)
```

### Correlation via X-Request-ID

The security headers middleware in `main.py` validates and propagates
`X-Request-ID` on every response. Use this to correlate:

1. **HTTP request log** (`main.py` middleware) -> `request_id=<uuid>`
2. **OpenTelemetry span** -> set `trace_id` + `request_id` as span attribute
3. **Application log** -> inject `request_id` via logging context

```python
# Correlation lookup in Grafana/Loki:
#   {app="ura-chatbot"} | json | request_id="abc-123"
```

### Log levels (production)

| Level   | Usage                                               |
|---------|-----------------------------------------------------|
| ERROR   | Unhandled exceptions, database failures              |
| WARNING | Degraded retrieval, guardrail triggers, PII redacted |
| INFO    | Request lifecycle, startup/shutdown, model load      |
| DEBUG   | Cache hits, token counts, individual passage scores  |

Production default: `LOG_LEVEL=info`. Set `LOG_LEVEL=debug` only in
development or during incident investigation.

---

## 10. Incident Response Playbook

### Slow responses (p95 > 2s)

1. **Check Grafana** -- `http_request_duration_ms{quantile="0.95"}` by path.
2. **Identify stage** -- Look at OpenTelemetry trace in Jaeger:
   - `rag.embed` slow? -> Embedding model resource contention.
   - `rag.search` slow? -> Qdrant overloaded; check collection size and segment count.
   - `rag.rerank` slow? -> Cross-encoder batch size too large; reduce `top_k`.
   - `rag.generate` slow? -> LLM inference bottleneck; check GPU utilization.
3. **Check per-stage timings** -- Each `trace_stage()` call records
   `rag.<stage>.duration_ms` as a span attribute. The parent `rag.pipeline`
   span aggregates all stage durations.

```bash
# Quick check via the analytics dashboard API:
curl -s http://localhost:8000/v1/analytics/dashboard | python -m json.tool
```

### Failed retrievals

1. **Symptom**: `retrieval_mode_total{mode="keyword"}` spikes (hybrid fallback).
2. **Check Qdrant health**: `curl http://localhost:6333/healthz`
3. **Check readiness endpoint**: `curl http://localhost:8000/ready`
   - `retrieval_mode: "keyword"` confirms Qdrant is down.
4. **Recovery**: Restart Qdrant container, then trigger re-index:
   ```bash
   curl -X POST http://localhost:8000/v1/index \
     -H "Authorization: Bearer $INDEX_API_KEY"
   ```

### LLM errors / hallucinations

1. **Symptom**: `faithfulness_score{quantile="0.5"}` drops below 0.5.
2. **Check escalation rate**: `rate(escalation_total[1h])`.
3. **Inspect traces**: Filter Jaeger for `gen_ai.faithfulness_score < 0.3`.
4. **Common causes**:
   - Qdrant index is stale -- re-index documents.
   - Grounding threshold too low -- raise `GROUNDING_THRESHOLD` in `.env`.
   - New topics not in knowledge base -- add relevant documents and re-index.
5. **Guardrail check**: Verify `ABSTENTION_THRESHOLD` and
   `ESCALATION_THRESHOLD` values in `.env` are appropriate.

### High error rate (> 1%)

1. **Identify error codes**: `http_errors_total` by `status` label.
2. **503 errors**: Model not initialized -- check startup logs.
3. **429 errors**: Rate limiting -- review `RATE_LIMIT` setting.
4. **500 errors**: Search logs by `request_id` for stack traces.

---

## Quick Reference: File Locations

| File                                 | Purpose                              |
|--------------------------------------|--------------------------------------|
| `App/backend/app/tracing.py`        | OpenTelemetry setup, span/metric helpers |
| `App/backend/app/analytics.py`      | Prometheus metrics store + middleware |
| `App/backend/app/main.py`           | `/metrics` endpoint, X-Request-ID    |
| `App/backend/app/guardrails.py`     | Security guardrails (alert signals)  |
| `App/backend/app/service.py`        | RAG pipeline (trace instrumentation) |
| `.env.example`                       | All observability env vars           |
| `docker-compose.yml`                | Container orchestration              |
