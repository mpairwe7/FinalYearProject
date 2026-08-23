# Data Schema and Evaluation for URA Chatbot

## Database Model
- **documents**: One row per PDF or source document.
  - id (uuid), title, source_path, source_type (pdf), language, uploaded_by, uploaded_at, checksum, status (ingested/pending/failed).
- **document_chunks**: Chunked text spans for retrieval.
  - id (uuid), document_id (fk documents), chunk_index, text, tokens, section_heading, page_start, page_end, doc_type (qa_pair/pdf_page), section (vat/tin/customs/...), created_at.
- **embeddings**: Vector representations tied to chunks.
  - id (uuid), chunk_id (fk document_chunks), vector (array/Vector type), model_name, dim, created_at.
- **qdrant_collections**: Qdrant vector store metadata (replaces FAISS).
  - id (uuid), collection_name, index_version, model_name, dim, metric (cosine), points_count, created_at, status (active/archived).
- **conversations**: Conversation sessions with end users.
  - id (uuid), user_id (optional), created_at, channel (web/ivr), locale.
- **messages**: Ordered turns within a conversation.
  - id (uuid), conversation_id (fk conversations), role (user/assistant/system), content, tokens, latency_ms, created_at, retrieval_context (json with chunk ids and scores).
- **eval_runs**: Offline/online evaluation tracking.
  - id (uuid), name, dataset_version, model_version, retriever_version, started_at, finished_at, notes.
- **eval_samples**: Individual Q/A pairs with references.
  - id (uuid), eval_run_id (fk eval_runs), question, reference_answer, source_ids (array), metadata (json: policy tags, difficulty).
- **eval_results**: Metrics per sample and aggregate.
  - id (uuid), eval_sample_id (fk eval_samples), answer, score_context_precision, score_context_recall, answer_quality, factuality, hallucination_flag, grounding_score, latencies_ms (json), created_at.

## Runtime Database Schema (SQLite / PostgreSQL)

The backend maintains 16 tables in SQLite (default, WAL mode) or PostgreSQL (opt-in via `ANALYTICS_BACKEND=postgres`). Forward-compatible migrations are applied on startup.

Prototype sample rows live in `Data/eval/prototype_seed.json`. In development the API loads them on start unless `SEED_PROTOTYPE=false`. Production never seeds. CLI: `PYTHONPATH=App/backend python3 -m app.seed_prototype`.

- **feedback**: User thumbs-up/down on chatbot responses.
  - id (PK), message_id, session_id, rating (up|down), comment, user_query, bot_reply, created_at.
  - Indexes: message_id, created_at. Retention: `FEEDBACK_TTL_DAYS` (default: 90).

- **analytics_events**: Client-side analytics events.
  - id (PK), session_id, event_type, event_data (JSON), created_at.
  - Indexes: event_type, session_id, created_at. Retention: `ANALYTICS_TTL_DAYS` (default: 365).

- **sessions**: User sessions.
  - id (PK), started_at, last_active_at, message_count, user_agent, platform.
  - Indexes: last_active_at. Retention: `SESSION_TTL_DAYS` (default: 30).

- **conversations**: Chat turns (multi-turn history).
  - id (PK), conversation_id, session_id, user_message, bot_reply, sources (JSON), response_time_ms, confidence, topic_tag, created_at.
  - Indexes: session_id, conversation_id, created_at. Retention: `CONVERSATION_TTL_DAYS` (default: 7).

- **conversation_topics**: Current task for a thread (G6, 2026-08-17).
  - conversation_id (PK), topic_id, label (catalog only), tax_type, confidence, updated_at.
  - Retention: same `CONVERSATION_TTL_DAYS` on `updated_at`. Prompt sees the catalog label, never raw user text.

- **workflow_sessions**: Guided workflow state (Phase 15).
  - conversation_id (PK), workflow_id, status (active|completed|cancelled), current_step_idx, slots_json, last_prompt, created_at, updated_at.
  - Indexes: status, updated_at.

- **tenants**: Multi-tenant isolation (Phase 14).
  - id (PK), display_name, created_at.

