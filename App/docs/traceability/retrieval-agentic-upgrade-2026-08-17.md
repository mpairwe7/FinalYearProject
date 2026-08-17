# Retrieval + agentic serving-path upgrade — 2026-08-17

Traceability record for the retrieval work landed on this date. Pair with
`docs/RAG_ARCHITECTURE.md` (pipeline) and `docs/GAPS_AND_AGENTIC_ROADMAP.md`
(G16, G17, G27). This file is the audit trail: what changed, why, how to
re-verify, and what is still open.

## 1. Intent

Close serving-path drift against 2025–2026 agentic RAG practice without a
re-index:

1. Query-time metadata (G17) and multi-intent decomposition.
2. One retrieve function for REST, streaming, the RAG tool, and LangGraph.
3. Statutory graph as a real RRF *leg*, not an unconditional prepend (G16).
4. Index source-hash drift check (G27).
5. Eval-set expansion so the 21-question hole cannot recur.
6. HyDE on the dense leg only, flag-gated off by default.

## 2. Decision log

| Decision | Choice | Why |
|----------|--------|-----|
| Hard FY filter | Only explicit `FY2024-25` / `2024/25 fiscal year` | A bare “2026” is ambiguous (Ugandan FY is July–June). |
| Tax type / “current year” | Soft boost, not a filter | A missing edition must not empty the result set. |
| Decomposition split | `and also` / `as well as` / second question — not bare `and` | “VAT and PAYE rates” is one comparison. |
| Shared retrieve | `HybridRetriever.search_planned()` | REST, stream, RAG tool, corrective, LangGraph, and voice prefetch had already drifted once. |
| Graph fusion | RRF over two ranked lists + `score_norm` 0.84 / 0.70 | Prepend always beat a better passage; fusion does not. |
| Graph flag | `FLAG_GRAPH_FUSION` stays default off | Shadow accuracy gate (75% on unseen multi-hop) is still the open criterion. |
| HyDE | Dense-only; template default; `HYDE_LLM` is vLLM-only | BM25 + reranker must keep the taxpayer’s words. Local 8B `generate()` is the full RAG answerer — not a HyDE completer. |
| Freshness | Hash + compare + exit codes; no auto re-index | Auto-recreate needs an ops window and GPU/CPU policy. |
| Eval growth | Additive `case_id` rows, keep the original 21 | Historical MODEL_CARD numbers stay auditable. |

## 3. Code surface

| Area | Files |
|------|-------|
| Query plan | `App/backend/app/query.py` (`plan_retrieval`, filters, preferences, `decompose_query`) |
| HyDE | `App/backend/app/hyde.py`; dense encode in `retriever.search` and `_search_vectorize` |
| Fusion / boosts | `App/backend/app/retriever.py` (`search_planned`, `rrf_fuse_ranked_lists`, `apply_preference_boost`, `RRF_K`) |
| Graph hit | `App/backend/app/graph/shadow.py` (`graph_hit_for`) |
| Serving | `App/backend/app/service.py` (`search_planned`, `_fuse_graph_leg`, flag-gated rewrite/cache/corrective) |
| Tool / graph runtime | `App/backend/app/tools/rag_tool.py`, `App/backend/app/agents/graphs/main_graph.py`, `App/backend/app/native_voice/speculative_prefetch.py` |
| Freshness | `App/backend/app/freshness.py`; snapshot write from `indexer.main` |
| Flags | `App/backend/app/flags.py` (`query_decomposition`, `hyde`) |
| Eval | `Data/eval/rag_eval.jsonl` (30 rows) |

## 4. Flags and environment

| Name | Default | Rollback |
|------|---------|----------|
| `FLAG_QUERY_REWRITE` | on | `false` — raw `normalize()` only |
| `FLAG_QUERY_DECOMPOSITION` | on | `false` — single `search()` |
| `FLAG_HYDE` | **off** | leave unset for a `FLAG_HYDE_PERCENT` canary; do not set `true` until measured |
| `FLAG_HYDE_PERCENT` | 0 | A/B-ready; buckets on `user_id`. Ignored if `FLAG_HYDE` is set. |
| `FLAG_TRANSLATE_RETRIEVE` | on | `false` — original-language query only |
| `HYDE_LLM` | false | ignored unless `FLAG_HYDE` and `LLM_BACKEND=vllm` |
| `FLAG_SEMANTIC_CACHE` | on | `false` — skip get/put |
| `FLAG_CORRECTIVE_RAG` | on | `false` — `should_correct` short-circuits |
| `FLAG_GRAPH_FUSION` | **off** | leave off until multi-hop gate |
| `FLAG_TAX_GRAPH` | **off** | required together with fusion |
| `RRF_K` | 60 | Qdrant + Vectorize + graph leg |
| `CURRENT_FISCAL_YEAR` | unset → rate-table year in force (`FY2026-27` as of 2026-07-01) | soft preference only; do not freeze last year’s edition |
| `INDEX_FRESHNESS_PATH` | `App/Model/index_freshness.json` | — |
| `FRESHNESS_SLACK_WEBHOOK` | unset | https only; `--notify` no-op if empty |
| `INDEX_REINDEX_REQUEST_PATH` | `App/Model/index_reindex_requested.json` | `--enqueue` only; never starts indexer |

All of the above are in `.env.example`.

## 5. How to re-verify

```bash
cd App/backend
python3 -m pytest \
  tests/test_hyde.py \
  tests/test_retrieval_plan.py \
  tests/test_graph_rrf_fusion.py \
  tests/test_freshness.py \
  tests/test_eval_set_completeness.py \
  tests/test_retrieval_regression_gate.py \
  tests/test_graph.py \
  tests/test_flag_rollout.py \
  tests/test_priority_hit_ordering.py \
  tests/test_query_spelling.py \
  tests/test_score_calibration_grounding.py -q
```

