# Model Swap Guide — URA Chatbot (2026)

One-line environment variable changes to swap models. All alternatives
below are **Apache-2.0 or MIT licensed** — commercially deployable
without restrictions.

---

## Quick Reference

```bash
# Current defaults (set in .env or docker-compose.yml)
LLM_MODEL=Qwen/Qwen3-8B           # llm.py    — answer generation
DENSE_MODEL=BAAI/bge-m3            # retriever.py / indexer.py — dense embeddings
DENSE_DIM=1024                     # must match DENSE_MODEL output dimension
RERANKER_MODEL=mixedbread-ai/mxbai-rerank-base-v2  # retriever.py — cross-encoder reranking
```

### Swap Checklist

| What changed | Re-index? | Restart API? | Update training_config.yaml? |
|-------------|-----------|-------------|------------------------------|
| `LLM_MODEL` | No | Yes | `models.generation.web_inference` |
| `DENSE_MODEL` / `DENSE_DIM` | **Yes** (`./scripts/reindex.sh`) | Yes | `models.embedding` |
| `RERANKER_MODEL` | No | Yes | No (not in training config) |
| `RERANK_ENABLED=false` | No | Yes | No |

---

## 1. LLM (Answer Generation)

| Model | Params | MMLU | License | VRAM (fp16) | Context | Notes |
|-------|--------|------|---------|-------------|---------|-------|
| **Qwen/Qwen3-8B** (default) | 8B | 74.7 | Apache-2.0 | 16 GB | 128K | Best quality per GPU dollar; hybrid thinking mode |
| Qwen/Qwen3-30B-A3B | 30B (3B active) | 81.4 | Apache-2.0 | 18 GB | 128K | MoE — 30B quality at 3B cost; needs vLLM/SGLang |
| Qwen/Qwen3-4B | 4B | ~68 | Apache-2.0 | 8 GB | 128K | Matches Qwen2.5-7B; fits small GPUs |
| microsoft/Phi-4-mini-instruct | 3.8B | 68 | MIT | 8 GB | 128K | Strong math/logic; MIT license |
| google/gemma-3-4b-it | 4B | ~65 | Apache-2.0 | 8 GB | 128K | Multimodal (text + image) |
| Qwen/Qwen2.5-3B-Instruct | 3B | 61 | Apache-2.0 | 6 GB | 6K | Legacy — smaller, lower quality |

### Swap command

```bash
# Upgrade to Qwen3-8B (recommended — fits on RTX A6000 easily)
LLM_MODEL=Qwen/Qwen3-8B LLM_CONTEXT_WINDOW=8192

# Budget option: MoE (30B quality, 3B active params)
LLM_MODEL=Qwen/Qwen3-30B-A3B LLM_CONTEXT_WINDOW=8192 LLM_BACKEND=vllm

# Downgrade for smaller GPUs
LLM_MODEL=Qwen/Qwen3-4B LLM_CONTEXT_WINDOW=8192

# Legacy (rollback)
LLM_MODEL=Qwen/Qwen2.5-3B-Instruct LLM_CONTEXT_WINDOW=6144
```

No code changes needed — the `LLM_MODEL` env var controls everything.

---

## 2. Embedding Model (Dense Retrieval)

| Model | Params | MTEB Multilingual | Dims | License | Notes |
|-------|--------|-------------------|------|---------|-------|
| **BAAI/bge-m3** (default) | 568M | 63.0 | 1024 | MIT | Battle-tested; great Luganda coverage |
| Qwen/Qwen3-Embedding-8B | 8B | **70.58** | flex (32-7168) | Apache-2.0 | +7.5 MTEB pts; Matryoshka dims |
| Qwen/Qwen3-Embedding-0.6B | 0.6B | ~58 | flex | Apache-2.0 | Smallest Qwen3 embedder |
| nomic-ai/nomic-embed-text-v2-moe | MoE | ~62 | flex | Apache-2.0 | First MoE embedding model |
| sentence-transformers/all-MiniLM-L6-v2 | 22M | ~48 | 384 | Apache-2.0 | Legacy — fast but low quality |

### Swap command (requires re-indexing)

```bash
# Upgrade to Qwen3-Embedding (highest MTEB, Matryoshka 1024-dim)
DENSE_MODEL=Qwen/Qwen3-Embedding-8B DENSE_DIM=1024

# Keep current default
DENSE_MODEL=BAAI/bge-m3 DENSE_DIM=1024

# Legacy (384-dim, no re-index if old collection exists)
DENSE_MODEL=sentence-transformers/all-MiniLM-L6-v2 DENSE_DIM=384
```

**After changing DENSE_MODEL, re-index the Qdrant collection:**

```bash
# Option A: Full re-index (recommended)
python -m App.backend.app.indexer --recreate

# Option B: Incremental (keeps old vectors, adds new — NOT recommended for dim change)
python -m App.backend.app.indexer
```

---

## 3. Reranker (Cross-Encoder)

| Model | Params | BEIR nDCG@10 | License | Notes |
|-------|--------|-------------|---------|-------|
| **mixedbread-ai/mxbai-rerank-base-v2** (default) | 500M | 55.6 | Apache-2.0 | RL-trained; best quality/latency ratio |
| mixedbread-ai/mxbai-rerank-large-v2 | 1.5B | **57.5** | Apache-2.0 | Highest open-source BEIR; +200ms latency |
| BAAI/bge-reranker-v2-m3 | 278M | 51.8 | Apache-2.0 | Multilingual; pairs with bge-m3 |
| Qwen/Qwen3-Reranker-0.6B | 600M | ~52 | Apache-2.0 | Qwen ecosystem; pairs with Qwen3-Embedding |
| cross-encoder/ms-marco-MiniLM-L-6-v2 | 22M | ~48 | Apache-2.0 | Legacy — 2021-era, fast but low quality |

