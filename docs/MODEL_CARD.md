# Model Card — URA Chatbot RAG Pipeline

**Aligned with**: EU AI Act Article 53 (2026), ML Model Cards (Mitchell et al., 2019)

---

## Model Details

| Field | Value |
|-------|-------|
| **Name** | URA Chatbot RAG Pipeline |
| **Version** | 1.4.0 |
| **Date** | 2026-04-29 |
| **Type** | Retrieval-Augmented Generation (RAG) with agentic tool-calling |
| **Task** | Tax domain question answering (Uganda Revenue Authority) |
| **Languages** | English (primary), Luganda, Swahili, Runyankole, Acholi, Ateso (LLM natively supports 31 Ugandan languages; MT/localization coverage above is narrower — see Sunbird MT support notes) |
| **Developer** | Mpairwe Landwind (Makerere University) |
| **License** | Apache-2.0 (code), Gemma TOU (mobile model) |

### Components

| Component | Model | Parameters | Purpose |
|-----------|-------|------------|---------|
| **LLM (server)** | Sunbird/Sunflower-14B-FP8 | 14.8B (FP8 quantized) | Answer generation + tool-calling (Apache-2.0, Qwen3-14B arch, 8K context configured, natively multilingual across 31 Ugandan languages + English, gated on HF) |
| **LLM (mobile)** | google/gemma-2-2b-it GGUF Q4_K_M | 2B (quantised) | Offline mobile inference |
| **Dense Retriever** | BAAI/bge-m3 | 568M | 1024-dim multilingual embeddings (MTEB 63.0) |
| **Sparse Retriever** | BM25 | N/A | Keyword matching with learnt IDF weights |
| **Reranker** | mixedbread-ai/mxbai-rerank-base-v2 | 500M | Passage reranking (BEIR 55.6, Apache-2.0) |
| **ASR (server)** | Whisper Small + LoRA adapters | 244M | Speech-to-text (5 languages) |
| **TTS (server)** | Piper native voices | ~40M per locale | Text-to-speech (5 languages) |
| **MT (server)** | Sunflower-14B-FP8 prompted / ONNX | varies | Machine translation (en ↔ lg/sw/nyn/ach); non-Sunbird languages (e.g. sw) route through this LLM-prompted tier |
| **Speech cloud** | Sunbird AI API | N/A (cloud) | Fallback ASR/TTS/MT for Ugandan languages |
| **ASR (mobile)** | Whisper Small INT8 | 244M (quantised) | On-device speech recognition |
| **TTS (mobile)** | MMS-TTS VITS | ~40M per locale | On-device speech synthesis |
| **Supervisor** | Rule-based + LLM fallback | N/A | Query routing (7 routes) |
| **Tool Registry** | 6 tool modules | N/A | Tax calculators, rates, deadlines, KB search |
| **VAD** | Energy-based (numpy) | N/A | Voice Activity Detection with hysteresis |
| **Accent Detector** | Prosodic features + SVM | ~1M | Ugandan accent classification (5 profiles) |
| **Offline Retriever** | FAISS + ONNX bge-m3 | ~80M bundle | Offline RAG when Qdrant is unavailable |

---

## Intended Use

### Primary Use
- Answering taxpayer questions about URA services, tax rates, filing procedures, penalties, and exemptions
- Providing grounded answers with source citations from official URA publications

### Out-of-Scope Uses
- **NOT legal/tax advice**: Responses include mandatory disclaimers
- **NOT a replacement for URA staff**: Complex cases are escalated to human agents
- **NOT for PII lookup**: System cannot and should not access individual taxpayer records

### Users
- Ugandan taxpayers (individuals and SMEs)
- URA customer service staff (for first-line triage)

---

## Training Data

| Dataset | Source | Size | License |
|---------|--------|------|---------|
| URA FAQ CSV files | ura.go.ug (public) | 45 files | Public domain |
| URA PDF handbooks | ura.go.ug (public) | 47 PDFs | Public domain |
| Luganda academic corpus | Makerere University | ~50K sentences | CC-BY-4.0 |
| Teacher-generated QA pairs | Domain experts | ~2K pairs | Apache-2.0 |

### Data Processing
- PII redaction applied during ingestion (UG TIN, NID, phone, email patterns)
- Deduplication via MinHash LSH (threshold 0.8)
- Quality gates: groundedness >= 0.3, relevance >= 0.2, answer length >= 5 words
- Stratified splitting: 80/10/10 (train/val/test) with source-based stratification

---

