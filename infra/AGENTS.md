# infra/

Compose files live at the repo root and under `App/`. Kubernetes here is
documentation only.

- `k8s/hpa-chat.yaml` / `keda-chat.yaml` — examples. Do not apply until a
  measured p95 exists (G33).
- `k8s/chaos-experiments.yaml` — game-day only (G34).
- `postgres/rls.sql` — template; the app does not apply it.
- Do not invent replica counts or claim a live cluster.
