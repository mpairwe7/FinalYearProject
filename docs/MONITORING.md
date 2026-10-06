# Monitoring & Observability Guide

How the URA chatbot is measured, alerted on and traced, and what to do when an
alert fires. Rewritten 2026-10-06 after an audit found the previous pipeline
dark end to end (gaps G99–G112 in
[`GAPS_AND_AGENTIC_ROADMAP.md`](GAPS_AND_AGENTIC_ROADMAP.md)): Prometheus got
401 from `/metrics`, counters differed per uvicorn worker, alert rules matched
no series, and nothing routed an alert anywhere.

Everything below is checked in CI: rule logic by `promtool test rules`
(`monitoring/tests/`), and the agreement between rules, dashboard, SLOs,
runbook links and the metrics the code really emits by
`tests/test_monitoring_config.py`.

## Quick start

```bash
# .env (never committed): the API and Prometheus share the scrape token
METRICS_TOKEN=$(openssl rand -hex 32)
GRAFANA_PASSWORD=$(openssl rand -base64 24)
ALERTMANAGER_WEBHOOK_URL=https://<your chat relay / ntfy topic / incident webhook>
OTEL_ENABLED=true                                   # traces + OTLP logs

docker compose --profile monitoring up -d            # + --profile monitoring-gpu for DCGM
```

| UI | Address (bound to 127.0.0.1 — tunnel or proxy with auth to reach it) |
| --- | --- |
| Grafana | http://127.0.0.1:3001 (user `admin`, password `GRAFANA_PASSWORD`) |
| Prometheus | http://127.0.0.1:9090 |
| Alertmanager | http://127.0.0.1:9093 |
| Jaeger | http://127.0.0.1:16686 |
| Loki (API) | http://127.0.0.1:3100 |

There is no committed default password. An unset `GRAFANA_PASSWORD` leaves
Grafana's first-login password, which it makes you change.

## 1. Architecture

```
 uvicorn workers (N)                          monitoring profile
 ┌──────────────────────┐   scrape + Bearer METRICS_TOKEN   ┌────────────┐   ┌──────────────┐
 │ app.analytics facade ├──────────── /metrics ────────────►│ Prometheus ├──►│ Alertmanager ├──► webhook
 │ (prometheus_client,  │  (all workers aggregated)          │  rules,    │   └──────────────┘
 │  PROMETHEUS_MULTI-   │                                    │  SLO burn  │◄── blackbox, redis-, node-,
 │  PROC_DIR)           │                                    └─────┬──────┘    dcgm-exporter, vLLM
 │ app.tracing (OTel)   ├── OTLP gRPC ──► OTel Collector ──► Jaeger │ (traces, tail-sampled)
 │ app.logging_config   ├── OTLP logs ──►       │       ──► Loki    │ (logs, 93 days)
 │ stdout JSON lines    │                       └──── :8889 ──► Prometheus (gen_ai.* metrics)
 └──────────────────────┘                                          ▼
                                                                Grafana (Prometheus + Loki + Jaeger, linked)
```

| Signal | Producer | Transport | Store / UI |
| --- | --- | --- | --- |
| Metrics | `App/backend/app/analytics.py` (prometheus_client) | Prometheus scrape of `/metrics` with `METRICS_TOKEN` | Prometheus, Grafana |
| GenAI metrics | `App/backend/app/tracing.py` | OTLP → Collector → `:8889` | Prometheus |
| Traces | `tracing.py` + FastAPI/httpx instrumentation | OTLP → Collector (tail sampling) | Jaeger |
| Logs | `logging_config.py` (root handler) | stdout JSON **and** OTLP → Collector | platform log pane; Loki |
| Real users | `App/frontend/src/lib/web-vitals.ts`, `client-errors.ts` | `POST /v1/telemetry/{vitals,errors}` | Prometheus |
| Synthetic | blackbox exporter | HTTP probes of `/health`, `/ready`, the frontend | Prometheus |

