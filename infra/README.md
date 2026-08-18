# infra/

Docker Compose lives at the repo root and under `App/`. Kubernetes stubs
here are documentation — there is no live cluster in this checkout.

- `k8s/hpa-chat.yaml` — example HPA on chat latency. Do not apply until
  a load baseline exists (G33).
- `k8s/keda-chat.yaml` — example KEDA scaler. Not applied.
- `k8s/chaos-experiments.yaml` — example Chaos Mesh. Game-day only.
- `postgres/rls.sql` — RLS template. Not auto-applied.
