# ADR-0064: Repository code and records live under App/

## Status

Accepted, 2026-11-18. Carried out in three phases, one pull request each.

- Phase A (done): remove the root pointer folders and the fine-tuning code, and move `configs/`.
- Phase B (done): remove the byte-identical duplicate PDFs from `Data/pdfs`.
- Phase C (done): move the root code and record folders under `App/`.

## Context and Problem Statement

The October 2026 monorepo standardisation added root folders that held only a
README pointing into `App/`: `agents/`, `apps/` (with `apps/api` and `apps/web`),
`packages/`, `prompts/` and `skills/`. They mirrored the layout of a split
monorepo without being one, so every reader met two maps of the same code, and the
copies drifted. For example, `prompts/README.md` still named `CLOUD_SYSTEM_PROMPT` as
the production prompt after ADR-0036 replaced it.

Other code sat at the root although the application lives in `App/`:
- `configs/` (the prototype env)
- `fine-tuning/` (two adapter evaluation scripts beside `App/ml/`)
- the larger code and record folders: `App/docs/`, `App/tests/`, `App/scripts/`, `App/ml/`,
  `App/evals/`, `App/governance/`, `App/plugins/`, `App/monitoring/`, `App/infra/` and
  `App/threat-model/`

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
  evaluation scripts in `fine-tuning/`, the training scripts in `App/ml/scripts`
  (`asr/train_luganda.py`, `mt/finetune_mt.py`, `mt/distill_mt.py`,
  `mt/backtranslate.py`, `tts/train_luganda_vits.py`), `requirements-tts-train.txt`
  and the `evals/dpo_job.py` scaffold with its test. The runtime can still load
  LoRA adapters a deployment supplies; Compose now mounts them from `Model/adapters`
  (git-ignored) rather than the deleted `fine-tuning/adapters`. The download,
  export and inference scripts in `App/ml/scripts`, and `App/evals/export_preferences.py`,
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
  cover one step at a time.
- **Phase C.** `App/scripts/consolidate_root_into_app.py` moves `docs/`, `tests/`,
  `scripts/`, `ml/`, `evals/`, `governance/`, `plugins/`, `monitoring/`, `infra/` and
  `threat-model/` under `App/` (231 entries; `docs/` and `scripts/` merge into the
  existing `App/docs/` and `App/scripts/`, with no name in common). It rewrites the
  paths to them from the tree as it was before the move, in five ways:
  - It re-resolves relative links and paths from each file's old location to its new one.
  - It gives root-relative paths `App/`. Inside `App/backend`, `App/frontend` and
    `App/blog`, and in a workflow step with a `working-directory`, a path that names
    that folder's own `tests/` or `scripts/` is left alone.
  - It rewrites links to this repository on GitHub.
  - It moves each moved Python file's `parents[n]`, `.parent` chain or nested
    `dirname` one level further when it reached the repository root.
  - It gives `"App" /` to a `ROOT / "docs"` join when `ROOT` traces back to the root.

  A short checked list covers what the script cannot infer. `app._root.CODE_ROOT`
  names the folder holding the `ml` and `plugins` packages (`App/` in a checkout, the
  project root in the image), and three backend imports use it. It also covers the
  CI coverage and `PYTHONPATH` settings, and three shell scripts whose root lookup
  moved one level down. A second run finds nothing to move.

## Consequences

- A reader meets one map, `AGENTS.md`, and the code it points to.
- `AGENTS.md` has 13 layout rows. The router test's floor follows it.
- The guardrails codemod's hand edits skip files that no longer exist, so a rerun
  stays a no-op.
- The gate commands are now `python3 App/governance/compliance_check.py` and
  `python3 App/threat-model/validate_threats.py`. The operator's own instructions
  outside the repository still name the old paths.
- The image build's path filter names only `App/backend/**`, `App/frontend/**` and
  the deploy files, so a change under `App/docs` or `App/governance` does not rebuild
  the Space. The image still copies `ml/scripts/lang_id.py`, now from `App/ml/`.

## Confirmation

- `App/tests/test_monorepo_architecture_governance.py` checks that every router row's
  path and guide exist.
- After phase C, both suites pass on the moved tree as CI runs them: `pytest App/tests`
  from the root, and `pytest tests/` from `App/backend`.
- The link check finds no new broken link.
- The governance and threat-model gates pass.
