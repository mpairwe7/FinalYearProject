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
- With `multi_tenant` on, every staff ticket read, write, aggregate, and live event is scoped to the authenticated tenant; a ticket's internal tenant routing key is never sent to the browser (ADR-0028).
- A tool's tier is its `ToolSchema.risk`. The autonomy gate may raise it, never lower it. `escalate_to_human` cannot be gated, and a refusal never depends on its audit write (ADR-0022).
- When action-level audit is enabled, high/critical tool effects require a durable intent row first. Reasoner-to-T1 fallback shares the original turn's tool budget, and a claim-verifier outage is recorded and disclosed rather than read as a pass (ADR-0028).
- Every turn on every transport writes one audit row with a `disposition`, and every model answer passes `_apply_output_guards`. The graph drafts; it does not get to approve its own draft.
- A call turn's audit row is written by the receptionist brain when the turn is over (`brain._TurnRecord`, ADR-0040), from what the caller was told and with its `disposition`; `generate(defer_audit=True)` hands the row's fields back. A new way for a call turn to end sets the record's disposition, and a new line said in reply goes through `_say_and_record`.
- A call's wrap-up is written only by an officer who handled it, once they are off it (`desk.wrapup`); a write the system makes to a ticket passes `officer_touch=False` so it is never the first response.
- A lobby event names its tenant, by its call id or with `tenant_id=`; one that names neither reaches no tenant-scoped console. Officer presence is read, counted and announced per tenant.
- The call receptionist never stops the API: `receptionist.start()` records why it could not start and `/ready` reports it (ADR-0041). A recognizer or voice failure on a call is reported to the brain (`stt.report_speech_failure`), never just logged. Call metrics go through `receptionist/telemetry.py`, whose labels are closed vocabularies.
- "No call room on this process" means "no live call" only where `config.sole_process_declared()` or the row is older than a call may last; nothing else may end or wrap up a live-looking call row.
- A call or staff socket that is refused is accepted, told why with a coded `error` frame where a caller reads it, and closed with its code (`ws._refuse_after_accept`); only a foreign origin is refused before accepting. A caller-facing message comes from a translated `call.error.*` key chosen by code, never from the server's English `detail`.
- A call route reaches the process that holds the call (ADR-0043): a new HTTP or WebSocket route for calls, the officers' desk or officer presence must match `process_role.CALL_PATH_RE` and nginx's call location (`NGINX_CALL_LOCATION`, copied into `App/deploy/cranecloud/nginx.conf`), or the chat process refuses it with 421 or a 1013 close. `App/backend/tests/test_call_process_routing.py` walks the app's routes under `/v1/calls`, `/v1/admin/calls` and officer presence against the list, and with `test_call_process_container.py` holds the list and nginx's location to each other; a route outside those prefixes is not found by the walk, so add it to the list and to nginx by hand. `/calls` is the frontend's page, not an API path.
- A figure is not shown before it is verified: streams go through `FigureReleaseGate`, and a tool answer waits for the guards.
- An answer links only to trusted hosts, and a guard change keeps both the red-team corpus and the benign controls passing (ADR-0029). Recognition is not the boundary: the fences, output guards and tool tiers are.
- Web text is evidence, never a verified fact (ADR-0021): it reaches the model only as spotlighted passages and never enters `verified_facts`. The model has no search tool, outbound queries come from `_SEARCH_VOCABULARY` only, and a failed live check is disclosed rather than hidden.
- The crawl reads the publisher's live site or nothing, and never an archive. It updates what became stale and leaves what is current alone, older never replaces newer, and crawled content reaches `dev` only through a pull request a person merges (ADR-0023). A crawl date is never an effective date, and a crawl never changes a rate table.
- A portal task a taxpayer allows runs once and is recorded first (ADR-0038): the lease on its shared row (`db.claim_workflow_session`) decides which worker runs it, `portal_task_approved` reaches the ledger before the connector does, and a store or ledger that cannot be written refuses the decision. An allow needs `X-Session-ID` or a signed-in user and `displayed_fields_sha256`. Never give a required identity field a default; a value the taxpayer did not give is `assumed` on the card.
- What a page says chooses nothing (ADR-0038): a `dom_actions` selector comes from the server's own map or recipe, never from `portal_context`; every action list leaves through `harden_dom_actions` (a click always waits for the taxpayer and never targets a link); DOM text is quoted as what the page shows. Do not put a wildcard on a shared host (`https://*.hf.space`) in `FRAME_ANCESTORS`.
- A portal task in a tier that reads maps to a read-only tool, and asking to check never proposes a write. An account task is refused unless the sign-in is bound to the TIN it names (`check_subject_tin_ownership` fails closed) and has the consent the task names. A value on a confirmation card that the person did not type is `assumed`; what they typed beats what a page shows (ADR-0038).
- Do not invent a live URA account API. Prototype defaults to `URA_ACCOUNT_API_MODE=mock` (`live=false`). Production rejects mock.
- Production (`APP_ENV=production`) must start with `auth_required`, `multi_tenant`, `audit_ledger`, and `ticket_queue` on.
- Always push changes via dedicated feature/fix branches and open a PR; never push directly to `dev` or `main`.
- Every open PR must resolve all CI check failures and all CodeRabbit AI review comments (`coderabbitai[bot]`) before merging (`gh pr checks` green + inline review findings addressed).
- Every code, config, model, flag, route, or infrastructure change must update living documentation in the same PR per 2026 industry standards (ISO/IEC 42001 Clause 7.5, NIST AI RMF 1.0 `GOVERN 1.4` & `MAP 4.2`, EU AI Act Art. 11/12/50/53, MADR 4.0 ADRs in `docs/adr/`, Diátaxis runbooks in `docs/runbooks/`, Model Cards in `docs/MODEL_CARD.md`, and CycloneDX 1.6 AIBOM in `governance/ai_risk_manifest.yaml`; see `.claude/skills/living-docs-adr-runbooks-traceability/SKILL.md` and `.claude/skills/refresh-docs/SKILL.md`).
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
