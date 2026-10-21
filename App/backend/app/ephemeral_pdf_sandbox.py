"""Ephemeral RAM-backed PDF/XLSX sandbox for authoritative URA & ULII documents.

Implements Phase D of the Sovereign Live Web Research & Self-Updating Qdrant plan:
1. Turn-scoped ephemeral workspace (:func:`ephemeral_sandbox`) that creates an
   isolated ``0o700`` directory under the system temp dir and unconditionally
   wipes it in a ``finally:`` block.
2. Safe streaming download (:func:`download_to_sandbox`) enforcing HTTPS,
   Tier-1 source authority, and a strict 25 MB mid-stream byte abort.
3. Structural security inspection via :func:`app.pdf_guards.inspect_pdf_bytes`,
   :func:`app.guardrails.scan_retrieved_text`, and
   :func:`app.mcp.validation.screen_tool_result`.
4. Page-level PDF/XLSX extraction, metadata classification (``fiscal_year``,
   ``doc_family``, ``doc_kind``), 24h Redis ``sha256`` chunk caching, and GPU 2
   micro-reranking against the taxpayer's query.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from .guardrails import scan_retrieved_text
from .mcp.validation import screen_tool_result
from .pdf_guards import QUERY_PDF_LIMITS, PdfRejected, inspect_pdf_bytes
from .web_research import (
    classify_source_authority,
    extract_fiscal_year,
    infer_doc_family,
    validate_outbound_research_url,
)

logger = logging.getLogger(__name__)

MAX_SANDBOX_PDF_BYTES = int(os.getenv("MAX_SANDBOX_PDF_BYTES", str(25 * 1024 * 1024)))
SANDBOX_DOWNLOAD_TIMEOUT_SECONDS = float(os.getenv("SANDBOX_DOWNLOAD_TIMEOUT_SECONDS", "12.0"))
SANDBOX_CACHE_TTL_SECONDS = int(os.getenv("SANDBOX_CACHE_TTL_SECONDS", str(24 * 3600)))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SANDBOX_REDIS_PREFIX = "ura:pdf_chunks:"
_CHUNK_TARGET_CHARS = 900
_CHUNK_OVERLAP_CHARS = 120


class SandboxDownloadRejected(ValueError):
    """Raised when a remote document fails URL, authority, or size-cap validation."""


@dataclass
class SandboxDocument:
    """Extracted, security-screened, and ranked document from the ephemeral sandbox."""

    url: str
    filename: str
    stem: str
    sha256: str
    page_count: int
    fiscal_year: str
    doc_family: str
    doc_kind: str  # "full_guide" | "public_notice"
    chunks: list[dict[str, Any]] = field(default_factory=list)
    top_chunks: list[dict[str, Any]] = field(default_factory=list)
    from_cache: bool = False


_CACHE_LOCK = threading.Lock()
_L1_CHUNK_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_redis_client: Any = None
_redis_init_attempted = False


def _get_redis() -> Any:
    """Lazy-initialize optional Redis client for 24h SHA-256 PDF chunk caching."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    with _CACHE_LOCK:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True
        try:
            import redis  # type: ignore[import-untyped]

            client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            client.ping()
            _redis_client = client
        except Exception:
            _redis_client = None
        return _redis_client


def clear_sandbox_cache() -> None:
    """Clear the L1 in-memory PDF chunk cache."""
    with _CACHE_LOCK:
        _L1_CHUNK_CACHE.clear()


def _default_sandbox_root() -> Path:
    """Return the base directory for ephemeral turn sandboxes."""
    configured = os.getenv("URA_WEB_SANDBOX_DIR", "").strip()
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "ura-web-sandbox"


@contextlib.contextmanager
def ephemeral_sandbox(turn_id: str = "", *, base_dir: Path | None = None) -> Iterator[Path]:
    """Create an isolated 0o700 turn directory and unconditionally delete it on exit."""
    safe_turn = re.sub(r"[^A-Za-z0-9_-]+", "_", str(turn_id or "turn").strip())[:48] or "turn"
    unique_suffix = uuid.uuid4().hex[:10]
    root = base_dir if base_dir is not None else _default_sandbox_root()
    root.mkdir(parents=True, exist_ok=True)
    sandbox_dir = root / f"{safe_turn}_{unique_suffix}"
    sandbox_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(sandbox_dir, 0o700)
    except OSError:
        pass
    try:
        yield sandbox_dir
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)


