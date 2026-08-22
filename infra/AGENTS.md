# infra/

Compose files live at the repo root and under `App/`. Kubernetes here is
documentation only.

- `k8s/hpa-chat.yaml` / `keda-chat.yaml` — examples. Measured p95:
  `docs/runbooks/capacity-slo.md` (2026-08-19). Do not apply until the
  hybrid vs blended SLI is agreed (G33). Do not invent replica counts.
- `k8s/chaos-experiments.yaml` — game-day only (G34).
- `postgres/rls.sql` — template; the app does not apply it.
- Do not invent replica counts or claim a live cluster.
