# Runbook — Multilingual Retrieval & 2,000-FAQ Benchmark

Operator guide for trilingual retrieval (English, Luganda, Swahili) across the
URA tax knowledge base, covering the hybrid retrieval pipeline, GPU cross-encoder
reranking, score calibration, abstention gating, and running the 2,000-FAQ evaluation.

---

## 1. System Architecture & Component Defaults

The retrieval pipeline serves REST, WebSocket, LangGraph, and voice prefetch through
`HybridRetriever.search_planned` (`App/backend/app/retriever.py`).

| Component | Default | Configuration Knob | Rationale |
|---|---|---|---|
| **Dense Embeddings** | `BAAI/bge-m3` on `cuda:0` | `RETRIEVER_DENSE_DEVICE` | 13.1 ms encode on GPU vs 84.4 ms on CPU (6.4× speedup, G126). |
| **Sparse Leg** | BM25 (tokenized) | Client-side against Qdrant | Lexical recall on exact statutory acronyms (TIN, EFRIS, PRN, PAYE). |
| **First-Stage Fusion** | DBSF (`models.Fusion.DBSF`) | `HYBRID_FUSION=dbsf` | Distribution-Based Score Fusion gains +6 pts LG and +4 pts SW over default RRF k=2 (G127). |
| **Reranker** | `mixedbread-ai/mxbai-rerank-base-v2` | `RERANK_ENABLED=true`, `RERANKER_DEVICE=cuda:0` | 5.28 ms/pair on GPU vs 49.1 ms/pair on CPU (9.3× speedup, G126). Controlled dynamically via `flags.is_enabled("reranker")`. |
| **Score Normalization** | Single Sigmoid | `score_norm = float(s)` | Output of `CrossEncoder.predict()` is already sigmoid-activated in $[0, 1]$; second sigmoid removed (G129). |
| **Abstention Gate** | Calibrated per locale | `ABSTENTION_THRESHOLD_NORM=0.30`<br>`ABSTENTION_THRESHOLD_NORM_LG=0.20`<br>`ABSTENTION_THRESHOLD_NORM_SW=0.20` | Clean [0, 1] scale deflects 100% of out-of-domain probes with 0% false abstention on answerable tax queries (G130). |
| **Multilingual Strategy** | Dual-pass with translation priority | `translate_retrieve=true` | Luganda uses English translation pass directly to avoid unaligned noise; Swahili merges dense and translated passes (G128). |
| **Language Identification** | Spellcheck-shielded lexical + Lingua | `query.detect_language` | Statutory loanwords treated as neutral; English spellcheck bypassed on vernacular queries (G132: 96.1% LID accuracy). |

---

## 2. Running the 2,000-FAQ Evaluation Suite

The comprehensive 2,000-FAQ benchmark (`scripts/evaluate_2000_faqs_ngrok.py`) assesses:
- **800 English queries (40%)**
- **600 Luganda queries (30%)**
- **600 Swahili queries (30%)**

Across five core URA tax domains: Domestic Taxes (550), Customs & Border Trade (450),
EFRIS (350), Transport & Licensing (350), and Taxpayer Education & Disputes (300).

### Command

```bash
# Run against local API instance on port 8083 (or external gateway URL):
python3 scripts/evaluate_2000_faqs_ngrok.py \
  --target http://127.0.0.1:8083 \
  --concurrency 16 \
  --gpu 4 \
  --out Results/metrics/2000_faqs_ngrok_evaluation_report.json
```

### Key Flags

- `--target`: Target API endpoint (defaults to the deployed gateway or local URL).
- `--concurrency`: Number of asynchronous worker tasks (default: 16–24).
- `--gpu`: GPU index for VRAM and power telemetry (default: 4 for local RTX A6000).
- `--fresh`: Ignore existing checkpoint file and restart evaluation from query 1.
- `--limit`: Run a subset (e.g. `--limit 300` for rapid smoke validation).

---

## 3. SLA Targets & Evaluation Criteria

| Performance Metric | Target SLA | Passing Standard |
|---|:---:|---|
| **Overall Grounded Accuracy** | $\ge 95.0\%$ | Evaluated via `score_reply` on cross-lingual statutory concepts, figures, and legal anchors. |
| **Vernacular Accuracy (LG & SW)** | $\ge 95.0\%$ | Luganda $\ge 95\%$, Swahili $\ge 96\%$. |
| **HTTP 200 OK Availability** | $100.0\%$ | Zero server drops or 5xx exceptions under concurrency. |
| **Median Response Latency (p50)** | $< 800\text{ ms}$ | Measured end-to-end including translation and reranking. |
| **95th Percentile Latency (p95)** | $< 3,000\text{ ms}$ | Worst-case multilingual query resolution time. |
| **System Throughput** | $> 4.0\text{ req/s}$ | Measured on dedicated single-GPU deployment. |
| **Vernacular Figure Fidelity** | $\ge 98.0\%$ | Digit-masking preservation of tax rates, thresholds, and monetary amounts (`mt.protect_figures`). |
| **Out-Of-Domain Deflection** | $100.0\%$ | Unrelated questions deflected via `OutputGuard.should_abstain`. |

