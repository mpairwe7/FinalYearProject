# ADR-0060: Observability, memory and system prompts each have one package

## Status

Accepted, 2026-11-17. The project owner asked for the observability, memory and
system prompt files to be organised into modules, as the governance records were
(ADR-0058), and chose code packages first, then registry records. This record
covers the code. It lands in three pull requests so each diff stays small enough
to review.

## Context and Problem Statement

[ADR-0056](0056-rag-package-layout.md) moved the RAG modules into job-named
packages. Three concerns stayed scattered:

- **Observability.** `tracing.py`, `analytics.py` (the metrics facade),
  `client_telemetry.py` and `security_events.py` sat at the top of `app/`
  beside 130 other modules.
- **Memory.** `app/memory/` held the working, episodic, semantic and procedural
  tiers, but the per-conversation case facts (`case_facts.py`) and the
  conversation's language (`language_state.py`) lived outside it.
- **System prompts.** Fourteen prompt constants were spread across nine modules,
  and `agents/prompts.py` held the specialist fragments. To read or review what
  the models are told, a reader had to know every caller.

## Considered Options

1. Move the observability and memory modules with the ADR-0056 codemod, and move
   each prompt constant into an `app/prompts/` package that its module imports
   it from under the old name.
2. The same moves, but have callers import the prompts from `app/prompts/` under
   new names and drop the old names.
3. A YAML prompt store loaded at runtime.

Option 2 renames every reference in code and tests for no gain to a reader.
Option 3 adds a loader, a file format and a failure mode (a missing or malformed
file at startup) to text that changes with the code that uses it. Option 1 gives
each prompt one home and changes no behaviour.

## Decision Outcome

Option 1. `App/backend/scripts/restructure_observability_memory_prompts.py`
performs every phase. It reuses the ADR-0056 codemod with a new move table, then
extracts the prompts. Run `--phase N --apply` on the commit before phase N's pull
request to reproduce it. `--verify` checks that every app import and patch-target
string resolves.

| Phase | Moves |
| --- | --- |
| 1 | `tracing`, `client_telemetry`, `security_events` into `observability/`; `case_facts`, `language_state` into `memory/` |
| 2 | `analytics` into `observability/` |
| 3 | `agents/prompts.py` to `prompts/agents.py`; each prompt constant into `prompts/{answer,query,receptionist,title,agents,vision}.py` |

File names do not change. A moved module keeps its error handling and its
public names. Old paths are removed in the same change, and nothing re-exports
them. `middleware/logger.py` stays where ADR-0056 put it, and
`journey_analytics.py` (staff dashboard analytics) is not observability.

Prompt constants move as text. `rag/prompt_builder.py` still assembles the answer
messages and imports `SYSTEM_PROMPT` and `CLOUD_SYSTEM_PROMPT` from
`prompts/answer.py`. The speech models' recognition prompts and the guardrails'
leak signatures stay with their code: they are detectors and model inputs, not
system prompts.

## Consequences

- A reader finds metrics, traces and telemetry in `observability/`, everything
  the assistant remembers about a conversation in `memory/`, and every system
  prompt in `prompts/`.
- A test that patches `module.NAME` still patches the name the module reads,
  because each module imports its prompt under the old name.
- Importing anything from `app.memory` runs the package's `__init__`, which loads
  the memory service. Phase 1 checked that `app.main` imports without a cycle.
- The governance registry records for prompts, memory and observability follow in
  a separate pull request and point at these packages.

## Confirmation

- `restructure_observability_memory_prompts.py --phase N --verify` reports no new
  problem after each phase.
- The root and backend suites pass in CI's order after each phase.
- A second `--apply` of the same phase changes nothing.