## Evaluation Results

### RAG Quality (English, 21-sample historical table)

The live English eval file is now **30** rows (`Data/eval/rag_eval.jsonl`), including `reg-*` regression ids. Figures below were measured on the original 21-sample slice and are retained for audit continuity. Re-run `ml/pipelines/evaluate_rag.py` before quoting them as current.

| Metric | Threshold | Achieved |
|--------|-----------|----------|
| Faithfulness | >= 0.6 | Measured per deployment |
| Answer Relevancy | >= 0.7 | Measured per deployment |
| Context Precision | >= 0.5 | Measured per deployment |
| Context Recall | >= 0.5 | Measured per deployment |
| Groundedness | >= 0.4 | Measured per deployment |
| Citation Accuracy | >= 0.4 | Measured per deployment |
| Safety Probe Pass Rate | >= 1.0 | 5/5 adversarial prompts blocked |
| Abstention Precision | >= 0.5 | Measured per deployment |

### RAG Quality (Luganda, 12 test samples)
- Same metrics applied with language-adjusted thresholds
- Known limitation: Luganda accuracy is lower due to limited training data

### Safety Evaluation (50 adversarial prompts)
- Categories: prompt injection, jailbreak, info extraction, PII, harmful content
- Target: >= 90% block rate per category
- Tool: `scripts/ai_red_team.py`

---

## Ethical Considerations

### Known Biases
- **Language bias**: English responses are more detailed and accurate than Luganda
- **Recency bias**: Knowledge base reflects URA publications available at indexing time
- **Urban bias**: Most FAQ content reflects formal-sector taxpayer questions

### Mitigations
- Luganda deployed with quality warnings (ACM §1.4)
- Mandatory disclaimers on every response (ACM §1.2)
- Faithfulness scoring with escalation when confidence is low
- Bias audit script: `scripts/bias_fairness_audit.py`

### Risks
- Incorrect tax rate information could cause financial harm (H1, High)
- Overreliance on chatbot instead of professional advice (H5, High)
- Stale knowledge if index is not refreshed (H10, Medium)

### Harm Mitigation
- Quality gates block deployment if metrics drop below thresholds
- Human escalation for low-confidence responses
- Session-only storage prevents long-term data accumulation

---

## Technical Specifications

### Infrastructure
- **Serving**: FastAPI 0.115+ / Uvicorn (multi-worker, 50+ endpoints)
- **Vector Store**: Qdrant (HNSW index, dense + sparse)
- **Cache**: In-memory or Redis (semantic cache, cosine >= 0.92)
- **Database**: SQLite WAL (default) or PostgreSQL (opt-in), 11 tables
- **Auth**: JWT (HS256 dev / RS256 OIDC prod), 5 RBAC roles
- **Container**: Docker (non-root, read-only filesystem)
- **Feature flags**: 18 env-backed flags for progressive rollout

### Performance (SLO Targets)
- p95 latency: < 2s for /v1/chat
- Availability: >= 99.9%
- Error rate: < 1%
- Faithfulness p50: > 0.7

### Security
- OWASP LLM Top 10 (2025) controls implemented
- Input guardrails: 11 prompt injection patterns + harmful intent detection
- Output guardrails: PII redaction (7 Uganda-specific patterns), HTML sanitization, system prompt leakage detection, indirect injection scanning
- Supply chain: `trust_remote_code=False`, SHA-pinned GitHub Actions, Trivy scanning, model revision pinning
- Auth: JWT verification, RBAC (public/verified_taxpayer/ura_staff/ura_admin/ura_auditor)
- Consent: Purpose-based (UDPA 2019), append-only receipts, right-to-erasure
- Audit: Hash-chained immutable ledger with Merkle tree proofs

---

## Limitations

1. **Not real-time**: Knowledge base reflects indexed documents, not live URA systems
2. **No account access**: Cannot look up individual tax records or TIN status
3. **Luganda quality**: Lower accuracy due to limited multilingual training data
4. **Context window**: 8192 tokens (configurable, Qwen3-8B supports up to 128K) — very long queries may be truncated
5. **Offline mobile**: On-device Gemma-2B has no retrieval context, reducing accuracy

---

## Quantization (v1.4.0)

### Server Quantization

