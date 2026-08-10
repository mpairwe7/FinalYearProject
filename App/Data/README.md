# Local retrieval corpus

This directory is the local source-of-truth for the URA retrieval harness.
It is intentionally ignored by Git because it contains source documents and
locally generated index state.

| Directory | Purpose | Intended Qdrant use |
| --- | --- | --- |
| `dataset/` | Curated `ura_*_faqs.csv` Q&A files | Yes |
| `faq_jsonl/` | Canonical per-CSV FAQ JSONL plus coverage manifest | Yes |
| `teacher_qa/` | QA pairs generated from the PDF corpus, in several JSONL schemas | Yes |
| `pdfs/` | Upstream URA guidance used to generate/refresh teacher-QA pairs | No |
| `eval/` | RAG and red-team evaluation fixtures | No |

The backend now defaults to `App/Data` locally. The vector corpus is built from
validated `faq_jsonl/` and normalised `teacher_qa/` rows. `faq_jsonl/` is
generated from every CSV and carries a source hash, stable row id, tag, and row
number; the indexer rejects a missing, partial, or stale export. PDFs are never
embedded directly. Do not copy an old sparse state from another corpus, because
it can map token IDs to incompatible Qdrant sparse vectors.

The canonical FAQ files contain one row per normalized question. If source CSVs
repeat a question, the exporter keeps the longest answer (then source filename
and row as deterministic tie-breakers) and records the retained and removed
source rows in `faq_corpus_manifest.json`; raw CSV inputs are never modified.
