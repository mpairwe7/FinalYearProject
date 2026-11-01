# ADR-0024: November 2026 Hybrid Search, Agentic RAG, Memory Summarization, Sandbox Confinement & Multi-Agent Orchestration Hardening

- **Date**: 2026-11-01
- **Architects**: URA AI Platform Engineering

## Status

Accepted

## Context and Problem Statement

A comprehensive November 2026 gap analysis across five core subsystems—**Hybrid Search**, **Agentic RAG**, **Memory Summarization**, **Code Execution Sandbox & Isolation**, and **Multi-Agent Systems (MAS) Orchestration**—benchmarked the codebase against peer-reviewed literature (Bruch et al. ACM TOIS 2024; Su et al. ICLR 2025 *BRIGHT*; Jiménez Gutiérrez et al. ICML 2025 *HippoRAG 2*; Lin et al. 2025 *Sleep-Time Compute*; Rasmussen et al. 2025 *Zep/Graphiti*; Chhikara et al. 2025 *Mem0*; Cemri et al. NeurIPS 2025 *MAST*; Kim et al. *Nature Machine Intelligence* 2026) and security standards (**OWASP Top 10 for Agentic Applications 2026 `ASI01`–`ASI10`**, **MCP `2026-07-28`**, **IETF RFC 8693 / RFC 8707**).

Several concrete gaps were identified:

1. **Hybrid Search (`HS-2..HS-5`)**:
   - `_RERANK_CHARS = 1200` in `retriever.py` truncated statutory passages packed up to `hard_max_chars = 4000` by `chunkers.py`, blinding `mxbai-rerank-base-v2` (8k token window) to the bottom 70% of chunks and Markdown tables.
   - Qdrant server-side `Fusion.DBSF` ran with `prefetch_limit = 20`, leaving empirical $\mu \pm 3\sigma$ normalization vulnerable to small-sample BM25 spikes.
   - `indexer.py` omitted vernacular FAQ columns (`question_lg`, `question_sw`, `question_nyn`, `question_ach`) from `_embedding_text`, and `annotate_fiscal_year` stamped only whole-FY bounds (`YYYY-07-01`..`YYYY-06-30`) even when a chunk explicitly stated a mid-year effective date.
2. **Agentic RAG (`AR-2, AR-3`)**:
   - `node_reflect` in `main_graph.py` called `corrective_rag._expand_query(query)`, which was a no-op whenever `"URA"` or `"Uganda"` already appeared in the query—causing reflection retries to execute the identical search query a second time.
   - `app/graph/` projected rate tables without `CROSS_REFERENCES` edges linking statutory Schedule provisions to their parent Act's charging sections.
3. **Memory Summarization (`MS-1..MS-4`)**:
   - `MemoryService.absorb_conversation` formatted episodic summaries purely as `"Discussed X, Y."` without recording sanitized tool/workflow/resolution outcomes, and lacked a sleep-time consolidation method (`consolidate_user_memory`).
   - `SemanticMemory` lacked chronological fiscal-year validity bounds (`valid_from_fy`, `valid_to_fy`) for historical `as_of_fy` queries and did not auto-supersede `registered_vat` on status changes.
   - `extract_conversation_entities` in `context_manager.py` allowed mutually exclusive `"Resident"` and `"Non-Resident"` states to coexist unless an apology keyword was present.
   - `ResolutionLedger.store_verified_slots` lacked source-tier provenance ranking (`jwt_claim > tool_verified > user_asserted`), and `ProceduralMemory.record_verified_trajectory` did not screen candidate trajectories with `scan_retrieved_text` (OWASP `ASI06:2026` / MINJA).
4. **Code Execution Sandbox & Isolation (`CS-1..CS-4`)**:
   - `_validate_document_content` in `documents.py` invoked `inspect_pdf_bytes` in the **parent FastAPI process** before `document_worker.try_isolated` ran, opening untrusted PDFs with C-extension `pypdfium2`/`PyMuPDF` inside the main API worker.
   - `document_worker.py` spawned child processes that inherited sensitive environment variables (`JWT_SECRET_KEY`, `POSTGRES_DSN`, `MCP_SERVER_TOKEN_*`), lacked POSIX `RLIMIT` caps, lacked `proc.kill()` (`SIGKILL`) escalation on timeout, and had no concurrency semaphore.
   - No capability-restricted `Decimal` AST / multi-period schedule evaluator existed for deterministic multi-row tax calculations.
