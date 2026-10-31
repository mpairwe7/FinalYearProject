# ADR-0019: Retrieval answer integrity — cache generations, in-force editions and decisions on calibrated evidence

- **Date:** 2026-10-30
- **Scope:** Backend retrieval, answer verification, and caching (`cache.py`, `retriever.py`, `query.py`,
  `corrective_rag.py`, `guardrails.py`, `indexer.py`, `llm.py`, `entailment.py`, `claim_verifier.py`, `cms.py`, `main.py` `/v1/index`).
- **Gaps:** G277–G290, G292–G294, G296–G297 fixed; G291 and G295 open (`docs/GAPS_AND_AGENTIC_ROADMAP.md`).

## Status

Accepted. Implemented on `fix/rag-retrieval-hardening-g277`; deployment pending.

## Context and Problem Statement

A two-track QA audit of the retrieval layer (one track reading the code, one
independent) found several paths that served a confident, cited, *wrong* answer.
None raised an error, and the existing gates could not see them:

1. **Cache.** The similarity tier ignored the index stamp, which the exact tier
   keyed on, so after a reindex a question matched its own old answer at cosine
   1.0. Tombstones and the fiscal year retired nothing. The compatibility guard
   needed two shared content terms, so "VAT rate" took the "VAT rate on exports"
   answer and PAYE took VAT's threshold. On Redis, every similarity lookup failed
   with WRONGTYPE because the scan matched the exact tier's string keys. Answers
   served during an outage were cached past it.
2. **The law in force.** The rerank step demoted any chunk whose name or text held
   "2020", "2021" or "2022" as a substring. That demoted the EAC CET 2022 version
   and the 2021 immigration fees regulations, both in force. It never moved with
   the fiscal year.
3. **Query rewriting.** The English fuzzy speller rewrote 23% of URA's own correct
   FAQ questions (taxes→taxis, exporting→importing, unregistered→registered).
   Its texting shorthand turned "Part B" into "Part be". The self-retrieval gate
   searches with the FAQ text, so it could not see this.
4. **Decisions on adjusted scores.** Soft ranking boosts were written into the
   score that abstention reads, lifting a 0.18 passage over the 0.30 threshold. A
   three-matched-words lift bypassed the lexical floor. A reranker failure was
   charged to Qdrant's circuit breaker. Unbounded reranker logits would have been
   clamped to 0/1.
5. **Recall and robustness.**
   - Corrective RAG dropped the question's locale and ran the English speller over
     vernacular text.
   - The multi-intent merge pruned the weaker intent away.
   - Tombstone reads failed open and matched by substring.
   - `POST /v1/index` rebuilt the live collection in place, keeping only the FAQ
     rows.
   - No payload index backed any filtered field.

Current practice points the same way:
- A single static similarity threshold gives no correctness guarantee for semantic
  caches (vCache, ICLR 2026).
- Retrieving over current text only gets dated legal questions wrong (FiscalQA Pro,
  2026; ICAIL 2026).
- Qdrant documents payload indexes for every filtered field.
- OWASP LLM08:2025 calls for immutable, attributable retrieval behaviour.

## Decision Outcome

1. **Cache generations (G277).** Both tiers and both backends key on
   `cache.cache_generation()`. It combines:
   - the freshness index stamp;
   - the corpus hash of the collection the retriever bound to
     (`register_index_binding`);
   - a digest of the active tombstones;
   - the fiscal year in force.

   An unreadable tombstone store yields a fresh value per read, never a reusable
   one. A semantic hit needs the same content terms once filler words are set
   aside, plus negation parity; cosine only chooses among phrasings. Redis
   similarity entries live in a per-tenant/locale/generation namespace and are
   read in one pipeline. An answer served from keyword fallback while Qdrant is
   configured but unavailable is not stored. Each lookup is counted by outcome.
2. **In-force editions (G278, G287).** `demote_stale_editions` reads edition
   metadata — the fiscal-year label, then a fiscal-year label in the source name,
   then a text cue for unlabelled chunks — never a bare year. It demotes editions
   `RETRIEVER_STALE_EDITION_YEARS` (4) or more behind `current_fiscal_year()`,
   unless the question names that year. Passage headers carry `Edition` and
   `Effective`, and prompt rule 8 says to prefer the most recent edition and name
   its fiscal year.
3. **Withdrawals (G279).** A tombstone names a document by exact identity: the
   same name, or URL against bare filename. A failed store read applies the last
   good read and is counted.
