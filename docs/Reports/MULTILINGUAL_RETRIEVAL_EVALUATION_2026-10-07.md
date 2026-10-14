# Multilingual retrieval evaluation: English, Luganda, Swahili

**Uganda Revenue Authority (URA) AI Taxpayer Assistant**
**Report date**: 2026-10-07
**System under test**: `dev` at `a5e0ff0e52` on the local GPU stack (the api bind-mounts the main checkout's `App/backend/app`). Index build `fc80e983f384`: 8,579 points (6,966 PDF chunks, 1,117 FAQ rows from 75 files, 489 crawl chunks, 6 teacher-QA). Qdrant 1.19.0; `BAAI/bge-m3` dense + client-side BM25 sparse, fused by Qdrant RRF; `mixedbread-ai/mxbai-rerank-base-v2` cross-encoder; Sunflower-14B-FP8 on vLLM for retrieval-time translation. vLLM on GPU 4, api on GPU 7 (RTX A6000).
**Benchmark**: `evals/retrieval_multilingual/` (2,751 queries), runner `scripts/eval_retrieval_multilingual.py`, full numbers in `evals/reports/retrieval_multilingual_2026-10-07.json`.

This report measures **retrieval** — which passages reach the answer step — not
the replies built from them. Every query names the points that answer it, so
each leg of the retriever can be scored on its own and against the others on
the same questions.

---

## 1. Verdict

**Luganda and Swahili retrieval is far behind English, and the stack as
deployed today is the worst of the configurations measured.** Since #541
(2026-10-06) the GPU stack runs without the cross-encoder and with bge-m3 on
CPU. In that state the passage handed to the answer step is right for **40%** of
Luganda and **41%** of Swahili questions, against **79%** for English
paraphrases (strict Hit@1, `faq` set).

| `search_planned` Hit@1 (95% CI) | English (paraphrase) | Luganda | Swahili |
|---|---|---|---|
| **C1 as deployed** (no reranker, CPU dense) | 0.79 (0.73–0.86) | **0.40** (0.33–0.47) | **0.41** (0.34–0.47) |
| **C2 reranker on** (GPU) | 0.88 (0.82–0.93) | 0.61 (0.54–0.67) | 0.78 (0.73–0.84) |
| **C3 BM25 only** (CPU-image fallback) | 0.63 (0.56–0.71) | **0.02** (0.01–0.05) | **0.05** (0.02–0.07) |
| Natively written lg / sw questions, C1 → C2 (n = 23 each) | – | 0.57 → 0.74 | 0.48 → 0.96 |
| Curated coverage-bank questions, C1 → C2 (LLM-judged, grade 3) | 0.97 → 0.95 | 0.70 → 0.83 | 0.75 → 0.91 |
| Same top passage as the English version of the question, C1 → C2 | – | 0.36 → 0.55 | 0.30 → 0.67 |

Turning the reranker back on is the single largest gain (+21 points Luganda,
+37 Swahili, +9 English), **but it must not be turned on as the code stands**:
the reranker's scores pass through a sigmoid twice (§6.2), which pins every
passage's relevance between 0.50 and 0.73. Abstention, context pruning and
corrective RAG all compare against thresholds that score can never cross. With
the reranker on, 100% of out-of-domain questions pass the retrieval gate and
71–74% reach the answer step carrying passages, against 22–37% today.

The other defects that matter most, all measured on the same queries:

- **The untranslated first pass displaces the translated one** whenever there is
  no reranker to arbitrate: production scores 15 (lg) and 25 (sw) points
  *below* its own translate leg (McNemar p < 10⁻⁵), and in BM25-only mode the
  untranslated pass takes the top slot 93–96% of the time.
- **The steps after retrieval override its ranking.** On questions where the
  service hands passages on, its top passage is right less often than
  `search_planned`'s: English 0.85 → 0.76, Luganda 0.65 → 0.52, Swahili 0.78 → 0.59
  (C2). A "language boost" that matches the letters `sw` inside the word
  "Answer" is one cause.
- **Human-written Luganda is often not recognised as Luganda.** 23 of 105
  coverage-bank and 8 of 23 corpus-written Luganda questions were taken for
  English, so they were answered in English and never translated;
  Sunflower-written Luganda was recognised every time.
- **Speed.** A cold Luganda or Swahili retrieval costs 0.66–0.72 s as deployed
  and 1.0–1.1 s with the reranker, against 0.13–0.16 s for English — one
  Sunflower translation (~0.4 s), two embeddings, two Qdrant queries and, with
  the reranker, three cross-encoder passes. Retrieval alone saturates at
  5.3 queries/s (C1) and 2.1 queries/s (C2).

Against current practice the components are sound — bge-m3 is still a
reasonable embedder for these languages (AfriMTEB retrieval 70.2; the best
open model, AfriE5, 75.4) — but the way they are wired loses most of what they
can do for Luganda and Swahili. §7 sets each component against current
practice, §8 lists twelve gaps (G126–G137), and §9 orders the fixes.

---

## 2. How a question is retrieved today

An English question and a Luganda or Swahili question take different paths.
Both end in `HybridRetriever.search_planned`, the one entry point REST,
streaming, the RAG tool, LangGraph and voice prefetch share.

**English.** One hybrid query: bge-m3 embeds the question, BM25 encodes it,
Qdrant fuses the two top-20 lists with RRF, near-duplicates are dropped, the
cross-encoder scores the survivors (when it is loaded), and `prune_context`
keeps up to `top_k` passages above a 0.20 floor and within 0.45 of the best.
Without reranker scores `prune_context` does nothing, so all `top_k` passages
go to the model.

**Luganda and Swahili.** The corpus is English and only English text is
embedded. The `question_lg` / `question_sw` fields that 23 VAT and
corporation-tax rows carry are stored in the payload but never indexed. So a
non-English question costs:

1. a **first pass on the untranslated text** — BM25 on Luganda or Swahili words
   against English passages, and bge-m3's cross-lingual embedding;
2. when the reranker is loaded, scoring of that pass twice (the raw text and
   its English form; the higher score is kept);
3. the **G18 translate leg**: `english_retrieval_query` rewrites known tax
   phrases with a lexicon (`normalize_luganda_tax_query`,
   `normalize_swahili_tax_query`), translates with Sunflower (prompted, greedy,
   cached per process), and runs a second full hybrid query on the English;
4. a merge of the two passes: deduplicate, sort by reranker relevance (or by
   RRF score when there is none), prune.

That is one translation call, two embeddings, two Qdrant queries and up to
three cross-encoder passes per question, against none, one, one and one for
English. The service around it adds a second translation of a differently
worded string (§6.3).

**After retrieval** (`generate()` and its streaming twin
`generate_retrieval_only`, which the web and WebSocket clients use): keyword
fallback when Qdrant returns nothing; corrective RAG when mean relevance is
under 0.50; a "language boost" for non-English turns; FAQ blending, priority
FAQ rows and the graph leg; the FAQ binding gate on the English form; then
`OutputGuard.should_abstain`, which refuses when the best reranker relevance
is under **0.30 for English and 0.02 for Luganda and Swahili**, or, with no
reranker, when the best IDF-weighted lexical coverage is under 0.50.

---

## 3. Method

### 3.1 Configurations

| Config | What it is | Settings |
|---|---|---|
| **C1 `as_deployed`** | What the GPU stack runs today | `RERANK_ENABLED=false`, `RETRIEVER_DENSE_DEVICE=cpu` — set in the base `App/docker-compose.yml` by #541 (2026-10-06) and not overridden by any GPU overlay |
| **C2 `rerank_gpu`** | What `docs/RAG_ARCHITECTURE.md` describes, and what ran before #541 | reranker on, bge-m3 and reranker on `cuda:0` |
| **C3 `sparse_only`** | What `HybridRetriever` falls back to on a CPU image without sentence-transformers | BM25 only, no embedder, no reranker (emulated by dropping both models after start-up) |

Each configuration ran inside the api container (`docker exec`), so it
inherited the stack's exact environment and flags: `translate_retrieve`,
`corrective_rag`, `query_rewrite`, `query_decomposition` and `agentic_mode` on;
`hyde` and `graph_fusion` off. The runner switched off, for its own process
only, the semantic cache (so no answer came from the live Redis), tickets,
handoff summaries and memory, and wrote to a scratch analytics database.

### 3.2 Query sets

| Set | en | lg | sw | Source | Gold |
|---|---|---|---|---|---|
| `faq` verbatim | 225 | – | – | the indexed question, ≤ 3 rows from each of the 75 FAQ files | the row (and any copy of it) |
| `faq` paraphrase / translation | 154 | 202 | 215 | Sunflower: an everyday-English paraphrase, then its Luganda and Swahili translation | same |
| `faq` code-switched | – | 205 | 210 | Sunflower: "as people in Uganda say it", English tax terms kept | same |
| `faq` typo / ASR-style copies | 110 / 154 | 154 / 202 | 177 / 215 | seeded keyboard typos; lower case, no punctuation, elision apostrophes dropped | same |
| `native` | – | 23 (+38 perturbed) | 23 (+42 perturbed) | the `question_lg` / `question_sw` written into the corpus for 23 rows | the row |
| `coverage` | 105 | 105 | 105 | `Data/eval/coverage_bank.jsonl`, curated taxpayer-voice questions | judged pool |
| `ood` | 32 | 27 | 28 | general knowledge, other countries' tax, other Ugandan agencies, chit-chat | none |

Two caveats decide how to read the `faq` numbers:

- **Circularity.** Its Luganda and Swahili are Sunflower translations, and the
  production pivot translates back with Sunflower. A round trip through one
  model recovers that model's English more easily than a person's Luganda, so
  `faq` flatters the translate leg. `native` and `coverage` were not generated
  for this report; read any Luganda or Swahili `faq` number against them.
- **Light paraphrases.** Sunflower's paraphrases often keep the official
  wording, so `faq/en` paraphrase is close to verbatim and optimistic. The
  curated `coverage/en` questions are the realistic English check.

Every generated query was kept only when `detect_language` and the
receptionist's word lists agreed on its language (148 generations failed and
were dropped). A first build without that check had 270 rows that were
Sunflower's translation of the prompt's own instruction, or a Luganda
"paraphrase" of an English question — see §6.3.