5. **Multi-Agent Systems Orchestration (`MO-1..MO-4`)**:
   - `ChatModel._run_graph` did not pass `thread_id=conversation_id` to `graph.run()`, leaving `DurableCheckpointStore` unused in production, and discarded `GraphOutcome.WAITING_USER`.
   - `node_act` dispatched read-only, idempotent tool plans sequentially rather than in parallel, and `node_observe` allowed failed (`ok: False`) upstream observations to persist across specialist handoffs.
   - `HttpTransport.call` authenticated to remote MCP servers with a static bearer token without attaching an RFC 8693 OBO + RFC 8707 audience-bound actor assertion header (`Mcp-Actor-Assertion`).

## Decision Outcome

We implemented a 3-phase upgrade across all five domains:

1. **Hybrid Search (`App/backend/app/retriever.py`, `App/backend/app/indexer.py`, `App/backend/app/pdf_corpus.py`, `ml/scripts/data_aug/chunkers.py`)**:
   - Raised `_RERANK_CHARS` default to `4000` and introduced `_DBSF_PREFETCH_MIN = 48` for Qdrant server-side DBSF prefetch legs.
   - Extended `_embedding_text(doc)` to index `question_lg`, `question_sw`, `question_nyn`, and `question_ach` directly while keeping `_vector_payload(doc)["text"]` clean.
   - Added `pymupdf` page-extraction fallback, level-tracked `heading_trail` (`(level, title)`), mid-paragraph Q&A splitting (`_INLINE_QA_SPLIT_RE`), question-header retention in `chunk.text`, and multilingual Q&A heading promotion (`_QA_HEADING_RE`, `80`-char Q&A floor) in `ml/scripts/data_aug/chunkers.py` so `URA-EFRIS-Handbook-FY2026-27-{English,Luganda,Runyakole,Swahili}.pdf` chunk into section-headed JSONL records.
   - Added CP1252 question-mark apostrophe/quote/bullet repair in `pdf_corpus.normalise_extracted_text`, stamped `language` (`en`, `lg`, `nyn`, `sw`, `ach`) and `tax_type`/`tag` (`efris`, `vat`, `paye`, etc.) on PDF chunk JSONL records, added fixed-point `mt.cache` memoization in `query.translate_query_for_retrieval`, raised `max_tokens=2048` in `gateway.gemini_generate` and `SpeechModel._gemini_translate` for Gemini 3.x thinking models, and wired pre-merge locale-aware preference boosting (`+0.08` on matching vernacular PDFs, `-0.08` on non-English PDFs during English queries) and vernacular native-hit preservation in `retriever.search_planned` / `_merge_translated_leg`.
   - Added `extract_provision_effective_date(text)` in `indexer.py` so `annotate_fiscal_year` stamps provision-specific effective dates when stated in statutory text.
2. **Agentic RAG (`App/backend/app/corrective_rag.py`, `App/backend/app/agents/graphs/main_graph.py`, `App/backend/app/graph/`)**:
   - Implemented `build_reflection_query()` combining uncovered question content tokens and statutory bridge concepts (`_STATUTORY_BRIDGE_EXPANSIONS`) and wired it into both `node_reflect` and `corrective_retrieve` so neither path re-runs a no-op duplicate query.
   - Added `EdgeKind.CROSS_REFERENCES` (`cross_references`), `_wire_provision_cross_references()` (`app/graph/build.py`), and `cross_referenced_provisions()` (`app/graph/query.py`) (expanding the projected statutory graph to 116 nodes and 211 edges across all 8 edge kinds).
