"""Document attachment processing — extraction, classification, analysis (2026).

Handles chat attachments (PDF, DOCX, XLSX/CSV, images, plain text): extracts
text and tables per file type, classifies the document against the URA
taxonomy (``vision.document_classifier``), pulls URA-specific fields (TINs,
UGX amounts, dates, reference numbers via ``vision.ocr``), and builds a
structured analysis that feeds chat grounding and PDF report generation.

Storage is a TTL registry keyed by an unguessable id: an in-process dict
fast path backed by an ephemeral JSON file store in the container's temp
dir, so all uvicorn workers of one container see the same documents
(``UVICORN_WORKERS=2`` in the deployed image — uploads and chat turns land
on different workers). Documents are transient chat context: they expire
after the TTL, die with the container, and are never written to the
analytics database.

All third-party extractors (PyMuPDF, python-docx, openpyxl, Pillow, OCR)
are lazy-imported and failure-guarded so the slim Crane Cloud profile
degrades to a warning instead of a crash, matching the rest of the app.

Endpoints (see ``main.py``):
    POST /v1/documents/analyze          → DocumentAnalysisResponse
    GET  /v1/documents/{doc_id}/report  → branded PDF bytes
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .vision.document_classifier import classify_document
from .vision.ocr import (
    clean_ocr_text,
    extract_dates,
    extract_reference_numbers,
    extract_tin_numbers,
    extract_ugx_amounts,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Limits & configuration
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = int(os.getenv("DOCUMENT_MAX_BYTES", str(10 * 1024 * 1024)))
DOCUMENT_TTL_SECONDS = int(os.getenv("DOCUMENT_TTL_SECONDS", "7200"))
DOCUMENT_REGISTRY_MAX = int(os.getenv("DOCUMENT_REGISTRY_MAX", "200"))
MAX_ATTACHMENTS_PER_TURN = 3

_MAX_TEXT_CHARS = 20_000
_MAX_PDF_PAGES = 40
_MAX_SHEETS = 5
_MAX_TABLE_ROWS = 200
_MAX_TABLE_COLS = 30
_MAX_TEXT_ROWS_PER_TABLE = 40
_MAX_FIELD_ITEMS = 10
_PASSAGE_CHAR_BUDGET = 6_000
_SCANNED_PDF_TEXT_THRESHOLD = 40
_OCR_PDF_PAGES = 3

SUPPORTED_EXTENSIONS = (
    ".pdf",
    ".docx",
    ".xlsx",
    ".xlsm",
    ".csv",
    ".txt",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".bmp",
    ".tiff",
)

_KIND_BY_EXTENSION: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".csv": "csv",
    ".txt": "text",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
    ".bmp": "image",
    ".tiff": "image",
}

_KIND_BY_CONTENT_TYPE: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/csv": "csv",
    "text/plain": "text",
}

_DOC_TYPE_LABELS: dict[str, str] = {
    "receipt": "Payment receipt",
    "tin_card": "TIN registration document",
    "assessment": "Tax assessment notice",
    "customs_declaration": "Customs declaration",
    "filing_form": "Tax return / filing form",
    "invoice": "Invoice",
    "generic": "General document",
}

_DOC_TYPE_HINTS: dict[str, str] = {
    "receipt": "You can ask the assistant to verify totals, payment dates, or EFRIS details from this receipt.",
    "tin_card": "You can ask the assistant about TIN obligations, updates, or what this registration covers.",
    "assessment": "You can ask the assistant to explain the assessed amounts, deadlines, or objection procedure.",
    "customs_declaration": "You can ask the assistant about duty calculations, HS codes, or clearance steps.",
    "filing_form": "You can ask the assistant to explain fields on this return or the filing deadlines.",
    "invoice": "You can ask the assistant about VAT treatment, EFRIS invoicing rules, or the amounts shown.",
    "generic": "You can ask the assistant questions about the content extracted from this document.",
}


class UnsupportedDocumentError(ValueError):
    """Raised when the uploaded file type has no extractor."""


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class TableSummary:
    """Compact summary of one table/sheet found in a document."""

    name: str
    rows: int
    cols: int
    headers: list[str] = field(default_factory=list)
    numeric_totals: dict[str, float] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "rows": self.rows,
            "cols": self.cols,
            "headers": self.headers,
            "numeric_totals": self.numeric_totals,
        }


@dataclass
class _Extraction:
    """Raw output of a per-type extractor before analysis."""

    text: str = ""
    tables: list[TableSummary] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class DocumentRecord:
    """A fully analysed attachment held in the TTL registry."""

    doc_id: str
    filename: str
    kind: str
    size_bytes: int
    doc_type: str
    confidence: float
    matched_keywords: list[str]
    text: str
    truncated: bool
    fields: dict[str, list[str]]
    tables: list[TableSummary]
    meta: dict[str, Any]
    summary: str
    warnings: list[str]
    created_at: float
    session_id: str = ""
    user_id: str = ""

    def to_response_payload(self) -> dict[str, Any]:
        """Shape consumed by ``DocumentAnalysisResponse`` in models.py."""
        return {
            "document_id": self.doc_id,
            "filename": self.filename,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "doc_type": self.doc_type,
            "confidence": self.confidence,
            "matched_keywords": self.matched_keywords,
            "fields": dict(self.fields),
            "tables": [t.to_payload() for t in self.tables],
            "text_preview": self.text[:600],
            "truncated": self.truncated,
            "summary": self.summary,
            "warnings": self.warnings,
            "expires_in_seconds": max(
                0, int(self.created_at + DOCUMENT_TTL_SECONDS - time.time())
            ),
        }

    def to_report_payload(self) -> dict[str, Any]:
        """Shape consumed by ``pdf_export.generate_document_report_pdf``."""
        return {
            "document_id": self.doc_id,
            "filename": self.filename,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "doc_type": self.doc_type,
            "doc_type_label": _DOC_TYPE_LABELS.get(self.doc_type, "Document"),
            "confidence": self.confidence,
            "matched_keywords": self.matched_keywords,
            "fields": dict(self.fields),
            "tables": [t.to_payload() for t in self.tables],
            "meta": dict(self.meta),
            "text": self.text,
            "truncated": self.truncated,
            "summary": self.summary,
            "warnings": self.warnings,
            "analyzed_at": self.created_at,
        }

    def passage_text(self, char_budget: int) -> str:
        """Grounding passage injected into retrieval hits for chat turns."""
        label = _DOC_TYPE_LABELS.get(self.doc_type, "Document")
        lines = [
            f"[User-attached document: {self.filename} | "
            f"detected type: {label} ({self.confidence:.0%} confidence)]"
        ]
        if self.summary:
            lines.append(f"Summary: {self.summary}")
        field_bits = []
        for key, title in (
            ("tins", "TINs"),
            ("amounts", "Amounts"),
            ("dates", "Dates"),
            ("references", "References"),
        ):
            values = self.fields.get(key) or []
            if values:
                field_bits.append(f"{title}: {', '.join(values[:5])}")
        if field_bits:
            lines.append("Key fields — " + "; ".join(field_bits))
        for table in self.tables[:3]:
            headers = ", ".join(table.headers[:8]) or "no headers"
            lines.append(
                f"Table '{table.name}': {table.rows} rows × {table.cols} cols "
                f"(columns: {headers})"
            )
        header = "\n".join(lines)
        remaining = max(200, char_budget - len(header) - 12)
        body = self.text[:remaining]
        return f"{header}\nContent:\n{body}" if body else header


# ---------------------------------------------------------------------------
# Table helpers
# ---------------------------------------------------------------------------

_NUMERIC_CLEAN_RE = re.compile(r"[,\sUGXugx]+")


def _as_number(value: Any) -> float | None:
    """Parse a cell as a number, tolerating commas and UGX prefixes."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = _NUMERIC_CLEAN_RE.sub("", value.strip())
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _summarize_table(name: str, rows: list[list[Any]]) -> tuple[TableSummary, str]:
    """Build a TableSummary + text rendering from raw rows (headers first)."""
    rows = [r[:_MAX_TABLE_COLS] for r in rows[: _MAX_TABLE_ROWS + 1]]
    if not rows:
        return TableSummary(name=name, rows=0, cols=0), ""

    headers = [str(c) if c is not None else "" for c in rows[0]]
    data_rows = rows[1:]
    n_cols = max((len(r) for r in rows), default=0)

    # Column totals where >=60% of populated cells parse as numbers.
    numeric_totals: dict[str, float] = {}
    for col in range(min(n_cols, len(headers))):
        values = [_as_number(r[col]) for r in data_rows if col < len(r) and r[col] not in (None, "")]
        parsed = [v for v in values if v is not None]
        if values and len(parsed) >= max(1, int(len(values) * 0.6)):
            header = headers[col].strip() or f"column_{col + 1}"
            numeric_totals[header] = round(sum(parsed), 2)

    summary = TableSummary(
        name=name,
        rows=len(data_rows),
        cols=n_cols,
        headers=[h for h in headers if h.strip()][:_MAX_TABLE_COLS],
        numeric_totals=numeric_totals,
    )

    text_lines = [f"[Table: {name}]"]
    for row in rows[: _MAX_TEXT_ROWS_PER_TABLE + 1]:
        cells = ["" if c is None else str(c).strip() for c in row]
        if any(cells):
            text_lines.append(" | ".join(cells))
    if len(rows) > _MAX_TEXT_ROWS_PER_TABLE + 1:
        text_lines.append(f"... ({len(rows) - _MAX_TEXT_ROWS_PER_TABLE - 1} more rows omitted)")
    return summary, "\n".join(text_lines)


