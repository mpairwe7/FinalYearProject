# monitoring/ (Prometheus, Alertmanager & OpenSLO)

Owns observability configuration, recording/alerting rules, and service-level objectives.

## Layout

| File | Role |
| --- | --- |
| `prometheus.yml` | Scrape targets and Alertmanager binding |
| `recording-rules.yml` | Pre-computed latency, error-rate, and SLI series |
| `alerting-rules.yml` | Production alerting rules (`UraEvalRegression`, SLA burn rates) |
| `alertmanager.yml` | Alert routing and receiver configuration |
| `slo/ura-chatbot.openslo.yaml` | OpenSLO objectives and error-budget definitions |
| `tests/alerting-rules.test.yml` | `promtool test rules` unit tests |

## Invariants

- Every metric series named in `recording-rules.yml`, `alerting-rules.yml`, or Grafana dashboards must be emitted by `App/backend/app/analytics.py` (`tests/test_monitoring_config.py` enforces this in CI).
- Burn-rate windows in `alerting-rules.yml` must match `slo/ura-chatbot.openslo.yaml`.