Single-container deployments (Crane Cloud, the HF Space) have no Collector:
logs go to stdout as JSON (the platform captures them) and `/metrics` is
readable with `METRICS_TOKEN` by any external Prometheus.

## 2. Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `METRICS_TOKEN` | unset | Bearer credential for `/metrics` (≥ 32 chars in production). Unset: only staff/operator auth can read it. |
| `PROMETHEUS_MULTIPROC_DIR` | `/tmp/ura-prometheus` (set by `entrypoint.sh` / supervisord) | Where each uvicorn worker writes samples; emptied before workers start. |
| `METRICS_MAX_SERIES_PER_METRIC` | `500` | Label sets per metric before new ones fold into `__other__`. |
| `OTEL_ENABLED` | `false` | Traces, OTLP logs and GenAI metrics. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` (compose) | Collector address. |
| `OTEL_TRACES_SAMPLER` / `_ARG` | parent-based always-on | Standard OTel sampling; the Collector tail-samples further. |
| `OTEL_LOGS_EXPORTER` | `otlp` | `none` keeps logs on stdout only. |
| `LOG_FORMAT` | `json` in production and compose, else text | `json` \| `text`. |
| `LOG_LEVEL` | `info` | Root and `app.*` level; `httpx`/`httpcore` stay at WARNING. |
| `ONLINE_EVAL_INTERVAL_SECONDS` | `21600` | Scheduled evaluation of recent conversations; `0` = off. |
| `ALERTMANAGER_WEBHOOK_URL` | unset | Where every alert is delivered (compose secret). |
| `AUDIT_TSA_URL`, `AUDIT_TSA_CA_CERT` | unset | RFC 3161 timestamps on audit seals ([runbook](runbooks/audit-trail.md#trusted-timestamps-rfc-3161)). |

## 3. Metrics

All API metrics go through the facade in `app/analytics.py`:
`metrics.inc(name, labels=…)`, `metrics.observe(name, value, labels=…)`,
`metrics.set_gauge(…)`, `metrics.add_gauge(…)`. Rules the facade enforces:

* **One namespace.** Every family is exported as `ura_<name>` exactly once.
  The bare-name duplicates the old store emitted are gone.
* **Real histograms.** Cumulative buckets chosen by unit suffix (`_ms`,
  `_seconds`/`_s`, `_bytes`, scores 0–1). `histogram_quantile()` and `rate()`
  are valid; nothing is recomputed from a sliding window.
* **All workers.** With `PROMETHEUS_MULTIPROC_DIR` set, one scrape returns the
  sum over every uvicorn worker. Up/down gauges (active connections) use
  `livesum`; latest-value gauges (`build_info`, eval results, timestamps) use
  `mostrecent`.
* **Bounded labels.** Request metrics are labelled with the route template
  (`/v1/tickets/{ticket_id}`, or `__unmatched__` for 404s), never the raw path.
  Over `METRICS_MAX_SERIES_PER_METRIC` label sets per family, new ones fold
  into `__other__`.

The staff analytics page reads `GET /v1/analytics/dashboard`, which takes the
same aggregated view (`metrics.snapshot()`), so it no longer shows one random
worker's numbers.

### HTTP and chat turns

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `ura_http_requests_total` | counter | `method`, `path` (route template), `status` | Every request |
| `ura_http_request_duration_ms` | histogram | `method`, `path` | Time to response headers |
| `ura_http_errors_total` | counter | `method`, `path`, `status` | Responses ≥ 400 |
| `ura_chat_turns_total` | counter | `channel`, `outcome` | One per chat turn on **every** transport. `channel`: `rest`, `sse`, `ws`, `voice`. `outcome`: `answered`, `abstained`, `clarification`, `escalated`, `blocked`, `out_of_scope`, `error` |
| `ura_chat_response_time_ms` | histogram | `channel`, `mode` | Whole-turn latency; `mode` is the retrieval mode |
| `ura_retrieval_mode_total` | counter | `mode` | Retrieval mode per turn |
| `ura_faithfulness_score` | histogram | — | Grounding score per scored turn |
| `ura_escalation_required_total` | counter | `channel` | Turns handed to a person |
| `ura_escalation_requested_total` | counter | `outcome` | Taxpayer-initiated handoffs (`POST /v1/escalate`) |
| `ura_feedback_total` | counter | `rating` | Thumbs up/down |
| `ura_chat_ws_active_connections`, `ura_voice_*` | gauge / counter / histogram | — | WebSocket and voice sessions (were never exposed before) |

Answer-integrity counters (`ura_contradicted_reply_withheld_total`,
`ura_reply_localization_figures_changed_total{locale}`,
`ura_reply_localization_protected_retry_total{locale,reason}`,
`ura_numeric_verification_rejected_total`, `ura_numeric_revision_*`) count
times a guard withheld or replaced something. A rising count is the guard
working; a *sustained* rise says a generation or translation tier degraded.
`protected_retry` counts a recovered condition: figures are masked before
translation and retried unmasked when the tier mangles the sentinels.

### Models, tools and quality

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `ura_llm_requests_total` | counter | `provider`, `model`, `operation`, `status` | Every model call (vLLM, Workers AI, Gemini) |
| `ura_llm_request_duration_seconds` | histogram | `provider`, `model`, `operation` | Call duration |
| `ura_llm_time_to_first_token_seconds` | histogram | same | Streaming calls only |
| `ura_llm_tokens_total` | counter | same + `type` (`input`/`output`) | From the provider's `usage` block — not a word count |
| `ura_tool_calls_total`, `ura_tool_call_duration_seconds` | counter / histogram | `tool`, `status` | Every MCP tool call |
| `ura_model_usage_total`, `ura_model_fallback_total`, `ura_model_tier_total` | counter | `task`, `model`, … | Routing decisions (`providers/routing.py`) |
| `ura_eval_metric{name,backend}`, `ura_eval_metric_passed{name}` | gauge | | Latest online evaluation (§8) |
| `ura_eval_last_run_timestamp_seconds`, `ura_eval_samples` | gauge | | When it ran, on how many turns |
| `ura_web_vitals_ms{metric,rating,route}`, `ura_web_vitals_cls{rating,route}` | histogram | | Real-user Core Web Vitals |
| `ura_client_errors_total{kind,route}` | counter | | Browser errors and error-boundary renders |

### Audit, security and housekeeping

| Metric | Labels | Meaning |
| --- | --- | --- |
| `ura_audit_append_failed_total` | `event_type` | Ledger appends that failed — including `generate` (answers) |
| `ura_audit_seals_total`, `ura_audit_seal_failed_total` | `trigger` | Seals made / failed |
| `ura_audit_last_seal_timestamp_seconds` | — | Last successful seal |
| `ura_audit_chain_breaks_total` | `scope` | Integrity check found a break |
| `ura_audit_tsa_tokens_total`, `ura_audit_tsa_failures_total` | `reason` | RFC 3161 timestamps obtained / missed |
| `ura_security_events_total` | `event` | OWASP-vocabulary security events (§6) |
| `ura_retention_runs_total`, `ura_retention_last_success_timestamp_seconds` | `status` | Personal-data retention job |
| `ura_storage_world_writable` | — | 1 when `ANALYTICS_DB_DIR` is world-writable |
| `ura_build_info` | `version`, `revision`, `environment` | Which build is answering |
| `ura_qdrant_index_*`, `ura_qdrant_backup_*`, `ura_qdrant_restore_drill_*` | — | Index lifecycle (read from status files at scrape time) |

## 4. SLOs and alerting

The SLOs are defined once, in OpenSLO v1:
[`monitoring/slo/ura-chatbot.openslo.yaml`](../monitoring/slo/ura-chatbot.openslo.yaml).

| SLO | Objective | Window | SLI (recording rule) |
| --- | --- | --- | --- |
| API availability | 99.9% of requests not 5xx | 30 days | `ura:http_errors_5xx:ratio_rate{5m,30m,1h,2h,6h,1d,3d}` |
| Chat latency (NFR-01) | 95% of turns within 3 s | 30 days | `ura:chat_turns_slow:ratio_rate{5m,30m,1h,6h}` |

The p95 targets that used to disagree (2 s here, 3 s in k6) are now one: the
3 s NFR-01 objective, measured as a share of turns. Generation latency is
reported apart (`ura:chat_response_time_ms:p95_by_mode_5m`): one A6000 does
not meet 3 s for hybrid generation from 4 concurrent turns
([capacity-slo](runbooks/capacity-slo.md)), and a blended figure hides that.

Burn-rate alerts follow the multi-window, multi-burn-rate method (Google SRE
workbook): page when the 1h **and** 5m windows burn faster than 14.4× the
budget (or 6h and 30m faster than 6×); ticket on 1d/2h at 3× or 3d/6h at 1×.

`monitoring/alerting-rules.yml` holds 37 rules in seven groups:
availability, latency, quality, audit-and-compliance, security, dependencies
and the Qdrant index lifecycle. Each carries `severity` (`critical` pages,
`warning` tickets), `team` and a `runbook_url` to a heading below or to a
runbook. Ratios aggregate with `sum()` before dividing — the old rules
divided each error series by its own request series and saw 100%.

Alertmanager (`monitoring/alertmanager.yml`) sends everything to the webhook
in `ALERTMANAGER_WEBHOOK_URL`, critical alerts every hour until resolved,
warnings every four. While `UraApiDown` fires, the API's other backend alerts
are inhibited as symptoms.

Test rule changes before pushing:

```bash
docker run --rm -v "$PWD/monitoring:/m:ro" -w /m --entrypoint promtool \
  prom/prometheus:v3.13.4 test rules tests/alerting-rules.test.yml
