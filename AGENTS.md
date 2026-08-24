# Agent repository map

URA taxpayer chatbot (capstone). **Runtime imports stay under `App/backend/app`.**
This file is the repo-wide router. Package rules live next to the code.

## Layout

| Path | Owns | Nested guide |
| --- | --- | --- |
| `App/backend` | FastAPI host, RAG, tools, MCP, graphs | `App/backend/AGENTS.md` |
| `App/frontend` | Next.js taxpayer + staff UI | `App/frontend/AGENTS.md` |
| `agents/` | Pointer to graphs / tools / MCP | `agents/README.md` |
| `apps/` | Pointer to deployable surfaces | `apps/README.md` |
| `apps/api` | FastAPI entry (`App/backend`) | `apps/api/AGENTS.md` |
| `apps/web` | Next.js entry (`App/frontend`) | `apps/web/AGENTS.md` |
| `evals/` | Golden sets, red-team gate, preference export | `evals/AGENTS.md` |
| `data/` + `Data/` | FAQ, PDFs, crawl, eval JSONL | `data/README.md` |
| `infra/` | Compose overlays and example HPA (not applied) | `infra/AGENTS.md` |
| `governance/` | Risk manifest + compliance gate | `governance/AGENTS.md` |
| `docs/` | Architecture, gaps, model card | `docs/AGENTS.md` |
| `tests/` | Pytest (agents + API e2e) | `tests/AGENTS.md` |
| `prompts/` | Pointer to runtime prompts | `prompts/README.md` |
| `skills/` | Pointer to Cursor/Claude skills | `skills/README.md` |
| `packages/` | Why this is not a split monorepo | `packages/README.md` |
| `configs/` | Prototype env | `configs/README.md` |

Do not move `App/backend` or `App/frontend`. Docker, HF Space, and CI import `app.*`.

## Invariants

- Do not default-on `hyde`, `graph_fusion`, `tool_rag`, or `tool_use`.
- Human oversight uses `ticket_queue` (registry default on). Every route into it — `escalate_to_human`, the supervisor's ESCALATE route, and the taxpayer's own `POST /v1/escalate` — must honour that flag rather than promise a handoff.
- Do not invent a live URA account API. Prototype defaults to `URA_ACCOUNT_API_MODE=mock` (`live=false`). Production rejects mock.
- Production (`APP_ENV=production`) must start with `auth_required`, `multi_tenant`, `audit_ledger`, and `ticket_queue` on.
- New FastAPI routes must land in `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS` + `COVERAGE`).
- A new flag lands in `flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`.

## Commands

```bash
source configs/prototype.env   # optional demo defaults
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q
cd App/frontend && bun test
python3 -m app.freshness --check
python3 evals/export_preferences.py
bash scripts/prototype.sh
```
