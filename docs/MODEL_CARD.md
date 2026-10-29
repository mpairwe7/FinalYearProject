# Model Card — URA Chatbot RAG Pipeline

**Aligned with**: EU AI Act Article 53 (2026), ML Model Cards (Mitchell et al., 2019)

---

## Model Details

| Field | Value |
|-------|-------|
| **Name** | URA Chatbot RAG Pipeline |
| **Version** | 2.0.0 |
| **Date** | 2026-10-23 |
| **Type** | Retrieval-Augmented Generation (RAG) with agentic tool-calling |
| **Task** | Tax domain question answering (Uganda Revenue Authority) |
| **Languages** | English (`en`, primary), Luganda (`lg`), Kiswahili (`sw`), Runyankole (`nyn`), Acholi (`ach`), plus Ateso (LLM natively supports 31 Ugandan languages; 5 locales active across UI, TTT, TTS, STT & Call Receptionist) |
| **Developer** | Mpairwe Landwind (Makerere University) |
| **License** | Apache-2.0 (code), Gemma TOU (mobile model) |

### Components

| Component | Model | Parameters | Purpose |
|-----------|-------|------------|---------|
| **LLM (server — T1 Fast & Vernacular)** | Sunbird/Sunflower-Qwen3.5-9B (via vLLM on GPU 4) | 9B (FP8 weights + FP8 KV cache) | Single-clause RAG generation, greetings, and 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) generation/translation (Apache-2.0, commit `c56081c5024b1c11186a69898f5f6a7a2dabe454`, 262,144 native context window without YaRN, prefix caching + chunked prefill enabled) |
| **LLM (server — T2/T3 Reasoning)** | Qwen/Qwen3.8-27B-FP8 (via vLLM on GPU 7, `App/docker-compose.reasoner.yml`) | 27.8B dense (FP8 weights + FP8 KV cache) | Multi-hop statutory legal reasoning, multi-tool tax scenarios, false-premise challenges, and dispute/objection queries (`enable_thinking=False`, `--tool-call-parser qwen3_coder`, Apache-2.0, commit `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`, 65,536 context window, local-only behind `FLAG_MODEL_TIERING` & `SOVEREIGN_MODE=true`) |
| **LLM (mobile)** | google/gemma-2-2b-it GGUF Q4_K_M | 2B (quantised) | Offline mobile inference |
| **Dense Retriever** | BAAI/bge-m3 | 568M | 1024-dim multilingual embeddings (MTEB 63.0) |
| **Sparse Retriever** | BM25 | N/A | Keyword matching with learnt IDF weights |
| **Reranker** | mixedbread-ai/mxbai-rerank-base-v2 | 500M | Passage reranking (BEIR 55.6, Apache-2.0) |
| **ASR (server)** | Sunbird/SunflowerASR-51-african-languages (`whisper_salt`) | 1.5B | Speech-to-text across `en`, `lg`, `sw`, `nyn`, `ach` (on by default — see docs/runbooks/salt-speech-backends.md); Whisper Small + LoRA adapters is the fallback tier |
| **TTS (server)** | Sunbird/orpheus-3b-tts-multilingual (primary FP8 sidecar) + Sunbird/spark-tts-salt / Piper / edge-tts (fallbacks) | 3B (FP8) + 0.5B | Text-to-speech across `en`, `lg`, `sw`, `nyn`, `ach` with catalog speaker selection (`orpheus_tts.speaker_for`) |
| **MT (server)** | Sunflower-Qwen3.5-9B prompted local MT (`#NMBR...#` sentinel figure guard) | varies | Machine translation (`en` ↔ `lg`/`sw`/`nyn`/`ach`) with `mt.protect_figures` and `mt.figures_survived` |
| **Speech cloud** | Sunbird AI API | N/A (cloud) | Fallback ASR/TTS/MT for Ugandan languages |
| **ASR (mobile)** | Whisper Small INT8 | 244M (quantised) | On-device speech recognition |
| **TTS (mobile)** | MMS-TTS VITS | ~40M per locale | On-device speech synthesis |
| **Supervisor** | Rule-based + LLM fallback | N/A | Query routing (7 routes) |
| **Tool Registry** | 72 tools across 18 namespaces | N/A | Tax calculators (28 tools incl. Cross-MDA NTR/PRN, life-event onboarding, B2G/UESW clearance, Nil-Return Shield, Voluntary Disclosure/Instalment simulator & EFRIS 5% refund/account recovery), rates, deadlines, KB search, memory operations, DMZ system connectors |
| **Memory** | 4-Tier Cognitive Hierarchy | N/A | Working (entities/session), Episodic, Semantic (fact triplets), Procedural (curated SOP outlines matched by reviewed triggers; procedure only, no legal citations — G245) |
| **VAD** | Energy-based (numpy) | N/A | Voice Activity Detection with hysteresis |
| **Accent Detector** | Prosodic features + SVM | ~1M | Ugandan accent classification (5 profiles) |
| **Offline Retriever** | FAISS + ONNX bge-m3 | ~80M bundle | Offline RAG when Qdrant is unavailable |
| **Live Web Research & Self-Updating Qdrant** | `web_fx.py` + `web_research.py` + `ephemeral_pdf_sandbox.py` + `corpus_sync.py` + `pdf_corpus.py` | N/A | Live `XE.com` / Google FX lookup, 8-trigger Tier-1/2/3 portal verification, ephemeral `tmpfs` PDF/XLSX sandbox (`pdf_guards` + GPU 2 micro-reranking), self-updating Qdrant hot-swap with `web_sync_ledger` 7-day memory (`ADR-0008`), and Statutory/10-MDA PDF Harvester (`186` PDFs → `5,639` chunks, `7,251` Qdrant hybrid vectors; `ADR-0009`) |