python -m pytest tests/test_monitoring_config.py -q
```

## 5. Tracing

Off unless `OTEL_ENABLED=true`. `app/tracing.py` sets up the SDK with
`service.name`, `service.version`, `deployment.environment.name` and a
per-worker `service.instance.id`, instruments FastAPI (server span per
request; inbound W3C `traceparent` honoured) and httpx (client spans;
`traceparent` injected into calls to vLLM, Qdrant, Sunbird, Cloudflare and MCP
servers), and bridges Python logging to OTLP.

GenAI attributes follow the OpenTelemetry GenAI semantic conventions, which
are still at **Development** status (moved to
`open-telemetry/semantic-conventions-genai` in June 2026) — expect renames:

| Span | Name | Key attributes |
| --- | --- | --- |
| Turn | `invoke_agent ura-assistant` | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, `gen_ai.usage.*` (turn total), `rag.stage.*.duration_ms`, events `gen_ai.evaluation.result` |
| Pipeline stage | `rag.<stage>` | `rag.<stage>.duration_ms` |
| Model call | `chat <model>` | `gen_ai.provider.name` (`vllm`, `cloudflare.workers_ai`, `gcp.gemini`), `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`/`output_tokens`, `gen_ai.response.finish_reasons`, `error.type` |
| Tool call | `execute_tool <tool>` | `gen_ai.tool.name`, `gen_ai.tool.call.id`, `gen_ai.tool.type` |

OTel metrics `gen_ai.client.operation.duration` (s) and
`gen_ai.client.token.usage` ({token}) are histograms, as the conventions
specify. Prompt and completion text are never recorded.

Responses carry `X-Request-ID` and, when a span is active, a W3C
`traceresponse` header (both exposed to browsers through CORS). The API no
longer returns a `traceparent` it invented. The Collector keeps every errored
or slower-than-3 s trace and 25% of the rest.

## 6. Logging

`app/logging_config.install_logging()` puts one handler on the **root**
logger, so the API's records, uvicorn's access and error lines and every
library's records share one format. JSON lines (production, compose) carry:

```json
{"timestamp": "2026-10-06T08:15:02.113+00:00", "level": "WARNING", "severity_text": "WARNING",
 "severity_number": 13, "logger": "app.security", "message": "security event authz_fail",
 "service": "ura-chatbot-api", "service.version": "1.2.0", "deployment.environment": "production",
 "trace_id": "…", "span_id": "…", "request_id": "7d1c…",
 "attributes": {"event": "authz_fail", "http_route": "/v1/admin/tickets", "http_status": 403, "actor": "…"}}