3. **Memory Summarization & Bi-Temporal Conflict Resolution (`App/backend/app/memory/`, `App/backend/app/context_manager.py`)**:
   - Extended `absorb_conversation` with sanitized `outcomes` formatting (`_format_episodic_outcomes`) and added `MemoryService.consolidate_user_memory()` for sleep-time contradiction reconciliation and decay pruning.
   - Added `valid_from_fy` and `valid_to_fy` bi-temporal bounds and `as_of_fy` filtering to `SemanticMemory`, added `registered_vat` to `SINGLE_VALUED_CATEGORIES`, and made `Resident`/`Non-Resident` mutually exclusive across turns in `extract_conversation_entities`.
   - Enforced `source_tier` rank (`jwt_claim > tool_verified > user_asserted`) in `ResolutionLedger.store_verified_slots` and prompt-injection / evaluator gates in `ProceduralMemory.record_verified_trajectory`.
4. **Sandbox Confinement & Deterministic Schedule Evaluator (`App/backend/app/documents.py`, `App/backend/app/document_worker.py`, `App/backend/app/tax/schedule_sandbox.py`)**:
   - Restricted parent-process `_validate_document_content` to pure-Python header/token checks when `DOCUMENT_PARSE_ISOLATED=true`, executing `inspect_pdf_bytes` inside `document_worker._child`.
   - Hardened `document_worker.py` with timed `_PARSE_SEMAPHORE.acquire(timeout=_TIMEOUT_S)`, pre-join pipe draining (`parent.poll` + `parent.recv` before `proc.join` to prevent >64 KiB OS pipe deadlocks), secret environment scrubbing (`_SENSITIVE_ENV_RE`), single-threaded arena bounds (`OMP_NUM_THREADS=1`, `MALLOC_ARENA_MAX=2`), POSIX `RLIMIT_CORE`/`RLIMIT_FSIZE`/`RLIMIT_CPU`/`RLIMIT_AS`, and `proc.kill()` (`SIGKILL`) timeout escalation.
   - Created `App/backend/app/tax/schedule_sandbox.py` (`evaluate_decimal_expression`, `evaluate_multi_period_tax_schedule`) providing a zero-I/O, capability-restricted `Decimal` AST and multi-period schedule evaluator.
5. **Multi-Agent Systems Orchestration (`App/backend/app/agents/graphs/main_graph.py`, `App/backend/app/service.py`, `App/backend/app/mcp/auth.py`, `App/backend/app/mcp/transport.py`, `App/backend/app/mcp/servers/wire.py`)**:
   - Wired `thread_id=conversation_id` into `ChatModel._run_graph`, admitted `GraphOutcome.WAITING_USER` and surfaced `pending_action` in `service.py`, added parallel `ThreadPoolExecutor` dispatch for `read_only` + `idempotent` tool plans in `node_act`, and pruned failed observations on specialist handoffs in `node_observe`.
   - Added `mint_obo_actor_assertion` and `verify_obo_actor_assertion` (`mcp/auth.py`), `Mcp-Actor-Assertion` header emission in `HttpTransport.call` (`mcp/transport.py`), and server-side header-to-`_meta` alignment verification in `StatelessMCPServer._check_headers` (`mcp/servers/wire.py`).

## Consequences

### Positive
- Eliminates C-extension PDF parsing in the parent FastAPI process when `DOCUMENT_PARSE_ISOLATED=true` and prevents secret leakage or unkillable worker hangs in `document_worker.py`.
- Improves reranker recall on long statutory passages/tables (`4000` chars) and stabilizes Qdrant DBSF score fusion (`_DBSF_PREFETCH_MIN = 48`).
- Prevents wasted no-op reflection retrieval loops and protects long-term memory against unverified slot overwrites and indirect prompt injection (`OWASP ASI06:2026`).
- Enables cryptographic per-request actor/tenant/audience binding (`RFC 8693` + `RFC 8707`) on remote MCP tool calls (`OWASP ASI03:2026`).

### Negative / Trade-offs
- Raising `_RERANK_CHARS` from `1200` to `4000` increases cross-encoder token length on long PDF chunks; operators on constrained CPU-only hosts can tune `RETRIEVER_RERANK_CHARS`.

## Confirmation

Verified by `tests/agents/test_november_2026_gap_closures.py`, `App/backend/tests/test_pdf_guards.py`, `App/backend/tests/test_graph.py`, `App/backend/tests/test_indexer.py`, `App/backend/tests/test_memory_lifecycle.py`, and `governance/compliance_check.py`.
