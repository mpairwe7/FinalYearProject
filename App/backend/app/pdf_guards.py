"""Shared PDF intake guards for query-time uploads and index-time export.

Untrusted PDFs are a parser, renderer, and prompt-injection surface. These
checks follow current intake practice (OWASP File Upload, CISA/NSA PDF
hardening, OWASP LLM01/LLM04/LLM10, NIST SSDF PW.5):

* require a real ``%PDF-`` header, not a polyglot that buries one later;
* refuse encryption, active content, and embedded files before extract/OCR;
* cap xref objects and page dimensions so a crafted file cannot bomb the
  worker that also serves chat.

Index-time export uses the same inspector in *best-effort* mode: a file that
cannot be opened is left to the chunker (existing behaviour); a file that
opens and fails a guard is skipped instead of being embedded.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ISO 32000 allows optional whitespace before the header. Anything else in
# that prefix is treated as a polyglot (HTML/JS/ZIP smuggled in front).
_MAX_LEADING_WHITESPACE = 8
_PDF_MAGIC = b"%PDF-"

# Action and structure names that imply code execution, local launch, remote
# go-to, form exfil, or hidden payloads. /URI annotations are *not* listed —
# official URA PDFs contain ordinary links.
_DANGEROUS_PDF_TOKEN = re.compile(
    r"/"
    r"(?:JavaScript|JS|Launch|SubmitForm|ImportData|GoToR|RichMedia|EmbeddedFiles|XFA)\b"
)

_DEFAULT_MAX_XREFS = int(os.getenv("DOCUMENT_MAX_PDF_XREFS", "20000"))
_CORPUS_MAX_XREFS = int(os.getenv("PDF_CORPUS_MAX_XREFS", "250000"))
_MAX_PAGE_EDGE_PT = float(os.getenv("DOCUMENT_MAX_PDF_PAGE_EDGE_PT", "14400"))  # 200 in


class PdfRejected(ValueError):
    """The PDF failed a security guard and must not be parsed further."""


class PdfUnavailable(RuntimeError):
    """PyMuPDF is missing; structural inspect cannot run."""


@dataclass(frozen=True)
class PdfGuardLimits:
    """Numeric and policy caps for one intake path."""

    max_xrefs: int = _DEFAULT_MAX_XREFS
    max_page_edge_pt: float = _MAX_PAGE_EDGE_PT
    reject_encrypted: bool = True
    reject_active_content: bool = True
    reject_embedded_files: bool = True
    fail_closed_on_parse: bool = True


QUERY_PDF_LIMITS = PdfGuardLimits()
CORPUS_PDF_LIMITS = PdfGuardLimits(
    max_xrefs=_CORPUS_MAX_XREFS,
    fail_closed_on_parse=False,
)


@dataclass
class PdfInspection:
    """Outcome of a structural inspection. ``findings`` are blocking."""

    page_count: int = 0
    xref_count: int = 0
    encrypted: bool = False
    findings: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_pdf_header(data: bytes) -> None:
    """Require ``%PDF-`` at the start, allowing only a short whitespace prefix."""
    if not data:
        raise PdfRejected("Empty PDF.")
    offset = 0
    limit = min(len(data), _MAX_LEADING_WHITESPACE)
    while offset < limit and data[offset] in b" \t\r\n":
        offset += 1
    if data[offset : offset + 5] != _PDF_MAGIC:
        raise PdfRejected("The file content does not match the .pdf extension.")


def scan_pdf_object_text(*fragments: str) -> list[str]:
    """Return blocking findings from PDF object dictionaries (not page text)."""
    findings: list[str] = []
    seen: set[str] = set()
    for fragment in fragments:
        if not fragment:
            continue
        for match in _DANGEROUS_PDF_TOKEN.finditer(fragment):
            token = match.group(0)
            if token in seen:
                continue
            seen.add(token)
            findings.append(f"PDF contains active or embedded content ({token}).")
    return findings


def _open_pdfium(source: bytes | Path) -> Any:
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]
    except ImportError as err:
        raise PdfUnavailable("pypdfium2 is not installed.") from err
    if isinstance(source, Path):
        with source.open("rb") as f:
            return pdfium.PdfDocument(f.read())
    return pdfium.PdfDocument(source)


def _open_fitz(source: bytes | Path) -> Any:
    try:
        import fitz  # type: ignore[import-untyped]
    except ImportError as err:
        raise PdfUnavailable("PyMuPDF is not installed.") from err
    if isinstance(source, Path):
        return fitz.open(source)
    return fitz.open(stream=source, filetype="pdf")


def _open_document(source: bytes | Path) -> tuple[Any, str]:
    """Open PDF with permissive pypdfium2 first, then PyMuPDF fallback."""
    try:
        return _open_pdfium(source), "pypdfium2"
    except Exception:
        pass
    try:
        return _open_fitz(source), "fitz"
    except Exception:
        pass
    raise PdfUnavailable("No PDF inspection engine (pypdfium2 or PyMuPDF) is installed.")


def inspect_open_pdf(doc: Any, limits: PdfGuardLimits = QUERY_PDF_LIMITS) -> PdfInspection:
    """Inspect an open PDF document (pypdfium2 or PyMuPDF). Does not close it."""
    result = PdfInspection()
    try:
        result.encrypted = bool(getattr(doc, "is_encrypted", False) or getattr(doc, "needs_pass", False))
    except Exception:
        result.encrypted = False
    if result.encrypted and limits.reject_encrypted:
        result.findings.append("Encrypted or password-protected PDFs are not accepted.")

    # Page count across pypdfium2 and PyMuPDF
    try:
        if hasattr(doc, "page_count"):
            result.page_count = int(doc.page_count)
        elif hasattr(doc, "__len__"):
            result.page_count = len(doc)
        else:
            result.page_count = 0
    except Exception:
        result.page_count = 0

    # XRef count (PyMuPDF specific or estimation)
    try:
        if hasattr(doc, "xref_length"):
            result.xref_count = int(doc.xref_length())
        else:
            result.xref_count = result.page_count * 10
    except Exception:
        result.xref_count = 0

    if result.xref_count > limits.max_xrefs:
        result.findings.append(
            f"PDF object count ({result.xref_count}) exceeds the {limits.max_xrefs} processing limit."
        )

    try:
        if hasattr(doc, "embfile_count"):
            embedded = int(doc.embfile_count())
        else:
            embedded = 0
    except Exception:
        embedded = 0
    if embedded and limits.reject_embedded_files:
        result.findings.append("PDFs with embedded files are not accepted.")

    if limits.reject_active_content and hasattr(doc, "xref_object"):
        fragments: list[str] = []
        try:
            catalog_xref = doc.pdf_catalog()
            fragments.append(str(doc.xref_object(catalog_xref)))
        except Exception:
            logger.debug("PDF catalog could not be read", exc_info=True)
        scan_limit = min(result.xref_count, limits.max_xrefs)
        for xref in range(1, scan_limit):
            try:
                fragments.append(str(doc.xref_object(xref)))
            except Exception:
                continue
        result.findings.extend(scan_pdf_object_text(*fragments))

    if result.page_count > 0:
        pages_to_measure = min(result.page_count, 8)
        for index in range(pages_to_measure):
            width_pt, height_pt = 0.0, 0.0
            try:
                page = doc[index]
                if hasattr(page, "rect"):
                    width_pt, height_pt = float(page.rect.width), float(page.rect.height)
                elif hasattr(page, "get_size"):
                    width_pt, height_pt = page.get_size()
            except Exception:
                continue
            if width_pt > limits.max_page_edge_pt or height_pt > limits.max_page_edge_pt:
                result.findings.append(
                    f"PDF page {index + 1} exceeds the {int(limits.max_page_edge_pt)}-point dimension limit."
                )
                break

    return result


def inspect_pdf_bytes(data: bytes, limits: PdfGuardLimits = QUERY_PDF_LIMITS) -> PdfInspection:
    """Validate header and structure. Always closes the temporary handle."""
    validate_pdf_header(data)

    # Active content scan directly on byte stream
    if limits.reject_active_content:
        findings = scan_pdf_object_text(data.decode("latin-1", errors="ignore"))
        if findings:
            raise PdfRejected(findings[0])

    doc = None
    try:
        doc, _engine = _open_document(data)
    except PdfUnavailable:
        inspection = PdfInspection()
        inspection.warnings.append("PyMuPDF is not installed; structural PDF inspect skipped.")
        return inspection
    except PdfRejected:
        raise
    except Exception as err:
        if limits.fail_closed_on_parse:
            raise PdfRejected("The PDF could not be parsed (corrupt or password-protected).") from err
        inspection = PdfInspection()
        inspection.warnings.append("PDF could not be opened for structural inspection.")
        return inspection
    try:
        inspection = inspect_open_pdf(doc, limits)
    finally:
        try:
            if hasattr(doc, "close"):
                doc.close()
        except Exception:
            pass
    if inspection.findings:
        raise PdfRejected(inspection.findings[0])
    return inspection


def inspect_pdf_path(path: Path, limits: PdfGuardLimits = CORPUS_PDF_LIMITS) -> PdfInspection:
    """Inspect a PDF on disk. Best-effort parse unless *limits* say otherwise."""
    try:
        with path.open("rb") as handle:
            header = handle.read(16)
    except OSError as err:
        raise PdfRejected(f"PDF could not be read: {path.name}") from err
    validate_pdf_header(header)

    doc = None
    try:
        doc, _engine = _open_document(path)
    except PdfUnavailable:
        inspection = PdfInspection()
        inspection.warnings.append(f"{path.name}: PyMuPDF is not installed; structural inspect skipped.")
        return inspection
    except PdfRejected:
        raise
    except Exception as err:
        if limits.fail_closed_on_parse:
            raise PdfRejected("The PDF could not be parsed (corrupt or password-protected).") from err
        inspection = PdfInspection()
        inspection.warnings.append(f"{path.name}: could not be opened for structural inspection.")
        return inspection
    try:
        inspection = inspect_open_pdf(doc, limits)
    finally:
        try:
            if hasattr(doc, "close"):
                doc.close()
        except Exception:
            pass
    if inspection.findings:
        raise PdfRejected(inspection.findings[0])
    return inspection
