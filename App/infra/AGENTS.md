# App/infra/

`compose/` holds the Compose overlays; the one stack is `App/docker-compose.yml`.
`monitoring/` holds the Prometheus, Alertmanager, Loki, OpenTelemetry and Grafana
configuration the base mounts; its own guide is `monitoring/AGENTS.md`.
Kubernetes here is documentation only.

- An overlay is listed after the base (`-f docker-compose.yml -f infra/compose/<file>`),
  so its paths are read from `App/`. Do not add a second stack: a new service goes
  in the base behind a profile, as `ura-plugins-server` does.

- `k8s/hpa-chat.yaml` / `keda-chat.yaml` — examples. Measured p95:
  `App/docs/runbooks/capacity-slo.md` (2026-08-19). Do not apply until the
  hybrid vs blended SLI is agreed (G33). Do not invent replica counts.
- `k8s/chaos-experiments.yaml` — game-day only (G34).
- `postgres/rls.sql` — template; the app does not apply it.
- Do not invent replica counts or claim a live cluster.
