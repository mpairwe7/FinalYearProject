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
- Extracted text is passed through `scan_retrieved_text` (OWASP LLM01) before
  classification. Attachment passages injected into chat are wrapped in
  `<untrusted_user_document>` and treated as evidence, not instructions.
- Records are bound to their upload session and/or authenticated user. A
  legacy record without either binding is never retrievable by document ID.
- Responses include a SHA-256 source fingerprint, extraction method, OCR
  backend/status, page numbers, region count, and mean OCR score. Extracted
  values retain a bounded source pointer (page, OCR box, and region score)
  when they can be matched to OCR text. The classifier score is a keyword
  heuristic, not a probability.

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
in the same Python worker. There is no ClamAV (or equivalent) malware scan
on this path.

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
