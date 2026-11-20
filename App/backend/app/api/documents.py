"""Document APIs: analyse an upload, its report and status, and re-index the corpus."""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response

from ..processing import documents
from ..observability.analytics import metrics
from ..auth import AuthContext, optional_user
from ..middleware.rate_limiter import DOCUMENT_RATE_LIMIT, limiter
from ..rag.pipeline import ChatModel
from ..rag.retriever import active_retrieval_mode
from .deps import get_model, require_ops_key
from .models import DocumentAnalysisResponse

logger = logging.getLogger(__name__)
router = APIRouter()

_DOCUMENT_MULTIPART_OVERHEAD_BYTES = int(
    os.getenv("DOCUMENT_MULTIPART_OVERHEAD_BYTES", str(512 * 1024))
)
_DOCUMENT_UPLOAD_CHUNK_BYTES = 64 * 1024


def _reject_oversized_document_request(request: Request) -> None:
    """Fail before multipart parsing when a client provides an oversized body."""
    raw_length = request.headers.get("content-length")
    if not raw_length:
        return
    try:
        content_length = int(raw_length)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
    if content_length < 0:
        raise HTTPException(status_code=400, detail="Invalid Content-Length header")
    max_request_bytes = documents.MAX_FILE_BYTES + _DOCUMENT_MULTIPART_OVERHEAD_BYTES
    if content_length > max_request_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Request exceeds the {max_request_bytes // (1024 * 1024)} MiB document upload limit",
        )


