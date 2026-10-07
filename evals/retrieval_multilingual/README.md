# Trilingual retrieval benchmark (en / lg / sw)

Measures **retrieval**, not replies: every query names the Qdrant points that
answer it, so each leg of the retriever (BM25, dense, fusion, the cross-encoder,
the G18 translate leg) can be scored on its own and against the others on the
same questions. Findings and the gaps they support:
`docs/Reports/MULTILINGUAL_RETRIEVAL_EVALUATION_2026-10-07.md`.

This is a **measurement**, not a CI gate. Part of it is machine-translated (see
the caveat below), and `docs/GAPS_AND_AGENTIC_ROADMAP.md` is explicit that
invented Luganda/Swahili must not gate anything.

## Sets

`queries.jsonl` holds one row per query (1,659). Each names a `parent`, and the
answer to it — gold point ids, question, answer text — is stored once in
`gold.jsonl`. The typo and ASR-style copies are not stored:
`load_queries` derives them with seeded functions, the same 1,092 copies on
every load, for 2,751 queries in all. `qrels_coverage.jsonl` is the judged
coverage pool.

| Set | Rows | What it is | Gold |
|---|---|---|---|
| `faq` | 2,223 | 225 indexed FAQ rows (≤ 3 per source file, 75 files), asked verbatim, as a paraphrase, and in Luganda and Swahili (plain and code-switched), plus `typo` and `asr` copies | the point(s) carrying that question |
| `native` | 126 | the 23 rows whose Luganda/Swahili questions are written into the corpus (`question_lg`, `question_sw`), plus perturbed copies | the row |
| `coverage` | 315 | `Data/eval/coverage_bank.jsonl`, 105 curated questions × 3 languages | judged pool (`qrels_coverage.jsonl`) |
| `ood` | 87 | questions the corpus must not answer: general knowledge, other countries' tax, other Ugandan agencies, chit-chat | none — any answer is wrong |

**Caveat.** The `faq` paraphrases and translations come from Sunflower, the model
the production pivot also translates with. A Sunflower EN→LG→EN round trip
recovers its own English more easily than a person's Luganda, so `faq` flatters
the translate leg. `native` and `coverage` were not generated here; read a
Luganda or Swahili `faq` number against them. Paraphrases are often close to
the original wording, so `faq/en` is optimistic too.

Each generated query was kept only if `detect_language` **and** the
receptionist's word lists (`lexical_hits`) agree on its language; 148
generations failed that and were dropped. The first build, before that check,
had 270 rows that were Sunflower's translation of the prompt's own instruction.

## Systems

`scripts/eval_retrieval_multilingual.py` docstring has the full list. In short:
single legs and fusion on the raw query and on the **pivot** (the English the
retriever searches with), Qdrant RRF (k = 2, its default) against client RRF
(k = 60) and DBSF, the cross-encoder where it runs, `search_planned` as
production calls it (`production`), and `ChatModel.generate_retrieval_only`
with no locale hint (`service`: language detection, routers, keyword fallback,
corrective RAG, FAQ blend, binding gates, abstention).

Relevance is `strict` (the gold point, or one with the same question) or
`lenient` (also a passage holding ≥ 60% of the gold answer's content words).
Metrics: Hit@1/3/5/10, MRR@10, nDCG@10 (graded for `coverage`), served-context
precision, abstention on answerable vs out-of-domain questions, the abstention
signal's AUROC, pivot fidelity, per-stage latency (cold and warm) and a
concurrency sweep. 95% bootstrap intervals; McNemar for paired Hit@1.

## Running it

Against the GPU stack (`docs/runbooks/` four-file bring-up), from the repo root:

```bash
# 1. Build the set (only when the index or the sampling changes)
docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e APP_ENV=development \
    -v "$PWD:/src" -w /src --entrypoint python app-api:gpu scripts/build_retrieval_multilingual_set.py

# 2. Copy the runner and set into the api container (inherits its exact environment)
docker exec ura-app-api mkdir -p /home/appuser/rm_eval
docker cp scripts/eval_retrieval_multilingual.py ura-app-api:/home/appuser/rm_eval/
docker cp evals/retrieval_multilingual/queries.jsonl ura-app-api:/home/appuser/rm_eval/
docker cp evals/retrieval_multilingual/gold.jsonl ura-app-api:/home/appuser/rm_eval/
E="docker exec -w /app ura-app-api python /home/appuser/rm_eval/eval_retrieval_multilingual.py"

# 3. One run per configuration; override the environment to measure another
$E run --config as_deployed
docker exec -w /app -e RERANK_ENABLED=true -e RETRIEVER_DENSE_DEVICE=cuda:0 ura-app-api \
    python /home/appuser/rm_eval/eval_retrieval_multilingual.py run --config rerank_gpu \
    --service-sample 0.5 --sample-perturbed 0.3
$E run --config sparse_only --sparse-only --no-service

# 4. With nothing else running: cold latency, concurrency sweep and the float16
#    check; the service path one question at a time; the isolated mechanisms
$E latency --config as_deployed
$E latency --config as_deployed_service --per-lang 20 --concurrency 1 --precision-queries 0 --service 20
$E mechanisms --config as_deployed
#    (repeat each with the -e overrides above and --config rerank_gpu…)

# 5. Candidate embedders, in their own container on a free GPU (pivots from any run file)
docker cp ura-app-api:/home/appuser/rm_eval/raw_sparse_only.jsonl /tmp/pivots.jsonl
docker run --rm --gpus '"device=5"' --network host --user "$(id -u):$(id -g)" -e HOME=/tmp -e USER=eval \
    -e HF_HOME=/app/hf_cache -e QDRANT_URL=http://127.0.0.1:6333 -v /mnt/nas1/caches/huggingface_cache:/app/hf_cache \
    -v "$PWD:/src:ro" -v /tmp:/work -w /src --entrypoint python app-api:gpu \
    scripts/eval_retrieval_multilingual.py embedders --work /work --pivots /work/pivots.jsonl
#    then docker cp /tmp/raw_emb_*.jsonl and env_emb_*.json into /home/appuser/rm_eval/

# 6. Judge the coverage pool (local Sunflower), then the report
$E judge
$E report --out /home/appuser/rm_eval/report.json
docker cp ura-app-api:/home/appuser/rm_eval/report.json evals/reports/retrieval_multilingual_<date>.json
docker cp ura-app-api:/home/appuser/rm_eval/qrels_coverage.jsonl evals/retrieval_multilingual/
```

Sunflower is a lenient judge (it graded 14 of 60 deliberately unrelated
passages relevant), so read judged numbers at grade 3 and as an upper bound;
the cross-lingual agreement measure in the report does not depend on it.

The runner turns off the semantic cache, tickets, handoff summaries and memory
for its own process and writes to a scratch analytics database, so it neither
reads cached answers nor leaves rows in the live stack.
