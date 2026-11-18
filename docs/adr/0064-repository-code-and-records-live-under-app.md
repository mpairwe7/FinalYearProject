# ADR-0064: Repository code and records live under App/

## Status

Accepted, 2026-11-18. Carried out in three phases, one pull request each.

- Phase A (done): remove the root pointer folders, and move `configs/` and `fine-tuning/`.
- Phase B: remove the byte-identical duplicate PDFs from `Data/pdfs`.
- Phase C: move the root code and record folders under `App/`.

## Context and Problem Statement

The October 2026 monorepo standardisation added root folders that held only a
README pointing into `App/`: `agents/`, `apps/` (with `apps/api` and `apps/web`),
`packages/`, `prompts/` and `skills/`. They mirrored the layout of a split
monorepo without being one, so every reader met two maps of the same code, and the
copies drifted. For example, `prompts/README.md` still named `CLOUD_SYSTEM_PROMPT` as
the production prompt after ADR-0036 replaced it.

Other code sat at the root although the application lives in `App/`:
- `configs/` (the prototype env)
- `fine-tuning/` (two adapter evaluation scripts beside `ml/`)
- the larger code and record folders: `docs/`, `tests/`, `scripts/`, `ml/`,
  `evals/`, `governance/`, `plugins/`, `monitoring/`, `infra/` and
  `threat-model/`

`Data/pdfs` also stored 11 PDFs twice under two names.

The service owner asked on 2026-11-18 for the root redundancy to be resolved into
`App/`.

## Considered Options

1. Remove the pointer folders and move the root folders under `App/`, in phases.
2. Remove only the pointer folders.
3. Keep the layout.

`App/backend` and `App/frontend` stay where they are (`CLAUDE.md`).

## Decision Outcome

Option 1, chosen by the service owner over the narrower option 2.

- **Phase A.** It deletes the five pointer folders and their router rows.
  `configs/` moves to `App/configs/`, and `fine-tuning/` to `ml/fine-tuning/` beside
  the other training code. The two evaluation scripts now compute the repository
  root one level higher, and the Compose adapter mounts read `ml/fine-tuning/adapters`.
- **Phases B and C.** Each gets its own pull request, so CI and a reverting commit
  cover one step at a time. Phase C moves folders with a rerunnable script that
  rewrites every path reference. 535 files and 12 workflows name those folders.

## Consequences

- A reader meets one map, `AGENTS.md`, and the code it points to.
- `AGENTS.md` has 13 layout rows. The router test's floor follows it.
- The guardrails codemod's hand edits skip files that no longer exist, so a rerun
  stays a no-op.
- Phase C changes the paths the operator's own gate commands use
  (`governance/compliance_check.py`, `threat-model/validate_threats.py`).

## Confirmation

- `tests/test_monorepo_architecture_governance.py` checks that every router row's
  path and guide exist.
- The link check finds no new broken link.
- The governance and threat-model gates pass.