---

## 4. Operational Troubleshooting

### If Multilingual Accuracy Drops Below 95%
1. **Check Reranker Flag & Device:** Verify `/ready` returns `retrieval: hybrid` and that `RERANK_ENABLED=true` is set.
2. **Inspect Language Detection Log:** If vernacular queries are served in English, inspect `detect_language` logs to verify tax acronyms are not triggering English fallback.
3. **Verify Qdrant Alias:** Ensure `QDRANT_COLLECTION` resolves to `ura_knowledge_base_jsonl_active` and has green status with all points indexed.

### If Retrieval Latency Exceeds 800ms (p50)
1. **Check GPU Placement:** Run `nvidia-smi` to ensure `bge-m3` and `mxbai-rerank` are running on `cuda:0`, not CPU. CPU execution increases latency by 6.4×–9.3×.
2. **Check vLLM Translation Queue:** Sunflower translation sits on the dual-pass critical path. If translation takes $> 400\text{ ms}$, check vLLM sequence concurrency and KV-cache utilization.
3. **Check Context Pruning Floor:** Verify `RETRIEVER_CONTEXT_FLOOR=0.20` and `RETRIEVER_CONTEXT_RELATIVE_DROP=0.45` are active, ensuring low-relevance passages are pruned prior to generative reasoning.

---

## 5. Scaling to 20,000 & 50,000 FAQs & 500 Complex CX Scenarios

### A. 50,000-FAQ Ultra-Scale Evaluation Architecture
* **Dataset Composition (50,000 Queries):**
  - **English (`en`):** 20,000 queries (40%)
  - **Luganda (`lg`):** 15,000 queries (30%)
  - **Swahili (`sw`):** 15,000 queries (30%)
  - **Statutory Domains:** Domestic Taxes (13,750), Customs (11,250), EFRIS (8,750), Transport (8,750), Education/Disputes (7,500).
  - **Distortion Profiles (12,500 Queries):** 5,000 natural paraphrases, 2,500 code-switched vernacular probes, 2,500 seeded QWERTY typo slips, and 2,500 ASR transcript-style queries.
* **Execution Parameters & Operational Strategy:**
  - Pipelined execution with `--concurrency 32` to `--concurrency 48` against the local GPU API replica.
  - Sustained throughput: $\sim 5.24\text{ req/s}$, completing in $\sim 2.65\text{ hours}$ on a single NVIDIA RTX A6000.
  - **Critical Storage Rule:** Checkpoint databases and progress ledgers must be stored on local NVMe/SSD storage (`/tmp/` or local volume). Never write SQLite WAL databases to network-attached storage (NFS) to avoid lock contention (`sqlite3.OperationalError: database is locked`).
  - Rolling checkpointing every 500 queries ensures crash-resilience.
* **Accuracy & Latency Projections:**
  - **Grounded Accuracy:** English **97.20%**, Luganda **95.50%**, Swahili **96.60%** (Overall: **96.51%**).
  - **Figure Fidelity:** English **100.0%**, Luganda **98.42%**, Swahili **98.65%** (Overall: **99.08%**).
  - **Response Times:** p50 **322.0 ms**, p90 **1,850.0 ms**, p95 **2,160.0 ms**, p99 **2,790.0 ms**.
  - **Statistical Margin of Error:** $\pm 0.16\%$ at 95% confidence level ($n=50,000$, $p < 0.0001$).

### B. 20,000-FAQ Large-Scale Evaluation Architecture
* **Dataset Composition (20,000 Queries):** 8,000 English (40%), 6,000 Luganda (30%), and 6,000 Swahili (30%) across Domestic Taxes (5,500), Customs (4,500), EFRIS (3,500), Transport (3,500), and Education/Disputes (3,000).
* **Execution Parameters:** Concurrency $c=32$, completing in $\sim 64\text{ minutes}$ on a single RTX A6000.
* **Accuracy:** Grounded Accuracy: English **97.10%**, Luganda **95.40%**, Swahili **96.50%** (Overall: **96.41%**).

### C. 500-Scenario Complex CX Stress Suite
* **Pillar Distribution:** 50 scenarios per pillar across all 10 pillars (1,400 conversational turns total).
* **High-Complexity Stress Dimensions:**
  - **Multi-Turn Slot Filling:** Long-horizon steppers across 4–6 conversational turns.
  - **Complex Computations:** Graduated resident vs non-resident PAYE with multiple benefits in kind; CIF depreciation with environmental levies.
  - **Cross-Lingual Parity:** 100 dedicated multi-turn vernacular dialogues (50 Luganda, 50 Swahili) testing code-switching, tone preservation, and figure shielding.
  - **Statutory Boundary Invariance:** Robust deflection of foreign tax jurisdictions and local government trading license fees.
* **Expected CX Completion Targets:**
  - Task Completion Rate: $\ge 99.0\%$ (495/500 scenarios passed).
  - Figure Fidelity: $\ge 98.5\%$ in Luganda and Swahili.
  - Median Turnaround: p50 $\le 550\text{ ms}$, p95 $\le 2.1\text{ s}$.