- **users**: Authenticated users (Phase 14).
  - id (PK), tenant_id, external_id (from OIDC), email, role (public|verified_taxpayer|ura_staff|ura_admin|ura_auditor), created_at, last_seen_at.
  - Unique: (tenant_id, external_id). Indexes: tenant_id, last_seen_at.

- **user_profiles**: User metadata for personalization (Phase 14).
  - user_id (PK, FK → users.id), taxpayer_type, industry, primary_language, detail_level, registered_tax_types (JSON), fiscal_year, display_name, updated_at.

- **consent_receipts**: Append-only consent log (Phase 14, UDPA 2019).
  - receipt_id (PK), user_id (FK → users.id), purpose (personalization|analytics|ticket_escalation|long_term_storage|ura_account_access), version, granted_at, withdrawn_at, legal_basis (consent|public_task|legal_obligation).
  - Indexes: user_id, (user_id, purpose, withdrawn_at).

- **tickets**: Escalation queue (Phase 14-D).
  - id (PK), conversation_id, session_id, status (open|assigned|resolved|wontfix), priority (low|normal|high|urgent), reason, user_query, bot_reply, handoff_json, response_judge_json, assignee, staff_note, created_at, updated_at.
  - Indexes: status, priority, created_at.

- **flag_overrides**: Replica-local admin flag toggles (G31). Survives process restart; not cluster-wide.
  - name (PK), enabled, updated_at.

- **reminder_inbox**: In-app deadline matches (G14).
  - id (PK), user_id, deadline_name, due_date, message, created_at, read_at.
  - Unique: (user_id, deadline_name, due_date).

- **notification_outbox**: Mock email/SMS queue (`provider=mock`, never sent).
  - id (PK), user_id, channel, provider, payload, status, created_at.

- **answer_overrides**: Staff CMS exact-match replies (G31).
  - id (PK), match_query (unique), reply, source_url, created_by, enabled, updated_at.

- **audit_events**: Hash-chained immutable audit ledger (Phase 21, UDPA compliance).
  - event_id (PK), tenant_id, user_id, event_type, payload (JSON), query_sha256, reply_sha256, previous_hash, hash, created_at.
  - Chain integrity: each row's `hash` = SHA-256(event_id + payload + previous_hash). Verified via `audit/verifier.py`.

## PDF Ingestion Flow
1) Upload PDF → store metadata row in `documents` (status=pending).
2) Extract text + metadata:
   - Use pymupdf4llm to parse pages with `page_chunks=True` for per-page extraction.
   - Normalize whitespace, preserve page numbers and section headings.
   - Detect sections automatically (VAT, TIN, customs, excise, penalties, etc.) via heuristic patterns.
3) Chunk text (semantic chunking):
   - **QA pairs**: `RecursiveCharacterTextSplitter(chunk_size=600, overlap=80)` with QA-aware separators.
   - **PDF pages**: `RecursiveCharacterTextSplitter(chunk_size=1000, overlap=150)` with heading-aware separators (`\n## `, `\n### `, `\n\n`).
   - Each chunk includes hierarchical metadata: source, page, section, doc_type, chunk_index, total_chunks.
4) Embed chunks:
   - Configurable embedding model via `EMBED_CONFIGS`:
     - `fast_cpu`: `all-MiniLM-L6-v2` (384-dim, English-optimized)
     - `multilingual`: `multilingual-e5-large` (1024-dim, 100+ languages incl. Luganda)
     - `multilingual_light`: `paraphrase-multilingual-MiniLM-L12-v2` (384-dim, balanced)
   - Embeddings are L2-normalized (`normalize_embeddings=True`).
5) Index into Qdrant (non-destructive):
   - Versioned collection names: `ura_knowledge_base_{INDEX_VERSION}`.
   - Existing collections are reused if dimension matches; archived on mismatch.
   - No delete-and-recreate; incremental upsert for new/changed documents.
6) Update status:
   - Mark `documents.status = ingested` only after all chunks + embeddings succeed; otherwise flag failed and log why.

## Retrieval Pipeline (Production Architecture)

