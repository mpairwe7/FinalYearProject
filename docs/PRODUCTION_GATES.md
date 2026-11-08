# Production activation gates — remaining gaps

Verified 2026-08-18. Companion to `docs/GAPS_AND_AGENTIC_ROADMAP.md`.
Decision log: `App/docs/traceability/prototype-production-gates-2026-08-18.md`.

Prototype features stay on for `APP_ENV=development`. They **do not**
become live URA integrations when you flip the environment. Production
startup (`_validate_production_env` + `app.production_readiness`)
fail-closes the unsafe defaults so a go-live cannot ship mock balances,
fixture news, or unacked RLS.

```bash
PYTHONPATH=App/backend python3 -m app.production_readiness --as-production
python3 scripts/validate_env.py --env production
```

## What activates in production

| Gap | Production gate (must pass to start) | Still required for full delivery |
|---|---|---|
| G12 account | `URA_ACCOUNT_API_MODE` is `off` or `live`. `mock` is rejected. `live` also needs https `URA_ACCOUNT_API_BASE`, `URA_ACCOUNT_API_TOKEN`, and `URA_ACCOUNT_LIVE_ACK=true`. Lookups never use http. | A real URA account contract. This repo does not invent balances. |
| G13 documents | `MALWARE_SCAN_REQUIRED=true` and `DOCUMENT_PARSE_ISOLATED=true`. | ClamAV at `CLAMD_HOST:CLAMD_PORT`. A dedicated parse pool / gVisor is later. |
| G14 notify | `NOTIFICATION_LIVE` / `NOTIFY_LIVE` must stay false. | SES or Africa's Talking. The mock outbox is not delivery. |
| G15 publications | `URA_PUBLICATIONS_URL` must be https. Fixture ingest is disabled. | A live URA publications page. Ingest never auto-`--recreate`. |
| G29 DPO | `DPO_RUN` without `EVAL_GATE_OK` is refused. | A measured eval, then a trainer. The job still exits 2 with no trainer wired. |
| G30 tenancy | `MULTI_TENANT_RLS_APPLIED=true` when multi-tenant is on. | Apply `infra/postgres/rls.sql` yourself. The app predicate is not RLS. |
| G31 CMS / seed | `SEED_PROTOTYPE` must be false. Overrides stay exact-match. | A git-backed FAQ editor later. |
| G517 kernel release (HF Space build) | The Space build runs `python -m app.kernel.release check`; an image whose rate tables, graph build or rules are not a recorded release fails the build. | Nothing further: CI checks the same on every commit. |
| G506 graph backend | `GRAPH_STORE_BACKEND` is `memory` or `neo4j`; `neo4j` needs `NEO4J_URI` with `neo4j+s://` or `bolt+s://`, `NEO4J_USER` and `NEO4J_PASSWORD`. | A threshold from ADR-0033 met (`python -m app.graph.neo4j_store`). None is today. |
| G507 semantic layer | When `FLAG_SEMANTIC_LAYER` is on: https `VAULT_ADDR`, `SEMANTIC_LAYER_VAULT_PATH`, `SEMANTIC_LAYER_DSA_REF`, `SEMANTIC_LAYER_DPIA_REF`, at least one certified metric, and no `SEMANTIC_LAYER_DSN`. | A data-sharing agreement, a DPIA, Vault, a read-only analytical database and a data owner certifying metrics. See `docs/runbooks/graph-backend-and-semantic-layer.md`. |
| G345 turn deadline | `AGENTIC_TURN_DEADLINE_S`, when set, must be a number above 0. | Unset uses the 120 s default. See `docs/runbooks/agent-fault-tolerance.md`. |
| G33 HPA/KEDA | Not a start blocker. | Measured p95: `docs/runbooks/capacity-slo.md` (2026-08-19). Apply `infra/k8s/` only after the hybrid vs blended SLI is agreed — do not invent replica counts. |
| G34 chaos | Not a start blocker. | Cluster game day. `tests/chaos/` is in-process only. |

Existing production checks still apply: RS256 + OIDC, `FLAG_AUTH_REQUIRED`,
`FLAG_MULTI_TENANT`, `FLAG_AUDIT_LEDGER`, `FLAG_TICKET_QUEUE`, Postgres,
pinned model revision, no wildcard CORS. See `docs/DEPLOYMENT.md` §12.

## Safe production baseline (gaps stay fail-closed)

```dotenv
APP_ENV=production
URA_ACCOUNT_API_MODE=off
URA_PUBLICATIONS_URL=https://ura.go.ug/en/news
MALWARE_SCAN_REQUIRED=true
DOCUMENT_PARSE_ISOLATED=true
SEED_PROTOTYPE=false
NOTIFICATION_LIVE=false
MULTI_TENANT_RLS_APPLIED=true
FLAG_TICKET_QUEUE=true
FLAG_HYDE=false
FLAG_GRAPH_FUSION=false
FLAG_TOOL_RAG=false
FLAG_TOOL_USE=false
```

Do not set `URA_ACCOUNT_API_MODE=live` until URA has issued a contract
and you can set `URA_ACCOUNT_LIVE_ACK=true` with a real https base URL.