# ---------------------------------------------------------------------------
# Per-type extractors (all lazy-import + failure-guarded)
# ---------------------------------------------------------------------------


def _extract_pdf(data: bytes) -> _Extraction:
    out = _Extraction()
    try:
        import fitz  # type: ignore[import-untyped]  # PyMuPDF
    except ImportError:
        out.warnings.append("PDF extraction unavailable — PyMuPDF is not installed.")
        return out
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        logger.warning("PDF parse failed", exc_info=True)
        out.warnings.append("The PDF could not be parsed (corrupt or password-protected).")
        return out
    try:
        page_count = doc.page_count
        out.meta["page_count"] = page_count
        if page_count > _MAX_PDF_PAGES:
            out.warnings.append(
                f"Only the first {_MAX_PDF_PAGES} of {page_count} pages were processed."
            )
        parts = []
        for i in range(min(page_count, _MAX_PDF_PAGES)):
            parts.append(doc[i].get_text("text"))
        text = "\n".join(parts).strip()

        # Scanned PDF: no embedded text layer — OCR the first few pages.
        if len(text) < _SCANNED_PDF_TEXT_THRESHOLD and page_count > 0:
            ocr_text = _ocr_pdf_pages(doc)
            if ocr_text:
                text = ocr_text
                out.meta["ocr_used"] = True
            else:
                out.warnings.append(
                    "This looks like a scanned PDF with no text layer, and no OCR "
                    "engine is available — little or no text could be extracted."
                )
        out.text = text
    finally:
        doc.close()
    return out


