# Capacity envelope — measured 2026-08-19

Traceability for GitHub issue
[#304](https://github.com/mpairwe7/FinalYearProject/issues/304)
(capacity envelope + published SLOs). Companion operator numbers:
`docs/runbooks/capacity-slo.md`.

This file is the audit log: what was measured, on which hardware, with
which stack, and what a later reviewer must **not** treat as a closed
G33 (HPA/KEDA still example-only).

## 1. Intent

URA will ask: how many concurrent taxpayers, what is p95, and what
happens when the LLM is slow. Before this run the repo had a k6 script,
aspirational SLO tables, and circuit breakers — not a measured envelope.

## 2. Stack under test

| Layer | Value |
|---|---|
| Date | 2026-08-19 |
| Host GPUs | NVIDIA RTX A6000 48 GiB (one card per vLLM run) |
| vLLM image | `vllm/vllm-openai:v0.8.5` |
| Model | local `App/Model/Sunflower-14B-FP8` (`Sunbird/Sunflower-14B-FP8`) |
| Engine | `max-model-len=4096`, `gpu-memory-utilization=0.70`, `--max-num-seqs 64`, Marlin weight-only FP8 |
| API | FastAPI `app.main:app`, `LLM_BACKEND=vllm`, `RATE_LIMIT=10000/minute` for the test (production default **30/minute**) |
| Qdrant (campaign C) | `qdrant/qdrant:v1.17.1` on `127.0.0.1:6333`, collection `ura_knowledge_base` |

Three campaigns:

| ID | What | Qdrant |
|---|---|---|
| A | vLLM hop only (`POST /v1/chat/completions`) | n/a |
| B | FastAPI `/v1/chat` + `/v1/chat/stream`, `QDRANT_ENABLED=false` | off (Vectorize fallback attempted, FAQ/calculator/workflow still served) |
| C | FastAPI with **Qdrant running and FAQ JSONL seeded** | on, sparse-only |

Issue comments: [analysis](https://github.com/mpairwe7/FinalYearProject/issues/304#issuecomment-5338857947),
[GPU stress](https://github.com/mpairwe7/FinalYearProject/issues/304#issuecomment-5339207845),
[API without Qdrant](https://github.com/mpairwe7/FinalYearProject/issues/304#issuecomment-5340316727).

## 3. Qdrant FAQ JSONL seed (campaign C)

Empty Qdrant is not a retrieval test. Campaign C seeded the canonical
FAQ JSONL corpus:

```bash
PYTHONPATH=App/backend QDRANT_URL=http://127.0.0.1:6333 \
  python -m app.indexer --export-faq-jsonl
# {'sources': 41, 'source_rows': 515, 'records': 508, 'duplicates_removed': 7}

PYTHONPATH=App/backend QDRANT_URL=http://127.0.0.1:6333 \
  SPARSE_ONLY_INDEX=true QDRANT_COLLECTION=ura_knowledge_base \
  python -m app.indexer --faq-jsonl-only --recreate
# total_upserted=508, faq_jsonl_documents=508
```

Scroll audit after index:

| Check | Result |
|---|---|
| Collection | `ura_knowledge_base`, status green |
| `points_count` | **509** (508 FAQ + 1 BM25-binding sentinel) |
| `indexed_vectors_count` | **508** |
| `payload.doc_type=faq_jsonl` | **508** |
| Distinct `source` files | **41** (matches `App/Data/faq_jsonl/*.jsonl`) |
| Vector config | sparse-only (`SPARSE_ONLY_INDEX=true`); no dense bge-m3 stamp |

`/ready` reported `retrieval_mode=vector`. The retriever logged
SPARSE-ONLY mode (no sentence-transformers). Dense Vectorize fallback
was attempted at init; Qdrant sparse BM25 is what actually held the
508 FAQ points.

Re-verify:

```bash
curl -s http://127.0.0.1:6333/collections/ura_knowledge_base \
  | python3 -c "import sys,json; r=json.load(sys.stdin)['result']; print(r['points_count'], r['indexed_vectors_count'], r['status'])"
```

## 4. Campaign A — vLLM hop (no FastAPI)

Short grounded prompts, `max_tokens=128`, mixed URA questions.
Client timeout 90s. 0 errors / 0 preemptions through **64** in-flight.

Sustained closed-loop:

| C | rps | p50 | p95 | p99 | max | gen tok/s |
|---|---|---|---|---|---|---|
| 1 | 0.34 | 2.40s | 7.18s (n=7, noisy) | 8.29s | 8.57s | 15 |
| 4 | 2.68 | 1.02s | **3.83s** | 5.83s | 8.20s | 78 |
| 8 | 2.81 | 1.76s | 5.95s | 9.14s | 9.19s | 90 |
| 16 | 5.73 | 1.78s | 5.97s | 9.59s | 9.78s | 172 |
| 32 | 9.88 | 1.99s | 6.42s | 10.75s | 10.94s | 307 |
| 48 | 11.68 | 2.46s | 9.02s | 13.53s | 14.01s | 366 |
| 64 | 12.92 | 3.22s | 11.43s | 17.76s | 18.27s | 405 |

Stream TTFT p95: 101 ms @C=1, 271 ms @C=8, 342 ms @C=16, 487 ms @C=32.

`max_tokens=512` tail (production default): p99 **18–22s**, max **27–34s**.
Still inside `LLM_TOTAL_BUDGET_SECONDS=70`.

Engine ceiling for a **full 4096-token** request: **17.86×** concurrent
(73k KV tokens at 0.70 util). Short RAG turns never filled KV (~4% at C=64).

## 5. Campaign B — FastAPI, Qdrant off

`QDRANT_ENABLED=false`. Mix of 8 URA questions. Warmup:

| `retrieval_mode` | Example | Service time |
|---|---|---|
| `calculator` | VAT rate | 12 ms |
| `workflow` | TIN registration | 12 ms |
| `hybrid` | PAYE brackets | **7.8 s** cold |

Sustained `/v1/chat` (limiter raised to 10000/min):

| C | ok/n | err | p95 | note |
|---|---|---|---|---|
| 1 | 279/279 | 0 | 13 ms (p99 **2.27s**, max **6.86s**) | uncached hybrid tail |
| 8 | 5414/5414 | 0 | 66 ms | cache + deterministic |
| 16 | 5304/7033 | **24.6% HTTP 429** | 118 ms on 200s | first hard fail |
| 32 | 2890/10363 | **72.1% HTTP 429** | 229 ms on 200s | |

`/v1/chat/stream` through C=32: 0 errors, TTFT p95 7–240 ms.
3s/10s client deadlines: **0 timeouts** (all 200, &lt; 380 ms).

## 6. Campaign C — FastAPI, Qdrant on, FAQ JSONL seeded

Same vLLM. Qdrant as in §3. 0 HTTP errors on these levels (shorter
windows than B, so the 10000/min bucket did not fill).

Mixed 8 queries (`calculator` / `workflow` / `faq_priority` / `hybrid`):

| C | ok | rps | p50 | p95 | p99 | max | hybrid share |
|---|---|---|---|---|---|---|---|
| 1 | 158 | 10.5 | 8 ms | 13 ms | **3.14s** | **6.74s** | 98/158 |
| 4 | 2061 | 137 | 27 ms | 47 ms | 62 ms | 230 ms | 1296/2061 |
| 8 | 2121 | 106 | 70 ms | 117 ms | 182 ms | 448 ms | 1328/2121 |
| 16 | 3220 | 160 | 82 ms | 219 ms | 395 ms | 1.09s | 1995/3220 |

Per-mode at C=1 (the only level with a cold LLM tail):

| mode | n | p50 | p95 | p99 | max |
|---|---|---|---|---|---|
| calculator | 20 | 8 ms | 10 ms | 10 ms | 10 ms |
| workflow | 20 | 8 ms | 10 ms | 10 ms | 10 ms |
| faq_priority | 20 | 8 ms | 76 ms | 1.00s | 1.24s |
| hybrid | 98 | 9 ms | 15 ms | **5.70s** | **6.74s** |

Hybrid-labelled turns **after cache** (PAYE / WHT / penalties / EFRIS):
p95 11 ms @C=1, 48 ms @C=4, 93 ms @C=8. That is **not** generation
time — the GPU hop was already cached. Use campaign A for uncached
LLM p95.

Stream mix: C=1 p95 9 ms (TTFT p95 7 ms); C=8 p95 87 ms (TTFT p95 69 ms).

## 7. Named limits (single A6000 + one FastAPI worker)

| Limit | Where it sits |
|---|---|
| Idle LLM service time | p50 ~1.0–2.4s (campaign A); cold hybrid on `/v1/chat` ~7s |
| p95 ≤ 3s on **generation** | Missed from **C=4** on the vLLM hop |
| p50 ≤ 2s on generation | Holds through **C=32** on vLLM; fails at C=48 |
| FAQ/calculator/workflow p95 | tens of ms; not the GPU |
| First API hard fail | `RATE_LIMIT` → **HTTP 429** (default 30/min; 10000/min still 429 after ~10k/min/IP) |
| Hang | Not observed at C=64 vLLM or C=32 FastAPI; 2s client timeout fail-fast on vLLM |
| Crash / KV / preemption | Not hit for short FAQ RAG. Full 4k context ceiling **~18 concurrent** |
| Throughput approach | ~13 rps / ~400 tok/s on vLLM at C=64 (still climbing slowly) |

## 8. SLO conflict (must not silently pick one)

| Source | Chat p95 |
|---|---|
| `docs/DEPLOYMENT.md` §10, `docs/MONITORING.md` §7, `monitoring/alerting-rules.yml` | **&lt; 2s** |
| `tests/load/k6-chat-slo.js` (NFR-01), `monitoring/prometheus-rules.yaml` | **&lt; 3s** |

A blended `/v1/chat` p95 will pass 2s whenever FAQ/calculator dominate
and fail as soon as the histogram is hybrid-only. Publish **per
`retrieval_mode`**, or define the SLO on `hybrid` only.

`k6-chat-slo.js` is **not** in `.github/workflows`. It does not hit
`/v1/chat/stream`. It will mostly measure fast paths unless queries are
pinned to hybrid and the limiter is raised.

## 9. What this does not close

- G33 HPA/KEDA — YAML still example-only. A measured p95 now exists;
  replica counts are still not to be invented.
- G34 cluster chaos.
- URA-agreed load profile (expected/peak sessions, daily volume).
- Multi-replica, Crane Cloud, or cloud-LLM (Groq/Workers AI) envelopes.
- Dense bge-m3 Qdrant (campaign C is sparse-only FAQ JSONL).

## 10. How to re-run

See `docs/runbooks/capacity-slo.md`.

## 11. Live e2e + high-load re-run (Qdrant + Sunflower)

Same stack as campaign C. Playwright `chat-flow.spec.ts` still mocks
the backend; this pass is **HTTP against the live API**.

Stubbed pytest regression (no live LLM): **43 passed**
(`test_resilience`, `tests/chaos`, `test_sunbird_retry_budget`,
`test_production_readiness`, `tests/test_all_endpoints_e2e`).

Live functional e2e: **9/9 passed**

| Case | Result |
|---|---|
| `GET /health` | 200 alive |
| `GET /ready` | 200, `model_loaded`, `retrieval_mode=vector`, 41 tags |
| Qdrant FAQ JSONL | 509 points, 508 indexed, green |
| vLLM | `Sunbird/Sunflower-14B-FP8` |
| `/v1/chat` VAT | `calculator`, 18.5 ms, 18% |
| `/v1/chat` TIN | `workflow`, 8.9 ms |
| `/v1/chat` PAYE | `hybrid`, Sunflower, **11.2 s** cold, 454 chars |
| `/v1/chat` WHT | `hybrid`, Sunflower, 5.0 s |
| `/v1/chat/stream` penalties | 200, TTFT **735 ms**, 26 SSE events, 3.8 s e2e |

High-load (0 HTTP errors; cache after the cold hybrid turns):

| Label | ok | rps | p50 | p95 | p99 | max |
|---|---|---|---|---|---|---|
| mix C=1 | 7 | 0.33 | 14 ms | **10.2 s** | 10.3 s | 10.3 s |
| mix C=8 | 1766 | 117 | 61 ms | 115 ms | 224 ms | 433 ms |
| mix C=16 | 1835 | 122 | 108 ms | 271 ms | 592 ms | 1.13 s |
| hybrid C=1 (cached) | 1597 | 133 | 7 ms | 12 ms | 16 ms | 23 ms |
| hybrid C=8 (cached) | 2439 | 162 | 42 ms | 97 ms | 177 ms | 884 ms |
| stream C=1 | 1611 | 134 | 7 ms | 11 ms | 13 ms | 162 ms |
| stream C=8 | 1738 | 145 | 52 ms | 78 ms | 182 ms | 237 ms |

Cold hybrid (e2e PAYE 11.2 s, mix C=1 p95 10.2 s) matches campaign A.
Cached hybrid p95 is not generation time.