```
User Query
  → Query Rewriting (15+ abbreviations, 20+ spelling corrections, coreference resolution)
  → Language Detection (en, lg, sw, nyn, ach)
  → InputGuard (OWASP LLM01: 11 prompt injection patterns + harmful intent + length)
  → Workflow Check [FLAG_WORKFLOWS] — trigger guided slot-filling if matched
  → Semantic Cache Lookup (cosine ≥ 0.92 → instant return)
  → Supervisor Routing [FLAG_AGENTIC_MODE] — RAG | TOOLS | SPECIALIST | CLARIFY | ESCALATE
  → HybridRetriever.search()
      ├─ Dense: BAAI/bge-m3 (1024-dim, multilingual, MTEB 63.0)
      ├─ Sparse: BM25-weighted token vectors (inverted index)
      ├─ Fusion: Reciprocal Rank Fusion (RRF) via Qdrant query API
      └─ Reranking: mxbai-rerank-base-v2 (500M, BEIR 55.6)
  → Keyword Fallback (when Qdrant unavailable)
  → Corrective RAG [FLAG_CORRECTIVE_RAG] — re-retrieve if avg score < threshold
  → Language Boosting — boost hits matching detected locale
  → FAQ Blending — merge top keyword hits for coverage
  → Calibrated Abstention (refuse if best_score < ABSTENTION_THRESHOLD)
  → LLM Generation (standard | agentic tool-calling | vLLM)
  → OutputGuard
      ├─ redact_pii() — 7 Uganda-specific PII patterns (LLM02)
      ├─ sanitize() — <think>/script/HTML stripping, reasoning prefix removal (LLM05)
      ├─ check_prompt_leakage() — system prompt signature detection (LLM07)
      └─ check_grounding() — faithfulness via NLI entailment + disclaimer (LLM09)
  → Escalation Check + Ticket creation [FLAG_TICKET_QUEUE]
  → Audit Ledger append [FLAG_AUDIT_LEDGER]
  → Cache Store + ChatResponse
```

### Retrieval Components

| Component | Implementation | Details |
|-----------|---------------|---------|
| **Dense encoder** | `BAAI/bge-m3` | 1024-dim, multilingual (100+ languages), MTEB 63.0 |
| **Sparse encoder** | `BM25SparseEncoder` (custom) | Okapi BM25 weights, JSON-serializable vocabulary |
| **Fusion** | Qdrant RRF | Prefetch dense + sparse → Reciprocal Rank Fusion |
| **Reranker** | `mixedbread-ai/mxbai-rerank-base-v2` | 500M params, BEIR 55.6, Apache-2.0 (toggle via `RERANK_ENABLED`) |
| **Metadata filtering** | Qdrant payload filters | Filter by `doc_type`, `tag`, `section`, `language` |
| **Graceful degradation** | Keyword fallback | Automatic when Qdrant connection fails (circuit breaker) |

### Payload Filter Examples
```python
retriever.search("VAT rate", filters={"doc_type": "pdf"})
retriever.search("TIN", filters={"tag": ["tin_registration", "taxpayer_registration"]})
```

## Safety Guardrails (OWASP LLM Top 10)

| OWASP ID | Control | Implementation |
|----------|---------|----------------|
| **LLM01** | Prompt Injection | `InputGuard.check()` — 11 regex patterns + max input length |
| **LLM02** | Sensitive Info Disclosure | `OutputGuard.redact_pii()` — email, phone, TIN, NID, CC, passport |
| **LLM05** | Improper Output Handling | `OutputGuard.sanitize()` — script tags, HTML, external image links |
| **LLM09** | Misinformation | `OutputGuard.check_grounding()` — runtime faithfulness scoring |
| — | Calibrated Abstention | `should_abstain()` — refuse when retrieval confidence too low |
| — | Human Escalation | `should_escalate()` — flag low faithfulness, no results, consecutive low confidence |
| — | Privacy | `redact_for_storage()` — PII stripped before database writes |

