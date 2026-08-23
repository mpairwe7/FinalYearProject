# Prototype gaps + production gates — 2026-08-18

Traceability record for the remaining-gap close and the production
activation layer. Pair with `docs/GAPS_AND_AGENTIC_ROADMAP.md` (living
register) and `docs/PRODUCTION_GATES.md` (operator checklist). This file
is the decision log: what shipped, why, how to re-verify, and what a
later audit must not treat as a live URA integration.

## 1. Intent

Make the laptop demo honest and the production start fail-closed.

- **Prototype:** staff and taxpayer flows work with sandbox TINs, an
  in-app inbox, exact-match overrides, and fixture publications.
- **Production:** those sandbox defaults become start blockers. The
  process does not invent a live URA account API, an email/SMS network,
  or a cluster autoscaler.

`FLAG_HYDE`, `FLAG_GRAPH_FUSION`, `FLAG_TOOL_RAG`, and `FLAG_TOOL_USE`
stay default **off**.

## 2. Decision log

| Decision | Choice | Why |
|----------|--------|-----|
| Account connector | `off` / `mock` / `live`. Dev defaults to mock (`live=false`). Production rejects mock. `live` needs https base, token, and `URA_ACCOUNT_LIVE_ACK`. | A fake balance in production is worse than no balance. |
| Action submit in mock | Proposal `ok=true`; `submit=true` still fail-closes (`URA action API is not configured`) | Confirmation must not look like a live filing. |
| Live lookup transport | https only | http to an "internal" account host is not a URA contract. |
| Document AV | `MALWARE_SCAN_REQUIRED` fail-closed when on; default fail-open in dev | Local demo must run without ClamAV. |
| Isolated parse | subprocess when `DOCUMENT_PARSE_ISOLATED`; production requires it | Not a parse pool or gVisor; still better than in-process extractors. |
| Notifications | In-app inbox + mock outbox. `NOTIFICATION_LIVE` must stay false | No SES / Africa's Talking in this repo. |
| Publications | https URL or offline fixture. Production forbids fixture. Never auto-`--recreate` | A laptop can ingest; an index rebuild stays an ops act. |
| Seed data | `Data/eval/prototype_seed.json`; skipped in production and under pytest | Deterministic demo rows without polluting CI. |
| Multi-tenant | Predicate + `infra/postgres/rls.sql`. Production needs `MULTI_TENANT_RLS_APPLIED=true` | The app filter is not Postgres RLS. |
| DPO | Export pairs; `DPO_RUN` without `EVAL_GATE_OK` exits 2 | No trainer is wired. |
| HPA / chaos | Example YAML + in-process drills only | Not applied to a cluster. |
| Flag persist | `flag_overrides` on **this replica** | Cluster-wide still needs `FLAG_*`. |
| Production-on flags | `auth_required`, `multi_tenant`, `audit_ledger`, `ticket_queue`, `voice_consent` | Safety flags must not be a percentage of anything. |

## 3. Feature auditability map

How a later reviewer traces each feature from claim → code → gate → test.

| Feature | Code | Production gate | Tests | Status a reviewer may claim |
|---------|------|-----------------|-------|-----------------------------|
| G12 sandbox account | `ura_account_mock.py`, `tools/ura_account.py` | mock rejected; live needs ACK + https | `test_remaining_gaps`, `test_production_readiness` | Demo only. Not live URA. |
| G13 upload AV / isolate | `malware_scan.py`, `document_worker.py` | both flags required | `test_malware_and_publications` | Guards shipped. ClamAV is ops. |
| G14 inbox / outbox | `notify.py`, `/v1/me/reminders`, `/admin/outbox` | `NOTIFICATION_LIVE` false | `test_remaining_gaps` | In-app live. Email/SMS not sent. |
| G15 publications | `publications.py`, `publications-ingest.yml` | https URL | `test_malware_and_publications` | Fixture in demo. No auto-recreate. |
| G28 red-team corpus | `guardrails.py`, `Data/eval/redteam_corpus.jsonl` | — | `test_redteam_corpus` | CI refuse-all on the corpus. |
| G29 preference export | `evals/export_preferences.py`, `evals/dpo_job.py` | `EVAL_GATE_OK` | `test_remaining_gaps` | Export only. |
| G30 tenancy | `tenancy.py`, `infra/postgres/rls.sql` | RLS ack | `tests/chaos` | Predicate shipped. RLS not auto-applied. |
| G31 overrides | `cms.py`, `/admin/overrides` | `SEED_PROTOTYPE` false | `test_seed_prototype` | Exact-match CMS. |
| G32 tickets | staff workbench + `ticket_queue` | flag must stay on | existing ticket suites | Shipped. |
| G33 / G34 | `infra/k8s/*`, `tests/chaos/` | not a start blocker | `test_failure_modes`; envelope: `capacity-envelope-2026-08-19.md` | G33 p95 measured 2026-08-19; HPA/KEDA still not applied. G34 still deferred. |
| Startup safety | `main._validate_production_env` | calls `gap_gate_errors()` | `test_production_hardening` (CI FastAPI) | Existing RS256 / OIDC / Postgres checks remain. |
| Gap probe | `app/production_readiness.py` | CLI `--as-production` | `test_production_readiness` | Exit 2 lists blockers. |