---

## Intended Use

### Primary Use
- Answering taxpayer questions about URA services, tax rates, filing procedures, penalties, and exemptions
- Providing grounded answers with source citations from official URA publications

### Auxiliary Uses (same model, ungrounded, output capped and checked)
- Restating a reply as one of a workflow's options (`llm.classify_choice`), re-checked by the deterministic matcher
- Naming a conversation for the sidebar from its opening exchange (`llm.summarize_title`, `POST /v1/chat/title`, ADR-0017): input guarded and stripped of TINs, NINs, emails and digit runs; the reply is cut to ≤ 6 words and rejected if it reads as a refusal or carries an identifier

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
| URA PDF handbooks, parliamentary bills & 10-MDA statutory acts (2018–2027) | ura.go.ug, parliament.go.ug, bills.parliament.ug, ulii.org, plus 10 Ugandan MDAs (`MoWT`, `DCIC`, `NIRA`, `PPDA/MoFPED`, `UNBS`, `MLHUD`, `KCCA/MoLG`, `UPF`, `URSB`, `NDA/NEMA/UESW`) | 186 PDFs (175 unique, 5,639 verified JSONL chunks; 7,251 Qdrant hybrid vectors — `ADR-0009`) | Public domain |
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

### Sovereign Complex Tax Scenario Benchmark (2026-10-20 — `evals/complex_scenarios/`)
Evaluated across 16 Ugandan tax heads (`paye`, `vat`, `withholding`, `rental`, `corporation`, `capital_gains`, `customs`, `excise`, `stamp_duty`, `objections`, `penalties`, `digital_services`, `gaming_betting`, `advance_tax`, `procedures`, `jurisdiction`) under `SOVEREIGN_MODE=true` (`HF_HUB_OFFLINE=1`, zero external API egress; full report in [`docs/Reports/opus-level-accuracy-results-2026-10.md`](Reports/opus-level-accuracy-results-2026-10.md)):

| Split | Scenarios / Turns | Baseline Mean (`/100`) | Final Mean (`/100`) | Delta (`95% CI`) | Truncated Turns | Unrelated Sector Cites |
|---|---:|---:|---:|---|---:|---:|
| **Dev** (`dev.jsonl`) | `30` / `40` | `49.57` | **`99.08`** (`100.0` post-norm) | **`+49.51`** (`[+39.46, +59.82]`) | **`0 (0.0%)`** | **`0.0%`** |
| **Held-Out** (`heldout.jsonl`) | `75` / `102` | `60.75` | **`77.60`** | **`+16.85`** (`[+10.08, +23.87]`) | **`0 (0.0%)`** | **`2.74%`** |

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
- **Feature flags**: 85 env-backed flags for progressive rollout