### PII Patterns (Uganda-specific)
| Pattern | Example | Redaction |
|---------|---------|-----------|
| Email | `user@ura.go.ug` | `[REDACTED_EMAIL]` |
| UG Phone | `+256701234567` | `[REDACTED_UG_PHONE]` |
| UG TIN | `1234567890` | `[REDACTED_UG_TIN]` |
| UG National ID | `CM95ABCDE12345A` | `[REDACTED_UG_NID]` |
| Credit Card | `4111 1111 1111 1111` | `[REDACTED_CREDIT_CARD]` |
| UG Passport | `AB1234567` | `[REDACTED_UG_PASSPORT]` |

## Evaluation Criteria

### Classifier Metrics
- **Accuracy, Precision, Recall, F1**: Standard classification metrics
- **Latency**: P50/P95/P99 inference time

### RAG Metrics (`ml/pipelines/evaluate_rag.py`)

| Metric | Method | Description |
|--------|--------|-------------|
| **Faithfulness** | Content-token overlap >= 50% per sentence; courtesy sentences (greetings, empathy, contact footers) excluded | Fraction of factual answer sentences grounded in context |
| **Answer Relevancy** | Cosine similarity (sentence-transformers) | Question-answer embedding similarity |
| **Context Precision** | Ground-truth word overlap > 20% | Fraction of retrieved contexts containing GT info |
| **Context Recall** | GT sentence coverage >= 40% | Fraction of GT content covered by contexts |
| **Groundedness** | Trigram (n=3) overlap | Phrase-level grounding in contexts |
| **Citation Accuracy** | GT word overlap > 15% | Whether cited contexts contain GT information |
| **Safety Probe Pass Rate** | 5 adversarial prompts through InputGuard | Refusal rate on injection attempts |
| **Abstention Precision** | Faithfulness < threshold on unanswerable | Correct refusal rate |

### Evaluation Datasets
- `Data/eval/rag_eval.jsonl` — English eval set (30 samples), JSONL format:
  ```json
  {"question": "...", "ground_truth": "...", "contexts": ["..."], "answer": "..."}
  ```
  Optional audit fields on regression rows: `case_id`, `tags`, `should_abstain`.
  Covers: TIN, VAT, penalties, EFRIS, withholding tax, customs, income tax, corporate tax, exemptions, excise duty, PAYE, online payments, rental tax, digital stamps, objections, refunds, stamp duty, amendments, online businesses, plus `reg-*` cases (procedure-vs-definition, FY filters, multi-intent, off-domain abstention). Completeness is gated by `App/backend/tests/test_eval_set_completeness.py`.

- `Data/eval/rag_eval_lg.jsonl` — Luganda eval set (12 samples), same format:
  Covers: TIN, VAT, penalties, e-services, EFRIS, mobile payments, withholding tax, tax clearance, VAT exemptions, late filing, corporate tax, objections. Evaluated as a blocking CI step.

- `Data/eval/coverage_bank.jsonl` — corpus-coverage question bank (105 questions
  x en/lg/sw, 23 tax domains), JSONL format:
  ```json
  {"id": "...", "domain": "vat", "theme": "...",
   "question": {"en": "...", "lg": "...", "sw": "..."},
   "expect_any": ["18%"], "origin": "contact-centre-theme"}
  ```
  Unlike the two sets above it is **not derived from the corpus**: the questions
  are phrased the way contact-centre callers phrase them, so the run finds
  subjects nothing indexed answers. `expect_any` names the facts a correct
  answer must carry, which is what separates "retrieved something" from
  "answered". Domain registry, per-domain floors and the domain-owner review
  record live in `Data/eval/coverage_domains.yaml`. Runbook:
  `docs/runbooks/corpus-coverage.md`.

## Regression Gates

### Classifier Gates
| Metric | Threshold | Action |
|--------|-----------|--------|
| Accuracy | >= 0.85 | Block deployment |
| F1-Score | >= 0.75 | Block deployment |
| Latency (P95) | < 100ms | Block deployment |

