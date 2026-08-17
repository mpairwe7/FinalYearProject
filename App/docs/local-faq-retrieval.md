# Local FAQ retrieval harness

The retrieval inputs are stored locally under `App/Data`:

- `Data/dataset/`: the 40 curated FAQ CSV source files.
- `Data/faq_jsonl/`: generated canonical FAQ JSONL and a coverage manifest.
- `Data/teacher_qa/`: QA pairs generated from PDFs, normalised from four
  supported JSONL schemas before indexing.
- `Data/pdfs/`: source material for generating/refreshing teacher-QA pairs;
  it must not be embedded directly into the Qdrant FAQ collection.
- `Data/eval/`: RAG and red-team fixtures; it must not be embedded.

Use the local-only Qdrant profile after the corpus has been populated:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  up -d --build redis qdrant api
```

Qdrant is available only on `127.0.0.1:6333`. The API reads validated FAQ JSONL
and teacher-QA JSONL from `/app/Data`, and the generated BM25 state belongs in
`/app/Model` so it is paired with the same local corpus and index build.

## Refresh and index

Generate canonical FAQ JSONL whenever a source CSV changes:

```bash
cd backend
PYTHONPATH=. python -m app.indexer --export-faq-jsonl
```

The export produces one JSONL file per `ura_*_faqs.csv` and
`faq_corpus_manifest.json`. The indexer verifies exact source coverage and
SHA-256 freshness before it can create vectors. Canonical JSONL contains one
record per normalized question: it retains the longest answer, breaking a tie
by source filename and source row, and records every removed duplicate row in
the manifest. The raw CSVs are not changed. It then normalises direct,
Gemma, instruction/output, and Qwen teacher-QA JSONL, deduplicating equivalent
QA pairs while retaining the richest evidence and source-format metadata.

The current local corpus has 487 raw FAQ CSV rows, 480 unique FAQ JSONL rows,
and 6 normalized teacher-QA rows. Thus the vector index contains 486 retrieval
documents plus its internal BM25 binding sentinel.

Start the local services, then recreate the collection:

```bash
docker compose -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  up -d --build redis qdrant api

cd backend
QDRANT_URL=http://127.0.0.1:6333 \
QDRANT_COLLECTION=ura_knowledge_base_jsonl \
PYTHONPATH=. python -m app.indexer --recreate
```

The resulting `ura_knowledge_base_jsonl` collection contains `faq_jsonl` and
`teacher_qa_jsonl` points only. It leaves any legacy collection untouched;
PDFs and `Data/eval/*.jsonl` are rejected by construction.

`HF_HUB_OFFLINE=1` means the required embedding and reranker assets must be
available locally before that rebuild. See
[the retrieval diagnosis](traceability/faq-retrieval-diagnosis-2026-07-21.md)
and the 2026-08-17 serving-path upgrade
[retrieval-agentic-upgrade-2026-08-17](traceability/retrieval-agentic-upgrade-2026-08-17.md)
for the source-policy and data-quality evidence.