### 3.3 Relevance, metrics and statistics

- **Relevance.** `strict`: the gold point, or any point with the same question
  text (a row repeated across files is not a miss). `lenient`: also any passage
  holding at least 60% of the gold answer's content words, which credits the PDF
  chunk an FAQ row was written from. The coverage set has no gold points; its
  candidates were pooled (top 5 of every system in every configuration) and
  graded 0–3 with UMBRELA's prompt — the open LLM-assessment method TREC's RAG
  track adopted in 2024 and still uses in 2025 — run on the local Sunflower.
  The judge proved lenient (§4.8), so only grade 3 is read as relevant, and a
  judge-free measure is reported beside it: whether the Luganda or Swahili
  question retrieves the same top passage as its English version.
- **Metrics.** Hit@1 (the first passage handed on is relevant), Hit@3/5/10,
  MRR@10, nDCG@10 (BEIR/MTEB's headline metric), the precision of the passages
  actually served, the abstention rate on answerable and out-of-domain
  questions, the AUROC of the score abstention decides on, translation-pivot
  fidelity (content-word F1 against the English the query was made from), cold
  and warm per-stage latency, and a concurrency sweep.
- **Statistics.** 95% bootstrap intervals (1,000 resamples) on every Hit@1 and
  nDCG@10; exact McNemar tests on paired Hit@1 between systems.

### 3.4 What it is measured against

| Area | Reference, as of October 2026 | Used here as |
|---|---|---|
| Accuracy | ISO/IEC 25059:2023 *functional correctness* for AI systems; nDCG@10 / Recall as reported by BEIR, MTEB and MMTEB | Hit@1, nDCG@10, served-context precision |
| Language parity | AfriMTEB (EACL 2026): 59 African languages including Luganda (11 datasets) and Swahili (24) | the en–lg and en–sw gap; embedder choice |
| Robustness | ISO/IEC 25059 *robustness* ("maintain its level of performance under all circumstances"); NIST AI 600-1 *confabulation* risk | perturbation deltas; out-of-domain abstention |
| Speed | ISO/IEC 25010 *time behaviour* and *capacity*; the ~800 ms time-to-first-audio norm for voice agents, with retrieval expected to overlap the model, not precede it | cold/warm p50 and p95 per stage; throughput under concurrency |
| Evaluation practice | TREC RAG 2024–25 (UMBRELA judging, pooled candidates); paired significance tests | coverage-set qrels; McNemar |

---

## 4. Accuracy

All figures are strict Hit@1 unless marked; `/` separates Hit@1 and nDCG@10.
95% intervals for every cell are in the JSON report.

### 4.1 Every leg, as deployed and as designed

`faq` set (paraphrase for English, translation for Luganda/Swahili), and the
23 natively written rows.

| System | en | lg | sw | native lg | native sw |
|---|---|---|---|---|---|
| **C1 as deployed** | | | | | |
| `bm25` (raw) | 0.66 / 0.75 | 0.02 / 0.04 | 0.02 / 0.04 | 0.09 | 0.00 |
| `dense` (raw, bge-m3 cross-lingual) | 0.86 / 0.92 | 0.16 / 0.21 | 0.65 / 0.78 | 0.22 | 0.70 |
| `hybrid` (raw, RRF k=2) | 0.83 / 0.90 | 0.09 / 0.16 | 0.41 / 0.65 | 0.17 | 0.35 |
| `pivot_bm25` | – | 0.37 / 0.51 | 0.56 / 0.66 | 0.43 | 0.57 |
| `pivot_dense` | – | 0.57 / 0.67 | 0.67 / 0.77 | 0.70 | 0.78 |
| `pivot_hybrid` (the G18 leg alone) | – | 0.54 / 0.65 | 0.66 / 0.78 | 0.57 | 0.74 |
| `production` (`search_planned`) | 0.79 / 0.88 | **0.40** / 0.55 | **0.41** / 0.64 | 0.57 | 0.48 |
| **C2 reranker on** | | | | | |
| `hybrid_rerank` (raw) | 0.88 / 0.92 | 0.11 / 0.15 | **0.20** / 0.34 | 0.22 | 0.35 |
| `pivot_hybrid_rerank` | – | 0.63 / 0.71 | 0.75 / 0.83 | 0.87 | 0.96 |
| `production` | 0.88 / 0.92 | 0.61 / 0.69 | 0.78 / 0.85 | 0.74 | 0.96 |
| **C3 BM25 only** `production` | 0.63 / 0.72 | 0.02 / 0.31 | 0.05 / 0.41 | 0.09 | 0.17 |

English verbatim questions score 0.95–0.96 in every configuration (BM25 wins
them on its own). Sunflower's code-switched Luganda and Swahili score the same
as its plain translations (C2: 0.61 and 0.79); real code-switching is heavier
and trips language detection instead (§6.4).

### 4.2 The language gap

With the reranker on (C2), Luganda trails English by **27 points** and
Swahili by **10**. As deployed the gaps are 39 and 38. The natively written
rows tell the same story with wider intervals (n = 23: Luganda 0.74, Swahili
0.96 in C2). These rows are close translations of the indexed question itself,
which is easier than a paraphrase — read them as an upper bound.

### 4.3 Without a reranker, the untranslated pass wins

`search_planned` merges the untranslated pass and the translated (G18) pass and
sorts by reranker relevance. With no reranker it sorts by RRF score — and RRF
scores from two different queries sit on the same scale, so the untranslated
pass's top hits, which are almost never relevant for Luganda, compete as
equals:

| Served passages | C1 lg | C1 sw | C2 lg | C2 sw | C3 lg | C3 sw |
|---|---|---|---|---|---|---|
| found only by the untranslated pass | 53% | 39% | 11% | 7% | 52% | 52% |
| top passage found only by it | 39% | 32% | 5% | 2% | **96%** | **93%** |
| …of which relevant | 0% | 0.2% | 0% | 0.9% | 0.5% | 0.2% |
| translate leg right, production wrong | 17% | 30% | 7% | 4% | 35% | 50% |

In C1 production is 15 (Luganda, p = 10⁻⁶) and 25 (Swahili, p < 10⁻⁶) points
below the translate leg alone. In C3 a single leg means equal RRF scores at
each rank, and the stable sort keeps the untranslated pass first, so the top
passage is almost always noise. `prune_context` does nothing without reranker
scores, so all six passages, roughly half of them from the untranslated pass,
reach the model.

With the reranker on, the untranslated pass is nearly harmless and sometimes
useful: production beats the translate leg alone for Swahili (0.78 vs 0.75)
and trails it for Luganda (0.61 vs 0.63); neither difference is significant
(p = 0.15, 0.13). That matches §4.6: bge-m3 understands Swahili directly, not
Luganda.

### 4.4 Fusion: Qdrant's default k = 2

The code asks Qdrant for RRF without a `k`, so Qdrant's default of 2 applies
(`RAG_ARCHITECTURE.md` said 60; corrected in this change). On the translated
query:

| First-stage fusion | en | lg | sw |
|---|---|---|---|
| RRF k = 2 (current) | 0.83 | 0.54 | 0.66 |
| RRF k = 60 (client-side) | 0.81 | 0.58 | 0.66 |
| DBSF (Qdrant) | 0.82 | **0.60** (p = 0.012) | **0.70** (p = 0.096) |
| dense alone | 0.86 | 0.57 | 0.67 |

DBSF is the better first stage for Luganda and Swahili and costs English
nothing. Note that dense alone beats both RRF variants: at k = 2 the weaker
BM25 leg pulls fused rankings down. With the reranker on top the choice
matters less, but the reranker can only reorder the 20 candidates fusion hands
it.

### 4.5 The cross-encoder helps on English text and hurts on Swahili

`mxbai-rerank-base-v2` is trained for English and Chinese (its card lists 109
languages and names neither Swahili nor Luganda). On the **translated** query it
adds 5 (English, p = 0.10), 9 (Luganda, p = 0.005) and 10 (Swahili, p = 0.001)
points. On the **raw Swahili** query it takes Hit@1 from 0.34 to 0.20
(p < 0.001). The production code already scores raw and translated text and
keeps the higher, which is why C2 works; it pays for that with three
cross-encoder passes per question (§5).

### 4.6 Candidate embedders, measured locally

The same 8,578 passages, each model in float16 at 512 tokens, cosine
similarity in memory (`raw` = the question as asked; `pivot` = production's
English translation of it). bge-m3 here matches the production index
(`pivot_dense` lg 0.57 in both), so the comparison is fair.

| Model | lg raw | sw raw | native lg raw | native sw raw | lg pivot | sw pivot | native lg pivot | en |
|---|---|---|---|---|---|---|---|---|
| `BAAI/bge-m3` (current) | 0.16 | 0.66 | 0.22 | 0.78 | 0.57 | 0.70 | 0.65 | 0.86 |
| `McGill-NLP/AfriE5-Large-instruct` | **0.26** | 0.65 | **0.48** | 0.74 | 0.58 | 0.68 | 0.74 | 0.87 |
| `intfloat/multilingual-e5-large-instruct` | 0.02 | 0.19 | 0.00 | 0.04 | 0.54 | 0.67 | 0.74 | 0.85 |

- **Luganda still needs the translation.** The best African-adapted model
  doubles raw Luganda retrieval on human-written questions (0.22 → 0.48) but
  stays far below translate-then-search (0.74).
- **Swahili needs it much less.** bge-m3 retrieves raw Swahili within 4 points
  of its translation on the `faq` set (0.66 vs 0.70) and within 9 on the
  natively written questions (0.78 vs 0.87), so for Swahili the translation
  could run beside the first pass instead of before the second, or feed only
  the BM25 leg.
- The AfriMTEB ranking (mE5-instruct above bge-m3 on African retrieval) does
  **not** carry over to this cross-lingual task: mE5-instruct cannot match a
  Luganda or Swahili question to an English passage at all. AfriE5's
  cross-lingual training is what makes the difference.

### 4.7 The full service path

`generate_retrieval_only` — what the web client actually gets — was run with no
locale hint, so it also detects the language. Its top passage is measured on
the same questions as `search_planned`, both over every question (routes
without passages, such as the calculator, count as misses) and over the
questions where the service handed passages on:

| C2 reranker on | en | lg | sw | native lg | native sw |
|---|---|---|---|---|---|
| `search_planned` Hit@1 | 0.85 | 0.64 | 0.75 | 0.74 | 0.96 |
| service Hit@1, all questions | 0.69 | 0.42 | 0.50 | 0.35 | 0.43 |
| service Hit@1, where it handed passages on | 0.76 | 0.52 | 0.59 | 0.44 | 0.63 |
| …`search_planned` on those same questions | 0.85 | 0.65 | 0.78 | 0.78 | 0.94 |
| top passage changed: lost / gained | 8 / 1 | 40 / 14 | 51 / 9 | 6 / 0 | 5 / 0 |
| language misdetected | 0 | 0 | 0 | **8 of 23** | 3 of 23 |

The first two rows are the translated (or paraphrased) questions that ran end
to end; the conditional rows add the code-switched ones. Every service-level
difference against `search_planned` is significant (p ≤ 0.004). Three things
change the order after retrieval (§6.5): the language boost, the FAQ rows and
priority rows the service blends in ahead of the reranked passages, and the
binding gate. The reshuffling happens mostly among FAQ rows — 90% of
`search_planned`'s Swahili top passages are already FAQ rows, 93% of the
service's — so the service is not preferring a better kind of passage, it is
picking a different, usually wrong, one (51 lost, 9 gained).

### 4.8 The coverage bank (curated, judged)

The 105 coverage-bank questions exist in all three languages and were not
written for this report, so they are the independent check on everything
above. They have no gold points, so two measures are used.

**Cross-lingual agreement** needs no judge: does the Luganda or Swahili version
of a question bring back the same top passage as its English version?

| Same top passage as English (top-5 overlap) | lg | sw |
|---|---|---|
| C1 `search_planned` | 0.36 (0.31) | 0.30 (0.38) |
| C1 service | 0.15 (0.22) | 0.17 (0.24) |
| C2 `search_planned` | 0.55 (0.56) | 0.67 (0.62) |
| C2 service | 0.25 (0.36) | 0.34 (0.39) |
| C3 `search_planned` | 0.06 (0.23) | 0.06 (0.23) |
| bge-m3 on the raw question | 0.17 (0.29) | 0.51 (0.50) |
| AfriE5 on the raw question | 0.29 (0.34) | 0.30 (0.41) |

Even with the reranker on, a Luganda question finds the same evidence as its
English twin about half the time, and through the service a quarter of the
time.

**Judged relevance.** 5,421 pooled passages (top 5 of every system in every
configuration) were graded by Sunflower with UMBRELA's prompt. The judge is
lenient: it graded every gold FAQ passage ≥ 2 (60/60) but also 23% of
deliberately unrelated passages ≥ 2, and it put 49% of the pool at the top
grade. So only the strict reading (grade 3) is reported, and absolute values
are optimistic:

| Top passage graded 3 | en | lg | sw |
|---|---|---|---|
| C1 `search_planned` | 0.97 | 0.70 | 0.75 |
| C2 `search_planned` | 0.95 | 0.83 | 0.91 |
| C3 `search_planned` | 0.90 | 0.37 | 0.51 |
| C1 service | 0.66 | 0.40 | 0.49 |
| C2 service | 0.70 | 0.56 | 0.61 |

The ordering matches the `faq` set — C2 above C1 above C3, English first,
Luganda last, the service below `search_planned` — so the machine-written
questions did not manufacture the findings, even if their absolute levels
differ.

---

## 5. Speed and performance

Measured with nothing else running on the stack: 40 questions per language,
every cache (translation, query embedding) cleared before each, then the same
question again warm. "Calls" is the mean number of times the stage ran.

### 5.1 One retrieval, cold

| `search_planned`, ms | total p50 | total p95 | translation | embedding (calls) | Qdrant (calls) | cross-encoder (calls) | warm p50 |
|---|---|---|---|---|---|---|---|
| **C1** en | 125 | 165 | – | 112 (1) | 9 (1) | – | 12 |
| **C1** lg | 658 | 958 | 377 | 220 (2) | 20 (2) | – | 54 |
| **C1** sw | 718 | 999 | 425 | 212 (2) | 20 (2) | – | 82 |
| **C2** en | 158 | 235 | – | 17 (1) | 16 (1) | 117 (1) | 136 |
| **C2** lg | 1,002 | 1,265 | 386 | 38 (2) | 34 (2) | 486 (3) | 561 |
| **C2** sw | 1,104 | 1,380 | 429 | 39 (2) | 31 (2) | 552 (3) | 638 |

- The Luganda/Swahili premium is the translation call plus the second pass.
  Each translation is a full generation on Sunflower-14B (~0.4 s), which reads
  prompts at only about 3,000 tokens/s on an RTX A6000 (Ampere has no FP8
  tensor cores) and then writes the English token by token.
- bge-m3 on CPU costs 112 ms per embedding against 17 ms on the GPU.
- The cross-encoder costs 117 ms for 20 passages and runs three times for every
  Luganda or Swahili question (the raw text, its translation, and the
  translated pass). Its results are not cached, so even a repeated question
  pays it (warm 0.56–0.64 s).
- **float16 is not a lever here.** Loading both models in float16 on the same
  card changed nothing (embedding 16.6 → 17.3 ms; cross-encoder 116 → 117 ms
  per 20 pairs; identical top passage in 40/40).

### 5.2 Under load

Mixed en/lg/sw questions, all distinct, `search_planned` only:

| Workers | C1 qps | C1 p50 / p95 ms | C2 qps | C2 p50 / p95 ms |
|---|---|---|---|---|
| 1 | 1.98 | 585 / 973 | 1.34 | 899 / 1,306 |
| 4 | 4.55 | 965 / 1,386 | 2.16 | 2,192 / 2,808 |
| 8 | 5.31 | 1,555 / 2,168 | 2.13 | 4,485 / 5,351 |
| 16 | 5.01 | 3,105 / 4,209 | 2.13 | 9,169 / 10,224 |

Retrieval alone saturates at about 5 questions/s as deployed and about 2 with
the reranker — before any generation. The C2 ceiling is the cross-encoder:
three serial passes per non-English question on one GPU, with no batching
across requests.

### 5.3 End to end, through the service

`generate_retrieval_only` — routers, the English form, retrieval, FAQ blending,
gates; no streamed generation — one question at a time with every cache
cleared (20 per language), p50 / p95:

| | en | lg | sw |
|---|---|---|---|
| C1 | 175 / 340 ms | 1,014 / 7,113 ms | 846 / 1,509 ms |
| C2 | 213 / 352 ms | 1,370 / 7,521 ms | 1,231 / 1,865 ms |

The p95 tail is not retrieval. The slowest Luganda turns (6.5–14.4 s) made one
translation call and no retrieval at all: they took the education or calculator
route, which does its own model work inside this call. In the full benchmark
run (four questions in flight), 66 of 73 Luganda/Swahili turns routed to
education and 36 of 50 routed to the calculator took more than 3 s. The
translation function is invoked 2.3–3.5 times per Luganda/Swahili turn; most
of those are cache hits, but not all (§6.3).

### 5.4 Against the budgets

For voice, the industry norm is about 800 ms from the end of the caller's
sentence to first audio, with retrieval expected to overlap the model rather
than precede it. A Luganda or Swahili question spends 0.85–1.4 s (p50) in the
service before generation starts, against 0.18–0.21 s for English. For text
chat, the capacity runbook treats 3 s p95 for a whole hybrid answer as NFR-01;
the Luganda retrieval half alone is past 7 s at p95 because of the education
and calculator routes.

---

## 6. Robustness

### 6.1 Typos and transcript-style input

Change in `search_planned` Hit@1 when the same question carries seeded
keyboard typos, or comes in transcript style (lower case, no punctuation,
elision apostrophes dropped: `ow'omusolo` → `owomusolo`). Paired: each
perturbed question is compared with its own clean question. C2's perturbed
copies are a stable 30% sample; cells under 11 questions are not reported.

| | C1 typo | C1 ASR-style | C2 typo | C2 ASR-style |
|---|---|---|---|---|
| en | −0.03 (n = 110) | +0.01 (154) | −0.02 (46) | −0.03 (69) |
| lg | +0.01 (154) | +0.01 (202) | −0.02 (65) | +0.01 (76) |
| sw | −0.07 (177) | −0.07 (215) | −0.09 (69) | −0.03 (94) |
| lg, written in the corpus | −0.20 (15) | −0.17 (23) | – | – |
| sw, written in the corpus | −0.11 (19) | −0.09 (23) | – | – |

The translation absorbs most noise in generated Luganda (±2 points). Swahili
loses 7–9 points to typos, and the natively written questions lose the most
(9–20 points), though on small samples — one question in 23 moves Hit@1 by 4
points.

### 6.2 Questions the corpus should not answer

**The reranker's relevance is passed through a sigmoid twice.** The production
`CrossEncoder` for `mxbai-rerank-base-v2` already applies `Sigmoid()`
(`activation_fn`, `num_labels = 1`), so `predict()` returns a probability;
`normalize_rerank_score` then applies a second sigmoid. Every relevance the
system reasons with therefore lies between 0.50 and 0.731:

| Question → top passage | `predict()` | logit | `score_norm` used by the gates |
|---|---|---|---|
| "What is the VAT rate in Uganda?" → the VAT-rate passage | 1.000 | 10.9 | 0.731 |
| same question → a passenger-baggage passage | 0.363 | −0.56 | 0.590 |
| "What is the capital city of France?" → the VAT-rate passage | 0.000 | −10.4 | **0.500** |

94% of the 90,605 passages scored in C2 fall exactly in [0.50, 0.731]; the rest
are the code's own adjustments (−0.15 for superseded years, +0.1 for a
preferred tax type). Every gate built on this number is inert whenever the
reranker runs: abstention (0.30 for English, 0.02 for Luganda/Swahili),
context pruning (0.20 floor, 0.45 drop), and corrective RAG (mean below 0.50)
can never fire. Corrective RAG triggered on **0 of 508** questions in both
configurations — without a reranker its trigger has no score at all.

