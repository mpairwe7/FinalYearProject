# ADR-0026: Docling RAG Document Intelligence, Multi-Column Reading Order, Table Triplet Serialization & Page/Shadow Provenance (G441–G450)

## Status

Accepted — 2026-11-06

## Context and Problem Statement

As a high-stakes government tax administration platform serving the Uganda Revenue Authority (URA), the assistant ingests statutory Acts, Parliamentary Tax Bills, Uganda Gazette notices, 10-MDA fee schedules (`Data/pdfs/`), live-researched URA Public Notices (`ephemeral_pdf_sandbox.py`), and taxpayer-uploaded receipts, invoices, and assessment notices (`documents.py`).

A November 2026 QA and audit review benchmarked against **IBM Docling** (Livathinos et al. 2025; Heron Layout Model `arXiv:2509.11720`; Granite-Docling; `DoclingDocument`; `HybridChunker`; `TableFormer`; `TripletTableSerializer`), **Citation-Enforced Fiscal RAG (`arXiv:2603.14170`)**, and **HierFinRAG (2026)** identified 10 concrete document-intelligence gaps (`G441`–`G450`) plus a `PyMuPDF` `import fitz` deprecation across the codebase:

1. **`G441` — Missing Unified Docling IR & Duplicate Table-Text Emission in Corpus Extraction**: `ml/scripts/data_aug/chunkers.py` (`extract_markdown`) called `page.extract_text()` (which extracted unformatted table cell text inline) and then appended `page.extract_tables()` at the bottom of the page, duplicating every table twice per page. Runtime and offline pipelines also lacked a unified `DoclingDocument` adapter (`docling_intelligence.py`).
2. **`G442` — Multi-Column Reading-Order Corruption**: `glyph_fusion.py` (`fuse_page_glyphs_and_ocr`) concatenated tokens in raw stream order and appended `ocr_inset` tokens at the end of the page, causing horizontal cross-gutter line interleaving on two-column Uganda Gazette notices and statutory bills.
3. **`G443` — `ContentLayer.FURNITURE` Contamination**: Running headers, footers, page numbers (`14 | P a g e`, `Page X of Y`, `THE UGANDA GAZETTE`), and recurring top/bottom margin chrome were mixed into body text, contaminating retrieval chunks and splitting multi-page sentences and tables.
4. **`G444` — Loss of Page Provenance (`page: ""`) in `pdf_corpus.py` and `chunkers.py`**: Whole-document markdown extraction discarded page boundaries, leaving every `pdf_chunk` point in Qdrant with `"page": ""`.
5. **`G445` — Truncated 5-Page Vector Glyph Evidence & Missing Attachment Page Locators**: `documents._extract_pdf` collected vector glyph bounding-box evidence only for the first 5 pages (missing identifiers on pages 6–40), and `attachment_passages()` hardcoded `"page": ""`.
6. **`G446` — Oversized Markdown Table Destruction & Cross-Page Table Fragmentation**: `_recursive_chunk` in `chunkers.py` ran sentence-splitting and raw character slicing on Markdown tables exceeding `hard_max_chars` (4000 chars), joining rows with spaces and losing column headers on continuation chunks; multi-page tables split across page breaks (`---`) were not stitched.
7. **`G447` — Naive Fixed-Window Slicing in `ephemeral_pdf_sandbox.py`**: `_chunk_pages` sliced pages at fixed 900-character offsets, cutting mid-word, mid-UGX-amount, and mid-table-row, and `_extract_pdf_pages` ignored vector tables.
8. **`G448` — Missing Merged-Cell (`col_span`) Header Propagation, Cell Bounding Boxes, Scanned-PDF Table Structuring & Docling `TripletTableSerializer`**: `detect_pdf_vector_table_grid` hardcoded cell `bbox=[0,0,0,0]` and turned horizontal merged header cells (`None`) into empty strings `""`; `StructuredTable` and `TableSummary` lacked Docling `TripletTableSerializer` semantic triplets (`[Table | RowKey] ColHeader = Value`); and scanned PDFs (`ocr_used=True`) passed empty `raw_text` to `structure_document_tables`.
9. **`G449` — Table Cell Pipe-Spoofing & Prompt-Injection Sanitization**: `StructuredTable.to_markdown()` and `documents._summarize_table()` did not escape internal `|` pipes or scrub bidi/control characters and prompt-injection payloads inside table cells.
10. **`G450` — Unaudited Vector-vs-OCR Shadow Discrepancies in HITL Routing**: `fuse_page_glyphs_and_ocr` flagged `consistent=False` when embedded PDF vector text disagreed with visual OCR on the same bounding box (a classic font-remapping or PDF shadow-layer forgery vector), but `documents.py` and `hitl_routing.py` never escalated the discrepancy.

## Decision Outcome

We remediate all 10 gaps (`G441`–`G450`) and complete the `import pymupdf` migration in five phases:

