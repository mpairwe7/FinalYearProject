# ADR-0061: Guardrails, agents and workflows each have a package laid out by job

## Status

Accepted, 2026-11-18. The project owner asked for guardrails, agents and
workflows to be organised into modules as observability, memory and system
prompts were (ADR-0060), with a reference agentic-project layout as inspiration
(`agents/` one file per role, `workflows/` for the flow graph and its state,
`guardrails/` with `input_checks` and `output_checks`). This record covers the
code. It lands in three pull requests.

## Context and Problem Statement

- **Guardrails.** One 2,518-line `guardrails.py` held the input checks, the
  prompt fences, personal data redaction, the figure release gate, link
  provenance and the output checks. The premise guard, guard friction and the
  PDF guards sat beside it at the top of `app/`. A reader looking for "what
  screens a taxpayer's message" or "what checks an answer" had to search one
  large file.
- **Workflows.** The LangGraph-style orchestration (graph, runtime, state,
  compactor) lived in `agents/graphs/`, apart from the YAML-driven guided
  journeys in `workflows/`. Both are workflows.
- **Agents.** `agents/` mixed agent roles with the graph. Its
  evaluator-optimizer is the role the reference layout calls a critic: it
  reviews an answer before the taxpayer reads it.

## Considered Options

1. Split `guardrails.py` into a package by job, with every importer pointed at
   the submodule that defines what it uses; move the other guard modules into
   it; move `agents/graphs/` to `workflows/graph/`; rename the evaluator
   `critic`.
2. The same layout, but keep the old `guardrails` names importable from the
   package's `__init__`.
3. Move `guardrails.py` into the package whole, as one module.

Option 2 leaves two import paths for every guardrail name, which ADR-0056
rejected for the RAG modules. Option 3 changes the folder and nothing a reader
sees. Option 1 matches the reference layout and changes no behaviour.

## Decision Outcome

Option 1. `App/backend/scripts/restructure_guardrails_agents_workflows.py`
performs each phase. Phase 1 uses `split_module.py`: it places each top-level
statement of `guardrails.py` by line range, moves a name that only one other
submodule uses into that submodule, gives each submodule the imports it uses
and imports from its siblings, rewrites every `from ... guardrails import` into
one import per submodule, and rewrites patch-target strings. The later phases
reuse the ADR-0056 codemod. Run `--phase N --apply` on the commit before phase
N's pull request.

| Phase | Change |
| --- | --- |
| 1 | `guardrails.py` becomes `guardrails/`: `input_checks` (prompt injection, `InputGuard`, `scan_*_text`), `fences` (spotlight fences), `pii` (redaction, the streaming DLP filter, `STORE_RAW_PROMPTS`), `output_checks` (`OutputGuard`, the figure release gate, link provenance) and `normalize` (Unicode canonicalisation both sides share) |
| 2 | `premise_guard`, `guard_friction` and `pdf_guards` move to `guardrails/premise.py`, `friction.py` and `pdf.py` |
| 3 | `agents/graphs/` moves to `workflows/graph/`; `agents/evaluator.py` becomes `agents/critic.py` |

The guardrails logger keeps the name `app.guardrails`, so log queries still
match. Nothing re-exports the old names. Routing stays in `agents/` (the
supervisor, its locale pattern tables and the tiebreak), because the supervisor
is the routing agent. Human approval stays in `workflows/graph/runtime.py`,
where the sealed approval is part of the graph's state.

## Consequences

- A reader finds input screening in `guardrails/input_checks.py`, answer checks
  in `guardrails/output_checks.py`, every flow in `workflows/`, and one file per
  agent role in `agents/`.
- Phase 1 changes about 100 files: every module that redacts personal data or
  screens text imports from a submodule now.
- The reference layout's `guardrails/policies.yaml` and `red_team.md` are not
  code here. Tool policy lives in `mcp/policy.py` and the risk tiers of ADR-0022.
  The red-team corpus and its gate are tests. The governance registry records
  for guardrails, agents and workflows follow in a separate pull request.

## Confirmation

- `restructure_guardrails_agents_workflows.py --phase N --verify` reports no new
  problem after each phase, and a second `--apply` changes nothing.
- `app.main` imports, and the root and backend suites pass in CI's order after
  each phase.
