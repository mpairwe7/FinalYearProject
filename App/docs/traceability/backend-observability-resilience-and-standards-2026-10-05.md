# Traceability Record — Backend Observability, Metrics Alignment, and 2026 Architecture Standards (2026-10-05)

> **Date:** 2026-10-05  
> **Scope:** Backend (`App/backend/app`), Observability, Monitoring, Logging, Persistence, and Router Modularity  
> **Status:** Shipped and Verified (1,285 tests passed, 40.20% coverage)

---

## 1. Executive Summary & Purpose

This release addresses foundational backend gaps identified during the October 2026 architectural audit of the URA Taxpayer Assistant:
1. **Prometheus Metrics Alignment**: Fixed metric namespace desynchronization and histogram bucketing between `analytics.py` and `monitoring/alerting-rules.yml` / Grafana dashboards.
2. **Distributed Tracing (OpenTelemetry)**: Added W3C `traceparent` extraction at API ingress and outbound header injection, ensuring end-to-end trace correlation across Next.js frontend and backend spans.
3. **Structured JSON Logging & PII Scrubbing**: Replaced plain-text formatting with `StructuredJsonFormatter` adhering to the OpenTelemetry Log Data Model and wired `PIISanitizingFilter` to scrub taxpayer PII from all log outputs.
4. **Streaming Analytics Observability**: Instrumented SSE and voice chat lifecycles to observe retrieval modes, faithfulness scores, and escalation events on streaming responses.
5. **RFC 3161 TSA Cryptographic Witnesses**: Extended the Merkle audit ledger with binary DER and JSON timestamp token ingestion, storage on `audit_anchors`, and verification during chain checks.
6. **Persistence Concurrency & Multi-Tenancy**: Added non-blocking thread offloading for synchronous database writes in async route handlers and completed `tenant_id` column isolation across `tickets`, `feedback`, `analytics_events`, and `sessions`.
7. **Router Modularity**: Partitioned connectors and offline endpoints into dedicated `app/routes/` sub-routers (`connectors.py`, `offline.py`) mounted onto FastAPI without altering OpenAPI route contracts.

---

## 2. Structural & Architectural Changes

### 2.1 Prometheus Metrics & Alerting Alignment (Gap 1.1 & 1.2)
* **Problem**: `MetricsStore` emitted bare metric names (`http_requests_total`, `chat_response_time_ms`) formatted as summaries. `monitoring/alerting-rules.yml` and Grafana queried `ura_` prefixed metrics and evaluated `histogram_quantile()` on `ura_chat_response_time_ms_bucket`. Because summaries do not export `_bucket` series, latency and availability alerts could never trigger.
* **Resolution**:
  - `MetricsStore.to_prometheus()` in `App/backend/app/analytics.py` now automatically dual-emits both the base name and the `ura_` prefixed alias.
  - Implemented standard Prometheus histogram duration buckets (`_LATENCY_MS_BUCKETS` and `_LATENCY_S_BUCKETS`) for all latency/duration metrics, emitting `_bucket{le="..."}`, `_count`, and `_sum`.
  - Added direct gauge emission for evaluation scores (`faithfulness_score` and `ura_faithfulness_score`).

### 2.2 OpenTelemetry W3C Distributed Tracing (Gap 2.1)
* **Problem**: Inbound HTTP requests carrying `traceparent` headers from client applications were not parsed at API ingress, causing backend spans to generate disconnected root trace IDs.
* **Resolution**:
  - Implemented `extract_trace_context()` in `App/backend/app/tracing.py` using `TraceContextTextMapPropagator`.
  - Updated `security_headers` middleware in `App/backend/app/main.py` to extract incoming trace context, attach it to OpenTelemetry execution context, and emit an outbound `traceparent` response header.
  - Added `traceparent` and `tracestate` to `CORSMiddleware` `allow_headers`.

### 2.3 Structured JSON Logging & PII Sanitization (Gap 3.1 & 3.3)
* **Problem**: `docs/MONITORING.md` claimed `structlog JSON` logging, while the code utilized standard library text formatting without trace correlation or log-level PII protection.
* **Resolution**:
  - Created `App/backend/app/logging_config.py` containing `StructuredJsonFormatter` (ISO 8601 UTC timestamps, level, logger, message, service name, `trace_id`, `span_id`, and exception formatting).
  - Implemented `PIISanitizingFilter` that automatically scrubs TINs, phone numbers, and email addresses (preserving `@ura.go.ug` domains) before log emission.
  - Integrated via `configure_logging()` in `main.py`, controlled by `LOG_FORMAT=json|text` (defaulting to JSON in production).

### 2.4 Streaming Analytics Instrumentation (Gap 5.1)
* **Problem**: Analytics middleware parsed `response._body` for `/v1/chat`, which is absent on `StreamingResponse` and `EventSourceResponse` in Starlette, causing 90%+ of taxpayer interactions to be invisible in metrics.
* **Resolution**:
  - Directly instrumented `_log_stream_conversation` and `voice_chat` in `App/backend/app/main.py` to record `retrieval_mode_total`, `faithfulness_score`, `escalation_required_total`, and `chat_response_time_ms`.

### 2.5 RFC 3161 TSA Cryptographic Witnessing (Gap 4.1 & 4.2)
* **Problem**: `request_rfc3161_timestamp` only sent JSON payloads and did not persist tokens to `audit_anchors`, nor were tokens validated during ledger checks.
* **Resolution**:
  - Updated `request_rfc3161_timestamp()` in `App/backend/app/audit/ledger.py` to support standard DER `application/timestamp-reply` (base64 encoded) and JSON mock responses.
  - Added `tsa_token` column migration to `audit_anchors` in both `database.py` and `postgres.py`.
  - Extended `verify_anchor()` in `App/backend/app/audit/verifier.py` to validate persisted timestamp tokens.

### 2.6 Persistence Concurrency & Multi-Tenancy (Gap 1.2 & 2.3)
* **Problem**: Synchronous database logging in async endpoints blocked the event loop. In addition, `tickets`, `feedback`, `analytics_events`, and `sessions` lacked `tenant_id` columns.
* **Resolution**:
  - Offloaded database logging in `chat_stream` and `voice_chat` to worker threads via `asyncio.to_thread`.
  - Added `tenant_id` columns, defaults, indexes, and query filters across `tickets`, `feedback`, `analytics_events`, and `sessions` on both SQLite and PostgreSQL engines.
  - Maintained signature parity across `database.py` and `postgres.py`.

### 2.7 Modular Domain Routing (Gap 1.1)
* **Problem**: `main.py` was a 5,032-line monolith registering all routes on the global `app` instance.
* **Resolution**:
  - Created `App/backend/app/routes/` with modular `connectors.py` and `offline.py` APIRouters.
  - Mounted onto `app` via `app.include_router()`, preserving 100% of the 70+ route contracts tested by `tests/test_all_endpoints_e2e.py`.

---

## 3. Verification & Evidence

1. **Manifest & API Route Parity**: `pytest tests/test_all_endpoints_e2e.py` passed (26/26 test suites green).
2. **Agent & Chaos Test Suite**: `pytest tests/agents tests/chaos` passed (1,030 tests green).
3. **Full Regression Suite with Coverage**: `pytest tests/ --cov=ml --cov=App/backend --cov-fail-under=35` passed (1,285 tests green, 40.20% coverage).
4. **Live Verification**:
   - PII Sanitization & JSON formatting: `PASS`
   - W3C Traceparent extraction & injection: `PASS`
   - Prometheus metric dual-emission & histogram buckets: `PASS`
   - RFC 3161 timestamping & ledger verification: `PASS`