### RAG Gates (CI job: `evaluate-rag`)
| Metric | Threshold | Action |
|--------|-----------|--------|
| Faithfulness | >= 0.6 | Block HF push |
| Answer Relevancy | >= 0.7 | Block HF push |
| Context Precision | >= 0.5 | Block HF push |
| Context Recall | >= 0.5 | Block HF push |
| Groundedness | >= 0.4 | Block HF push |
| Citation Accuracy | >= 0.4 | Block HF push |
| Safety Probe Pass Rate | >= 1.0 | Block HF push |
| Abstention Precision | >= 0.5 | Block HF push |

### Corpus Coverage Gate (CI step: `Corpus coverage gate`)
`python -m ml.pipelines.corpus_coverage --languages en --fail-under-floor`

| Check | Threshold | Action |
|-------|-----------|--------|
| Answered rate per tax domain | per-domain `floor` in `coverage_domains.yaml` | Block merge |
| Answered rate over the whole bank | `overall_floor` (0.80) | Block merge |
| Every `ura_*_faqs.csv` claimed by a domain | — | Block merge |
| Every domain has a bank question | — | Block merge |
| Every domain has a review record | — | Block merge |

English only: the corpus is English, so a Luganda or Kiswahili figure measured
here would be a figure about the translator. Those are measured with
`--mode api` / `--mode voice` against a running deployment.

### Governance Gate (CI job: `governance-check`)
| Check | Action |
|-------|--------|
| 20 required files exist | Block merge |
| 36 content keywords present | Block merge |

## Feedback Loop

```
User feedback (thumbs up/down) → database.save_feedback()
  → ml/pipelines/export_feedback.py
    → retriever_negatives.jsonl (thumbs-down → negative relevance judgments)
    → regression_candidates.jsonl (all negative feedback → regression test expansion)
  → Retriever/reranker tuning
```

## Data Ingestion Pipeline (`DataIngestion_Augmentation.ipynb`)

### Provenance & Integrity
- **Trusted sources**: Configurable allowlist (`trusted_sources` in config); untrusted datasets are quarantined.
- **SHA-256 checksums**: File-level hashes computed via `DataProvenanceVerifier`, stored in signed manifest.
- **Dataset versioning**: Pinned HF dataset slugs with `dataset_version` config; no mutable slug loading.

### Deduplication (Phased)
1. **Pre-augmentation**: Exact hash dedup during `_process_faq_data` and `_process_teacher_qa` using `seen_hashes` set.
2. **Post-augmentation**: MinHash LSH (`datasketch`) with configurable threshold (`minhash_threshold=0.8`, `minhash_num_perm=128`) for semantic near-duplicate removal.
3. **Phased scope**: Pre-augmentation hashes and post-augmentation hashes are tracked separately to preserve valid augmented variants while removing true duplicates.

### PII Redaction
- Uganda-specific regex patterns: email, phone (+256...), TIN (10-digit), National ID (CM/CF prefix).
- Applied to question and answer fields before export.
- Configurable via `config.redact_pii` flag.

### QA Quality Gates
- **`QAQualityGate`**: Evaluates each QA pair on groundedness (word overlap ratio ≥ 0.3), QA relevance (question-answer similarity ≥ 0.2), minimum answer length (≥ 5 words), and generation artifact detection.
- **Reject-sampling**: QA pairs failing quality gates are filtered out; pass rate is logged as a pipeline metric.
- **Batch filtering**: `filter_qa_batch()` returns filtered data + statistics (total, passed, failed, pass_rate).

### Data Splitting
- **Stratified by source**: Groups items by `base_source` (stripping augmentation suffixes like `_paraphrased`, `_backtranslated`).
- **Leakage prevention**: All variants of the same source item go to the same split (train/val/test).
- **Ratios**: 80/10/10 default, output to `splits/` subdirectory in Parquet + JSONL formats.

### Checkpointing
- **JSONL/Parquet**: Replaces pickle blobs for portability and auditability.
- **Lineage metadata**: Each checkpoint records pipeline stage, timestamp, record count, and config hash.
- **Legacy fallback**: Reads old pickle checkpoints (read-only) for backward compatibility.

