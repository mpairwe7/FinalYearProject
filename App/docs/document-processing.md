# Document processing controls

The document-analysis feature is intentionally bounded and evidence-aware.
It accepts one supported file (`PDF`, `DOCX`, `XLSX`, `CSV`, image, or text)
per request and requires either `X-Session-ID` or an authenticated user.
Macro-enabled Office (`.xlsm`, `.docm`, `.pptm`) is rejected.

## Enforced controls

- 10 MiB source limit; clients must send `Content-Length` when possible.
- The API reads the uploaded file in 64 KiB chunks, caps multipart fields and
  file count, and validates PDF, Office ZIP, and image content before parsing.
- PDF intake (`app/pdf_guards.py`) requires `%PDF-` at the start (short
  whitespace prefix only), refuses encryption, JavaScript / Launch / GoToR /
  XFA / embedded files, and caps xref objects and page dimensions. If PyMuPDF
  is absent (slim Crane Cloud profile), only the header check runs and
  extraction degrades to a warning. Rendered OCR pages are limited by pixels
  and a document-level OCR deadline.
- Office containers are limited by entry count, uncompressed size, and
  compression ratio. Zip-slip paths, encrypted ZIP entries, and
  `vbaProject.bin` macros are rejected.
- Upload filenames are stripped to a basename; NULs and path segments are
  dropped.
- Extracted text (across `documents.py`, `vision/encoder.py`, `voice_stream_v2.py`, and `/v1/voice/vision/chat`) is scrubbed of invisible Unicode control/bidi overrides (`strip_hidden_control_and_bidi`) and passed through `scan_retrieved_text` (OWASP LLM01 / ASI01) before classification or model context injection. Attachment and visual-evidence passages injected into chat are wrapped in `<untrusted_user_document>` and treated as evidence, not instructions.
- Image uploads apply `ImageOps.exif_transpose` before OCR/VLM decoding so rotated phone camera photos (EXIF orientations 3/6/8) are right-side-up. When embedded Python OCR readers (`paddleocr`, `easyocr`, `pytesseract`) are absent in the GPU runtime (`Dockerfile.gpu`), `_extract_image` falls back to the system `tesseract` CLI binary (`ocr_backend="tesseract_cli"`).
- Portal screenshot uploads (`doc_type="portal_screenshot"`) run through `diagnose_portal_screenshot` (`documents.py`) and `diagnose_ura_error` / `extract_task_entities` (`portal_copilot.py`), generating `what_needs_to_be_done`, a `checklist` of `detected` vs `action_needed` items, `recommended_prompts`, and an auth-aware `copilot_handoff` (`inbuilt_portal_url` with `&intent={remediation_task_id}` and `prefill_state`) covering the 8 statutory URA error families, URA's **Apply To Tax Payment In Installment** wizard (Sections B–F), and **EFRIS First-Time Registration / Fiscal Document Validation / Detailed VAT Reports**.
- Records are bound to their upload session and/or authenticated user. A legacy record without either binding is never retrievable by document ID.
- Responses include a SHA-256 source fingerprint, extraction method, OCR backend/status, page numbers, region count, and mean OCR score. Extracted values (including 10-digit `tins`, 14-character `nins`, 12–15 digit `prns`, and `efris_invoices`) retain a bounded source pointer (page, OCR or PDF vector glyph box, and region score).
- Automated tax reconciliation audits commercial documents (invoices, receipts, assessments, WHT certificates) across `UGX` and `USD`: reconciles taxable subtotal, standard 18.0% VAT, zero-rated/exempt (0%) supplies, and Withholding Tax (`6%`/`15%`) net payable equations, cross-checking table column totals against summary totals and routing discrepancies or unbordered fallback tables into `hitl_routing.assess_document_for_human_review`.
- Branded PDF reports (`pdf_export.py`) embed PDF metadata, a deterministic `Report Audit SHA-256` digest, HITL officer review badges, line-item vs. total arithmetic verification warnings on tax summaries, and safe token wrapping for long unbroken strings.
- Interactive taxpayer and officer inspection modal allows inline verification of
  extracted tax fields, arithmetic breakdown, audit notes, and quick suggested
  questions in English, Luganda, and Swahili without requiring a raw PDF download.

Index-time handbook export uses the same PDF inspector in best-effort mode:
a file that opens and fails a guard is skipped rather than embedded.

## Local evaluation setup

For document/PDF evaluation, use the local-only Compose override described in
[local-document-evaluation.md](local-document-evaluation.md). It disables
hosted inference and fallback paths, uses only private Docker networking for
OCR, and keeps document/report state in local container storage.

For high-volume or untrusted uploads, keep parsing/OCR in a separately
resource-limited local worker or sandbox. Application-level limits reduce
exposure, but they cannot forcibly terminate a native parser already executing
in the same Python worker. `malware_scan.py` can INSTREAM the bytes to
clamd (`CLAMD_HOST` / `CLAMD_PORT`). `MALWARE_SCAN_REQUIRED=true`
fail-closes when the daemon is down; the default is fail-open with a
warning. Set `DOCUMENT_PARSE_ISOLATED=true` to run extractors in a
spawned subprocess (`document_worker.py`). That is not a dedicated
parse pool.

## Report exports

Conversation exports are capped at 50 messages / 30,000 characters. Tax
exports accept at most 100 typed `Decimal` line items. All exports are rate
limited, produce stage latency/size metrics, and use a Unicode font when the
runtime provides Noto Sans or DejaVu Sans. If neither is installed, text is
safely replaced instead of causing a server error.

The dated implementation and verification records are:

- [traceability/document-pdf-evaluation-2026-07-21.md](traceability/document-pdf-evaluation-2026-07-21.md)
  — original bounded upload / OCR / local-eval profile.
- [traceability/document-pdf-guards-2026-08-17.md](traceability/document-pdf-guards-2026-08-17.md)
  — structural PDF guards, Office macro/zip-slip, indirect-injection wrap,
  and threat-registry T21–T28.
- [traceability/prototype-production-gates-2026-08-18.md](traceability/prototype-production-gates-2026-08-18.md)
  — production requires `MALWARE_SCAN_REQUIRED` and `DOCUMENT_PARSE_ISOLATED`.
