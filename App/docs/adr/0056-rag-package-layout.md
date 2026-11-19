# ADR-0056: The RAG modules live in packages named for their job

## Status

Accepted, 2026-11-14. The project owner asked for the backend to follow a
reference `rag-project` layout (`api/`, `middleware/`, `rag/`, `ingestion/`,
`embeddings/`, `vectorstore/`, `db/`, `config/`), and chose the reference file
names and a two-step delivery. This record covers step 1, which moves modules.
Step 2 carves the chat, documents and health routes, the error handlers and the
rate limiter out of `main.py` into `api/` and `middleware/`, and the answer
prompts out of `rag/generator.py` into `rag/prompt_builder.py`. Amended
2026-11-15 when step 2 landed (the step 2 section below).

## Context and Problem Statement

`App/backend/app` held about 140 modules in one folder. The RAG path was spread
across them under names that said little about their job: the orchestrator was
`service.py` (15,000 lines), answer generation was `llm.py`, the CPU embedder was
`dense_cpu.py`, and the pgvector leg was `pgvector_dense.py`. Corpus builders,
Qdrant lifecycle code, the analytics store and the API models sat beside the
speech, receptionist and ticketing modules. A reader looking for "where is
retrieval" or "where is ingestion" had to know the repository already.

## Considered Options

1. Move the modules into job-named packages and take the reference names where
   a module fills a reference slot one to one.
2. Move the modules but keep their current file names.
3. Add the new packages as facades that re-export the old modules.

Option 3 leaves two import paths for every module and a compatibility layer
nobody removes. Option 2 keeps `service.py` and `llm.py`, the two names that
explain least. The owner chose option 1.

## Decision Outcome

Option 1. `App/backend/scripts/restructure_rag_layout.py` performs the whole
move: rerun it with `--apply` on the parent commit to reproduce the diff. Old
paths are removed in the same change; nothing re-exports them.

| Package | Modules (old name in brackets where it changed) |
| --- | --- |
| `rag/` | `pipeline` (`service`), `retriever`, `generator` (`llm`), `query`, `hyde`, `corrective_rag`, `context_manager`, `cache`, `claim_verifier`, `claim_polarity`, `entailment`, `faq_binding`, `turn_context`, `turn_record`, `english_rendering`, `live_research_turn`, `offline_rag` |
| `ingestion/` | `indexer`, `pdf_corpus`, `faq_corpus`, `crawl_corpus`, `docling_intelligence`, `document_normalization` |
| `embeddings/` | `embedder` (`dense_cpu`) |
| `vectorstore/` | `vector_db` (`pgvector_dense`), `index_lifecycle`, `qdrant_backup`, `corpus_sync`, `point_ids` |
| `db/` | `database`, `postgres` |
| `api/` | `models` (the Pydantic request and response models), and `connectors`, `offline`, `quality` (the old `routes/` package) |
| `middleware/` | `logger` (`logging_config`) |
| `config/` | `flags` |

A module that imported a renamed module by name now uses the new name:
`from .rag import pipeline` and `pipeline.ChatModel`, not an alias back to
`service`. Aliases that carried the old name follow it (`service_module` is now
`pipeline_module`, `llm_module` is now `generator_module`). Where the new name
was already taken in a file, the import is aliased `embedder_module`.

The safety layer (`guardrails`, `premise_guard`), translation (`mt`), the agent
graph (`agents/`), tools, speech, the receptionist and ticketing stay where they
were. They are not RAG modules, and moving them would only add churn.

### Step 2: routes, middleware and prompts

`App/backend/scripts/carve_out_modules.py` performs step 2, one spec per source
module: `--spec main` carves `main.py`, `--spec prompts` carves
`rag/generator.py`. Each output commit is the script's output, unedited.

| Module | What it took |
| --- | --- |
| `api/rag.py` | `/v1/chat`, `/v1/chat/stream`, `/v1/chat/title`, `/v2/chat/stream`, `/tags`, `/faq/{tag}` |
| `api/documents.py` | `/v1/documents/analyze`, its report and status, and `/v1/index` with the rebuild lock |
| `api/health.py` | `/health`, `/ready`, `/v1/index/freshness`, `/metrics` and the metrics-reader check |
| `api/deps.py` | `get_model`, `get_speech_model`, `experiment_log_fields`, the ops key and `require_admin_access`, shared by the routers and `main.py` |
| `middleware/rate_limiter.py` | the slowapi `limiter` and the per-route ceilings (`RATE_LIMIT`, `DOCUMENT_RATE_LIMIT`, ...) |
| `middleware/error_handler.py` | the handler that gives every 401 a `WWW-Authenticate: Bearer` challenge |
| `config/settings.py` | the API's title, version and description |
| `rag/prompt_builder.py` | `SYSTEM_PROMPT`, `CLOUD_SYSTEM_PROMPT`, the answer contracts, few-shot prompts, `passage_header`, `spotlight_passage`, `fence_system_aside` |

`main.py` stays the composition root: it builds the app and its middleware
stack, runs the lifespan, keeps the routes no step moved, and includes the three
routers. A router imports what it shares from `deps.py`, never from `main.py`, so
there is no import cycle. Names imported across modules lost their leading
underscore. Message assembly (`_build_messages`, `_build_tool_messages`) stays in
`generator.py`, because it budgets tokens against the loaded model; only the
prompt text and passage formatting moved.

