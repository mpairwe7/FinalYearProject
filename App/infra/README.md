# App/infra/

`compose/` holds the Docker Compose overlays. The one stack is
`App/docker-compose.yml`; an overlay is listed after it, from `App/`:

```bash
docker compose -f docker-compose.yml -f infra/compose/docker-compose.full.yml up -d
```

Paths inside an overlay are read from `App/`, the folder of the first file, so an
overlay is never run alone. Kubernetes stubs here are documentation — there is
no live cluster in this checkout.

- `k8s/hpa-chat.yaml` — example HPA on chat latency. A load baseline
  exists (`App/docs/runbooks/capacity-slo.md`, 2026-08-19). Still do not
  apply until the hybrid vs blended p95 SLI is agreed (G33).
- `k8s/keda-chat.yaml` — example KEDA scaler. Not applied.
- `k8s/chaos-experiments.yaml` — example Chaos Mesh. Game-day only.
- `postgres/rls.sql` — RLS template. Not auto-applied.
- `monitoring/` — the configuration the base's `monitoring` profile mounts:
  Prometheus scrape targets, recording and alerting rules, Alertmanager routing,
  the OpenTelemetry Collector, Loki, the blackbox prober and Grafana provisioning.
  It also holds the OpenSLO objectives and the `promtool` rule tests, which CI
  checks and Compose does not mount. `App/docs/MONITORING.md` explains it.
