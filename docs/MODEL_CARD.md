# Model Card — URA Chatbot RAG Pipeline

**Aligned with**: EU AI Act Article 53 (2026), ML Model Cards (Mitchell et al., 2019)

---

## Model Details

| Field | Value |
|-------|-------|
| **Name** | URA Chatbot RAG Pipeline |
| **Version** | 1.1.0 |
| **Date** | 2026-04-13 |
| **Type** | Retrieval-Augmented Generation (RAG) |
| **Task** | Tax domain question answering (Uganda Revenue Authority) |
| **Languages** | English (primary), Luganda (secondary) |
| **Developer** | Mpairwe Landwind (Makerere University) |
| **License** | Apache-2.0 (code), Gemma TOU (mobile model) |

### Components

| Component | Model | Parameters | Purpose |
|-----------|-------|------------|---------|
| **LLM (server)** | Qwen/Qwen2.5-3B-Instruct | 3B | Answer generation |
| **LLM (mobile)** | google/gemma-2-2b-it GGUF Q4_K_M | 2B (quantised) | Offline mobile inference |
| **Dense Retriever** | BAAI/bge-m3 | 568M | 1024-dim multilingual embeddings |
| **Sparse Retriever** | BM25 | N/A | Keyword matching |
| **Reranker** | cross-encoder/ms-marco-MiniLM-L-6-v2 | 22M | Passage reranking |
| **ASR (mobile)** | Whisper Small INT8 | 244M (quantised) | On-device speech recognition |
| **TTS (mobile)** | MMS-TTS VITS | ~40M per locale | On-device speech synthesis |

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

### RAG Quality (English, 21 test samples)

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
- **Serving**: FastAPI + Uvicorn (multi-worker)
- **Vector Store**: Qdrant v1.17.1 (HNSW index)
- **Cache**: Redis 7.4 (semantic cache, cosine >= 0.92)
- **Container**: Docker (non-root, read-only filesystem)

### Performance (SLO Targets)
- p95 latency: < 2s for /v1/chat
- Availability: >= 99.9%
- Error rate: < 1%
- Faithfulness p50: > 0.7

### Security
- OWASP LLM Top 10 (2025) controls implemented
- Input guardrails: 11 prompt injection patterns
- Output guardrails: PII redaction, HTML sanitization, system prompt leakage detection
- Supply chain: SHA-pinned GitHub Actions, Trivy scanning, cosign signing

---

## Limitations

1. **Not real-time**: Knowledge base reflects indexed documents, not live URA systems
2. **No account access**: Cannot look up individual tax records or TIN status
3. **Luganda quality**: Lower accuracy due to limited multilingual training data
4. **Context window**: 6144 tokens — very long queries may be truncated
5. **Offline mobile**: On-device Gemma-2B has no retrieval context, reducing accuracy

---

## Updates & Monitoring

- **Continuous evaluation**: RAG metrics computed on every Nth request (configurable)
- **Drift detection**: Faithfulness score monitoring via Prometheus/Grafana
- **Feedback loop**: User thumbs up/down feeds into retriever tuning pipeline
- **Re-indexing**: Triggered via POST /v1/indexing/trigger when URA publishes new content

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