def _safe_filename_from_url(url: str, *, default_ext: str = ".pdf") -> str:
    """Derive a sanitized filename from *url*."""
    parsed = urlparse(str(url or ""))
    raw_name = unquote(Path(parsed.path or "").name).strip()
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_name).strip("-._")
    if not cleaned:
        cleaned = f"ura-doc-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:10]}{default_ext}"
    if not cleaned.lower().endswith((".pdf", ".xlsx", ".xls", ".docx")):
        cleaned = f"{cleaned}{default_ext}"
    return cleaned[:120]


def _default_stream_downloader(url: str, max_bytes: int) -> bytes:
    """Stream *url* over HTTPS while enforcing *max_bytes* mid-stream."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,*/*;q=0.8",
    }
    buf = bytearray()
    with httpx.Client(timeout=SANDBOX_DOWNLOAD_TIMEOUT_SECONDS, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            content_len = resp.headers.get("Content-Length")
            if content_len and content_len.isdigit() and int(content_len) > max_bytes:
                raise SandboxDownloadRejected(
                    f"Remote Content-Length ({content_len} bytes) exceeds sandbox cap ({max_bytes} bytes)."
                )
            for chunk in resp.iter_bytes(chunk_size=65536):
                if not chunk:
                    continue
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise SandboxDownloadRejected(
                        f"Streamed download exceeded sandbox cap of {max_bytes} bytes."
                    )
    return bytes(buf)


def download_to_sandbox(
    url: str,
    sandbox_dir: Path,
    *,
    max_bytes: int = MAX_SANDBOX_PDF_BYTES,
    byte_fetcher: Callable[[str, int], bytes] | None = None,
) -> tuple[Path, bytes, str]:
    """Validate *url*, stream bytes into *sandbox_dir* with a 25 MB cap, and return ``(path, bytes, sha256)``."""
    problem = validate_outbound_research_url(url)
    if problem:
        raise SandboxDownloadRejected(f"Disallowed sandbox URL ({problem}): {url}")
    auth = classify_source_authority(url)
    if auth.tier != 1:
        raise SandboxDownloadRejected(f"Sandbox only downloads Tier-1 official documents (got tier={auth.tier}): {url}")

    fetcher = byte_fetcher or _default_stream_downloader
    raw_bytes = fetcher(url, max_bytes)
    if not raw_bytes:
        raise SandboxDownloadRejected("Downloaded file is empty.")
    if len(raw_bytes) > max_bytes:
        raise SandboxDownloadRejected(
            f"Downloaded file ({len(raw_bytes)} bytes) exceeds sandbox cap ({max_bytes} bytes)."
        )

    sha256_hex = hashlib.sha256(raw_bytes).hexdigest()
    filename = _safe_filename_from_url(url)
    file_path = sandbox_dir / filename
    file_path.write_bytes(raw_bytes)
    try:
        os.chmod(file_path, 0o600)
    except OSError:
        pass
    return file_path, raw_bytes, sha256_hex


def _sanitize_page_text(raw_text: str) -> str:
    """Screen extracted page text for indirect prompt injection and credential leaks."""
    text = str(raw_text or "").strip()
    if not text:
        return ""
    cleaned, flagged = scan_retrieved_text(text)
    violations = screen_tool_result(cleaned)
    if violations:
        logger.warning("Dropping sandbox page due to security violations: %s", violations)
        return ""
    if flagged and not cleaned.strip():
        return ""
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _extract_pdf_pages(raw_bytes: bytes) -> list[tuple[int, str]]:
    """Inspect PDF bytes with ``pdf_guards`` and extract sanitized text per page."""
    inspect_pdf_bytes(raw_bytes, limits=QUERY_PDF_LIMITS)
    pages: list[tuple[int, str]] = []

    # 1. Primary: pypdfium2 (matches documents.py and pdf_guards.py)
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]

        pdf = pdfium.PdfDocument(raw_bytes)
        try:
            for idx in range(len(pdf)):
                page = pdf[idx]
                try:
                    textpage = page.get_textpage()
                    try:
                        txt = textpage.get_text_range() or ""
                    finally:
                        textpage.close()
                finally:
                    page.close()
                sanitized = _sanitize_page_text(txt)
                if sanitized:
                    pages.append((idx + 1, sanitized))
            if pages:
                return pages
        finally:
            pdf.close()
    except PdfRejected:
        raise
    except Exception:
        logger.debug("pypdfium2 extraction failed; trying pdfplumber/fitz fallback", exc_info=True)

    # 2. Secondary: pdfplumber
    try:
        import pdfplumber  # type: ignore[import-untyped]

        with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf_plumb:
            for idx, page in enumerate(pdf_plumb.pages):
                txt = page.extract_text() or ""
                sanitized = _sanitize_page_text(txt)
                if sanitized:
                    pages.append((idx + 1, sanitized))
        if pages:
            return pages
    except PdfRejected:
        raise
    except Exception:
        logger.debug("pdfplumber extraction failed; trying PyMuPDF fallback", exc_info=True)

    # 3. Tertiary: PyMuPDF (fitz)
    try:
        import fitz  # type: ignore[import-untyped]

        with fitz.open(stream=raw_bytes, filetype="pdf") as doc:
            for idx in range(len(doc)):
                txt = doc[idx].get_text("text") or ""
                sanitized = _sanitize_page_text(txt)
                if sanitized:
                    pages.append((idx + 1, sanitized))
        return pages
    except PdfRejected:
        raise
    except Exception as exc:
        raise PdfRejected(f"Failed to extract text from PDF: {exc}") from exc


def _extract_xlsx_pages(raw_bytes: bytes) -> list[tuple[int, str]]:
    """Extract sanitized text rows per worksheet from an ``.xlsx`` workbook."""
    import openpyxl  # type: ignore[import-untyped]

    wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), read_only=True, data_only=True)
    pages: list[tuple[int, str]] = []
    try:
        for idx, sheet_name in enumerate(wb.sheetnames[:10], start=1):
            ws = wb[sheet_name]
            lines: list[str] = [f"Worksheet: {sheet_name}"]
            for row_idx, row in enumerate(ws.iter_rows(values_only=True)):
                if row_idx >= 300:
                    break
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    lines.append(" | ".join(cells[:25]))
            sanitized = _sanitize_page_text("\n".join(lines))
            if sanitized:
                pages.append((idx, sanitized))
    finally:
        wb.close()
    return pages


def _chunk_pages(
    pages: list[tuple[int, str]],
    *,
    filename: str,
    url: str,
    fiscal_year: str,
    doc_type: str = "pdf",
) -> list[dict[str, Any]]:
    """Split extracted pages into overlapping retrieval chunks with full provenance."""
    chunks: list[dict[str, Any]] = []
    for page_num, page_text in pages:
        heading_match = re.search(r"^(?:SECTION|PART|CHAPTER|TABLE|\d+\.\d*)\s+[^\n]{3,90}", page_text, re.I | re.M)
        section = heading_match.group(0).strip() if heading_match else f"Page {page_num}"

        if len(page_text) <= _CHUNK_TARGET_CHARS:
            chunks.append(
                {
                    "text": page_text,
                    "source": filename,
                    "source_url": url,
                    "fiscal_year": fiscal_year,
                    "doc_type": doc_type,
                    "section": section,
                    "page": page_num,
                }
            )
            continue

        start = 0
        step = max(200, _CHUNK_TARGET_CHARS - _CHUNK_OVERLAP_CHARS)
        while start < len(page_text):
            slice_text = page_text[start : start + _CHUNK_TARGET_CHARS].strip()
            if slice_text:
                chunks.append(
                    {
                        "text": slice_text,
                        "source": filename,
                        "source_url": url,
                        "fiscal_year": fiscal_year,
                        "doc_type": doc_type,
                        "section": section,
                        "page": page_num,
                    }
                )
            if start + _CHUNK_TARGET_CHARS >= len(page_text):
                break
            start += step
    return chunks


def classify_doc_kind(filename: str, first_pages_text: str, page_count: int) -> str:
    """Classify a URA document as ``"public_notice"`` or ``"full_guide"``."""
    combined = f"{filename} {first_pages_text[:600]}".lower()
    if re.search(r"\b(public[-\s]+notice|practice[-\s]+note|circular|penalty[-\s]+waiver|amnesty)\b", combined):
        return "public_notice"
    if page_count <= 5 and not re.search(r"\b(handbook|sector[-\s]+guide|guide|act|valuation|tariff)\b", combined):
        return "public_notice"
    return "full_guide"


def _get_cached_doc_payload(sha256_hex: str) -> dict[str, Any] | None:
    """Read cached extracted chunks and metadata for *sha256_hex* from L1 or Redis."""
    now = time.time()
    with _CACHE_LOCK:
        entry = _L1_CHUNK_CACHE.get(sha256_hex)
        if entry is not None:
            if entry[0] > now:
                return entry[1]
            _L1_CHUNK_CACHE.pop(sha256_hex, None)

    r_client = _get_redis()
    if r_client is not None:
        try:
            raw = r_client.get(f"{SANDBOX_REDIS_PREFIX}{sha256_hex}")
            if raw:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    with _CACHE_LOCK:
                        _L1_CHUNK_CACHE[sha256_hex] = (now + SANDBOX_CACHE_TTL_SECONDS, payload)
                    return payload
        except Exception:
            logger.debug("Redis sandbox chunk cache read failed", exc_info=True)
    return None


def _set_cached_doc_payload(sha256_hex: str, payload: dict[str, Any]) -> None:
    """Store extracted chunks and metadata for *sha256_hex* in L1 and Redis (24h TTL)."""
    now = time.time()
    with _CACHE_LOCK:
        _L1_CHUNK_CACHE[sha256_hex] = (now + SANDBOX_CACHE_TTL_SECONDS, payload)
    r_client = _get_redis()
    if r_client is not None:
        try:
            r_client.setex(
                f"{SANDBOX_REDIS_PREFIX}{sha256_hex}",
                SANDBOX_CACHE_TTL_SECONDS,
                json.dumps(payload),
            )
        except Exception:
            logger.debug("Redis sandbox chunk cache write failed", exc_info=True)


def rank_sandbox_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    *,
    retriever: Any = None,
    top_k: int = 4,
) -> list[dict[str, Any]]:
    """Rank extracted chunks against *query* using GPU 2 cross-encoder/embedder or lexical overlap."""
    if not chunks:
        return []
    q = str(query or "").strip()
    scored = [dict(c) for c in chunks]

    # 1. Try GPU 2 cross-encoder reranker if loaded on retriever
    reranker = getattr(retriever, "_reranker", None) if retriever is not None else None
    if reranker is not None and q:
        try:
            pairs = [(q, str(c.get("text") or "")) for c in scored]
            raw_scores = reranker.predict(pairs)
            for idx, s in enumerate(raw_scores):
                val = float(s)
                norm = 1.0 / (1.0 + math.exp(-val)) if (val < 0.0 or val > 1.0) else val
                scored[idx]["score_rerank"] = val
                scored[idx]["score"] = round(norm, 4)
            scored.sort(key=lambda c: float(c.get("score_rerank", 0.0)), reverse=True)
            return scored[: max(1, top_k)]
        except Exception:
            logger.debug("Sandbox cross-encoder rerank failed; falling back to lexical", exc_info=True)

    # 2. Lexical + numeric keyword overlap fallback (deterministic in unit tests)
    q_terms = {
        tok
        for tok in re.findall(r"[a-z0-9]+", q.lower())
        if len(tok) >= 2 and tok not in {"the", "and", "for", "with", "what", "how", "are", "is", "in", "of", "to"}
    }
    for c in scored:
        txt_low = str(c.get("text") or "").lower()
        c_terms = set(re.findall(r"[a-z0-9]+", txt_low))
        overlap = len(q_terms & c_terms) / max(len(q_terms), 1)
        # Boost chunks containing statutory percentages or UGX figures
        has_num = 0.10 if re.search(r"\b(\d+(?:\.\d+)?%|\d{1,3}(?:,\d{3})+)\b", txt_low) else 0.0
        c["score"] = round(min(0.99, 0.50 + 0.40 * overlap + has_num), 4)

    scored.sort(key=lambda c: (float(c.get("score", 0.0)), -int(c.get("page", 1))), reverse=True)
    return scored[: max(1, top_k)]


def fetch_and_extract_sandbox_document(
    url: str,
    query: str,
    *,
    turn_id: str = "",
    retriever: Any = None,
    top_k: int = 4,
    byte_fetcher: Callable[[str, int], bytes] | None = None,
    base_dir: Path | None = None,
    doc_family_hint: str = "",
) -> SandboxDocument:
    """Download *url* into an ephemeral sandbox, inspect & extract chunks, rank top chunks, and wipe disk."""
    with ephemeral_sandbox(turn_id or "web_turn", base_dir=base_dir) as sandbox_dir:
        file_path, raw_bytes, sha256_hex = download_to_sandbox(
            url,
            sandbox_dir,
            byte_fetcher=byte_fetcher,
        )
        filename = file_path.name
        stem = file_path.stem

        cached = _get_cached_doc_payload(sha256_hex)
        if cached is not None:
            chunks = list(cached.get("chunks") or [])
            top_chunks = rank_sandbox_chunks(query, chunks, retriever=retriever, top_k=top_k)
            return SandboxDocument(
                url=url,
                filename=str(cached.get("filename") or filename),
                stem=str(cached.get("stem") or stem),
                sha256=sha256_hex,
                page_count=int(cached.get("page_count") or len(chunks)),
                fiscal_year=str(cached.get("fiscal_year") or ""),
                doc_family=str(cached.get("doc_family") or doc_family_hint or infer_doc_family(filename)),
                doc_kind=str(cached.get("doc_kind") or "full_guide"),
                chunks=chunks,
                top_chunks=top_chunks,
                from_cache=True,
            )

        is_xlsx = filename.lower().endswith((".xlsx", ".xls"))
        pages = _extract_xlsx_pages(raw_bytes) if is_xlsx else _extract_pdf_pages(raw_bytes)
        if not pages:
            raise PdfRejected("No readable text pages remained after security screening.")

        page_count = len(pages)
        first_two_pages = "\n".join(txt for _, txt in pages[:2])
        fiscal_year = (
            extract_fiscal_year(filename)
            or extract_fiscal_year(url)
            or extract_fiscal_year(first_two_pages)
        )
        doc_family = doc_family_hint or infer_doc_family(f"{filename} {url} {first_two_pages[:400]}")
        doc_kind = classify_doc_kind(filename, first_two_pages, page_count)
        doc_type = "xlsx" if is_xlsx else "pdf"

        chunks = _chunk_pages(
            pages,
            filename=filename,
            url=url,
            fiscal_year=fiscal_year,
            doc_type=doc_type,
        )
        top_chunks = rank_sandbox_chunks(query, chunks, retriever=retriever, top_k=top_k)

        payload_to_cache = {
            "filename": filename,
            "stem": stem,
            "page_count": page_count,
            "fiscal_year": fiscal_year,
            "doc_family": doc_family,
            "doc_kind": doc_kind,
            "chunks": chunks,
        }
        _set_cached_doc_payload(sha256_hex, payload_to_cache)

        return SandboxDocument(
            url=url,
            filename=filename,
            stem=stem,
            sha256=sha256_hex,
            page_count=page_count,
            fiscal_year=fiscal_year,
            doc_family=doc_family,
            doc_kind=doc_kind,
            chunks=chunks,
            top_chunks=top_chunks,
            from_cache=False,
        )


def serialize_sandbox_document(doc: SandboxDocument) -> dict[str, Any]:
    """Serialize a :class:`SandboxDocument` to a JSON-compatible dictionary."""
    return asdict(doc)
