# Runbook — capacity envelope and SLOs

Operator numbers for issue #304. Audit log:
`App/docs/traceability/capacity-envelope-2026-08-19.md` (measured
2026-08-19 on one RTX A6000 + `vllm/vllm-openai:v0.8.5` +
Sunflower-14B-FP8).

Do not apply `infra/k8s/hpa-chat.yaml` from these replica counts — there
are none. This is a **single-instance** ceiling.

## Headroom (one GPU, one API worker)

| Workload | Comfortable | Stress | Extreme (measured, 0% crash) |
|---|---|---|---|
| Uncached LLM generation (`max_tokens=128`) | ≤4 concurrent if p95 must stay ≤3s | 32 in-flight, p95 ~6.4s, ~10 rps | 64 in-flight, p95 ~11s, p99 ~18s, ~13 rps |
| Stream first token | TTFT p95 ~100 ms idle | ~490 ms at 32 in-flight | not a hang |
| `/v1/chat` FAQ / calculator / workflow | hundreds of rps, p95 tens–hundreds of ms | — | **HTTP 429** at `RATE_LIMIT` (default **30/minute** per IP) |
| Cold hybrid `/v1/chat` with Qdrant FAQ JSONL | one turn ~7s | same GPU curve once generating | 45s `LLM_DEADLINE` / 70s chain budget |

Capacity headroom to quote: **~3 rps of short uncached generations**
before p95 crosses ~3s; **~13 rps** before the GPU token pipe flattens.
The public cap is `RATE_LIMIT`, not the GPU, until that limiter is raised.

## Seed Qdrant with FAQ JSONL

Campaign C used a dedicated Qdrant and this seed (508 FAQ records, 41
sources, plus one BM25 sentinel → 509 points):

```bash
docker run -d --name ura-qdrant -p 127.0.0.1:6333:6333 qdrant/qdrant:v1.17.1

PYTHONPATH=App/backend QDRANT_URL=http://127.0.0.1:6333 \
  python -m app.indexer --export-faq-jsonl

PYTHONPATH=App/backend QDRANT_URL=http://127.0.0.1:6333 \
  SPARSE_ONLY_INDEX=true QDRANT_COLLECTION=ura_knowledge_base \
  python -m app.indexer --faq-jsonl-only --recreate

curl -s http://127.0.0.1:6333/collections/ura_knowledge_base
# points_count=509, indexed_vectors_count=508, status=green
```

`--recreate` drops the collection. Do not run it against a production
index.

## Load the GPU + API

```bash
# vLLM (pick a free GPU 0-7)
docker run --gpus device=$GPU --ipc=host -p 18011:8000 \
  -e HF_HUB_OFFLINE=1 \
  -v $PWD/App/Model/Sunflower-14B-FP8:/model:ro \
  vllm/vllm-openai:v0.8.5 \
  --model /model --served-model-name Sunbird/Sunflower-14B-FP8 \
  --port 8000 --max-model-len 4096 --gpu-memory-utilization 0.70 --max-num-seqs 64

PYTHONPATH=App/backend LLM_BACKEND=vllm LLM_MODEL=Sunbird/Sunflower-14B-FP8 \
  VLLM_BASE_URL=http://127.0.0.1:18011/v1 QDRANT_ENABLED=true \
  QDRANT_URL=http://127.0.0.1:6333 QDRANT_COLLECTION=ura_knowledge_base \
  RERANK_ENABLED=false RATE_LIMIT=10000/minute SPEECH_ENABLED=false \
  python -m uvicorn app.main:app --host 127.0.0.1 --port 18080 --workers 1
```

k6 (not in CI; does not cover `/v1/chat/stream`):

```bash
k6 run --env BASE_URL=http://127.0.0.1:18080 tests/load/k6-chat-slo.js
```

Pin hybrid questions if you intend to validate the 3s generation NFR
rather than FAQ/calculator p95.

## SLO split

| Surface | Measured idle | Do not use |
|---|---|---|
| FAQ / calculator / workflow | p95 ≪ 2s | as proof the LLM SLO holds |
| Hybrid generation | p50 ~2s, p95 ≥3s from 4 concurrent | a single blended `/v1/chat` p95 |
| Stream TTFT | ~100 ms idle on vLLM | e2e 2s as a first-token SLO |

Docs currently disagree (2s vs 3s). Until one table is chosen, treat
**3s p95 on hybrid** as NFR-01 and **2s** as unmet for generation.

## When p95 is high

1. Split Grafana by `retrieval_mode` (or inspect response `retrieval_mode`).
2. FAQ/calculator slow → API/threadpool, not GPU.
3. Hybrid slow → vLLM queue (campaign A curve) or `LLM_DEADLINE_SECONDS`.
4. Sudden 429 → `RATE_LIMIT`, not capacity loss.
5. Hang toward 45–70s → deadline/budget path in `service.py`; should
   return, not block forever.