Parts of the reference layout were not adopted:

- `logs/app.log` and `logs/error.log`. The service writes one JSON line per record
  to stdout, which the container platform collects. The Space disk is wiped on
  every restart, so log files there would be lost.
- `tests/test_rag.py` and `App/tests/test_api.py` as the only test files. The 235
  focused test modules stay; merging them would hide which behaviour a failure
  belongs to.
- `utils/helpers.py`. No module is a grab-bag of helpers, and creating one would
  invite one.
- `ingestion/loader.py`, `chunker.py` and `parser.py` as file names. No module
  fills each slot one to one: every corpus builder loads and chunks its own
  source, and the chunkers live in `App/ml/`.

## Consequences

- Import paths changed: `app.service` is `app.rag.pipeline`, `app.llm` is
  `app.rag.generator`, and so on (the table above). Commands changed with them:
  `python -m app.ingestion.indexer`, `python -m app.vectorstore.index_lifecycle`,
  `python -m app.vectorstore.vector_db`, `python -m app.ingestion.pdf_corpus`.
- Logger names follow the module (`logging.getLogger(__name__)`): a log query
  that filtered on `app.service` must now filter on `app.rag.pipeline`.
- Patch points moved with their modules. Stub `answer_in_english` on
  `app.rag.english_rendering`; the cloud prompt is
  `app.rag.prompt_builder.CLOUD_SYSTEM_PROMPT`. A test that patches something
  through `app.main` that a router now reads must patch the router's module
  instead; `carve_out_modules.py --verify` lists every such patch.
- The carved handlers log as `app.api.rag`, `app.api.documents` and
  `app.api.health` instead of `app.main`.
- The routers are registered after the routes left in `main.py`. Every request
  still reaches the handler it reached before (the confirmation below checks it).
- A branch cut before this change that adds an import of an old path fails at
  import time. Merge `dev`, then run the script with `--verify`: it lists every
  import or patch-target string that no longer resolves, and exits non-zero only
  on findings outside the nine it found on `dev` before the move, so it works as a
  gate.
- The app code keeps no compatibility layer, but the HF Space build does, on
  purpose. The Space builds `FROM` a pinned image of `dev`, which predates this
  change until the next roll, and a rollback may pin an older image still. Its
  CPU patch (`_app_source`) and its dense-leg load (`DENSE_LOADER`) therefore look
  for each moved file under its new name first and its old name second. Drop the
  old names once no pinned or rollback image predates the move.
- GitHub tracks code-scanning alerts by path. On `dev` no dismissed alert sits in
  a moved file, so no dismissal is lost; the 36 open alerts in moved files close
  at the old paths and open again at the new ones.
- The public API is unchanged: the same 139 routes, the same middleware stack and
  the same OpenAPI schemas.

## Confirmation

- `python3 App/backend/scripts/restructure_rag_layout.py --verify` exits 0: it
  reports only nine findings, all present on the parent commit. Seven are
  strings that only look like module paths (logger names such as `app.security`,
  test fixtures such as `app.test`), and two import a test-only `app.mcp.servers`.
  The script also found a real defect that predates the move:
  `agents/graphs/main_graph.py` imported `tax.money.parse_money_amount`, which
  does not exist, so a remembered amount reached a calculator unparsed. #741
  fixed it (#740). Every other import and patch-target string in the repository
  resolves.
- The HF Space image check builds on the old pin: the CPU patch and the dense-leg
  load find the old file names, and the smoke check requires a non-empty dense
  table whenever the base ships the dense artifact.
- The OpenAPI schema, route table and middleware stack dumped from `app.main`
  before and after the move are identical except one endpoint docstring, which
  now names `app.vectorstore.index_lifecycle`.
- `App/tests/agents/test_agentic_rag_context_sandbox_2026_11.py` still finds exactly
  the seven transports allowed to import the orchestrator, now `rag.pipeline`.
- `ruff check App/ml/ App/backend/ --select=E9,F63,F7,F82` passes, and the broader
  `F401,F811,F821` report has the same 126 findings before and after.
- Both CI test steps (`pytest App/tests/` from the root, then `pytest App/tests/` in
  `App/backend`) pass.

For step 2:

- The OpenAPI schema, route table, middleware stack and exception handlers dumped
  from `app.main` are byte-identical before and after. For every route a sample
  request was matched the way Starlette matches it, and each of the 143 lands on
  the same handler.
- `restructure_rag_layout.py --verify` still reports only the nine findings of
  the parent commit, so every import inside the carved handlers resolves.
- `test_agentic_rag_context_sandbox_2026_11.py` finds exactly the eleven
  allowed importers of the orchestrator: the seven transports plus the four
  carved `api/` modules.
- Run on the parent of the main carve-out, `carve_out_modules.py --spec main`
  reproduces that commit byte for byte.
- `restructure_rag_layout.py`, `carve_out_modules.py` and `split_module.py` were deleted on 2026-11-19, once the move was long finished (G686). The code is in git at `61e29d3e`. The `--verify` results above are as recorded at the time: its string heuristic has since gone stale and was never a CI check.