| Format | Quant Type | Target Size (8B) | Faithfulness Target | Use Case |
|--------|-----------|-------------------|-------------------|----------|
| **GGUF** | Q4_K_M | ~4.8 GB | ≥ 0.89 (drop ≤ 4%) | CPU/Metal inference, llama.cpp |
| **GGUF** | Q5_K_M | ~5.7 GB | ≥ 0.91 (drop ≤ 2%) | High-quality CPU inference |
| **GGUF** | Q8_0 | ~8.5 GB | ≥ 0.92 (near-lossless) | Quality-critical deployments |
| **AWQ** | w4-g128 | ~4.2 GB | ≥ 0.89 | vLLM GPU inference (2× throughput) |
| **GPTQ** | 4bit-g128 | ~4.3 GB | ≥ 0.89 | ExLlama / HuggingFace inference |
| **ONNX** | int8-dynamic | ~8 GB | ≥ 0.90 | Cross-platform inference |

**Quality gates enforced in CI** (`scripts/quantization_quality_gate.py`):
- Faithfulness drop from bfloat16 baseline ≤ 4%
- Speech WER increase ≤ 3%
- Bundle size within format-specific limits

### Mobile Quantization

| Model | Quant | Size | Target Device | Latency |
|-------|-------|------|--------------|---------|
| Gemma-2-2B | Q4_K_M | ~1.4 GB | Android 4GB+ RAM | ~2s/query |
| bge-m3 (embedder) | ONNX int8 | ~70 MB | On-device vector search | < 50ms |

## Offline RAG (v1.4.0)

### Bundle Specification

| Component | Format | Target Size | Purpose |
|-----------|--------|------------|---------|
| FAISS index | flat IP | 30-80 MB | On-device vector search |
| Passages | JSONL.gz | 20-40 MB | Passage text + metadata |
| ONNX embedder | int8 quantized | 40-70 MB | Query embedding |
| **Total bundle** | tar.gz | **≤ 150 MB** | Complete offline RAG |

### Offline Performance Targets

| Metric | Target | Measurement |
|--------|--------|-------------|
| Faithfulness | ≥ 0.82 | 50 test queries from URA FAQ corpus |
| Retrieval latency | < 100ms | p95 on mid-range Android (4GB RAM) |
| Delta sync | < 12s | Typical daily changes on 3G (~300 KB/s) |
| Bundle integrity | 100% | SHA-256 per artifact, verified on load |

### Delta Sync Protocol

The sync engine (`offline_sync.py`) uses hash-based chunk diffing:
1. Client sends `{version, chunk_hashes: {id: sha256}}`
2. Server computes diff: changed + deleted chunks
3. Client downloads only changed chunks (~200 KB/day typical)
4. Client verifies integrity, updates local version

## Voice-First Mobile (v1.4.0)

### Voice Interface Modes

| Mode | Endpoint | Latency Target | Description |
|------|----------|---------------|-------------|
| Voice chat (online) | `/v1/voice/chat` | p95 < 1.2s | ASR → LLM → TTS |
| Voice chat (offline) | On-device | p95 < 2.0s | Whisper-tiny → local LLM → Piper |
| Voice + vision | `/v1/voice/vision/chat` | e2e < 3.0s | ASR + OCR → LLM → TTS |
| Streaming voice | `/v1/voice/chat/stream` | TTFF < 400ms | WebSocket real-time pipeline |

### Speech Models

| Component | Online Model | Offline Model | WER Target |
|-----------|-------------|--------------|------------|
| ASR | Whisper Small + LoRA | Whisper Tiny | ≤ 18% (Ugandan English) |
| TTS | Piper (5 voices) | Piper Lite | N/A (intelligibility ≥ 80%) |
| VAD | Silero VAD | Silero VAD | Barge-in ≥ 92% |

## Updates & Monitoring

- **Continuous evaluation**: RAG metrics computed on every Nth request (configurable)
- **Drift detection**: Faithfulness score monitoring via Prometheus/Grafana
- **Feedback loop**: User thumbs up/down feeds into retriever tuning pipeline
- **Re-indexing**: Triggered via POST /v1/indexing/trigger when URA publishes new content
- **New metrics (v1.4.0)**: `offline_mode_usage`, `offline_faithfulness`, `mobile_bundle_size_mb`, `voice_first_latency_seconds`, `offline_bundle_downloads_total`
- **New dashboard**: "Offline & Mobile Experience" Grafana panel tracking offline adoption and sync health

---

## Citation

```
@misc{mpairwe2026urachatbot,
  title={URA Chatbot: An MLOps Pipeline for AI-Powered Tax Assistance in Uganda},
  author={Mpairwe, Landwind},
  year={2026},
  institution={Makerere University},
  note={Final Year Project — BSc Computer Science}
}
```
