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
| `tools/` | Registered tools (`ToolRegistry`) |
| `mcp/` | MCP client + tax-calculator server |
| `guardrails.py` | Input/output OWASP LLM01–09 |
| `flags.py` | Registry + rollout; do not add a flag here only |

## Rules

- Retrieval flags `hyde`, `graph_fusion`, `tool_rag` stay default off.
- `ticket_queue` default on; escalate tool returns `ok: false` when it is off.
- Document analyze: PDF guards, then `malware_scan` (fail-open unless `MALWARE_SCAN_REQUIRED`). `DOCUMENT_PARSE_ISOLATED` runs extractors in a subprocess.
- `URA_ACCOUNT_API_MODE=mock` is sandbox only (`live=false`). Production rejects it.
- Production gap gates: `app/production_readiness.py` (malware isolated parse, https publications, RLS ack, no seed, no live notify). See `docs/PRODUCTION_GATES.md`.
- Publications ingest hashes `URA_PUBLICATIONS_URL` and enqueues reindex. Never auto-`--recreate`.
- Flag PATCH persists to `flag_overrides` on **this replica**. Cluster-wide still needs `FLAG_*`.