Audit ledger (`audit/`) and `flag_variants` on conversations are unchanged:
every chat/stream/voice/WS turn still persists which flags served it
(G26). Replica-local `flag_overrides` are not a cluster audit trail.

## 4. Code surface

| Area | Files |
|------|-------|
| Gates | `app/production_readiness.py` (new), `main.py`, `scripts/validate_env.py` |
| Account | `ura_account_mock.py`, `tools/ura_account.py` |
| Docs ingest | `publications.py`, `malware_scan.py`, `document_worker.py` |
| Notify / CMS | `notify.py`, `cms.py`, `seed_prototype.py` |
| Tenancy | `tenancy.py`, `infra/postgres/rls.sql` |
| Frontend | Settings sandbox card, `/admin/overrides`, `/admin/outbox` |
| Docs | `docs/PRODUCTION_GATES.md`, this file, `GAPS_AND_AGENTIC_ROADMAP.md` §2.8 |

## 5. Flags and environment (2026-08-18)

Measured from `flags.all()`: **49** flags. Production-on set:
`auth_required`, `multi_tenant`, `audit_ledger`, `ticket_queue`,
`voice_consent`.

New or tightened env (all in `.env.example`):

| Name | Dev default | Production |
|------|-------------|------------|
| `URA_ACCOUNT_API_MODE` | mock | `off` or `live` + ACK |
| `URA_ACCOUNT_LIVE_ACK` | unset | required for `live` |
| `MALWARE_SCAN_REQUIRED` | false | true |
| `DOCUMENT_PARSE_ISOLATED` | false | true |
| `URA_PUBLICATIONS_URL` | fixture ok | https only |
| `SEED_PROTOTYPE` | true | false |
| `NOTIFICATION_LIVE` | false | must stay false |
| `MULTI_TENANT_RLS_APPLIED` | unset | true |
| `EVAL_GATE_OK` | unset | required before `DPO_RUN` |

## 6. How to re-verify

```bash
PYTHONPATH=App/backend python3 -m pytest \
  App/backend/tests/test_production_readiness.py \
  App/backend/tests/test_remaining_gaps.py \
  App/backend/tests/test_seed_prototype.py \
  App/backend/tests/test_malware_and_publications.py \
  App/backend/tests/test_redteam_corpus.py \
  tests/chaos/test_failure_modes.py -q

PYTHONPATH=App/backend python3 -m app.production_readiness --as-production
python3 scripts/validate_env.py --env production
```

Inventory (do not cite from memory):

```bash
PYTHONPATH=App/backend python3 -c "
from app.flags import flags, _PRODUCTION_ON_FLAGS
from app.tools import ToolRegistry
print(len(flags.all()), sorted(_PRODUCTION_ON_FLAGS))
print(len(ToolRegistry.all()), len(ToolRegistry.namespaces()))
"
```

Expected on 2026-08-18: **49** flags, **25** tools, **11** namespaces.

Verified this session: readiness + remaining-gaps + seed + chaos = 20
passed; malware/publications = 5 passed. `--as-production` on an
unconfigured laptop exits 2 (G13, G15, G30), which is the intended
fail-closed signal.

## 7. Still open (do not mark shipped)

- Live URA account / actions (G12) — needs a contract. ACK is not the API.
- Real email / SMS (G14).
- Dedicated parse pool / gVisor (G13 remainder).
- Applied Postgres RLS on a live database (G30).
- Axolotl / DPO trainer (G29).
- Applied HPA / KEDA and a cluster game day (G33 / G34). Envelope
  numbers exist (`capacity-envelope-2026-08-19.md`); they are not an
  applied autoscaler.
- `FLAG_TOOL_RAG` / HyDE / graph-fusion measurement before raising percent.

## 8. Audit verdict (2026-08-18)

| Practice | Code | Default | Docs agree |
|----------|------|---------|------------|
| Mock account never `live=true` | Yes | mock in dev | Yes |
| Production rejects mock / fixture seed | Yes | start blocker | Yes (`PRODUCTION_GATES.md`) |
| Live account https + ACK | Yes | off unless ACK | Yes |
| Malware fail-closed when required | Yes | fail-open in dev | Yes |
| Isolated parse optional | Yes | off in dev; required in prod | Yes |
| Notify mock outbox | Yes | `live=false` | Yes |
| Publications no auto-recreate | Yes | enqueue only | Yes |
| DPO refuse without eval gate | Yes | no trainer | Yes |
| RLS SQL not auto-applied | Yes | ack required in prod | Yes |
| HyDE / graph fusion / tool RAG / tool use | Yes | **off** | Yes |
| Live URA balances | No | fail-closed | Yes (GAPS) |

Doc authority: **GAPS** is the living register. `PRODUCTION_GATES.md` is
the operator checklist. This file is the dated decision log.
