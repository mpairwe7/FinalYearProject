# ADR-0064: Repository code and records live under App/

## Status

Accepted, 2026-11-18. Carried out in three phases, one pull request each.

- Phase A (done): remove the root pointer folders and the fine-tuning code, and move `configs/`.
- Phase B (done): remove the byte-identical duplicate PDFs from `Data/pdfs`.
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

- **Phase A.** It deletes the five pointer folders and their router rows, and
  moves `configs/` to `App/configs/`. The service owner also asked for the
  fine-tuning code to go, so phase A removes it. That covers the two LoRA
  evaluation scripts in `fine-tuning/`, the training scripts in `ml/scripts`
  (`asr/train_luganda.py`, `mt/finetune_mt.py`, `mt/distill_mt.py`,
  `mt/backtranslate.py`, `tts/train_luganda_vits.py`), `requirements-tts-train.txt`
  and the `evals/dpo_job.py` scaffold with its test. The runtime can still load
  LoRA adapters a deployment supplies; Compose now mounts them from `Model/adapters`
  (git-ignored) rather than the deleted `fine-tuning/adapters`. The download,
  export and inference scripts in `ml/scripts`, and `evals/export_preferences.py`,
  stay.
- **Phase B.** `Data/pdfs` held 11 documents twice under two names. The export
  already chunked only the first name in sort order and recorded the other as
  `duplicate_of`, so the copies removed are exactly the ones it skipped: the indexed
  corpus and every passage's source name stay the same. `crawl_state.json`'s
  entries for the removed names now name the kept copy, so the crawler's hash
  record matches the files. The crawler hashes each PDF after downloading it, so
  a fetched copy of a removed name is recognised as the kept file and not saved
  again.
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