4. **Rewriting only fixes misspellings (G282).** The fuzzy speller never changes a
   word the served corpus uses. The retriever registers the BM25 vocabulary it
   loads, the service registers the FAQ rows, and the state file is the fallback.
   Texting shorthand applies only to lowercase letters that label nothing.
   Corrective RAG passes the locale and skips English spelling for vernacular
   questions (G280).
5. **Decisions read calibrated evidence (G281, G283, G284).**
   - `score_calibrated` is the reranker's own score. Abstention, pruning, corrective
     RAG and the drift histogram read it through `gating_relevance`; boosts and
     demotions only reorder.
   - Reranker logits are squashed, not clamped, and counted.
   - A rerank failure serves unscored hits and never touches Qdrant's circuit.
   - The three-word lexical lift needs at least half the floor in IDF coverage.
6. **Multi-intent recall (G285).** Each sub-query batch is pruned against its own
   best, and each batch's best hit keeps a place.
7. **Index operations (G286).** `build_index` ensures keyword payload indexes on
   every filtered field. `python -m app.indexer --ensure-payload-indexes` backfills
   a live collection. `POST /v1/index` runs the staged lifecycle; it returns 409 on
   a legacy non-alias collection rather than deleting it.
8. **A stack that can store its state (G296).** The compose tmpfs mounts are owned
   by the image's `appuser`. As bare entries they were root-owned, so the
   read-only API could not create its SQLite store, and tombstones, conversations
   and workflow state failed at startup. This was found by running the compose
   stack, not by any test.

## Consequences

The semantic cache hits less often. That is deliberate: a miss costs one retrieval,
while a false hit costs the taxpayer the right answer.
`semantic_cache_lookups_total{outcome="guard_rejected"}` shows how often the old
guard would have served one. Old Redis similarity keys are never read again and
expire within the TTL.

Questions about years four or more behind the one in force still retrieve their
editions when they name the year. Unlabelled documents are never demoted by
filename. A long, situational on-topic question with little IDF coverage can now
abstain on a sparse-only deployment where it used to answer. Abstention and
pruning ignore preference boosts, so a weak passage from this year's edition no
longer passes because of its year.

`/v1/index` now needs `QDRANT_COLLECTION` to be an alias. It also needs the full
corpus mounted: the lifecycle reads every source, and the canary retrieval gate
must pass.

Still open:
- **Calibration and evaluation:**
  - vernacular abstention thresholds (G291);
  - per-language gold sets (G295).

Closed in the 2026-10-31 follow-up pass (`App/backend/tests/test_gap_closures_oct2026.py`):
- **Answer verification & overrides (G288, G289, G290, G293):** False-premise rate percentages in the user's question are no longer exempted by `numeric_contradiction` unless negated; `has_money_amount(message)` only approves unsupported claims whose figures derive from `verified_facts` or user scenario amounts; `cms.lookup` normalizes punctuation and runs on both REST and streaming transports; and claim support is verified per passage rather than across a concatenated bag-of-words union.
- **Retriever lifecycle & effective dates (G292, G294, G297):** Shared `get_shared_retriever()` singleton across `service.py`, `rag_tool.py`, and `main_graph.py`; RLock-serialized `initialize()` and periodic `_maybe_refresh_alias_binding()` that reloads BM25 state and recovers `_sparse_ok = True` after an alias swap; `SemanticCache.clear()` and `RedisSemanticCache.clear()` wired into `POST /v1/index`; ISO `valid_from`/`valid_to`/`superseded_by` metadata populated by `annotate_fiscal_year`; and root `Dockerfile` aligned to `cu126`.

## Confirmation

- `App/backend/tests/test_gap_closures_oct2026.py`
- `App/backend/tests/test_cache_generation_and_guard.py`
- `App/backend/tests/test_retrieval_temporal_and_tombstones.py`
- `App/backend/tests/test_query_spelling_corpus_guard.py`
- `App/backend/tests/test_retrieval_gates_and_merge.py`
- `App/backend/tests/test_cache_exact_tier.py`, `App/backend/tests/test_faq_grounding.py`,
  `App/backend/tests/test_query_spelling.py`, `App/backend/tests/test_retrieval_plan.py`
- Compose verification stack (`-p ura-g277`), recorded in `docs/GAPS_AND_AGENTIC_ROADMAP.md` G277–G287
