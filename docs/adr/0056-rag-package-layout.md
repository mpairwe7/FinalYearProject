# ADR-0056: The RAG modules live in packages named for their job

## Status

Accepted, 2026-11-14. The project owner asked for the backend to follow a
reference `rag-project` layout (`api/`, `middleware/`, `rag/`, `ingestion/`,
`embeddings/`, `vectorstore/`, `db/`, `config/`), and chose the reference file
names and a two-step delivery. This record covers step 1, which moves modules.
Step 2 carves the chat, documents and health routes, the error handlers and the
rate limiter out of `main.py` into `api/` and `middleware/`, and the answer
prompts out of `rag/generator.py` into `rag/prompt_builder.py`.

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

Parts of the reference layout were not adopted:

- `logs/app.log` and `logs/error.log`. The service writes one JSON line per record
  to stdout, which the container platform collects. The Space disk is wiped on
  every restart, so log files there would be lost.
- `tests/test_rag.py` and `tests/test_api.py` as the only test files. The 235
  focused test modules stay; merging them would hide which behaviour a failure
  belongs to.
- `utils/helpers.py`. No module is a grab-bag of helpers, and creating one would
  invite one.
- `ingestion/loader.py`, `chunker.py` and `parser.py` as file names. No module
  fills each slot one to one: every corpus builder loads and chunks its own
  source, and the chunkers live in `ml/`.

## Consequences

- Import paths changed: `app.service` is `app.rag.pipeline`, `app.llm` is
  `app.rag.generator`, and so on (the table above). Commands changed with them:
  `python -m app.ingestion.indexer`, `python -m app.vectorstore.index_lifecycle`,
  `python -m app.vectorstore.vector_db`, `python -m app.ingestion.pdf_corpus`.
- Logger names follow the module (`logging.getLogger(__name__)`): a log query
  that filtered on `app.service` must now filter on `app.rag.pipeline`.
- Patch points moved with their modules. Stub `answer_in_english` on
  `app.rag.english_rendering`; the cloud prompt is
  `app.rag.generator.CLOUD_SYSTEM_PROMPT`.
- A branch cut before this change that adds an import of an old path fails at
  import time. Merge `dev`, then run the script with `--verify`: it lists every
  import or patch-target string that no longer resolves.
- The public API is unchanged: the same 139 routes, the same middleware stack and
  the same OpenAPI schemas.

## Confirmation

- `python3 App/backend/scripts/restructure_rag_layout.py --verify` reports only
  the ten findings it reports on the parent commit (logger names such as
  `app.security` and a test-only `app.mcp.servers`). Every other import and
  patch-target string in the repository resolves.
- The OpenAPI schema, route table and middleware stack dumped from `app.main`
  before and after the move are identical except one endpoint docstring, which
  now names `app.vectorstore.index_lifecycle`.
- `tests/agents/test_agentic_rag_context_sandbox_2026_11.py` still finds exactly
  the seven transports allowed to import the orchestrator, now `rag.pipeline`.
- `ruff check ml/ App/backend/ --select=E9,F63,F7,F82` passes, and the broader
  `F401,F811,F821` report has the same 126 findings before and after.
- Both CI test steps (`pytest tests/` from the root, then `pytest tests/` in
  `App/backend`) pass.