```

* `request_id` is on every record written while serving a request (a context
  variable set by the request middleware), not only the access line.
* Anything passed with `extra=` lands in `attributes`. Search Loki with
  `{service_name="ura-chatbot-api"} | event="audit.seal"` and the like.
* Redaction (`guardrails.redact_pii_text`) covers the message, its arguments,
  `extra` fields and tracebacks, on stdout and OTLP alike. It is pattern-based
  (TINs, phone numbers, e-mails, NINs, cards) and cannot recognise names, so
  code must still not log free text it does not need.
* Loki keeps logs 93 days. A local Loki volume is not write-once storage —
  ship the same stream to object storage with object lock where logs are
  audit evidence ([audit-trail runbook](runbooks/audit-trail.md#seals)).

### Security events

401, 403 and 429 responses are logged as OWASP Logging Vocabulary events by
`app/security_events.py` and counted in `ura_security_events_total{event}`:
`authn_login_fail` (with the token rejection reason), `authz_fail`,
`excess_rate_limit_exceeded`. Records name the route, status and authenticated
actor; never the token or client IP.

## 7. Real-user monitoring

The browser measures Core Web Vitals with Google's `web-vitals` library (v6,
soft navigations on) and posts batches to `POST /v1/telemetry/vitals` when the
page is hidden — only with analytics consent. Uncaught errors, unhandled
rejections and error-boundary renders go to `POST /v1/telemetry/errors`
(error class, Next.js digest, script location, route; **never the message**).
Both endpoints are anonymous, rate-limited and fold ids out of routes. The
Next.js server logs `onRequestError` as JSON (`src/instrumentation.ts`).

Thresholds are assessed at the **75th percentile** of real users (Core Web
Vitals, unchanged for 2026):

| Metric | Good | Poor |
| --- | --- | --- |
| LCP | ≤ 2.5 s | > 4 s |
| INP | ≤ 200 ms | > 500 ms |
| CLS | ≤ 0.1 | > 0.25 |
| FCP | ≤ 1.8 s | > 3 s |
| TTFB | ≤ 800 ms | > 1.8 s |

Lighthouse CI (`lighthouse-live.yml`) stays as the lab check; the field data
above is what users experience.

## 8. Online evaluation

Every `ONLINE_EVAL_INTERVAL_SECONDS` (6 h by default) each worker re-scores a
sample of recent conversations with the evaluation harness
(`app/evaluation.py`) and publishes `ura_eval_metric` /
`ura_eval_metric_passed`. `POST /v1/evaluate` (ops key) runs one on demand.
Offline, CI scores the golden sets (`ml.pipelines.evaluate_rag`) and the
production gate fails a pull request when any metric drops more than 0.03
below the committed baseline (`ml/configs/quality_baseline.json`), as well
as when it misses an absolute threshold.

## 9. Dashboard

`monitoring/grafana/dashboards/ura-chatbot-overview.json` (provisioned):
service health and SLOs (budget left, slow-turn share, p95 by transport and by
mode, probes), conversations (outcomes, containment, retrieval mode,
faithfulness, online evaluation, feedback), models and tools (tokens/s, time
to first token, error ratio, fallbacks, tool calls, vLLM queue), audit,
security and real users (audit health, security events, Web Vitals p75) and a
Loki panel of security and audit events. Logs link to traces by `trace_id`
and spans link back to their logs.

## 10. Incident playbooks

### API down or unscrapeable

`UraApiDown` / `UraProbeFailing`. Check the probe first: if
`probe_success{instance="http://api:8000/health"}` is 1 but `up{job="ura-api"}`
is 0, the API is up and the scrape is being refused — compare
`METRICS_TOKEN` in `.env` with the compose secret Prometheus mounts
(`docker exec ura-prometheus cat /run/secrets/ura_metrics_token | wc -c`; never
print it). Otherwise check `docker compose ps api` and the API's JSON logs.

### High error rate

`UraAvailabilityBudgetBurnFast/Slow`. Break the 5xx down by route:
`sum by (path, status) (rate(ura_http_requests_total{status=~"5.."}[5m]))`,
then search the logs for `level="ERROR"` with the same `request_id`s. 4xx
responses never count against the budget.

### Slow responses

`UraChatLatencyBudgetBurnFast` / `UraChatLatencyP99High`. Split
`ura:chat_response_time_ms:p95_by_mode_5m` and `…_by_channel_5m`: generation
modes point at the model (see LLM serving), FAQ/calculator modes at the API.
Open a slow trace in Jaeger: the `rag.<stage>` spans show where the time went.

### LLM serving

`UraLlmTimeToFirstTokenHigh`, `UraLlmErrorRateHigh`, `VllmDown`,
`VllmQueueBacklog`, `GpuMemoryNearlyFull`. Read `vllm:num_requests_waiting`
and the DCGM GPU memory panel; a backlog with full KV cache is capacity, an
error spike with an empty queue is the server. `ura_model_fallback_total`
shows answers moving to the cloud chain.

### LLM errors / hallucinations

`UraFaithfulnessLow`. Check index freshness (`ura_qdrant_index_drift`), the
retrieval mode mix and recent `generate` audit rows (their
`provenance.index_corpus_hash` and `prompt_template_sha256` say which corpus
and prompt answered).

### Online evaluation

`UraEvalRegression` / `UraEvalStale`. Run `POST /v1/evaluate` with the ops key
for the full report (per-segment breakdown by topic, locale, taxpayer type and
flag variant). Stale means the scheduled run is failing:
`ura_eval_runs_failed_total` and the logs say why.

### Conversation outcomes

`UraEscalationRateHigh` / `UraAbstentionRateHigh`. Containment is
`ura:chat_turns:containment_ratio_1h`. Rising abstention is a knowledge gap,
not an outage: route the abstained questions to the corpus backlog
([corpus coverage](runbooks/corpus-coverage.md)).

### Failed retrievals

`UraRetrievalDegraded`, `QdrantDown`, `QdrantQueryErrors`. Check
`curl http://localhost:6333/healthz` and `/ready` (`retrieval_mode: keyword`
confirms Qdrant is down); restart Qdrant, then re-index if the collection is
small ([qdrant staged rebuild](runbooks/qdrant-staged-rebuild.md)).

