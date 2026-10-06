# Backend (FastAPI agent host)

Python 3.12. Package: `App/backend/app` (imported as `app`).

## Commands

```bash
cd App/backend
PYTHONPATH=. python3 -m pytest tests/ -q
# from repo root:
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents -q
PYTHONPATH=App/backend python3 -m app.seed_prototype   # development only
```

## Boundaries

| Module | Role |
| --- | --- |
| `agents/` | Supervisor, golden-set routing, LangGraph |
| `routes/` | Modular domain APIRouters (`connectors.py`, `offline.py`) |
| `tools/` | Registered tools (`ToolRegistry`) |
| `mcp/` | MCP client + tax-calculator server |
| `guardrails.py` | Input/output OWASP LLM01–09 |
| `logging_config.py` | Root JSON logging: request id, trace ids, scrubbed `attributes` and tracebacks |
| `analytics.py` | The one metrics facade (prometheus_client, all workers); `record_chat_turn` |
| `tracing.py` | OTel setup; `llm_call`, `trace_tool_call`, GenAI spans and turn token usage |
| `audit/` | Hash-chained ledger, `turns.append_turn` (one row per answer while `audit_ledger` is on; a failed append is counted and logged, never retried), RFC 3161 seals (`tsa.py`) |
| `mt.py` | Shared translation cache + the figure-fidelity guard |
| `flags.py` | Registry + rollout; do not add a flag here only |

## Rules

- Retrieval flags `hyde`, `graph_fusion`, `tool_rag` stay default off.
- Answers are generated in English and localized in **one** place
  (`localize_reply`). A new exit from `_generate_en` needs no translation call;
  a new branch in `run_chat_turn` does, and must use the locale from the
  retrieval **result**, never the caller's parameter.
- Every translation goes through `mt` — the cache and `figures_survived`. A
  translation that changes a money amount or a percentage is refused and the
  English text is served.
- `ticket_queue` default on; the escalate tool **and** `POST /v1/escalate` both
  return `ok: false` when it is off, rather than promising a handoff. Taxpayer
  escalations route through `_maybe_create_ticket` so one conversation gets one
  officer, with the transcript, the team routing and the live queue event.
- Document analyze: PDF guards, then `malware_scan` (fail-open unless `MALWARE_SCAN_REQUIRED`). `DOCUMENT_PARSE_ISOLATED` runs extractors in a subprocess.
- `URA_ACCOUNT_API_MODE=mock` is sandbox only (`live=false`). Production rejects it.
- Production gap gates: `app/production_readiness.py` (malware isolated parse, https publications, RLS ack, no seed, no live notify). See `docs/PRODUCTION_GATES.md`.
- Publications ingest hashes `URA_PUBLICATIONS_URL` and enqueues reindex. Never auto-`--recreate`.
- Flag PATCH persists to `flag_overrides` on **this replica**. Cluster-wide still needs `FLAG_*`.
- Metrics go through `analytics.metrics` only (never `prometheus_client` directly): label values must be bounded (route templates, enums), and a rule or dashboard panel may only name series the code emits — `tests/test_monitoring_config.py` fails otherwise.
- A new chat transport calls `record_chat_turn` once per turn and audits through `ChatModel.record_turn_audit` (or goes through `run_chat_turn`, which does both). Audit the text that was served, after localization.
- A new model client wraps its HTTP call in `tracing.llm_call(...)` and reports the provider's `usage`; never estimate tokens from words.
