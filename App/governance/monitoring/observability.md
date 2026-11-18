# What the system records about itself

Each signal the URA AI Chatbot emits, whether it can hold personal data, who can
read it and how long it is kept. The code is in
[`App/backend/app/observability/`](../../backend/app/observability/analytics.py)
and [`middleware/logger.py`](../../backend/app/middleware/logger.py)
(ADR-0060); the configuration is the top-level [App/monitoring/](../../monitoring/AGENTS.md)
folder. Checked against the code on 2026-11-18.

| Signal | What it records | Personal data | Read through | Kept |
| --- | --- | --- | --- | --- |
| Metrics | `ura_*` counters and histograms by route template, channel, outcome, reply language, provider and model (`analytics.py`) | None. Labels come from closed sets, and each family is capped at `METRICS_MAX_SERIES_PER_METRIC` series | Prometheus, which scrapes `/metrics` with `METRICS_TOKEN`. Without the token the endpoint falls back to staff admin access (`require_admin_access`) | 30 days (`--storage.tsdb.retention.time` in `docker-compose.yml`) |
| Logs | JSON lines with the request id and trace ids. Attributes and tracebacks are scrubbed, and raw prompts are not stored (`STORE_RAW_PROMPTS=false`) | Scrubbed before writing | Loki | 2,232 hours, 93 days (`App/monitoring/loki.yaml`) |
| Traces | OpenTelemetry spans for turns, model calls and tool calls: timings, counts, token usage, the conversation id, intent and language (`tracing.py`) | The conversation id; no message text | An OTLP collector | Off by default (`OTEL_ENABLED=false`). When on, the bundled collector forwards to Jaeger, whose storage in `docker-compose.yml` is in memory and lost on restart |
| Real-user telemetry | Core Web Vitals and client error classes from the browser (`client_telemetry.py`) | None. The route label is one of the client's page templates (`client_telemetry.ROUTES`, kept equal to the frontend's pages by a test) or `/other`, so no path segment a person typed reaches a label or the log line (G669, fixed). The error message is never kept | Metrics and one WARNING log line | As metrics and logs |
| Security events | Authentication failures, authorisation denials, rate-limit hits and guardrail probing, in the OWASP Logging Vocabulary (`security_events.py`) | The authenticated actor, when there is one. Never the client IP or the token | Logs and `ura_security_events_total` | As metrics and logs |
| Audit ledger | One hash-chained row per answered turn while the `audit_ledger` flag is on. A failed append writes no row: it is logged, counted in `ura_audit_append_failed_total` and alerted by `AuditAppendFailing`, and the answer is still delivered | Yes | Staff audit roles ([audit](../audit/README.md)) | Per the retention settings ([audit](../audit/README.md)) |

Alerts on these signals route to the owning team
([alerting-rules.yml](../../monitoring/alerting-rules.yml)); the
[monitoring README](README.md) covers drift, language parity and cost.