def _ocr_pdf_pages(doc: Any) -> str:
    """Rasterise the first pages of a scanned PDF and OCR them (best effort)."""
    try:
        import numpy as np
        from PIL import Image  # type: ignore[import-untyped]

        from .vision.ocr import extract_text as _ocr_extract_text
    except ImportError:
        return ""
    parts = []
    try:
        for i in range(min(doc.page_count, _OCR_PDF_PAGES)):
            pix = doc[i].get_pixmap(dpi=150)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            page_text = _ocr_extract_text(np.array(img))
            if page_text:
                parts.append(page_text)
    except Exception:
        logger.warning("Scanned-PDF OCR failed", exc_info=True)
    return clean_ocr_text(" ".join(parts)) if parts else ""


def _extract_docx(data: bytes) -> _Extraction:
    out = _Extraction()
    try:
        from docx import Document  # type: ignore[import-untyped]
    except ImportError:
        out.warnings.append("DOCX extraction unavailable — python-docx is not installed.")
        return out
    try:
        doc = Document(io.BytesIO(data))
    except Exception:
        logger.warning("DOCX parse failed", exc_info=True)
        out.warnings.append("The DOCX file could not be parsed.")
        return out

    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    out.meta["paragraph_count"] = len(paragraphs)
    parts = ["\n".join(paragraphs)]

    for idx, table in enumerate(doc.tables[:_MAX_SHEETS], start=1):
        rows = [[cell.text for cell in row.cells] for row in table.rows]
        summary, table_text = _summarize_table(f"Table {idx}", rows)
        out.tables.append(summary)
        if table_text:
            parts.append(table_text)
    out.meta["table_count"] = len(doc.tables)
    out.text = "\n\n".join(p for p in parts if p).strip()
    return out


