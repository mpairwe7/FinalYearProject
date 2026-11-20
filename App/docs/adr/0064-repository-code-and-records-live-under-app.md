# ADR-0064: Repository code and records live under App/

## Status

Accepted, 2026-11-18. Carried out in four phases, one pull request each.

- Phase A (done): remove the root pointer folders and the fine-tuning code, and move `configs/`.
- Phase B (done): remove the byte-identical duplicate PDFs from `App/Data/pdfs`.
- Phase C (done): move the root code and record folders under `App/`.
- Phase D (done): move the root data, build and security files under `App/`, and
  remove the duplicate Trivy steps.
- Phase E (done, 2026-11-19): merge the two Compose stacks into one base and one overlay.
- Phase F (done, 2026-11-20): the working `.env` moves to `App/.env`, and the Compose
  overlays move from `App/` to `App/infra/compose/`.

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
  `.env` (until phase F, below).

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
- `App/` has one Compose stack (phase E, 2026-11-19). `docker-compose.yml` defines the
  services. `docker-compose.full.yml` is an overlay that turns it into the image-based,
  hardened variant, so that variant is now two files:
  `COMPOSE_FILE=App/docker-compose.yml:App/docker-compose.full.yml`.
  - **The decision changed.** The merge was first measured and declined. The four
    services both files defined shared 96 of 209 lines (45%), an exact merge needs
    `!override` tags, and the stacks cannot be run here. The owner then asked for the
    merge. The two files had already drifted once without notice: phase D left four
    builds pointing at a Dockerfile that was gone.
  - **What moved.** The base took the 13 services only the full file defined
    (`postgres`, `api-dev`, `caddy` and the ten monitoring services), with their seven
    volumes and two secrets. Each sits behind a profile, so the base starts the same
    four services as before. The overlay keeps only what differs for `redis`, `qdrant`,
    `api` and `frontend`, and is 186 lines where the file had 559. The two files
    together went from 780 lines to 731; the gain is one home for each setting, not size.
  - **`vllm` stays in the overlay.** `docker-compose.local-sunflower.yml` defines its
    own `vllm` on the base, and Compose would merge the two.
  - **One network.** The full stack's `ura-network` is now the base's `app-network`. A
    running full stack stays on the old network, and a `down` with the new files does
    not remove it. Stop it once with `docker compose -p app down` before the first `up`. That needs no
    Compose file, stops the containers of every profile, removes the old network and
    keeps the volumes.
  - **Compose v2.24.4 or later.** The overlay uses `!override`.
  - **The overlay alone is not a stack.** `docker compose -f App/docker-compose.full.yml`
    now stops with "service redis has neither an image nor a build context".
  - **The OWASP ZAP job runs again.** It called `docker compose` from the repository
    root, where no Compose file has been since phase D, so it failed on every push to
    `dev` from then on (G691). It now names the pair.
- A checkout now has `App/Model/bm25_state.json`, as the image does, so
  `_simple_search` scores with BM25 locally too. The committed state dates from
  May, and with it one Kiswahili FAQ row did not retrieve itself (G673). That is
  fixed in the scoring, and the acronym test runs with the state again. The image
  rebuilds its state from the corpus, so production was not affected.
- The working settings file is `App/.env` (phase F, 2026-11-20). The templates had moved
  in phase D and the file itself had not, so eight callers named the repository root:
  the base stack's `env_file`, the backend's cloud settings (which read `.env` from
  the working directory, so only when started at the root), `validate_env.py`, three
  Space secret scripts and the ZAP job. Each now names `App/.env`. A ninth, the
  monitoring check's `touch .env`, was found by CI and not by the search, so a test
  now fails on any workflow that creates the file at the root. The root
  `.dockerignore` excludes `**/.env`: a pattern there is anchored at the context root,
  so `.env` alone would have left `App/.env` inside every build that starts at the root.
  A checkout made before this phase must move its own file: `mv .env App/.env`.
- `App/.env` is now also the file Compose fills `${NAME}` from, in the base and in every
  overlay. Compose reads the `.env` in the folder of the first file for that, and while
  the settings file sat at the root it was only the `api` service's `env_file`. So a
  value in the file now wins over a default written as `${NAME:-default}`, where before
  the default won. That is what the documents already assumed when they say to put
  `METRICS_TOKEN` or `POSTGRES_PASSWORD` in `.env`. An independent check of this phase
  found it: the first comparison had used an empty file. Five values that define a
  stack are therefore written out and no longer read from the file: the collector
  address and JSON logs in the full overlay, the two local translation backends in the
  GPU overlay, and `SOVEREIGN_MODE=true` in the production overlay. The file's values
  are for a process on the host, and read into those stacks they would point the
  container at `localhost`, turn a cloud leg back on, or switch hardening off.
