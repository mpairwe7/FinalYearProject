# ADR-0064: Repository code and records live under App/

## Status

Accepted, 2026-11-18. Carried out in four phases, one pull request each.

- Phase A (done): remove the root pointer folders and the fine-tuning code, and move `configs/`.
- Phase B (done): remove the byte-identical duplicate PDFs from `App/Data/pdfs`.
- Phase C (done): move the root code and record folders under `App/`.
- Phase D (done): move the root data, build and security files under `App/`, and
  remove the duplicate Trivy steps.

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

`App/Data/pdfs` also stored 11 PDFs twice under two names.

The service owner asked on 2026-11-18 for the root redundancy to be resolved into
`App/`.

After phase C the root still held `Data/`, `Model/`, `Results/` and `Artifacts/`,
the Dockerfiles and Compose files, `requirements.txt`, `pyproject.toml`, the
`Caddyfile`, `.env.example`, and the configuration of nine security tools. The corpus
was split in two: the tracked FAQ CSVs, PDFs and crawl sat in the root `Data/`, and
the corpora derived from them sat in `App/Data`, so the image build copied one into
the other before indexing. `frontend-deploy.yml` also ran its own Trivy filesystem
scan and SBOM, which `security-trivy.yml` already runs. The service owner asked for
the remaining root files and flows to move into `App/` too, and for duplicate files
and workflows to go.

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
- **Phase B.** `App/Data/pdfs` held 11 documents twice under two names. The export
  already chunked only the first name in sort order and recorded the other as
  `duplicate_of`, so the copies removed are exactly the ones it skipped: the indexed
  corpus and every passage's source name stay the same. `crawl_state.json`'s
  entries for the removed names now name the kept copy, so the crawler's hash
  record matches the files. The crawler hashes each PDF after downloading it, so
  a fetched copy of a removed name is recognised as the kept file and not saved
  again.
- **Phases B and C.** Each gets its own pull request, so CI and a reverting commit
  cover one step at a time.
- **Phase C.** `App/scripts/consolidate_root_into_app.py` (deleted once phase D was done; it is in
  git at `5c619a04`) moves `docs/`, `tests/`,
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
- **Phase D.** The same script, now driven by one table of moves, moves 29 root names
  (76 entries):
  - `Data/` merges into `App/Data/`. Its README becomes the "Source corpora" section
    of `App/Data/README.md`. `Model/`, `Results/` and `Artifacts/` move to `App/`.
  - `.semgrep/`, `.security/` and the nine tool configurations move to `App/security/`.
  - The Dockerfiles, Compose files, requirements files, `pyproject.toml`, `Caddyfile`
    and `.env.example` move to `App/`. The root `docker-compose.yml` becomes
    `App/docker-compose.full.yml`, because `App/docker-compose.yml` is a different stack.
  - `SECURITY.md`, `CONTRIBUTING.md` and `CODEOWNERS` move to `.github/`, where GitHub
    also reads them.

  Four moved names also exist under `App/` (`Dockerfile`, `requirements.txt`,
  `pyproject.toml`, `docker-compose.yml`). The script rewrites those names only in
  files that address the root: root files, `.github/`, `App/governance/` and
  `App/threat-model/`. It rebases the `./` paths in moved Compose files, the quoted
  paths in `pyproject.toml`, and the anchored patterns in `CODEOWNERS` and the ignore
  files. Three more rules cover what a path rule cannot see:
  - Scripts run from the root wrote `Results/...` and `Data/eval/feedback`, outputs
    that do not exist until a run makes them. Their 29 defaults now name `App/`.
  - `APP_DATA_ROOT` now names the tracked corpora in a checkout. Seven backend paths
    that joined `PROJECT_ROOT / "Data"` use it, and the BM25, `Results` and
    `Artifacts` lookups use `CODE_ROOT`. Both are `/app` paths in the image, as before.
  - Tools that read their configuration from the working directory are told where it
    is. Each Trivy step names `App/security/trivy.yaml`, which names its ignore file.
    Coverage runs with `--cov-config=App/pyproject.toml`, and its omit patterns match
    at any depth.

  The build no longer copies `Data/` into `App/Data` before indexing.
  `frontend-deploy.yml` loses its Trivy scan, SARIF upload and SBOM steps, which
  `security-trivy.yml` already runs. `.gitignore` ignores `App/Model/*` except the
  three tracked files: `.gitkeep`, `README.md` and `bm25_state.json`. Compose in
  `App/` mounts the adapters from `./Model/adapters`. The full stack reads the root
  `.env`.

  Before the move, the Compose `postgres` service loses its committed default
  password (G672). GitGuardian reads a moved file as new and flagged it. The
  service publishes port 5432, so the default was a working login.

  The rate tables cite their source PDFs by path. Those citations now name
  `App/Data/pdfs/`, which changes the kernel's content hash. Release 1.2.3 records
  that change as an engineering baseline, the same status as 1.2.2. The graph is
  unchanged (335 nodes, 618 edges) and no rate changed. The held-out complex-scenario
  set names corpus files too, so its recorded digest is recomputed.

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
- After phase D the root holds only what a tool reads from there: `.github/`, the
  ignore files, `.pre-commit-config.yaml`, `.coderabbit.yaml` and `.gitguardian.yaml`.
  It also holds the readme, the licence and the agent guides.
- `App/` still has two Compose stacks, `docker-compose.yml` and
  `docker-compose.full.yml`. A follow-up merges them. Until then the bare
  `docker compose` commands in `App/docs/DEPLOYMENT.md` mean
  `-f App/docker-compose.full.yml`.
- A checkout now has `App/Model/bm25_state.json`, as the image does, so
  `_simple_search` scores with BM25 locally too. With it, one Kiswahili FAQ row
  does not retrieve itself (G673). The acronym test runs without the state, as it
  always had.

## Confirmation

- `App/tests/test_monorepo_architecture_governance.py` checks that every router row's
  path and guide exist.
- After phase C, both suites pass on the moved tree as CI runs them: `pytest App/tests`
  from the root, and `pytest tests/` from `App/backend`.
- The link check finds no new broken link.
- The governance and threat-model gates pass.
- After phase D, both suites pass the same way. `python -m app.kernel.release check`
  passes. `docker compose config` resolves the full stack with its monitoring
  profiles, and the full stack with the MCP overlay. A second run of the script
  finds nothing to move.
