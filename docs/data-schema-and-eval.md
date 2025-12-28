# Data Schema and Evaluation for URA Chatbot

## Database Model
- **documents**: One row per PDF or source document.
  - id (uuid), title, source_path, source_type (pdf), language, uploaded_by, uploaded_at, checksum, status (ingested/pending/failed).
- **document_chunks**: Chunked text spans for retrieval.
  - id (uuid), document_id (fk documents), chunk_index, text, tokens, section_heading, page_start, page_end, created_at.
- **embeddings**: Vector representations tied to chunks.
  - id (uuid), chunk_id (fk document_chunks), vector (array/Vector type), model_name, dim, created_at.
- **faiss_index_metadata** (optional if using external vector store):
  - id (uuid), index_uri, index_type (faiss/hnsw), model_name, dim, metric (cosine/dot), last_built_at, doc_count.
- **conversations**: Conversation sessions with end users.
  - id (uuid), user_id (optional), created_at, channel (web/ivr), locale.
- **messages**: Ordered turns within a conversation.
  - id (uuid), conversation_id (fk conversations), role (user/assistant/system), content, tokens, latency_ms, created_at, retrieval_context (json with chunk ids and scores).
- **eval_runs**: Offline/online evaluation tracking.
  - id (uuid), name, dataset_version, model_version, retriever_version, started_at, finished_at, notes.
- **eval_samples**: Individual Q/A pairs with references.
  - id (uuid), eval_run_id (fk eval_runs), question, reference_answer, source_ids (array), metadata (json: policy tags, difficulty).
- **eval_results**: Metrics per sample and aggregate.
  - id (uuid), eval_sample_id (fk eval_samples), answer, score_context_precision, score_context_recall, answer_quality, factuality, hallucination_flag, latencies_ms (json), created_at.

## PDF Ingestion Flow
1) Upload PDF -> store metadata row in `documents` (status=pending).
2) Extract text + metadata:
   - Use pdfminer/pymupdf to parse pages.
   - Normalize whitespace, keep page numbers and section headings if available.
3) Chunk text:
   - Sliding window with overlap (e.g., 500 tokens window, 50 overlap) -> insert rows in `document_chunks`.
4) Embed chunks:
   - Call embedding model (e.g., OpenAI text-embedding-3-large or local model) -> write rows in `embeddings` (and update vector index).
5) Update status:
   - Mark `documents.status = ingested` only after all chunks + embeddings succeed; otherwise flag failed and log why.

## Evaluation Criteria
- **Retrieval**: context_precision, context_recall, MRR@k, Recall@k using reference chunk ids.
- **Answer Quality**: LLM-judge or rubric scoring for faithfulness, helpfulness, completeness (0-5 scale per dimension).
- **Factuality**: hallucination_flag (boolean) + fact_score (0-1) derived from groundedness checks.
- **Latency**: total latency, retrieval latency, generation latency (ms) stored per message and aggregated per eval_run.
- **Safety/Policy**: tag unsafe outputs and count violations per eval_run.

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
| `kaggle-training.yml` | Schedule/Manual | Full dataset processing on GPU |
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