- Agent and editor files leave the repository root. `AGENTS.md` moves to
  `App/AGENTS.md` and stays the router the governance test reads. `CLAUDE.md`,
  `.claude/` and `.vscode/` are untracked and ignored, at the owner's request, so the
  three project skills are no longer in review and the compliance gate no longer lists
  one of them. A root `AGENTS.md` or `CLAUDE.md` on a developer's machine is a local
  pointer to `App/AGENTS.md`.
  Git removes a file from the working tree when a checkout moves to a commit that no
  longer tracks it, so a checkout that wants its copies back restores them once, from
  the repository root: `git archive 06ecc9a3 CLAUDE.md .claude .vscode | tar -x`. The
  restored `CLAUDE.md` names a root `AGENTS.md` that no longer exists, so change its
  link to `App/AGENTS.md`.
- An ignored file is not protected the way an untracked one is. Checking out a branch
  that still tracks these paths overwrites the local copies without a warning, and
  checking out a newer branch again deletes them. An independent check measured both.
  So merge `dev` into an open branch before checking it out, or keep copies of local
  agent files outside the repository. A `CLAUDE.md` with uncommitted edits stops the
  first move with git's own error, and nothing is lost in that case.
- `App/` holds one Compose file (phase F). The eleven overlays are in
  `App/infra/compose/`, and a command lists them after the base:
  `-f docker-compose.yml -f infra/compose/docker-compose.full.yml`. Compose reads the
  paths of every file from the folder of the first one, so no path inside an overlay
  changed. Two files that were not overlays are gone. `docker-compose.dev2.yml` served
  a model that is in neither the model card nor the AIBOM, mounted a root folder that
  phase C removed, and no command, workflow or test named it. One runbook sentence
  mentioned it as a stack that might be left running. `docker-compose.plugins.yml` held one
  service, which the base now defines behind the `plugins` profile.

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
- The Compose merge was compared, not run. `docker compose config --no-interpolate --format json`
  resolved 26 combinations before and after: the base alone, every overlay and documented
  chain on it, the full stack under eight profile sets, and the full stack with the MCP
  overlay. Nothing differs except the network's name, the profile-gated definitions the
  base gained, and one volume the full model now declares and does not mount
  (`qdrant_backups`). The services each combination starts are the same.
- The review then changed `api-dev` (dev profile) on purpose (G693). It had not worked:
  the image's entrypoint ignores `command`, so its `--reload` never applied, and its
  read-only source mount hid the folder the database is created in. It now sets its own
  entrypoint with `--reload` and keeps the database in `/tmp/ura-data`. The comparison,
  rerun, differs in `api-dev` alone.
- `App/tests/test_compose_build_paths.py` keeps the overlay an overlay: it may only change
  services the base defines (and add `vllm`), it may not repeat a setting the base already
  has, and the pair must resolve to the hardened variant. That last test merges the two
  files with Docker Compose, and fails if an `!override` or `!reset` tag is dropped.
- `App/tests/test_compose_build_paths.py` checks that every Compose build names a Dockerfile that exists. `docker compose config` does not check that. After phase D, four builds in `App/docker-compose.yml` and two overlays still named the root `Dockerfile`; they now name `App/Dockerfile`.
- Phase F was checked the same way. What Compose resolves was recorded before and after
  for 16 combinations: the base, the base with each overlay, and the four chains the
  runbooks use. They differ in three things: the `api` service's `env_file` is
  `App/.env`, the base defines `ura-plugins-server`, and its network exists when that
  profile is on. With no profile the stack starts the same four services on the same
  network. With a marker value in a scratch `App/.env`, Compose read it when started
  from `App/`, from the root with `-f`, and from the root with `COMPOSE_FILE`.
- `App/tests/test_compose_build_paths.py` holds the phase: no Compose file reads an env
  file outside `App/`, the root build context excludes `.env` at any depth, the backend
  names `App/.env`, and every build in an overlay still names a Dockerfile that exists.
- The comparison was repeated with `App/.env.example` standing in as a filled settings
  file. After the five values were written out, the base, the GPU chain and the
  connector and MCP overlays resolve the same with or without it. The full overlay
  differs in three values an operator is meant to set: the frontend's
  `NEXT_PUBLIC_API_URL` build argument and the Hugging Face token passed to vLLM under
  two names. Both need the operator's own value: the template's API URL is the
  development port, which the full stack does not publish, and its token is a
  placeholder. The production overlay's required `CORS_ORIGINS` is met by the
  template's localhost value, and the API then refuses to start with a localhost
  origin in production, so that stack still fails closed, later. A name the template
  only carries as a comment (`REDIS_URL`, the connector and web-research flags) now
  reaches the stacks too if someone sets it. `test_compose_build_paths.py` fails if one of the five is read from the
  file again, or if an agent or editor file is tracked at the root.