def _extract_xlsx(data: bytes) -> _Extraction:
    out = _Extraction()
    try:
        import openpyxl  # type: ignore[import-untyped]
    except ImportError:
        out.warnings.append("Spreadsheet extraction unavailable — openpyxl is not installed.")
        return out
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:
        logger.warning("XLSX parse failed", exc_info=True)
        out.warnings.append("The spreadsheet could not be parsed.")
        return out
    try:
        sheet_names = wb.sheetnames
        out.meta["sheet_count"] = len(sheet_names)
        if len(sheet_names) > _MAX_SHEETS:
            out.warnings.append(
                f"Only the first {_MAX_SHEETS} of {len(sheet_names)} sheets were processed."
            )
        parts = []
        for name in sheet_names[:_MAX_SHEETS]:
            ws = wb[name]
            rows: list[list[Any]] = []
            for row in ws.iter_rows(values_only=True):
                if any(c is not None and str(c).strip() for c in row):
                    rows.append(list(row))
                if len(rows) > _MAX_TABLE_ROWS:
                    break
            if not rows:
                continue
            summary, table_text = _summarize_table(name, rows)
            out.tables.append(summary)
            if table_text:
                parts.append(table_text)
        out.text = "\n\n".join(parts).strip()
    finally:
        wb.close()
    return out


def _extract_csv(data: bytes) -> _Extraction:
    out = _Extraction()
    raw = data.decode("utf-8", errors="replace")
    try:
        rows = [row for row in csv.reader(io.StringIO(raw)) if any(c.strip() for c in row)]
    except csv.Error:
        logger.warning("CSV parse failed", exc_info=True)
        out.warnings.append("The CSV file could not be parsed.")
        return out
    if not rows:
        out.warnings.append("The CSV file contains no data rows.")
        return out
    summary, table_text = _summarize_table("CSV data", rows)
    out.tables.append(summary)
    out.text = table_text
    return out


def _extract_image(data: bytes) -> _Extraction:
    out = _Extraction()
    try:
        import numpy as np
        from PIL import Image  # type: ignore[import-untyped]
    except ImportError:
        out.warnings.append("Image processing unavailable — Pillow is not installed.")
        return out
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        out.meta["width"], out.meta["height"] = img.size
        out.meta["format"] = img.format or ""
        rgb = img.convert("RGB")
    except Exception:
        logger.warning("Image decode failed", exc_info=True)
        out.warnings.append("The image could not be decoded.")
        return out

    from .vision.ocr import extract_text as _ocr_extract_text

    text = _ocr_extract_text(np.array(rgb))
    if not text:
        # Same ad-hoc fallback the voice+vision endpoint uses.
        try:
            import pytesseract  # type: ignore[import-untyped]

            text = pytesseract.image_to_string(rgb, lang="eng")
        except Exception:
            text = ""
    if text.strip():
        out.text = clean_ocr_text(text)
        out.meta["ocr_used"] = True
    else:
        out.warnings.append(
            "No text could be extracted from this image (no OCR engine available "
            "or the image has no readable text)."
        )
    return out