### Retention

`RetentionJobFailing` / `RetentionJobStale`. The hourly job purges expired
conversations, documents, memory and voice data (DPPA 2019 storage
limitation). The API logs which store failed (`Retention cleanup failed for
store=…`); fix the store and the next run catches up.

### Security events

`AuthenticationFailureSpike` / `AuthorizationFailureSpike`. Query
`{service_name="ura-chatbot-api"} | event="authn_login_fail"` in Loki: one
`security_reason` repeated by everyone is an IdP or key problem; many
attempts against one route is an attack — rate limits already apply, block at
the edge if it continues.

### Dependencies

`RedisDown`: rate limits and the response cache fall back to per-worker
memory (limits are then per worker). `QdrantDown`: see Failed retrievals.

## 11. Live verification (2026-10-06)

Run on the local GPU host against the branch: a separate API container
(2 uvicorn workers, GPU 3, its own database and Redis DBs) attached to the
stack's Qdrant and Redis, a throwaway vLLM 0.8.5 serving Sunflower-14B-FP8,
and this directory's Prometheus, Alertmanager, Collector, Loki and Jaeger
configs with a webhook catcher. Nothing in the shared stack was restarted.

| Check | Result |
| --- | --- |
| Scrape with `METRICS_TOKEN` | `up{job="ura-api"} = 1`; without a credential `/metrics` refuses (503 with no operator key configured) |
| Two workers, one count | 50 requests → 8 consecutive scrapes all read `50.0` (files `counter_10.db`, `counter_11.db`) |
| Route labels | 41 requests to a non-route counted under `path="__unmatched__"` |
| REST + SSE audit | `generate` rows with `channel` `rest` and `sse`, schema 2, provenance block; `verify_ledger` full scope: valid, 2 seals checked |
| RFC 3161 | Scheduled seals carry DigiCert tokens (~8 KB DER), verified with chain against the system bundle |
| Model telemetry | `ura_llm_requests_total{status="ok"}`; streamed call reported 983 input / 246 output tokens (`stream_options.include_usage`), time to first token 0.57 s; the SSE audit row carries the same `usage` (`source: provider`) |
| Traces | `POST /v1/chat/stream` → `invoke_agent ura-assistant` → `rag.*` stages → `chat Sunbird/Sunflower-14B-FP8` with `gen_ai.*` attributes; `gen_ai.evaluation.result` on grounding; `traceresponse` header returned |
| Logs | JSON on stdout with `request_id`; OTLP → Loki with `event`, `request_id`, `http_route` as structured metadata |
| Security event | Bad bearer → 401 → `authn_login_fail` record + `ura_security_events_total` |
| RUM | `POST /v1/telemetry/vitals` (text/plain) → 204; route folded to `/staff/tickets/:id` |
| Alert delivery | Prometheus → Alertmanager → webhook delivered `QdrantDown`, `RedisDown`, `StorageWorldWritable` with runbook links |
| Scheduled jobs | Online evaluation published `ura_eval_metric_passed`; retention `status="ok"`; `ura_build_info` exported |

