# FAQ retrieval diagnosis — 2026-07-21

## Intended source policy

| Source | Intended role | Qdrant eligibility |
| --- | --- | --- |
| `Data/dataset/ura_*_faqs.csv` | Curated authoritative FAQ answers | Include |
| `Data/teacher_qa/*.jsonl` | Teacher QA pairs generated from PDFs | Include after schema normalisation and validation |
| `Data/pdfs/*.pdf` | Upstream material for teacher-QA generation | Exclude |
| `Data/eval/*.jsonl` | Evaluation/red-team cases | Exclude |

## Implemented source-policy correction

The indexer and `/v1/index` now build only validated FAQ JSONL plus normalised
teacher-QA JSONL. A canonical exporter produces one JSONL file for every FAQ
CSV and records source SHA-256, tag, source row, and stable chunk id in each
record. The indexer rejects stale, partial, malformed, or provenance-mismatched
FAQ exports before touching Qdrant.

Teacher-QA direct `question`/`answer`, Gemma prompt text, instruction/output,
and Qwen chat-message rows are normalised and deduplicated. `redteam_corpus`
is not an input directory and its malformed final record remains excluded.

## Local harness evidence

- `App/Data` contains a byte-for-byte local copy of 117 source files from the
  original data directory: 40 CSVs, 69 PDFs, 3 evaluation JSONL files, and 4
  teacher-QA JSONL files.
- The backend now defaults to `App/Data/dataset` and loads 40 FAQ tags / 487
  valid CSV rows without relying on a parent-directory data mount.
- Local Qdrant is bound only to `127.0.0.1:6333`. The successful local rebuild
  writes `Model/bm25_state.json` with a corpus hash and a matching collection
  sentinel, preventing sparse-vector queries from using a mismatched corpus.

## Retrieval quality findings

- CSV structure is clean: 487 loadable rows, no blank Q/A fields, and no extra
  columns. Seven duplicate questions with differing answer text are now removed
  from the canonical vector corpus using the documented deterministic policy;
  their retained and discarded source rows remain auditable in the manifest.
- The keyword intent binder previously over-authorized one-term queries. It now
  allows a one-term definition query only when the FAQ question itself is the
  matching generic definition; bare one-term queries clarify safely.
- There are four byte-identical PDF duplicate groups. They should not affect
  Qdrant once PDFs are excluded, but generation should deduplicate sources so
  identical teacher-QA pairs are not regenerated.

## Local reindex verification

On 2026-07-21, the local-only rebuild completed against
`ura_knowledge_base_jsonl` with the cached BGE-M3 model and no remote model or
vector service:

- 480 `faq_jsonl` points (one for each unique normalized FAQ question across
  all 40 FAQ CSVs; seven duplicate raw rows are excluded from vectors).
- 6 normalised, deduplicated `teacher_qa_jsonl` points.
- 1 BM25 binding sentinel: 487 Qdrant points and 486 indexed vectors total.
- The pre-existing `ura_knowledge_base` collection remains unchanged at 729
  points; the local profile selects the new isolated collection instead.
- A local hybrid query for “Who must register for VAT?” returned the exact
  `ura_vat_faqs.csv` FAQ first, with the BM25 binding check enabled.

The raw source CSVs retain all seven conflicting duplicate rows. The canonical
manifest identifies the retained and removed provenance for editorial review;
the content owner can still reconcile the underlying answer text without
reintroducing duplicate retrieval candidates.

## Hosted deployment verification

Read-only configuration checks on 2026-07-21 found no `QDRANT_URL`,
`QDRANT_API_KEY`, `QDRANT_COLLECTION`, or local data-mount variable on the
Crane Cloud app. It therefore cannot be using a configured remote Qdrant
collection. The Hugging Face Space reports `retrieval_mode: hybrid` and 40 FAQ
tags loaded, but the HF API does not expose a Qdrant secret name or collection
point counts. Secret values are intentionally unreadable.

Consequently, the hosted collection cannot yet be proven to contain the full
480 canonical FAQ JSONL points. Verification requires either a network-reachable
Qdrant endpoint plus a read-only API key, or a protected deployment status
endpoint that returns only collection name, document counts by `doc_type`, and
the BM25 binding hash. No hosted configuration or remote collection was changed
during this check.
