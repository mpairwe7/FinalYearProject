# Local retrieval corpus

This directory is the local source-of-truth for the URA retrieval harness.
It is intentionally ignored by Git because it contains source documents and
locally generated index state.

| Directory | Purpose | Indexed |
| --- | --- | --- |
| `dataset/` | Curated `ura_*_faqs.csv` Q&A files | via `faq_jsonl/` |
| `faq_jsonl/` | Canonical per-CSV FAQ JSONL plus coverage manifest | Yes |
| `teacher_qa/` | QA pairs generated from the PDF corpus, in several JSONL schemas | Yes |
| `pdfs/` | Upstream URA guidance | via `pdf_jsonl/` |
| `pdf_jsonl/` | Hierarchical PDF chunk JSONL plus coverage manifest | Yes |
| `crawl_jsonl/` | Chunked crawl pages plus coverage manifest (source: `Data/crawl/pages`) | Yes |
| `eval/` | RAG and red-team evaluation fixtures | No |

Every corpus reaches the vector store as **validated JSONL** carrying source
hashes and stable record ids, so the indexer rejects a missing, partial or
stale export instead of silently indexing an incomplete corpus. Raw CSVs, PDFs
and crawl JSON are never embedded directly.

Current size: **7,924 vector documents** — 480 FAQ, 6 teacher-QA, 7,035 PDF
chunks (135 unique PDFs after skipping 11 byte-identical duplicates), 403 crawl
chunks. The single-container deployments serve all of it through an in-image
Qdrant sidecar; see `docs/CLOUDFLARE_FALLBACKS.md` for the tier order.

## Building the corpus

The two chunk corpora need an offline export first: they depend on
`ml/scripts/data_aug/chunkers.py` and, for PDFs, `pymupdf4llm` — neither of
which ships in the serving image. Run these from a source checkout:

```bash
cd App/backend
python -m app.indexer --export-faq-jsonl     # CSV  -> faq_jsonl/
python -m app.indexer --export-pdf-jsonl     # PDFs -> pdf_jsonl/   (minutes)
python -m app.indexer --export-crawl-jsonl   # crawl -> crawl_jsonl/ (seconds)
python -m app.index_lifecycle --rebuild       # safely stage + promote Qdrant
python scripts/reindex_vectorize.py --create # build Vectorize from the same JSONL
```

A corpus with no export is skipped rather than failing, so a FAQ-only
deployment keeps working. `reindex_vectorize.py` is stricter: it refuses to seed
Vectorize from a subset unless `--allow-partial` is passed, because Vectorize is
what serves during a Qdrant outage — seeding it from fewer documents turns an
outage into a silent capability regression.

## PDF and crawl chunking

PDF chunks are cut on the markdown heading hierarchy, keep rate tables atomic,
and carry a contextual prefix (`[Document: … — Part A > 4.3 Certainty]`) that is
embedded with the chunk but excluded from the text shown and cited. The heading
trail is the citation locator: whole-document markdown extraction loses page
numbers, so `page` is empty and `section` addresses the chunk instead.

URA's PDFs encode the period as a glyph that decodes to `U+FFFD`, so extraction
yields `1<?>2` for `1.2` and renders table-of-contents leaders as runs of
replacement characters. `app.pdf_corpus.normalise_extracted_text` repairs both
and the min-chars floor then drops the contents pages, which clean down to
nothing.

Crawl pages are shaped by two measured facts: the median page body is ~156
characters (category, author and pagination stubs), and the same URL is captured
repeatedly across crawls. Only the newest capture per URL above
`CRAWL_MIN_PAGE_CHARS` is exported — about 30 % of the pages, holding ~92 % of
the text.

## Fiscal-year metadata

Every vector document carries a `fiscal_year` (`FY2024-25`), parsed from the
source filename and empty when unknown. **Empty means unknown, never stale** —
roughly two thirds of URA filenames carry no fiscal year at all.

Among near-identical passages the retriever keeps the newer *known* edition
regardless of rank (`app.retriever._dedupe_candidates`). Retrieval scores cannot
distinguish a repealed rate from the one in force, because the FY2023-24 and
FY2025-26 phrasings of a rate table are near-identical text; without this the
oldest handbook in the corpus can silently evict the current one.

## Deduplication

The canonical FAQ files contain one row per normalized question. If source CSVs
repeat a question, the exporter keeps the longest answer (then source filename
and row as deterministic tie-breakers) and records the retained and removed
source rows in `faq_corpus_manifest.json`; raw CSV inputs are never modified.

Do not copy an old sparse state from another corpus: it can map token IDs to
incompatible Qdrant sparse vectors. The indexer stamps the corpus hash and the
embedder identity into the collection, and the retriever verifies both at
startup — a mismatch disables the affected half rather than serving desynced or
wrong-encoder results.