def _extract_text_file(data: bytes) -> _Extraction:
    return _Extraction(text=data.decode("utf-8", errors="replace").strip())


_EXTRACTORS = {
    "pdf": _extract_pdf,
    "docx": _extract_docx,
    "xlsx": _extract_xlsx,
    "csv": _extract_csv,
    "image": _extract_image,
    "text": _extract_text_file,
}


# ---------------------------------------------------------------------------
# Analysis pipeline
# ---------------------------------------------------------------------------


def detect_kind(filename: str, content_type: str = "") -> str:
    """Map a filename/MIME pair to an extractor kind or raise.

    The extension is authoritative: an unrecognised extension is rejected
    regardless of the declared MIME type (client MIME is untrusted). The
    MIME fallback applies only to extension-less filenames.
    """
    name = (filename or "").lower().strip()
    for ext, kind in _KIND_BY_EXTENSION.items():
        if name.endswith(ext):
            return kind
    if name.endswith(".xls"):
        raise UnsupportedDocumentError(
            "Legacy .xls workbooks are not supported — please re-save as .xlsx."
        )
    if "." not in name.rsplit("/", 1)[-1]:
        ctype = (content_type or "").split(";")[0].strip().lower()
        if ctype.startswith("image/"):
            return "image"
        if ctype in _KIND_BY_CONTENT_TYPE:
            return _KIND_BY_CONTENT_TYPE[ctype]
    raise UnsupportedDocumentError(
        "Unsupported file type. Supported: " + ", ".join(SUPPORTED_EXTENSIONS)
    )


def _build_summary(
    kind: str,
    doc_type: str,
    confidence: float,
    fields: dict[str, list[str]],
    tables: list[TableSummary],
    meta: dict[str, Any],
    text: str,
) -> str:
    label = _DOC_TYPE_LABELS.get(doc_type, "Document")
    if confidence > 0:
        sentences = [f"{label} ({confidence:.0%} classification confidence)."]
    else:
        sentences = ["Document type could not be determined from the content."]

    counts = []
    for key, singular, plural in (
        ("tins", "TIN", "TINs"),
        ("amounts", "UGX amount", "UGX amounts"),
        ("dates", "date", "dates"),
        ("references", "reference number", "reference numbers"),
    ):
        n = len(fields.get(key) or [])
        if n:
            counts.append(f"{n} {singular if n == 1 else plural}")
    if counts:
        sentences.append("Extracted " + ", ".join(counts) + ".")

    if tables:
        total_rows = sum(t.rows for t in tables)
        unit = "sheet" if kind == "xlsx" else "table"
        sentences.append(
            f"Contains {len(tables)} {unit}{'s' if len(tables) != 1 else ''} "
            f"with {total_rows} data row{'s' if total_rows != 1 else ''}."
        )

    words = len(text.split())
    if words:
        if meta.get("page_count"):
            sentences.append(f"About {words} words across {meta['page_count']} page(s).")
        else:
            sentences.append(f"About {words} words of text extracted.")

    hint = _DOC_TYPE_HINTS.get(doc_type)
    if hint:
        sentences.append(hint)
    return " ".join(sentences)


