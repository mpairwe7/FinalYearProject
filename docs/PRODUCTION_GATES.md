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
| G33 HPA/KEDA | Not a start blocker. | Apply `infra/k8s/` after a measured p95. |
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