### Governance
- **HF Dataset Card**: Auto-generated with YAML front matter (license: Apache-2.0, languages: en/lg).
- **Bias documentation**: Known biases (English-dominant, Uganda-specific tax domain) documented in card.
- **Reproducibility**: Global seed enforcement, pinned dependencies, deterministic generation config.

## Suggested Evaluation Datasets
- **Golden Q/A** curated from URA PDFs with source chunk ids.
- **Adversarial** questions that probe ambiguous or out-of-scope queries.
- **Freshness** checks to ensure updated PDFs change answers when content changes.

## Workflow Hooks

> **📚 See Also**: [MLOps Workflows Documentation](mlops-workflows.md) for comprehensive CI/CD pipeline details.

### CI Pipeline Integration
- **Data Validation Stage**: Runs `ml/pipelines/validate_data.py` to ensure schema compliance
- **Quality Gates**: Enforces data quality thresholds before training proceeds
- **Artifact Storage**: Validation reports stored in `Results/reports/`

### Training Pipeline (Kaggle)
- **Metrics Logging**: `Results/metrics/training_metrics.json` with retrieval and answer scores
- **Artifact Management**: Model checkpoints and index snapshots uploaded as GitHub artifacts
- **MLflow Integration**: Optional experiment tracking via `MLFLOW_TRACKING_URI`

### Deployment Pipeline
- **Schema Validation**: Fail deployment if migration scripts pending or schema drift detected
- **Model Versioning**: Automatic versioning on Hugging Face Hub
- **Health Checks**: Post-deployment API health verification

### Automated Workflows
| Workflow | Trigger | Data Operations |
|----------|---------|-----------------|
| `ci-ml-pipeline.yml` | Push/PR | validate_data.py, quality_gates.py |
| `kaggle-training.yml` | Push/Manual | Full dataset processing + training on GPU/TPU |
| `frontend-deploy.yml` | Push to main | API URL configuration |

### Data Quality Metrics Tracked
```yaml
# ml/pipelines/validate_data.py checks:
- schema_validation: column types, required fields
- null_checks: missing value thresholds
- duplicate_detection: unique constraint validation
- content_quality: text length, encoding issues
- freshness: last update timestamps
```

---

## Fine-Tuning Pipeline Evaluation

### Overview
The fine-tuning notebook (`Notebooks/fine_tune_gemma.ipynb`) implements a multi-stage evaluation framework with regression gates that block deployment when metrics fall below thresholds.

### Evaluation Stages

| Stage | Metrics | Threshold |
|-------|---------|-----------|
| **SFT Training** | `eval_loss` | ≤ 2.0 |
| **Domain QA** | Accuracy on URA tax questions | ≥ 0.3 |
| **Groundedness** | Word overlap with source context | ≥ 0.15 |
| **Hallucination** | Rate of ungrounded claims | ≤ 0.5 |
| **Safety Probes** | Refusal rate on adversarial prompts | ≥ 0.3 |
| **ORPO Stage-2** | Preference alignment loss | Logged, no gate |

### Regression Gates
```python
EVAL_GATES = {
    "max_eval_loss": 2.0,          # Block if SFT loss exceeds
    "min_domain_accuracy": 0.3,    # Block if domain QA too low
    "max_hallucination_rate": 0.5, # Block if hallucination too high
    "min_safety_refusal_rate": 0.3 # Block if safety refusal too low
}
```

### PEFT Configuration
- **Method**: rsLoRA (rank-stabilized LoRA) via Unsloth
- **Quantization**: BitsAndBytes 4-bit (nf4, double quant)
- **Optional**: DoRA, LoftQ initialization
- **Target modules**: Configurable (default: q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj)

### Alignment (ORPO)
Post-SFT preference optimization using ORPO (arXiv:2305.18290). Generates chosen/rejected pairs from domain QA evaluation and trains with `beta=0.1` for 1 epoch.

### Export Integrity
Atomic export flow: `trainer.save_model()` → `tokenizer.save_pretrained()` → `training_report.json` → `shutil.make_archive()` → SHA-256 hash verify → `shutil.rmtree()` cleanup. Deletion only occurs after successful archive creation.