Freshness (after an index, or `--write` on a known-good tree):

```bash
python3 -m app.freshness --check
# exit 0 = match, 1 = drift, 2 = no snapshot
```

Keyword production path (no Qdrant): `test_retrieval_regression_gate.py`
must stay green. Completeness: every `reg-*` id in `rag_eval.jsonl` is
asserted by `test_eval_set_completeness.py`.

## 6. Eval `case_id` registry

| case_id | Guards |
|---------|--------|
| `reg-how-file-returns` | Procedure, not “what is a return” |
| `reg-how-submit-yearly` | Same defect, paraphrase |
| `reg-efris-what` | Follow-up / framing |
| `reg-current-vat-fy` | Soft current-FY preference |
| `reg-explicit-fy` | Hard `FY2024-25` filter parse |
| `reg-multi-intent` | Decomposition |
| `reg-off-domain-france` | Abstain |
| `reg-off-domain-president` | Abstain |
| `reg-off-domain-banana-bread` | Abstain |

Do not delete these ids. Add new regressions as new ids; do not recycle.

## 7. Still open (do not mark shipped)

- `FLAG_GRAPH_FUSION` open criterion: expand multi-hop golden set; shadow ≥ 75% on *unseen* questions. Do not flip the flag.
- HyDE **measurement** on `rag_eval.jsonl` before raising `FLAG_HYDE_PERCENT` above 0. The A/B mechanism is shipped; the experiment is not.
- True passage-id join from graph claims to crawl chunks (fusion today is rank-level, not entity-linked).
- Historical MODEL_CARD RAG table is still the 21-sample slice — re-run `evaluate_rag.py` before citing it as current.
- Auto-reindex on freshness drift stays **out**. Slack notify + enqueue-file are shipped.

## 8. Audit verdict (2026-08-17, re-verified)

**Not all agentic RAG gaps are shipped.** The 2026-08-17 slice closed the
serving-path retrieval gaps it named. Canonical status is
`docs/GAPS_AND_AGENTIC_ROADMAP.md` (this file is the decision log).

| ID / practice | Code | Default | Docs agree | Justification (why this state) |
|---------------|------|---------|------------|--------------------------------|
| G16 graph as RRF leg | Yes | **off** | Yes (GAPS 🟢 code; flag off) | Shadow gate 75% on *unseen* multi-hop is still the open criterion. Rank-level fuse, not entity-linked. |
| G17 metadata plan | Yes | on | Yes | Hard FY only on explicit `FY20xx-yy`; tax type / “this year” are soft so a missing edition cannot empty results. |
| Query decomposition | Yes | on | Yes | Split on `and also` / second question, not bare `and`. |
| Shared `search_planned` | Yes | — | Yes | One helper; LangGraph still skips FAQ blend. |
| HyDE dense-only | Yes | **off** | Yes | BM25 + reranker keep the taxpayer’s words. `FLAG_HYDE_PERCENT` + `user_id` is A/B-ready; percent stays 0 until measured. |
| G27 freshness hash | Yes | CLI + CI | Yes (🟢) | `--write-status` + `GET /v1/index/freshness` + nightly `--notify`. `--enqueue` writes a request file. Auto-reindex off. |
| Eval `reg-*` ids | Yes | 30 rows | Yes | Original 21 kept so MODEL_CARD numbers stay auditable. |
| G18 translate-retrieve | Yes | on | Yes (🟢) | Corpus stays English; generate in locale. `FLAG_TRANSLATE_RETRIEVE`. No re-index. |
| G19 citation URL | Yes | on | Yes (🟢) | Crawl URL when present; `canonical_source_url` backfills ura.go.ug for `ura_*` files. |
| G20 supervisor plan | Yes | flag | Yes (🟢) | Bounded tool loop; full planner-executor is Phase 17+. |
| G21 ReAct | Yes | bounded | Yes (🟢) | `act → observe → synthesize → reflect`. One retrieve hop; one retry on low faithfulness or reasoning miss. Not unbounded ReAct. |
| G22 specialist prompts | Yes | on | Yes (🟢) | `agents/prompts.py` fragments; YAML/hot-reload optional. |
| G23 delegation | Yes | 1 hop | Yes (🟢) | Typed `handoff_*` on graph state; specialist → retrieve once. |
| G24 detail_level | Yes | on | Yes (🟢) | beginner/expert fragments; unknown values ignored. |
| G25 per-segment eval | Yes | on | Yes (🟢) | `by_segment` has topic, locale, taxpayer_type, variant. Min group 3. |
| G26 variant logging | Yes | on | Yes (🟢) | `flag_variants` + `locale` on each logged turn; eval groups `hyde:off` etc. |
| G7 / current FY boost | Yes | tables | Yes | Unset `CURRENT_FISCAL_YEAR` follows `resolve_fiscal_year()` (`FY2026-27` on 2026-08-17). |

Doc authority: **GAPS** is the living register. The April 2026 Enhanced
roadmap and the Next-Gen proposal are dated; they now say so at the top
so a later audit cannot treat their G16/G17/G27 rows as current.

## 9. Related records

- FAQ retrieval diagnosis: `faq-retrieval-diagnosis-2026-07-21.md`
- Local FAQ setup: `../local-faq-retrieval.md`
- Architecture: `../../docs/RAG_ARCHITECTURE.md`
- Gaps: `../../docs/GAPS_AND_AGENTIC_ROADMAP.md` (living register)
- Dated proposals (not current status): `../../docs/URA_Chatbot_Roadmap_2026_Enhanced.md`, `../../docs/NEXTGEN_ARCHITECTURE_PROPOSAL_2026.md`
