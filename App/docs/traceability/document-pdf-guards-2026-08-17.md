# Document / PDF guards and threat-model update — 2026-08-17

## Scope

This record covers query-time document intake (`POST /v1/documents/analyze`),
index-time PDF export (`pdf_corpus.export_pdf_chunks_to_jsonl`), the OCR
sidecar, and the living STRIDE registry. It extends the 2026-07-21 local
evaluation record; it does not add ClamAV, Presidio, or an `mcp_document_parser`
tool.

Standards used as the intake bar: OWASP File Upload Cheat Sheet, CISA/NSA PDF
hardening (no JS / Launch / embedded files), OWASP LLM Top 10 2025 (LLM01
indirect injection, LLM04 poisoning, LLM06 agency, LLM10 unbounded
consumption), NIST SSDF PW.5 / PW.6.

## Phased change log

| Phase | Outcome | Primary implementation | Evidence |
| --- | --- | --- | --- |
| P0 — PDF structure | Fail-closed header, encryption, active-content, xref, and page-edge checks before extract/OCR. Index-time export skips a file that fails the same inspector. | `backend/app/pdf_guards.py`, `documents.py`, `pdf_corpus.py` | `backend/tests/test_pdf_guards.py` |
| P1 — Office / image | Reject zip-slip, encrypted ZIP, and `vbaProject.bin`; drop `.xlsm`. Pillow pixel cap. OCR sidecar magic-byte reject. | `documents.py`, `ocr_service.py`, `frontend/src/lib/attachments.ts` | Office + filename tests in `test_pdf_guards.py` |
| P2 — Indirect injection | Scrub extracted text with `scan_retrieved_text`; wrap attachment passages in `<untrusted_user_document>`; system rule that uploads are evidence only. | `documents.py`, `llm.py`, `guardrails.py` | Scrub + wrapper tests in `test_pdf_guards.py` / `test_documents.py` |
| P3 — Threat model | Living registry T21–T28; document DFD nodes in pytm; LLM06 now mapped. | `threat-model/validate_threats.py`, `threat-model/tm.py` | `python threat-model/validate_threats.py` |

## Verification (this session)

| Check | Result |
| --- | --- |
| `python threat-model/validate_threats.py` | Re-verified 2026-08-17: PASSED. 28 threats (T01–T28, no duplicate ids), 27 mitigated, 1 accepted (T21). STRIDE 6/6. OWASP LLM 10/10. ATLAS 19 mappings / 10 unique. All evidence paths exist. |
| `tests/test_pdf_guards.py tests/test_pdf_corpus.py` | Re-verified 2026-08-17: **41 passed** (Python 3.12.8). Includes live PyMuPDF cases plus slim-profile skip and inspect-warning propagation onto the analysis record. |
| `tests/test_documents.py` | Still not collected in this environment (`slowapi` missing from the system interpreter). Filename, scrub, wrapper, and `.xlsm` assertions are also in `test_pdf_guards.py`. |
| ClamAV / malware scan | Not claimed. Application-level parsers still share the API worker. |

## Operator notes

- Query-time PDFs are fail-closed. Index-time export is best-effort on
  unreadable bytes (so the existing stubbed corpus tests keep working) and
  skip-on-finding for files PyMuPDF can open.
- `/URI` annotations are allowed. Official URA handbooks contain ordinary
  links. `/JavaScript`, `/Launch`, `/GoToR`, `/SubmitForm`, `/ImportData`,
  `/XFA`, `/RichMedia`, and `/EmbeddedFiles` are not.
- Macro-enabled Office (`.xlsm`, `.docm`, `.pptm`, or `vbaProject.bin` inside
  a `.xlsx`/`.docx`) is rejected. Re-save as `.xlsx` / `.docx`.
- Extracted TINs and amounts stay in the analysis record on purpose. PII
  redaction applies to model output and analytics, not to the taxpayer's own
  upload fields.
