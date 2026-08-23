# infra/

Docker Compose lives at the repo root and under `App/`. Kubernetes stubs
here are documentation — there is no live cluster in this checkout.

- `k8s/hpa-chat.yaml` — example HPA on chat latency. A load baseline
  exists (`docs/runbooks/capacity-slo.md`, 2026-08-19). Still do not
  apply until the hybrid vs blended p95 SLI is agreed (G33).
- `k8s/keda-chat.yaml` — example KEDA scaler. Not applied.
- `k8s/chaos-experiments.yaml` — example Chaos Mesh. Game-day only.
- `postgres/rls.sql` — RLS template. Not auto-applied.
