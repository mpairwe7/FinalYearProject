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
| `Data/` | FAQ, PDFs, crawl, eval JSONL | `Data/README.md` |
| `ml/` | Offline training, corpus pipelines, local ASR/MT/TTS wrappers | `ml/AGENTS.md` |
| `infra/` | Compose overlays and example HPA (not applied) | `infra/AGENTS.md` |
| `monitoring/` | Prometheus, Alertmanager, OpenSLO & promtool tests | `monitoring/AGENTS.md` |
| `governance/` | Risk manifest + compliance gate | `governance/AGENTS.md` |
| `threat-model/` | STRIDE / OWASP / MITRE ATLAS threat model as code | `threat-model/AGENTS.md` |
| `docs/` | Architecture, gaps, model card | `docs/AGENTS.md` |
| `tests/` | Pytest (agents + API e2e) | `tests/AGENTS.md` |
| `prompts/` | Pointer to runtime prompts | `prompts/README.md` |
| `skills/` | Pointer to Cursor/Claude skills | `skills/README.md` |
| `packages/` | Why this is not a split monorepo | `packages/README.md` |
| `plugins/` | System plugins (EFRIS, DTS) & connectors | `plugins/AGENTS.md` |
| `configs/` | Prototype env | `configs/README.md` |

Do not move `App/backend` or `App/frontend`. Docker, HF Space, and CI import `app.*`.

## Invariants

- Do not default-on `hyde`, `graph_fusion`, `tool_rag`, or `tool_use`.
- Only officers (`ura_staff`) handle taxpayers live — claim or reply to a ticket, hold its collision lock, take or end a call. Administrators audit the service and auditors read it; the API refuses both (`_require_live_officer`, `_desk_writer`), not just the console.
- Human oversight uses `ticket_queue` (registry default on). Every route into it — `escalate_to_human`, the supervisor's ESCALATE route, and the taxpayer's own `POST /v1/escalate` — must honour that flag rather than promise a handoff.
- A tool's tier is its `ToolSchema.risk`. The autonomy gate may raise it, never lower it. `escalate_to_human` cannot be gated, and a refusal never depends on its audit write (ADR-0022).
- Every turn on every transport writes one audit row with a `disposition`, and every model answer passes `_apply_output_guards`. The graph drafts; it does not get to approve its own draft.
- A figure is not shown before it is verified: streams go through `FigureReleaseGate`, and a tool answer waits for the guards.
- Web text is evidence, never a verified fact (ADR-0021): it reaches the model only as spotlighted passages and never enters `verified_facts`. The model has no search tool, outbound queries come from `_SEARCH_VOCABULARY` only, and a failed live check is disclosed rather than hidden.
- The crawl reads the publisher's live site or nothing, and never an archive. It updates what became stale and leaves what is current alone, older never replaces newer, and crawled content reaches `dev` only through a pull request a person merges (ADR-0023). A crawl date is never an effective date, and a crawl never changes a rate table.
- Do not invent a live URA account API. Prototype defaults to `URA_ACCOUNT_API_MODE=mock` (`live=false`). Production rejects mock.
- Production (`APP_ENV=production`) must start with `auth_required`, `multi_tenant`, `audit_ledger`, and `ticket_queue` on.
- Always push changes via dedicated feature/fix branches and open a PR; never push directly to `dev` or `main`.
- New FastAPI routes must land in `tests/test_all_endpoints_e2e.py` (`EXPECTED_ENDPOINTS` + `COVERAGE`).
- A new flag lands in `flags.py`, `docs/RAG_ARCHITECTURE.md`, and `.env.example`.
- On sign-in, a guided workflow is adopted only from the anonymous session the request itself proves (`db.adopt_workflow_session`, ADR-0013). Never add candidate keys a caller could derive from a conversation id alone; "signed in" is decided per request, never read back from stored slots.
- A new workflow YAML needs its own prefix in `RESUME_CODE_PREFIXES` (`journey_copilot.py`).
- GitHub Actions (ADR-0011): pin every action by commit SHA with a `# vX.Y.Z` comment, pass `inputs.*`/`github.event.*` through `env:` (never `${{ }}` inside `run:`), set `persist-credentials: false` unless the job pushes, and take Node/Bun versions from `App/frontend/.nvmrc` / `.bun-version`. Enforced by `tests/test_ci_supply_chain_policy.py` and `.github/workflows/actions-security.yml` (zizmor + actionlint).

## Commands

```bash
source configs/prototype.env   # optional demo defaults
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents tests/chaos -q
cd App/frontend && bun run test
python3 -m app.freshness --check
python3 evals/export_preferences.py
bash scripts/prototype.sh
```