def analyze_document(
    data: bytes,
    filename: str,
    content_type: str = "",
    *,
    session_id: str = "",
    user_id: str = "",
) -> DocumentRecord:
    """Extract, classify, and analyse an uploaded document; register it.

    Raises ``UnsupportedDocumentError`` for unknown types and ``ValueError``
    for empty/oversized payloads (the endpoint maps these to 415/413/422).
    """
    if not data:
        raise ValueError("Empty file.")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"File exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit.")

    kind = detect_kind(filename, content_type)
    extraction = _EXTRACTORS[kind](data)

    text = re.sub(r"\n{3,}", "\n\n", extraction.text or "").strip()
    truncated = len(text) > _MAX_TEXT_CHARS
    if truncated:
        text = text[:_MAX_TEXT_CHARS]
        extraction.warnings.append(
            f"Extracted text was truncated to {_MAX_TEXT_CHARS} characters for analysis."
        )

    classification = classify_document(text)
    fields = {
        "tins": extract_tin_numbers(text)[:_MAX_FIELD_ITEMS],
        "amounts": extract_ugx_amounts(text)[:_MAX_FIELD_ITEMS],
        "dates": extract_dates(text)[:_MAX_FIELD_ITEMS],
        "references": extract_reference_numbers(text)[:_MAX_FIELD_ITEMS],
    }
    summary = _build_summary(
        kind,
        classification.doc_type.value,
        classification.confidence,
        fields,
        extraction.tables,
        extraction.meta,
        text,
    )

    record = DocumentRecord(
        doc_id=uuid.uuid4().hex,
        filename=os.path.basename(filename or "document")[:120] or "document",
        kind=kind,
        size_bytes=len(data),
        doc_type=classification.doc_type.value,
        confidence=classification.confidence,
        matched_keywords=classification.matched_keywords,
        text=text,
        truncated=truncated,
        fields=fields,
        tables=extraction.tables,
        meta=extraction.meta,
        summary=summary,
        warnings=extraction.warnings,
        created_at=time.time(),
        session_id=session_id or "",
        user_id=user_id or "",
    )
    _store(record)
    logger.info(
        "document analyzed: id=%s kind=%s type=%s conf=%.2f chars=%d warnings=%d",
        record.doc_id[:8],
        kind,
        record.doc_type,
        record.confidence,
        len(text),
        len(record.warnings),
    )
    return record


# ---------------------------------------------------------------------------
# TTL registry — in-process dict + shared ephemeral file spool
# ---------------------------------------------------------------------------
#
# The deployed image runs uvicorn with multiple workers (UVICORN_WORKERS=2
# in Dockerfile.cranecloud) and consecutive requests round-robin across
# them: the upload can land on worker A and the chat/report request on
# worker B. A pure in-process dict silently loses the attachment in that
# case (live-verified on the HF Space), so every stored record is mirrored
# as a JSON file in an ephemeral per-container spool directory (0600,
# unguessable doc-id filename, TTL-purged, dies with the container). The
# dict stays the fast path; a miss falls through to the spool.

_registry: OrderedDict[str, DocumentRecord] = OrderedDict()
_registry_lock = threading.Lock()

_STORE_DIR = Path(
    os.getenv("DOCUMENT_STORE_DIR", "")
    or Path(tempfile.gettempdir()) / "ura_document_store"
)

_DOC_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _spool_write(record: DocumentRecord) -> None:
    """Mirror a record into the shared spool (best-effort).

    ``record.doc_id`` is server-generated (``uuid.uuid4().hex`` in
    :func:`analyze_document`), never user input, so it is safe in a path.
    """
    try:
        _STORE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = _STORE_DIR / f"{record.doc_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(record)))
        tmp.chmod(0o600)
        tmp.replace(path)
    except OSError:
        logger.warning("document spool write failed (memory-only fallback)", exc_info=True)