async def _read_document_upload_bounded(upload: Any) -> bytes:
    """Read an upload in chunks, enforcing the byte limit before a full copy exists."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_DOCUMENT_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > documents.MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {documents.MAX_FILE_BYTES // (1024 * 1024)} MiB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/v1/documents/analyze",
    response_model=DocumentAnalysisResponse,
    tags=["documents"],
)
@limiter.limit(DOCUMENT_RATE_LIMIT)
async def analyze_uploaded_document(
    request: Request,
    ctx: AuthContext = Depends(optional_user),
) -> DocumentAnalysisResponse:
    """Analyse an uploaded document (PDF, DOCX, XLSX, CSV, image, or text).

    Accepts multipart/form-data with a single ``file`` part. Extracts text
    and tables, classifies the document against the URA taxonomy, and pulls
    URA-specific fields (TINs, UGX amounts, dates, references).

    The returned ``document_id`` can be passed in chat ``attachment_ids``
    to ground answers on the document, and used with
    ``GET /v1/documents/{document_id}/report`` to download a PDF report.
    Documents are held in memory only and expire after a TTL.
    """
    import asyncio

    _reject_oversized_document_request(request)
    session_id = request.headers.get("X-Session-ID", "")
    if not session_id and not ctx.user_id:
        raise HTTPException(
            status_code=422,
            detail="X-Session-ID or an authenticated identity is required for document uploads",
        )
    try:
        form = await request.form(
            max_files=1,
            max_fields=8,
            max_part_size=documents.MAX_FILE_BYTES,
        )
    except Exception as err:
        logger.info("Document multipart parsing rejected: %s", err)
        raise HTTPException(status_code=422, detail="Invalid or oversized multipart document upload") from err
    uploads = form.getlist("file")
    upload = uploads[0] if len(uploads) == 1 else None
    if upload is None or isinstance(upload, str):
        raise HTTPException(status_code=422, detail="Missing 'file' part in form data")

    data = await _read_document_upload_bounded(upload)
    if not data:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(data) > documents.MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {documents.MAX_FILE_BYTES // (1024 * 1024)} MiB limit",
        )

    started = time.perf_counter()
    try:
        record = await asyncio.to_thread(
            documents.analyze_document,
            data,
            upload.filename or "document",
            upload.content_type or "",
            session_id=session_id,
            user_id=ctx.user_id or "",
        )
    except documents.UnsupportedDocumentError as err:
        raise HTTPException(status_code=415, detail=str(err)) from err
    except ValueError as err:
        detail = str(err)
        code = 422
        if "malware" in detail.lower():
            code = 422
        raise HTTPException(status_code=code, detail=detail) from err
    finally:
        metrics.observe("document_analysis_duration_ms", (time.perf_counter() - started) * 1000)

    metrics.inc("documents_analyzed_total")
    ocr_status = str(record.meta.get("ocr_status", "not_used"))
    ocr_backend = str(record.meta.get("ocr_backend", "not_used"))
    metrics.inc("document_ocr_total", labels={"backend": ocr_backend, "status": ocr_status})
    if record.meta.get("ocr_regions"):
        metrics.observe("document_ocr_regions", float(record.meta["ocr_regions"]))
    return DocumentAnalysisResponse(**record.to_response_payload())


@router.get("/v1/documents/{document_id}/report", tags=["documents"])
@limiter.limit(DOCUMENT_RATE_LIMIT)
def document_report(
    request: Request,
    document_id: str = Path(..., pattern=r"^[a-f0-9]{32}$"),
    _ctx: AuthContext = Depends(optional_user),
) -> Response:
    """Download the branded PDF analysis report for an analysed document."""
    record = documents.get_document(
        document_id,
        session_id=request.headers.get("X-Session-ID", ""),
        user_id=_ctx.user_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found or expired")

    etag = f'W/"{document_id}-{hashlib.sha256(str(record.meta.get("source_sha256", "")).encode()).hexdigest()[:12]}"'
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip() == etag:
        return Response(
            status_code=304,
            headers={
                "ETag": etag,
                "Cache-Control": "private, no-transform, max-age=300",
            },
        )

    cached_bytes = record.meta.get("_cached_report_pdf")
    if cached_bytes is not None and isinstance(cached_bytes, bytes):
        pdf_bytes = cached_bytes
    else:
        # Lazy import AFTER the 404 check so unknown/expired ids stay 404 even
        # on a runtime without fpdf2.
        from ..processing.pdf_export import generate_document_report_pdf

        started = time.perf_counter()
        pdf_bytes = generate_document_report_pdf(record.to_report_payload())
        record.meta["_cached_report_pdf"] = pdf_bytes
        metrics.inc("pdf_exports_total", labels={"kind": "document_report"})
        metrics.observe("pdf_export_bytes", len(pdf_bytes), labels={"kind": "document_report"})
        metrics.observe(
            "pdf_export_duration_ms", (time.perf_counter() - started) * 1000, labels={"kind": "document_report"}
        )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="ura_document_report_{document_id[:8]}.pdf"'
            ),
            "ETag": etag,
            "Cache-Control": "private, no-transform, max-age=300",
        },
    )


@router.get("/v1/documents/{document_id}/status", tags=["documents"])
@limiter.limit(DOCUMENT_RATE_LIMIT)
def document_status(
    request: Request,
    document_id: str = Path(..., pattern=r"^[a-f0-9]{32}$"),
    _ctx: AuthContext = Depends(optional_user),
) -> dict[str, Any]:
    """Inspect processing status and metadata of an analysed document."""
    record = documents.get_document(
        document_id,
        session_id=request.headers.get("X-Session-ID", ""),
        user_id=_ctx.user_id,
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Document not found or expired")

    now = time.time()
    ttl_left = max(0, int(record.created_at + documents.DOCUMENT_TTL_SECONDS - now))
    return {
        "document_id": record.doc_id,
        "filename": record.filename,
        "kind": record.kind,
        "doc_type": record.doc_type,
        "status": "ready",
        "size_bytes": record.size_bytes,
        "tables_count": len(record.tables),
        "fields_count": sum(len(v) for v in record.fields.values()),
        "has_cached_report": bool(record.meta.get("_cached_report_pdf")),
        "expires_in_seconds": ttl_left,
    }


_INDEX_REBUILD_LOCK = threading.Lock()


@contextlib.contextmanager
def _exclusive_index_rebuild():
    """One rebuild at a time, in this process and across workers sharing the status dir.

    Two overlapping calls — or a retry after a proxy timeout, likely when a full
    rebuild takes minutes — both built the same candidate collection, and the
    second deleted the first's half-built candidate under it. A second call is
    now refused with 409 while one runs. The file lock covers the workers of one
    host; replicas on other hosts still need the lifecycle run as a single job.
    """
    if not _INDEX_REBUILD_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An index rebuild is already running")
    handle = None
    try:
        try:
            import fcntl

            from ..freshness import LIFECYCLE_STATUS_PATH

            path = LIFECYCLE_STATUS_PATH.with_name("index_rebuild.lock")
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise HTTPException(status_code=409, detail="An index rebuild is already running") from None
        except OSError:
            logger.warning("Index rebuild lock file unavailable; serialising within this process only", exc_info=True)
        yield
    finally:
        if handle is not None:
            handle.close()
        _INDEX_REBUILD_LOCK.release()


@router.post("/v1/index", tags=["knowledge"])
def trigger_indexing(
    request: Request,
    model: ChatModel = Depends(get_model),
) -> dict:
    """Trigger document re-indexing into the Qdrant vector store.

    Requires ``Authorization: Bearer <INDEX_API_KEY>`` when configured. Runs
    the staged index lifecycle (``app.vectorstore.index_lifecycle``): the whole corpus —
    FAQ, teacher QA, PDF and crawl chunks — into a candidate collection, a
    canary retrieval gate, then one atomic alias swap; then re-initialises the
    hybrid retriever (G286).

    It used to rebuild the live collection in place from FAQ and teacher-QA
    rows only, with ``recreate=True``. One call deleted the PDF and crawl
    corpus — most of the index — left every search failing while it rebuilt,
    and wrote no freshness status, so cached answers outlived the rebuild. A
    legacy physical collection (not an alias) is refused with 409 rather than
    deleted; the message names the alias to migrate to.
    """
    require_ops_key(request)

    from ..ingestion.faq_corpus import CorpusValidationError
    from ..freshness import write_lifecycle_status
    from ..vectorstore.index_lifecycle import IndexLifecycleError, rebuild_if_needed

    def _record_failure(exc: Exception) -> None:
        with contextlib.suppress(Exception):
            write_lifecycle_status(ok=False, error=str(exc))

    with _exclusive_index_rebuild():
        try:
            stats = rebuild_if_needed(force=True)
        except IndexLifecycleError as exc:
            _record_failure(exc)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CorpusValidationError as exc:
            _record_failure(exc)
            raise HTTPException(status_code=422, detail=f"Vector corpus validation failed: {exc}") from exc
        except Exception as exc:
            _record_failure(exc)
            # The alias may already point at the new collection: bookkeeping
            # after promotion re-raises. Re-bind, so this process does not go
            # on querying new vectors with the old BM25 state and cache stamp.
            model._retriever_ready = model._retriever.initialize()
            logger.exception("Index rebuild failed")
            raise HTTPException(status_code=500, detail="Index rebuild failed; see the lifecycle status") from exc

    # Re-initialise the retriever so it picks up the new collection
    model._retriever_ready = model._retriever.initialize()
    # Retire cached answers across Redis / in-memory caches and MT cache immediately
    from ..rag.cache import reset_index_stamp
    from .. import mt as _mt

    reset_index_stamp()
    cache_obj = getattr(model, "_cache", None)
    if cache_obj is not None and hasattr(cache_obj, "clear"):
        with contextlib.suppress(Exception):
            cache_obj.clear()
    with contextlib.suppress(Exception):
        _mt.cache.clear()
    # Same resolver as /ready and the chat path: a reindex that leaves the
    # retriever sparse-only must not report "hybrid" either.
    stats["retrieval_mode"] = active_retrieval_mode(
        model._retriever, ready=model._retriever_ready
    )

    return stats