1. **Phase 1 — Docling Unified IR, `ContentLayer.FURNITURE` Segregation & 2D XY-Cut Reading Order (`G441`, `G442`, `G443`)**:
   - Added `App/backend/app/docling_intelligence.py` implementing `DoclingDocumentIR`, `DoclingItem`, `DoclingProvenance`, `ContentLayer` (`BODY` vs `FURNITURE`), `convert_document_bytes()` (supporting native `docling.document_converter.DocumentConverter` when installed with zero-crash fallback to `native_structural`), `strip_document_furniture()`, and `sort_spatial_reading_order()`.
   - Updated `ml/scripts/data_aug/chunkers.py` (`extract_markdown`) to filter out characters inside `page.find_tables()` bounding boxes (`_char_outside_bboxes`) before extracting prose text, eliminating duplicate table emission.
   - Wired 2D XY-Cut reading-order sorting into `fuse_page_glyphs_and_ocr()` (`glyph_fusion.py`) with an empty-gutter width check (`gutter_gap >= max(24.0, eff_width * 0.08)`) so two-column pages read column-by-column while single-column word streams stay intact.
2. **Phase 2 — Row-Safe Table Chunking, Cross-Page Table Stitching, Merged Headers & `TripletTableSerializer` (`G446`, `G448`)**:
   - Added `_split_markdown_table_rows()` and `_stitch_cross_page_tables()` in `ml/scripts/data_aug/chunkers.py` (and `split_markdown_table_preserving_headers()` / `stitch_cross_page_markdown_tables()` in `docling_intelligence.py`) so oversized tables split strictly on row boundaries with the 2-line Markdown header repeated on every chunk, and continuation tables across page breaks merge cleanly.
   - Enhanced `detect_pdf_vector_table_grid()` (`vision/table_structuring.py`) to forward-fill horizontal merged header cells (`propagate_merged_table_headers`), record `col_span`, and recover cell bounding boxes from `find_tables()`.
   - Added `serialize_table_to_triplets()` (`StructuredTable.to_triplets()` and `TableSummary.triplets`), injecting semantic row-column-value triplets into `DocumentRecord.passage_text()`, and wired OCR evidence into `structure_document_tables` for scanned PDFs.
3. **Phase 3 — End-to-End Page & Bounding-Box Provenance (`G444`, `G445`)**:
   - `extract_markdown()` stamps `<!-- page:N -->` markers; `chunk_markdown()` resolves `Chunk.page_numbers` and `Chunk.page_range` while stripping markers from `Chunk.text` (preserving `chunk_id` hash stability); and `pdf_corpus.py` (`_chunk_pdf_records`, `ingest_pdf_jsonls`) persists `"page"` into Qdrant vector payloads.
   - `documents._extract_pdf()` collects numeric/identifier vector glyph evidence across all 40 pages (`_MAX_PDF_PAGES`) and `attachment_passages()` populates `"page"` locators.
4. **Phase 4 — Structure-Aware Ephemeral PDF Sandbox Chunking (`G447`)**:
   - `ephemeral_pdf_sandbox.py` (`_finalize_extracted_pdf_pages`, `_split_page_text_structure_aware`) strips furniture, appends structured vector tables, and splits pages on paragraph/sentence/table-row boundaries with header repetition.
5. **Phase 5 — Table Cell Sanitization, Shadow-Layer Forgery Detection & `import pymupdf` (`G449`, `G450`)**:
   - `sanitize_table_cell_text()` strips hidden bidi/control chars, scrubs prompt injections (`scan_retrieved_text`), and escapes internal `|` pipes in `StructuredTable.to_markdown()` and `documents._summarize_table()`.
   - `summarize_fusion_discrepancies()` (`glyph_fusion.py`), `documents._extract_pdf()`, and `assess_document_for_human_review()` (`hitl_routing.py`) flag overlapping vector-vs-OCR mismatches (`glyph_ocr_discrepancies`) and escalate to `priority="high"`, `suggested_action="urgent_audit"`.
   - Replaced all deprecated `import fitz` statements with `import pymupdf` across `App/backend/`, `ml/`, and `scripts/`.

## Consequences

- **Positive**: Eliminates table duplication and mid-row table slicing in the statutory PDF corpus, restores 1-based page provenance across PDF chunks and attachments, prevents multi-column cross-gutter scrambling, enriches table retrieval with Docling semantic triplets, detects vector-vs-OCR PDF shadow tampering, and eliminates `fitz` deprecation warnings.
- **Negative / Trade-offs**: Full corpus page numbers in Qdrant populate as PDFs are re-exported via `python -m app.indexer --export-pdf-jsonl`; existing JSONL files remain valid and backward-compatible without forced re-export.

## Confirmation

Verified by `tests/agents/test_docling_rag_document_intelligence_2026.py`, `App/backend/tests/test_documents.py`, `App/backend/tests/test_pdf_corpus.py`, `tests/agents/test_ephemeral_pdf_sandbox.py`, `tests/test_data_augmentation.py`, `governance/compliance_check.py`, and `threat-model/validate_threats.py`.