def _spool_read(doc_id: str) -> DocumentRecord | None:
    """Fetch a spool entry for a (user-supplied) doc id.

    The id arrives from user input (chat ``attachment_ids``, the report
    URL), so no path is ever built from it: the entry is selected by
    comparing directory-listing names against the expected filename.
    """
    if not _DOC_ID_RE.fullmatch(doc_id):
        return None
    expected_name = f"{doc_id}.json"
    path: Path | None = None
    try:
        for entry in _STORE_DIR.iterdir():
            if entry.name == expected_name:
                path = entry
                break
    except OSError:
        return None
    if path is None:
        return None
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        payload = json.loads(raw)
        payload["tables"] = [TableSummary(**t) for t in payload.get("tables", [])]
        return DocumentRecord(**payload)
    except (TypeError, ValueError, KeyError):
        logger.warning("document spool entry unreadable", exc_info=True)
        return None


def _spool_purge() -> None:
    """Drop expired spool entries and enforce the size cap (best-effort)."""
    try:
        entries = sorted(_STORE_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    now = time.time()
    excess = len(entries) - DOCUMENT_REGISTRY_MAX
    for i, path in enumerate(entries):
        try:
            if i < excess or now - path.stat().st_mtime > DOCUMENT_TTL_SECONDS:
                path.unlink(missing_ok=True)
        except OSError:
            continue


def _store(record: DocumentRecord) -> None:
    with _registry_lock:
        _purge_expired_locked()
        _registry[record.doc_id] = record
        _registry.move_to_end(record.doc_id)
        while len(_registry) > DOCUMENT_REGISTRY_MAX:
            _registry.popitem(last=False)
    _spool_write(record)
    _spool_purge()


def _purge_expired_locked() -> None:
    now = time.time()
    expired = [k for k, v in _registry.items() if now - v.created_at > DOCUMENT_TTL_SECONDS]
    for k in expired:
        del _registry[k]


def get_document(doc_id: str, *, session_id: str = "") -> DocumentRecord | None:
    """Fetch a live document; enforces the session binding set at upload."""
    with _registry_lock:
        _purge_expired_locked()
        record = _registry.get(doc_id)
    if record is None:
        # Another worker may have analysed it — check the shared spool.
        record = _spool_read(doc_id)
        if record is not None and time.time() - record.created_at > DOCUMENT_TTL_SECONDS:
            record = None
        if record is not None:
            with _registry_lock:
                _registry[record.doc_id] = record
                _registry.move_to_end(record.doc_id)
    if record is None:
        return None
    if record.session_id and record.session_id != (session_id or ""):
        return None
    return record


def resolve_attachments(
    attachment_ids: list[str] | None,
    *,
    session_id: str = "",
) -> list[DocumentRecord]:
    """Resolve chat ``attachment_ids`` to live records (missing ids dropped)."""
    records: list[DocumentRecord] = []
    seen: set[str] = set()
    for doc_id in attachment_ids or []:
        if doc_id in seen or len(records) >= MAX_ATTACHMENTS_PER_TURN:
            continue
        seen.add(doc_id)
        record = get_document(doc_id, session_id=session_id)
        if record is not None:
            records.append(record)
    return records


def attachment_passages(records: list[DocumentRecord]) -> list[dict[str, Any]]:
    """Build retrieval-hit dicts for attached documents.

    These are prepended to the normal retrieval hits so attachment content
    flows through the same LLM01 scrub + spotlight markers in
    ``llm._build_messages``, counts as grounding for faithfulness scoring,
    and prevents spurious abstention. ``doc_type: "attachment"`` marks them
    for redaction in ``ChatModel.contexts_json`` (user content must not be
    persisted verbatim to analytics).
    """
    if not records:
        return []
    per_budget = max(800, _PASSAGE_CHAR_BUDGET // len(records))
    return [
        {
            "text": record.passage_text(per_budget),
            "answer": "",
            "question": "",
            "source": f"attached:{record.filename}",
            "chunk_id": record.doc_id,
            "page": "",
            "section": record.doc_type,
            "doc_type": "attachment",
            "score_rrf": 1.0,
        }
        for record in records
    ]