Found by the run and fixed in the same change: tracing was initialised
inside the lifespan, after Starlette had built the middleware stack, so
requests had no server span (now initialised at import); generation submitted
to the LLM thread pool lost the turn's context, so its tokens and span fell
outside the turn (now submitted with `contextvars.copy_context()`); Jaeger
2.21 removed the query API Grafana's data source calls (pinned to 2.20).

Found and reported, not changed here: the host's root disk is 99% full, so
Loki's WAL (90% threshold) refuses writes on this machine and
`QdrantHostDiskLow` would fire; the first turns after an API restart spend
30–55 s in hybrid retrieval while models load (visible in the trace as
`rag.hybrid_search`); the shared stack's vLLM container had been stopped, so
its API was answering without the local LLM.

## File locations

| File | Purpose |
| --- | --- |
| `App/backend/app/analytics.py` | Metrics facade, request middleware, chat-turn recorder |
| `App/backend/app/tracing.py` | OTel setup, GenAI spans/metrics, turn token usage |
| `App/backend/app/logging_config.py` | Root JSON logging, request id, redaction |
| `App/backend/app/security_events.py` | OWASP-vocabulary security events |
| `App/backend/app/client_telemetry.py` | RUM endpoints' models and recording |
| `App/backend/app/audit/turns.py`, `audit/tsa.py` | Audit rows per turn; RFC 3161 seals |
| `monitoring/prometheus.yml`, `recording-rules.yml`, `alerting-rules.yml` | Scrape, SLIs, alerts |
| `monitoring/alertmanager.yml`, `blackbox.yml`, `otel-collector.yaml`, `loki.yaml` | Routing, probes, OTLP pipeline, log store |
| `monitoring/slo/ura-chatbot.openslo.yaml` | SLO definitions |
| `monitoring/tests/alerting-rules.test.yml` | promtool unit tests |
| `tests/test_monitoring_config.py` | Config ↔ code consistency |
