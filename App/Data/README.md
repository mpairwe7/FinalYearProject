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
| `dense/` | The local dense leg's pinned model and corpus vectors (`manifest.json` lists each vector's Qdrant point id), built in CI by `python -m app.vectorstore.vector_db build-artifact`; an empty directory is enough for a local image build (ADR-0034) | Loaded into pgvector on the HF Space |
| `eval/` | RAG and red-team evaluation fixtures | No |

Every corpus reaches the vector store as **validated JSONL** carrying source
hashes and stable record ids, so the indexer rejects a missing, partial or
stale export instead of silently indexing an incomplete corpus. Raw CSVs, PDFs
and crawl JSON are never embedded directly.

Current size: **7,251 vector documents** — 1,117 FAQ, 6 teacher-QA, 5,639 PDF
chunks (175 unique PDFs across `186` total sources after skipping 11 byte-identical duplicates — including all 8 Parliament 2026 Tax Bills, 19 URA Laws & Acts compendiums, and 15 statutory/fee/PRN guides across the 10 high-dependency Ugandan MDAs; see `docs/adr/0009-statutory-and-10-mda-pdf-harvester-and-qdrant-indexing.md`), 489 crawl
chunks. The single-container deployments serve all of it through an in-image
Qdrant sidecar; see `docs/CLOUDFLARE_FALLBACKS.md` for the tier order.

## Building the corpus

The two chunk corpora need an offline export first: they depend on
`ml/scripts/data_aug/chunkers.py` and, for PDFs, `pypdfium2` / `pymupdf4llm` — neither of
which ships in the serving image. Run these from a source checkout:

```bash
cd App/backend
python -m app.ingestion.indexer --download-pdfs --export-pdf-jsonl \
  --pdf-source-urls "https://www.parliament.go.ug/page/tax-bills-2026,https://ura.go.ug/download-category/laws-and-acts/"
python -m app.ingestion.indexer --export-faq-jsonl     # CSV  -> faq_jsonl/
python -m app.ingestion.indexer --export-pdf-jsonl     # PDFs -> pdf_jsonl/   (minutes)
python -m app.ingestion.indexer --export-crawl-jsonl   # crawl -> crawl_jsonl/ (seconds)
python -m app.vectorstore.index_lifecycle --rebuild       # safely stage + promote Qdrant
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
replacement characters. `app.ingestion.pdf_corpus.normalise_extracted_text` repairs both
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
regardless of rank (`app.rag.retriever._dedupe_candidates`). Retrieval scores cannot
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

## Source corpora

Canonical datasets live in `App/Data/` (capital D) for historical CI paths.
Point new eval and crawl jobs at `App/Data/eval` and `Data/crawl_jsonl`.
`App/Data/crawl/pages/*.json` are content-addressed captures of URA's live site, and
`App/Data/crawl/crawl_state.json` is the ledger that says which capture each URL stands on,
when the site last confirmed it, and what each run did. Neither is edited by hand:
changes arrive as a reviewed pull request (ADR-0023, `docs/runbooks/crawl-operations.md`).
`App/Data/eval/prototype_seed.json` is the local demo fixture (`SEED_PROTOTYPE`, never production).

## Statutory & 11-MDA PDF Corpus Traceability (`App/Data/pdfs/` → `App/Data/pdf_jsonl/`)

`App/Data/pdfs/` tracks **173 official PDFs** (plus 4 Word and Excel forms), each unique by SHA-256 (11 byte-identical copies stored under a second name were removed on 2026-11-18, ADR-0064; the export had already skipped them), exported into `5,639` hierarchical chunks in `App/Data/pdf_jsonl/pdf_corpus_manifest.json` and indexed into Qdrant `ura_knowledge_base_jsonl_active`):
- **Parliament of Uganda (`https://www.parliament.go.ug/page/tax-bills-2026`)**: All 8 FY2026-27 Tax Amendment Bills (`Income Tax`, `Value Added Tax`, `Excise Duty`, `Tax Procedures Code`, `Stamp Duty`, `Lotteries and Gaming`, `External Trade`, and `Traffic and Road Safety`).
- **URA Laws, Acts & Regulations (`https://ura.go.ug/download-category/laws-and-acts/` + `?cp=2`)**: All 19 consolidated Domestic Tax & Customs Acts, Regulations, EAC CET, and COMESA Rules of Origin compendiums.
- **11 High-Dependency Ugandan MDAs (`MDA_STATUTORY_PDF_REGISTRY` in `App/backend/app/ingestion/pdf_corpus.py`; see `docs/adr/0009-statutory-and-10-mda-pdf-harvester-and-qdrant-indexing.md`)**:
  1. `MoWT` (`MoWT-Traffic-and-Road-Safety-Act-Cap-361.pdf`)
  2. `DCIC` (`DCIC-Uganda-Citizenship-and-Immigration-Control-Act-Cap-313.pdf`, `DCIC-Uganda-Citizenship-and-Immigration-Control-Fees-Regulations-2021.pdf`)
  3. `NIRA` (`NIRA-Summary-of-Registration-of-Persons-Fees-2024-FY2024-25.pdf`, `NIRA-Registration-of-Persons-Act-2015.pdf`)
  4. `PPDA / MoFPED` (`PPDA-Public-Procurement-and-Disposal-of-Public-Assets-Act.pdf`)
  5. `UNBS` (`UNBS-Uganda-National-Bureau-of-Standards-Act.pdf`, `UNBS-E-Portal-ASYCUDA-and-Compulsory-Standards-Notice-FY2026-27.pdf`, `UNBS-PVoC-Import-Clearance-Scheme-FAQs-FY2025-26.pdf`)
  6. `MLHUD` (`MLHUD-Registration-of-Titles-Act-Cap-230.pdf`)
  7. `KCCA & MoLG` (`KCCA-Trade-Licensing-Act-Cap-79.pdf`, `MOLG-Local-Governments-Act-Cap-243.pdf`)
  8. `UPF` (`UPF-Traffic-and-Road-Safety-Speed-of-Motor-Vehicles-Order-2026-FY2026-27.pdf`, `MoWT-UPF-Roads-Act-2019-Express-Penalty.pdf`)
  9. `URSB` (`URSB-Companies-Act-2012.pdf`, `URSB-Business-Names-Registration-Amendment-Rules-2024-FY2024-25.pdf`, `URSB-Trademarks-Schedule-Fees-2025-FY2025-26.pdf`, `URSB-Client-Charter-2025-2030-FY2025-26.pdf`)
  10. `NDA / NEMA / UESW` (`NDA-National-Drug-Policy-and-Authority-Act.pdf`, `NEMA-Technical-Guide-URA-PRN-EIA-Fees.pdf`)
  11. `NITA-U / PDPO` (`NITA-U-National-Information-Technology-Authority-Uganda-Act-2009.pdf`, `NITA-U-PDPO-Data-Protection-and-Privacy-Act-2019.pdf`)