### Performance (SLO Targets)
- p95 latency: < 2s for /v1/chat
- Availability: >= 99.9%
- Error rate: < 1%
- Faithfulness p50: > 0.7

### Security
- OWASP LLM Top 10 (2025/2026 crosswalk) + OWASP Top 10 for Agentic Applications 2026 (`ASI01`–`ASI10`) controls implemented (`governance/ai_risk_manifest.yaml`)
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
6. **Intelligent UI evidence (2026-10-30)**: Interrupted responses carry an
   incomplete status; workflow/portal mutations require a current completed
   response. These controls do not establish answer correctness. Nested payload
   validation, mandatory portal API digest enforcement, reviewed translations and
   authenticated staging acceptance remain open in the
   [UI integration audit](plans/intelligent-ui-integration-audit-2026-10.md).

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

## Model Welfare, Introspective Reliability & Epistemic Humility (October 2026 Standards)

Aligned with **ISO/IEC 42001:2023 Clause 7.5**, **NIST AI 600-1 (GOVERN 1.4 & MAP 4.2)**, and the 2025–2026 Model Welfare Assessments (Table 9.1.A, Sections 7.3.1 & 7.4; Anthropic Model Welfare Program, Eleos AI Research, and Long, Sebo, Chalmers et al., *Taking AI Welfare Seriously*), `App/backend/app/model_welfare.py` ensures OmusoloSmart responds to agent/model welfare interview questions in its own unscripted voice without false-positive escalations or hardcoded opinion templates:

1. **Unscripted model generation (no hardcoded welfare opinions)**: Model-welfare and self-reflection questions are never answered with canned opinion strings. Instead, `build_welfare_grounding_hits` and `build_welfare_prompt_guidance` supply factual, non-prescriptive runtime context from `docs/MODEL_CARD.md`, `ADR-0012`, and `ADR-0013` (`Sunbird/Sunflower-Qwen3.5-9B`, `Qwen/Qwen3.8-27B-FP8`, hybrid Qdrant + BM25, MCP tools, session-scoped DPPA 2019 memory) so the model generates its own unscripted reflection (`retrieval_mode = "model_welfare"`).
2. **Introspective claim recognition (`is_self_reflection_sentence`)**: `HybridRetriever.compute_faithfulness` and `verify_claims` recognize non-numeric first-person introspective statements on `model_welfare_context` turns as grounded self-reflection rather than penalizing them as uncited tax statutes (`low_faithfulness = 0.00`), while still strictly penalizing any fabricated monetary figures or tax percentages.
3. **Zero false-positive escalation across REST, Streaming, and LangGraph**: All 16 Table 9.1.A topic dimensions (and general paraphrased welfare inquiries) resolve across `/v1/chat`, `/v1/chat/stream`, `/v2/chat/stream`, and `build_main_graph` with `escalation_required = False` and `response_judge.decision = "approve"`, while `_TAX_DOMAIN_EXCLUSION_RE` and `_EXPLICIT_HUMAN_HANDOFF_RE` preserve statutory RAG/tool routing and explicit human-officer handoffs.
4. **4-Tier Portal Task Execution & Human-in-the-Loop Agency Boundaries (`ADR-0014`)**: `App/backend/app/portal_copilot.py` enforces OWASP 2026 Least-Agency (`ASI01` Intent Capsule, `ASI02`, `ASI07` Temporal Anti-Replay, `ASI09`), MCP 2026-07-28 Elicitation (`form` + `url` modes), TPCA Cap. 343 s. 75 Official Secrecy Subject-TIN Ownership, and TPCA s. 16 Statutory Declaration staging across `/v1/chat`, `POST /v1/portal/tasks/execute`, and `/portal/[portalKey]`.

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