**What that does to out-of-domain questions.** Abstention as each
configuration actually behaves, on 32 English, 27 Luganda and 28 Swahili
out-of-domain questions:

| | en | lg | sw |
|---|---|---|---|
| C1 retrieval gate passes (lexical floor) | 34% | 63% | 50% |
| C1 service hands passages on | 22% | 37% | 29% |
| C2 retrieval gate passes (double sigmoid) | **100%** | **100%** | **100%** |
| C2 service hands passages on | **72%** | **74%** | **71%** |
| C1 abstention signal AUROC, answerable vs OOD | 0.95 | 0.73 | 0.70 |
| C2 reranker AUROC, answerable vs OOD | 0.95 | 0.92 | 0.96 |

Questions about other countries' tax are caught by the jurisdiction guard
whatever the retrieval does (22 of 23 deflected in C1). The rest are not. As
deployed, questions about *other Ugandan agencies* (a passport, a birth
certificate, an exam re-mark) reach the answer step with passages 6 of 8 times
in Luganda and 5 of 8 in Swahili, against 3 of 8 in English.

**A fixed scale is necessary but not sufficient.** Undoing the second sigmoid
in the C2 data and sweeping the threshold:

| Threshold on the reranker's own probability | OOD answered en / lg / sw | Answerable abstained en / lg / sw |
|---|---|---|
| 0.30 (today's English value) | 94% / 100% / 100% | 0% / 0% / 0% |
| 0.90 | 50% / 70% / 71% | 0% / 0% / 0% |
| 0.99 | 31% / 44% / 39% | 0% / 0.8% / 1.1% |
| 0.995 | 16% / 37% / 29% | 0% / 1.1% / 1.4% |

The reranker separates answerable from out-of-domain questions well (AUROC
0.92–0.96), but it is confidently wrong about many off-topic ones, and the
reason is in the corpus as much as the model: for 8 of the 10 general-knowledge
questions probed, the top passage was a fragment of a PDF table. "Will it rain in Kampala
tomorrow?" gets a table of URA office hours at probability 0.981; "Who won the
2022 football World Cup?" a list of sports-betting licensees at 0.887; "What is
photosynthesis?" a tax-incentive table that mentions hoes at 0.867. Abstention
needs a calibrated threshold per language **and** a second signal (an
answerability or entailment check, which the repository already has in
`entailment.py`), and the index needs fewer table fragments.

### 6.3 The translation pivot

- **Fidelity.** The English the retriever searches with shares 45% (Luganda)
  and 53% (Swahili) of its content words with the English the question was made
  from. Meaning mostly survives — dense retrieval on the pivot is close to
  English — but wording does not, which is why BM25 on the pivot (0.37, 0.56)
  trails dense on the pivot (0.57, 0.67).
- **Failure modes, in 1,971 production translations.** No repetition loops and
  no translated instructions — the labelled prompt (`Luganda: … / English:`) and
  its one-shot example hold. But: one translation carried the prompt's own
  constraint into the query ("…(URA) in Uganda — do NOT write Tanzania or .tz;
  use Uganda and .ug."); an acronym was expanded into a different system ("ERM"
  → "Electronic Cargo Tracking System"); and short questions were flattened
  ("EFRIS kye ki?" → "EFRIS electronic fiscal receipting system?") or turned
  into statements.
- **Why the prompt shape matters.** The first build of this benchmark asked
  Sunflower to translate with the instruction and the text unlabelled: 270
  rows came back as a translation of the instruction itself, or as Luganda
  where English was asked for. Production's labelled shape avoided that; it is
  load-bearing and should be kept.
- **Two translations per turn, and they disagree.** The service translates the
  raw message for its routers, while the retriever translates the *rewritten*
  query, and the rewriter expands acronyms ("VAT" → "Value Added Tax (VAT)"). On
  six questions containing an acronym, every one sent two different strings to
  the translator and got two different English questions back:

  | Asked | Router form | Retrieval form |
  |---|---|---|
  | Nsobola ntya okufuna TIN? | How can I register for a Tax Identification Number? | How can I get a Taxpayer Identification Number (TIN)? |
  | Kiwango cha VAT nchini Uganda ni kiasi gani? | What is the VAT rate in Uganda? | What is the Value Added Tax (VAT) rate in Uganda? |
  | PAYE ni nini na nani anaikata? | *PAYE is a tax deducted from employees' salaries by the Uganda Revenue Authority (URA).* | Pay As You Earn (PAYE) is what and who cuts it? |

  The last row shows two more defects at once: the router's translation
  *answered* the question instead of translating it, and the rewriter's English
  spelling corrector changed the Swahili "nini" to "nin" before the retriever's
  translation saw it.

### 6.4 Recognising the language

The service decides the answer language from the text. On Sunflower-written
Luganda and Swahili it was right every time (832 of 832). On questions people
wrote, it was not:

| | Taken for English |
|---|---|
| Coverage bank, Luganda | 23 of 105 |
| Coverage bank, Swahili | 8 of 105 |
| Corpus-written Luganda | 8 of 23 |
| Corpus-written Swahili | 3 of 23 |

The misses are short questions carrying English tax terms, which is how
Ugandans write: "Nnina kwewandiisa ddi ku VAT?", "Withholding tax kye ki?",
"VAT ni nini?". Such a question is answered in English and searched without
its translation leg. The generated "code-switched" questions were always
recognised, so generated code-switching is milder than the real thing; the
coverage bank is the better test.

### 6.5 After retrieval: the language boost and FAQ blending

For any non-English turn, both chat paths add 0.3 to the RRF score of every
passage whose source or first 200 characters contain `luganda`, `oluganda` or
`lg` (Luganda) or `swahili`, `kiswahili` or `sw` (Swahili), then **re-sort the
whole list by RRF score**. Two defects, measured in isolation on the same
questions:

- **Substring match.** `sw` occurs inside "Answer", which every FAQ passage
  begins with; `lg` occurs almost nowhere. So for Swahili the boost lifts 3.0
  of 6 passages per question on average, for Luganda none.
- **Re-sorting by RRF discards the reranker's order.** With the reranker on,
  the block moves the top passage for 44% of Luganda and 28% of Swahili
  questions and costs **11** and **6** points of Hit@1. Without the reranker it
  happens to *help* Swahili (+18 points) by lifting FAQ rows above PDF chunks — an
  accidental FAQ-first rule for one language.

### 6.6 Configuration drift nobody can see

- #541 (2026-10-06, a security-and-benchmark PR) set `RERANK_ENABLED=false` and
  `RETRIEVER_DENSE_DEVICE=cpu` in the base compose file; no GPU overlay restores
  them. `/ready` still reports `retrieval: hybrid`. The registry's `reranker` flag
  shows "on" and nothing reads it.
- `/ready` reports `translation: unavailable` on the GPU stack, because it only
  probes the Sunbird cloud, while retrieval translates through the local
  Sunflower successfully.
- Nothing measures, per language, how often the pivot fails, how much of the
  served context came from the untranslated pass, or how often the service
  abstains — the three numbers this report had to build a harness to see.
- `Dockerfile` and `Dockerfile.gpu` do not copy `ml/`, so the language detector
  falls back to a character heuristic unless `ml/` is bind-mounted (it is on
  the local stack; the HF Space reports `lingua`). In that image, "Ani alina
  okwewandiisa ku VAT?" was detected as English.
- Three report generators (`scripts/evaluate_800_faqs_multilingual.py`,
  `scripts/evaluate_2000_faqs_ngrok.py` and
  `scripts/benchmark_conversational_emotional_live.py`) print "**MET** ✅" for
  every target whatever was measured; the 2,000-FAQ report of 2026-09-28 marks
  a 53 s p95 "MET" against a 3 s target.

---

## 7. Against current practice (October 2026)

| Practice | This stack | Measured here | Verdict |
|---|---|---|---|
| **Embedder chosen on African-language results.** AfriMTEB (EACL 2026) retrieval: AfriE5-large-instruct 75.4, mE5-large-instruct 74.1, bge-m3 70.2, Qwen3-Embedding-8B 69.9; EmbeddingGemma 12.2 on the Lite suite. Global MMTEB leaders do not lead here. | bge-m3 | AfriE5 doubles raw corpus-written Luganda (0.22 → 0.48); mE5-instruct fails cross-lingually (0.00) | Sound choice; AfriE5 worth a trial for native-language fields (G134) |
| **Language-aware lexical retrieval.** Qdrant ≥ 1.15 does BM25 server-side with a multilingual tokenizer, stemming and stopwords; bge-m3 also emits learned sparse weights, which beat BM25 on MIRACL. | Client-side BM25, `[a-z0-9]+` tokens, English IDF | BM25 on raw Luganda/Swahili: 0.02; on the translation: 0.37 / 0.56 | BM25 adds nothing for raw lg/sw and drags RRF down (G127); it only earns its place on English text |
| **Tuned hybrid fusion.** RRF with k ≈ 60 or score-based fusion (DBSF); Qdrant exposes both. | Qdrant RRF, default k = 2 | DBSF +6 points Luganda (p = 0.012) | Gap (G127) |
| **Multilingual or LLM rerankers for low-resource languages.** jina-reranker-v3 (93 languages), Qwen3-Reranker, bge-reranker-v2-m3; LLM rerankers competitive on African languages (ACL 2024). | mxbai-rerank-base-v2 (English/Chinese-tuned), raw text and translation dual-scored | Raw Swahili: 0.34 → 0.20 with it; translation: +9 to +10 | Works only through the translation; a multilingual reranker would let one pass replace three (G133) |
| **Translate-test vs native retrieval for African languages.** CIRAL's baselines set BM25 over query and over document translations against multilingual dense retrievers; for LLM reranking, translating the documents into English worked best (ACL 2024). | Query translation (Sunflower) at query time, untranslated pass merged in | Translation is needed for Luganda, optional for Swahili dense | Right idea, wrong merge (G128); no document-side translation (G134) |
| **Context-preserving chunks.** Contextual retrieval (a generated header per chunk) cut failed retrievals by 49%, 67% with reranking. | 6,966 PDF chunks (81% of the index), including bare table fragments | Table fragments are the top passage for 8 of 10 off-topic probes | Gap (G130) |
| **Calibrated abstention.** NIST AI 600-1 names confabulation a core generative-AI risk; ISO/IEC 25059 asks for robustness "under all circumstances". | Thresholds on a double-sigmoided score; 0.02 for lg/sw | 100% of out-of-domain questions pass with the reranker on | Gap (G129, G130) |
| **Late interaction.** ColBERT-style multi-vector retrieval (Jina-ColBERT-v2, 94 languages; bge-m3's own multi-vector head). | Not used | – | Not measured; not a priority before G128–G131 |
| **LLM-judged evaluation with human checks.** UMBRELA in TREC RAG 2024–25, pooled candidates, rankings that track human ones. | No Luganda/Swahili retrieval evaluation; generators that print "MET" | Sunflower as a judge is lenient (23% false positives) | Gap (G136); a stronger judge or native-speaker spot checks are needed |
| **Storage efficiency.** Qdrant 1.19 TurboQuant and memory tiers; payload indexes for filtered fields. | No quantization; no payload indexes (filters on `fiscal_year`, `tag`, `doc_type` scan) | Qdrant ≈ 9–34 ms per query at 8.6k points | Not a gap at this size; add payload indexes before the corpus or tenants grow |

---

## 8. Gaps

Registered in `docs/GAPS_AND_AGENTIC_ROADMAP.md` with the same numbers. 🔴 =
wrong answers or unsafe behaviour at scale; 🟡 = measurable loss.

| ID | Area | Gap | Evidence | Fix |
|---|---|---|---|---|
| **G126** 🔴 | Accuracy, operations | The GPU stack has run without the reranker, and with bge-m3 on CPU, since #541 (2026-10-06); `/ready` and the flag console cannot show it | C2 → C1: lg 0.61 → 0.40, sw 0.78 → 0.41, en 0.88 → 0.79 (§1) | Restore `RERANK_ENABLED=true` and `RETRIEVER_DENSE_DEVICE=cuda:0` in the GPU overlay **after G129**; make the `reranker` flag real or delete it |
| **G127** 🟡 | Accuracy | First-stage fusion is Qdrant RRF at its default k = 2 (docs said 60); BM25 drags fused rankings below dense alone | DBSF +6 lg (p = 0.012), +4 sw (p = 0.10), ±0 en (§4.4) | Fuse with DBSF, or pass `Rrf(k=…)`, behind a setting; re-measure under the reranker |
| **G128** 🔴 | Accuracy | Without a reranker the untranslated pass displaces the translated one: production scores below its own translate leg, and the BM25-only fallback collapses | C1 −15 lg / −25 sw (p < 10⁻⁵); C3 0.02 / 0.05; untranslated top passage relevant in 0 of 157 C1 Luganda cases (§4.3) | Luganda: search the translation only. Swahili: keep the raw *dense* leg. With no reranker, rank merged lists by the translated pass, never by cross-query RRF ties |
| **G129** 🔴 | Robustness, safety | Reranker scores are sigmoided twice (0.50–0.731), so abstention, pruning and corrective RAG never fire when the reranker runs; the lg/sw threshold 0.02 has no calibration behind it | "capital of France" → 0.500; 100% of OOD questions pass the gate, 71–74% reach the answer step in C2; corrective RAG 0/508 (§6.2) | Score with `activation_fn=Identity` (or stop re-squashing); recalibrate per language on the OOD and answerable sets; test against real reranker output, not mocked logits |
| **G130** 🟡 | Robustness | Out-of-domain questions get answers more often in Luganda and Swahili than in English, even as deployed; the reranker rates PDF table fragments 0.87–0.98 for off-topic questions | C1: 37% / 29% vs 22% reach the answer step; other agencies 6/8 and 5/8 vs 3/8; AUROC 0.73 / 0.70 vs 0.95 (§6.2) | Per-language thresholds once G129 is fixed; an answerability or entailment check as a second signal; clean or drop table-fragment chunks; gate on the OOD set in all three languages |
| **G131** 🟡 | Accuracy | Steps after retrieval override its ranking: the language boost matches `sw` inside "Answer" and then re-sorts by RRF score, discarding the reranker's order; FAQ blending and priority rows go ahead of reranked passages | Service top passage vs `search_planned` (C2): en 0.85 → 0.76, lg 0.65 → 0.52, sw 0.78 → 0.59 (p ≤ 0.004); boost alone −11 lg, −6 sw (§4.7, §6.5) | Delete the substring boost (use a payload language field if a preference is wanted); never re-sort by `score_rrf` when reranker scores exist; measure blending and priority rows on this benchmark before keeping them ahead |
| **G132** 🟡 | Robustness | Human-written Luganda and Swahili with English tax terms is taken for English, so it is answered in English and searched untranslated | Luganda: 23/105 coverage, 8/23 corpus-written; Swahili: 8/105, 3/23; Sunflower-written: 0/832 (§6.4) | Treat tax acronyms as language-neutral; when the word lists find Luganda or Swahili words, run the translate leg regardless; add the coverage bank to the language-ID eval |
| **G133** 🟡 | Speed | A Luganda or Swahili retrieval costs 5–7× an English one: translation sits on the critical path, the cross-encoder runs three times and is never cached, and retrieval alone saturates at 5.3 / 2.1 queries/s | Cold 0.66–0.72 s (C1), 1.0–1.1 s (C2) vs 0.13–0.16 s; through the service p50 0.85–1.4 s vs 0.18–0.21 s, with a 7 s Luganda p95 from the education and calculator routes (§5) | Translate while the first pass runs (or drop it for Luganda, G128); one cross-encoder pass over both candidate sets with the English query; cache rerank scores per index build; batch across requests; bge-m3 back on GPU (112 → 17 ms). float16 gains nothing |
| **G134** 🟡 | Accuracy | Only English is indexed: the corpus's own Luganda/Swahili questions (23 rows) are stored but never searched, and every Luganda question depends on query-time translation | Best open African embedder retrieves raw Luganda at 0.26 (corpus-written 0.48) vs 0.58 / 0.74 via translation; raw Swahili already 0.66 vs 0.70 (§4.6) | Index reviewed Luganda/Swahili variants of FAQ questions; evaluate AfriE5 for that field; let Swahili skip or parallelise translation |
| **G135** 🟡 | Robustness | A turn is translated twice (router form, acronym-expanded retrieval form) and the two disagree — the router's can be an answer, not a translation; the English spelling corrector rewrites Swahili before translation; rarer leaks of prompt text and invented acronym expansions | 6/6 acronym questions translated twice into different English; content-word F1 0.45 lg / 0.53 sw; 1 constraint leak in 1,971 (§6.3) | Translate once per turn, before rewriting, and hand that English to both routers and retriever; skip the English spelling corrector for lg/sw; reject translations containing prompt text or that read as answers |
| **G136** 🟡 | Evaluation | Nothing measured retrieval for Luganda and Swahili; the CI ranking gate is English-only; three report generators print "MET ✅" whatever was measured | 53 s p95 reported "MET" against 3 s (§6.6) | Compute pass/fail from the thresholds; run this benchmark (native, coverage, OOD) on the GPU stack for every retrieval change |
| **G137** 🟡 | Observability | `/ready` says `hybrid` with the reranker off and `translation: unavailable` while local translation works; no per-language metric for pivot failures, untranslated-pass share or abstention; `ml/` is missing from the images | §6.6 | Report reranker state, dense device and the configured MT backend's reachability; bounded-label metrics by locale; ship the language detector inside `app/` or copy `ml/` |

---

## 9. What to do, in order

The order matters: the largest accuracy gain (G126) makes the safety defect
(G129) worse, so it has to come second.

1. **Fix the double sigmoid and recalibrate abstention per language (G129,
   G130).** Score with the reranker's raw output, set one threshold per
   language from this benchmark's answerable and out-of-domain questions, and
   add the existing entailment check as a second signal for what the threshold
   cannot separate. Measured headroom on the corrected scale: out-of-domain
   acceptance falls from 100% to 16% (en), 37% (lg) and 29% (sw) at a cost of at
   most 1.4% of answerable questions.
2. **Then restore the reranker and GPU embeddings on the GPU stack (G126).**
   +21 (Luganda), +37 (Swahili) and +9 (English) points of Hit@1; cold latency
   +0.34–0.39 s for Luganda/Swahili and +33 ms for English, until step 6.
3. **Remove the language boost's substring match and its RRF re-sort (G131).**
   +11 (Luganda) and +6 (Swahili) points with the reranker on, measured on the
   isolated block. Then measure FAQ blending and priority rows the same way.
4. **Stop the untranslated pass from outranking the translation (G128).**
   Translation-only retrieval for Luganda; for Swahili keep the raw dense leg.
   This is what keeps the BM25-only fallback usable: there the translate leg
   alone scores 0.37 (lg) and 0.56 (sw) against 0.02 and 0.05 today.
5. **Recognise code-switched Luganda and Swahili (G132)**, so a quarter to a
   third of real Luganda questions are not answered in English.
6. **Take translation and reranking off the critical path (G133, G135).**
   Translate once per turn and in parallel with the first pass; one
   cross-encoder pass; cache rerank scores. Target: a cold Luganda/Swahili
   retrieval at or under 0.6 s with the reranker on, and the 5.3 / 2.1
   queries-per-second ceilings lifted accordingly.
7. **Fuse with DBSF (G127)** — +6 points for Luganda at the first stage, nothing
   lost in English; re-measure under the reranker before keeping it.
8. **Index Luganda and Swahili (G134).** Reviewed question variants for the FAQ
   rows first; evaluate AfriE5 as the encoder for that field.
9. **Make it visible and keep it measured (G136, G137).** `/ready` and metrics for
   the reranker, translation and abstention per language; this benchmark on the
   GPU stack for every retrieval change; report generators that can say "not
   met".

---

## 10. Reproducing this, and what it does not show

`evals/retrieval_multilingual/README.md` has the commands: build the set,
copy the runner into the api container, one `run` per configuration, then
`latency`, `mechanisms`, `embedders`, `judge` and `report`. The raw per-query
records stay in the container's scratch directory; the summary is
`evals/reports/retrieval_multilingual_2026-10-07.json`, and the judged
coverage pool is `evals/retrieval_multilingual/qrels_coverage.jsonl`.

What it does not show:

- **Machine-written Luganda and Swahili.** The `faq` set's non-English
  questions are Sunflower translations, which flatters a Sunflower translation
  pivot (§3.2). The natively written set has 23 questions per language; its
  intervals are wide.
- **LLM relevance judgments.** The coverage bank is judged by Sunflower with
  UMBRELA's prompt over a depth-5 pool, not by people; ranks 6–10 count as
  unjudged. The judge passed all 60 gold passages but also graded 14 of 60
  unrelated ones relevant, so judged numbers are an upper bound; the
  cross-lingual agreement measure in §4.8 does not depend on it. A native
  speaker grading even 200 of those passages would settle it.
- **No answers.** It measures what reaches the answer step, not the replies, so
  a wrong passage the model ignores, or a right one it misreads, is not
  visible here.
- **One index, one card.** One index build (`fc80e983f384`), RTX A6000s, an
  otherwise idle stack. The HF Space's Cloudflare Vectorize path was not
  measured; C3 emulates only the BM25 fallback underneath it.
- **Two languages.** Runyankole and Acholi, which the code also routes, were not
  measured.
- **Embedders in memory.** §4.6 compares models outside the production path; a
  swap would still need an index rebuild and a run of this benchmark.
- **Service sampling.** The C2 service path ran on a stable 50% of `faq`
  questions and every native, coverage and out-of-domain question.

---

## Sources

- Uemura, Zhang, Adelani. *AfriMTEB and AfriE5: Benchmarking and Adapting Text Embedding Models for African Languages*. EACL 2026. [arXiv:2510.23896](https://arxiv.org/abs/2510.23896), [ACL Anthology](https://aclanthology.org/2026.eacl-long.171) — Table 2 (Full) and Table 3 (Lite) retrieval columns quoted in §4.
- Enevoldsen et al. *MMTEB: Massive Multilingual Text Embedding Benchmark*. ICLR 2025. [arXiv:2502.13595](https://arxiv.org/pdf/2502.13595v4)
- Zhang et al. *Qwen3 Embedding: Advancing Text Embedding and Reranking Through Foundation Models*. 2025. [arXiv:2506.05176](https://arxiv.org/pdf/2506.05176)
- Vera et al. *EmbeddingGemma: Powerful and Lightweight Text Representations*. 2025. [arXiv:2509.20354](https://arxiv.org/pdf/2509.20354)
- Jina AI. *jina-reranker-v3: 0.6B listwise reranker*. [jina.ai](https://jina.ai/models/jina-reranker-v3/)
- Mixedbread. *mxbai-rerank-base-v2* model card (109 languages; Swahili and Luganda not named). [huggingface.co](https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v2)
- Jha et al. *Jina-ColBERT-v2: A General-Purpose Multilingual Late Interaction Retriever*. [arXiv:2408.16672](https://arxiv.org/pdf/2408.16672)
- Qdrant. *Hybrid and Multi-Stage Queries* — RRF default k=2, configurable `Rrf(k=…)`, DBSF. [qdrant.tech](https://qdrant.tech/documentation/concepts/hybrid-search/)
- Qdrant. *Qdrant 1.15* (multilingual tokenizer, stemming, stopwords; server-side BM25 inference from 1.15.2) and *Qdrant 1.19* (per-tenant BM25 IDF, TurboQuant). [1.15](https://qdrant.tech/blog/qdrant-1.15.x/), [1.19](https://qdrant.tech/blog/qdrant-1.19.x/)
- Upadhyay et al. *A Large-Scale Study of Relevance Assessments with Large Language Models* (UMBRELA in the TREC 2024 RAG track). [arXiv:2411.08275](https://arxiv.org/abs/2411.08275); TREC 2025 RAG qrels appendices. [trec.nist.gov](https://trec.nist.gov/pubs/trec34/appendices/trec2025-rag-qrels/ensemble_umbrela1.html)
- Adeyemi et al. *CIRAL: A Test Collection for CLIR Evaluations in African Languages*. [uwspace](https://uwspace.uwaterloo.ca/handle/10012/20520); Adeyemi et al. *Zero-Shot Cross-Lingual Reranking with Large Language Models for Low-Resource Languages*. ACL 2024. [arXiv:2312.16159](https://arxiv.org/pdf/2312.16159)
- Anthropic. *Contextual Retrieval* (−49% failed retrievals; −67% with reranking). Summarised at [maginative.com](https://www.maginative.com/article/anthropics-contextual-retrieval-technique-enhances-rag-accuracy-by-67)
- Akera et al. *Sunflower: A New Approach to Expanding Coverage of African Languages in Large Language Models*. PMLR 314 (2026). [proceedings.mlr.press](https://proceedings.mlr.press/v314/akera26a.html)
- ISO/IEC 25059:2023 *Quality model for AI systems*. [iso25000.com](https://iso25000.com/index.php/en/iso-25000-standards/iso-25059); NIST AI 600-1 *Generative AI Profile*. [airc.nist.gov](https://airc.nist.gov/docs/NIST.AI.600-1.GenAI-Profile.ipd.pdf)
- Voice-agent latency budgets (~800 ms to first audio; overlap retrieval with the model). [twig.so](https://www.twig.so/blog/voice-ai-agents-latency-budget-800ms), [elevenlabs.io](https://elevenlabs.io/blog/voice-agent-latency-optimization)