### Swap command (no re-index needed)

```bash
# Upgrade to mxbai (recommended)
RERANKER_MODEL=mixedbread-ai/mxbai-rerank-base-v2

# Premium quality (slower, +200ms)
RERANKER_MODEL=mixedbread-ai/mxbai-rerank-large-v2

# Multilingual-focused
RERANKER_MODEL=BAAI/bge-reranker-v2-m3

# Legacy (rollback)
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

# Disable reranking entirely (fastest, lowest quality)
RERANK_ENABLED=false
```

No code changes or re-indexing needed — reranker operates on already-retrieved passages.

---

## 4. Mobile LLM (On-Device GGUF)

| Model | Effective Params | Q4_K_M Size | License | Context | Notes |
|-------|-----------------|-------------|---------|---------|-------|
| **google/gemma-3n-E2B-it** | 2B | ~1.3 GB | Apache-2.0 | 32K | Outperforms 27B models; multimodal |
| Qwen/Qwen3-1.7B | 1.7B | ~1.1 GB | Apache-2.0 | 128K | Matches Qwen2.5-3B quality |
| microsoft/Phi-4-mini-instruct | 3.8B | ~2.5 GB | MIT | 128K | Strong reasoning |
| google/gemma-2-2b-it (current) | 2B | ~1.5 GB | Apache-2.0 | 8K | Legacy — less capable |

### Swap procedure

1. Download the GGUF from HuggingFace (e.g., `unsloth/gemma-3n-E2B-it-GGUF`)
2. Place in `MobileApp/ura_chatbot/assets/models/`
3. Update `lib/core/inference/on_device_llm.dart` model filename
4. Update `assets/speech/manifest.json` with new SHA-256 hash

---

## 5. AVOID List (License Issues)

| Model | License | Problem |
|-------|---------|---------|
| jina-reranker-v3 | CC BY-NC 4.0 | **Non-commercial only** |
| jina-embeddings-v3 | CC BY-NC 4.0 | **Non-commercial only** |
| Meta-Llama-3.1-8B | Llama 3.1 Community | Requires acceptance; redistribution limits |
| Cohere embed/rerank | Proprietary API | API-dependent; no self-hosting |
| OpenAI text-embedding-3 | Proprietary API | API-dependent; data leaves your network |
| Mistral Large | Proprietary | Requires Mistral API |

---

## 6. Recommended Stack by GPU Budget

### RTX A6000 / A100 (48+ GB VRAM) — Maximum Quality
```bash
LLM_MODEL=Qwen/Qwen3-8B
DENSE_MODEL=Qwen/Qwen3-Embedding-8B
RERANKER_MODEL=mixedbread-ai/mxbai-rerank-large-v2
# Total VRAM: ~30 GB (all three models fit on one GPU)
```

### RTX 4090 / A6000 (24 GB VRAM) — Balanced
```bash
LLM_MODEL=Qwen/Qwen3-8B
DENSE_MODEL=BAAI/bge-m3
RERANKER_MODEL=mixedbread-ai/mxbai-rerank-base-v2
# Total VRAM: ~18 GB
```

### RTX 3090 / 4080 (16 GB VRAM) — Budget
```bash
LLM_MODEL=Qwen/Qwen3-4B
DENSE_MODEL=BAAI/bge-m3
RERANKER_MODEL=BAAI/bge-reranker-v2-m3
# Total VRAM: ~12 GB
```

### CPU Only (No GPU)
```bash
LLM_MODEL=Qwen/Qwen3-4B
LLM_TORCH_DTYPE=float32
LLM_DEVICE=cpu
DENSE_MODEL=sentence-transformers/all-MiniLM-L6-v2
DENSE_DIM=384
RERANKER_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2
# RAM: ~8 GB
```

---

## 7. Re-Indexing After Embedding Model Change

When you change `DENSE_MODEL` or `DENSE_DIM`, the Qdrant vector index
must be rebuilt because vector dimensions changed.

### Using the reindex script (recommended)

```bash
# 1. Set new model in .env
DENSE_MODEL=Qwen/Qwen3-Embedding-8B
DENSE_DIM=1024

# 2. Run the reindex script (pre-flight checks + post-verification)
./scripts/reindex.sh

# 3. Restart API to pick up new model
docker compose restart api
```

The script validates Qdrant is reachable, runs `--recreate`, and
verifies the collection has vectors with the correct dimension.

### Manual re-index

```bash
# Full re-index (drops old collection, rebuilds from Data/)
python -m App.backend.app.indexer --recreate

# CSVs only (useful when only FAQ data changed)
python -m App.backend.app.indexer --recreate --csvs-only

# PDFs only
python -m App.backend.app.indexer --recreate --pdfs-only

# Verify
curl http://localhost:6333/collections/ura_knowledge_base | python -m json.tool
# Should show: vectors_count > 0, vector_size = 1024
```

**No re-index needed for:**
- LLM changes (`LLM_MODEL`) — model is used for generation, not indexing
- Reranker changes (`RERANKER_MODEL`) — reranker operates post-retrieval
- Threshold changes (`GROUNDING_THRESHOLD`, `ABSTENTION_THRESHOLD`)

---

## 8. Files to Update When Swapping

When swapping models, update these locations to keep everything consistent:

| File | What to update |
|------|---------------|
| `.env` | The env var itself (`LLM_MODEL`, `DENSE_MODEL`, etc.) |
| `ml/configs/training_config.yaml` | `models.embedding`, `models.generation`, `mobile_export` |
| `docker-compose.yml` | vLLM `--model` default (only if changing LLM default) |
| This guide | Quick Reference table at top |
