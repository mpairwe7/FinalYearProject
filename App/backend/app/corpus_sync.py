"""Self-updating Qdrant comparator, hot-swap engine, and tombstone coordinator.

Implements Phase E of the Sovereign Live Web Research & Self-Updating Qdrant plan:
1. Strict origin invariant (:func:`validate_qdrant_sync_origin`) ensuring only
   HTTPS documents from ``ura.go.ug``, ``www.ura.go.ug``, and ``ulii.org`` can
   ever modify Qdrant or the local PDF JSONL corpus.
2. Counterpart discovery (:func:`find_existing_qdrant_counterpart`) across
   ``App/Data/pdf_jsonl/`` and Qdrant for a given ``doc_family`` or ``sha256``.
3. Four-outcome comparison (:func:`compare_and_sync_ura_document`):
   - ``verified_current``: ``sha256`` matches existing corpus; stamp 7-day
     ``web_sync_ledger`` entry without touching Qdrant.
   - ``skipped_older``: discovered PDF has an older ``fiscal_year`` than the
     active Qdrant document; stamp ``web_sync_ledger`` and skip.
   - ``replaced_outdated``: discovered full guide has a strictly newer
     ``fiscal_year`` and ``page_count >= 0.5 * existing.page_count``; tombstone
     and delete old Qdrant points, embed and upsert new chunks, archive old
     ``.jsonl`` to ``App/Data/archive/``, write new ``.jsonl``, refresh
     ``index_freshness.json``, and stamp ``web_sync_ledger``.
   - ``added_new``: public notice or net-new document family; embed and upsert
     chunks additively into Qdrant, write ``.jsonl``, refresh freshness
     snapshot, and stamp ``web_sync_ledger``.
4. Fast-path skip check (:func:`should_skip_web_search_for_family`) consulting
   ``web_sync_ledger`` so subsequent turns serve directly from Qdrant in <200 ms.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._root import APP_DATA_ROOT as _APP_DATA_ROOT
from .ephemeral_pdf_sandbox import SandboxDocument
from .freshness import SNAPSHOT_PATH, write_snapshot
from .indexer import PDF_JSONL_DIR, QDRANT_COLLECTION, _embedding_text
from .pdf_corpus import PDF_JSONL_SCHEMA_VERSION, PDF_MANIFEST_NAME, _stable_id
from .retriever import deterministic_point_id, fiscal_year_rank
from .web_research import extract_fiscal_year, infer_doc_family

logger = logging.getLogger(__name__)

ARCHIVE_DIR = Path(os.getenv("PDF_ARCHIVE_DIR", str(_APP_DATA_ROOT / "archive")))
WEB_SYNC_TTL_SECONDS = int(os.getenv("WEB_SYNC_TTL_SECONDS", str(7 * 86400)))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_SYNC_LOCK_PREFIX = "ura:web_sync_lock:"

#: Only official Ugandan statutory/tax authority hosts may ever persist to Qdrant.
_QDRANT_SYNC_ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "ura.go.ug",
        "www.ura.go.ug",
        "ulii.org",
        "www.ulii.org",
    }
)

#: Families that represent additive or generic documents rather than single-edition handbooks.
_NON_REPLACING_FAMILIES: frozenset[str] = frozenset(
    {
        "",
        "general_tax",
        "ura_general_publication",
        "public_notice",
    }
)

_PROCESS_LOCKS_GUARD = threading.Lock()
_FAMILY_LOCKS: dict[str, threading.Lock] = {}
_redis_client: Any = None
_redis_init_attempted = False


class CorpusSyncOriginRejected(ValueError):
    """Raised when a document URL is not eligible to write to Qdrant."""


@dataclass
class ExistingCorpusDocument:
    """Metadata of an existing document in Qdrant / ``App/Data/pdf_jsonl/``."""

    source: str
    stem: str
    sha256: str
    fiscal_year: str
    doc_family: str
    page_count: int
    jsonl_path: Path | None = None


@dataclass
class SyncOutcome:
    """Result of comparing and syncing a :class:`SandboxDocument` with Qdrant."""

    action_taken: str  # "verified_current" | "skipped_older" | "replaced_outdated" | "added_new"
    doc_family: str
    source_uri: str
    ura_url: str
    fiscal_year: str
    superseded_source: str = ""
    upserted_points: int = 0
    deleted_old_source: bool = False
    tombstone_id: str = ""
    jsonl_path: str = ""
    archived_jsonl_path: str = ""
    ledger_entry: dict[str, Any] = field(default_factory=dict)


def _get_redis() -> Any:
    """Lazy-initialize optional Redis client for distributed sync locks."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    with _PROCESS_LOCKS_GUARD:
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


@contextlib.contextmanager
def _family_sync_lock(doc_family: str) -> Iterator[bool]:
    """Acquire an in-process and optional Redis lock for *doc_family*."""
    key = (doc_family or "general_tax").strip().lower()
    with _PROCESS_LOCKS_GUARD:
        proc_lock = _FAMILY_LOCKS.setdefault(key, threading.Lock())

    acquired_proc = proc_lock.acquire(timeout=15.0)
    if not acquired_proc:
        yield False
        return

    redis_key = f"{_SYNC_LOCK_PREFIX}{key}"
    r_client = _get_redis()
    acquired_redis = False
    try:
        if r_client is not None:
            try:
                acquired_redis = bool(r_client.set(redis_key, "1", nx=True, ex=60))
                if not acquired_redis:
                    yield False
                    return
            except Exception:
                logger.debug("Redis sync lock unavailable; using process lock only", exc_info=True)
        yield True
    finally:
        if acquired_redis and r_client is not None:
            with contextlib.suppress(Exception):
                r_client.delete(redis_key)
        proc_lock.release()


def validate_qdrant_sync_origin(url: str) -> None:
    """Enforce that *url* uses HTTPS and originates from ``ura.go.ug`` or ``ulii.org``."""
    raw = str(url or "").strip()
    if not raw:
        raise CorpusSyncOriginRejected("Empty document URL cannot be synced to Qdrant.")
    parsed = urlparse(raw)
    if (parsed.scheme or "").lower() != "https":
        raise CorpusSyncOriginRejected(f"Qdrant sync requires HTTPS scheme (got {parsed.scheme!r}): {raw}")
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _QDRANT_SYNC_ALLOWED_HOSTS:
        raise CorpusSyncOriginRejected(
            f"Host {host!r} is not authorized to modify Qdrant (allowed: {sorted(_QDRANT_SYNC_ALLOWED_HOSTS)})."
        )


def _inspect_jsonl_file(jsonl_path: Path) -> ExistingCorpusDocument | None:
    """Read metadata from a single ``.jsonl`` file in ``pdf_jsonl_dir``."""
    if not jsonl_path.is_file() or jsonl_path.name == PDF_MANIFEST_NAME:
        return None
    try:
        record_count = 0
        first_rec: dict[str, Any] = {}
        with jsonl_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s:
                    continue
                if record_count == 0:
                    first_rec = json.loads(s)
                record_count += 1
        if record_count == 0:
            return None
        stem = jsonl_path.stem
        source = str(first_rec.get("source") or f"{stem}.pdf")
        sha256 = str(first_rec.get("source_sha256") or "")
        fy = str(first_rec.get("fiscal_year") or extract_fiscal_year(source) or extract_fiscal_year(stem))
        family = str(
            first_rec.get("doc_family")
            or infer_doc_family(f"{source} {stem} {str(first_rec.get('text') or '')[:300]}")
        )
        return ExistingCorpusDocument(
            source=source,
            stem=stem,
            sha256=sha256,
            fiscal_year=fy,
            doc_family=family,
            page_count=record_count,
            jsonl_path=jsonl_path,
        )
    except Exception:
        logger.debug("Failed to inspect JSONL file %s", jsonl_path, exc_info=True)
        return None


def find_existing_qdrant_counterpart(
    doc_family: str,
    *,
    incoming_sha256: str = "",
    retriever: Any = None,
    pdf_jsonl_dir: Path | None = None,
) -> ExistingCorpusDocument | None:
    """Locate the active counterpart document in ``pdf_jsonl_dir`` or Qdrant for *doc_family*."""
    target_dir = pdf_jsonl_dir if pdf_jsonl_dir is not None else PDF_JSONL_DIR
    family_clean = (doc_family or "").strip()
    sha_clean = (incoming_sha256 or "").strip().lower()

    candidates: list[ExistingCorpusDocument] = []
    if target_dir.is_dir():
        for jsonl_path in sorted(target_dir.glob("*.jsonl")):
            info = _inspect_jsonl_file(jsonl_path)
            if info is None:
                continue
            if sha_clean and info.sha256.lower() == sha_clean:
                return info
            if family_clean and family_clean not in _NON_REPLACING_FAMILIES and info.doc_family == family_clean:
                candidates.append(info)

    # Also check Qdrant payload if retriever client is available and no local match was found
    client = getattr(retriever, "_client", None) if retriever is not None else None
    collection_name = getattr(retriever, "collection_name", None) or QDRANT_COLLECTION
    if client is not None and not candidates:
        try:
            from qdrant_client import models

            should_conditions: list[Any] = []
            if sha_clean:
                should_conditions.append(
                    models.FieldCondition(
                        key="source_sha256",
                        match=models.MatchValue(value=sha_clean),
                    )
                )
            if family_clean and family_clean not in _NON_REPLACING_FAMILIES:
                should_conditions.append(
                    models.FieldCondition(
                        key="doc_family",
                        match=models.MatchValue(value=family_clean),
                    )
                )

            by_source: dict[str, dict[str, Any]] = {}
            offset: Any = None
            while should_conditions:
                scrolled = client.scroll(
                    collection_name=collection_name,
                    scroll_filter=models.Filter(should=should_conditions),
                    limit=256,
                    offset=offset,
                    with_payload=True,
                    with_vectors=False,
                )
                if isinstance(scrolled, tuple):
                    points, offset = scrolled[0], scrolled[1]
                else:
                    points = getattr(scrolled, "points", [])
                    offset = getattr(scrolled, "next_page_offset", None)

                for pt in points or []:
                    payload = getattr(pt, "payload", None) or (pt if isinstance(pt, dict) else {})
                    if not isinstance(payload, dict) or payload.get("_meta"):
                        continue
                    src = str(payload.get("source") or "").strip()
                    if not src:
                        continue
                    rec = by_source.setdefault(
                        src,
                        {
                            "source": src,
                            "stem": Path(src).stem,
                            "sha256": str(payload.get("source_sha256") or ""),
                            "fiscal_year": str(payload.get("fiscal_year") or extract_fiscal_year(src)),
                            "doc_family": str(payload.get("doc_family") or infer_doc_family(src)),
                            "count": 0,
                        },
                    )
                    rec["count"] += 1

                if offset is None or not points:
                    break

            for meta in by_source.values():
                if sha_clean and meta["sha256"].lower() == sha_clean:
                    return ExistingCorpusDocument(
                        source=meta["source"],
                        stem=meta["stem"],
                        sha256=meta["sha256"],
                        fiscal_year=meta["fiscal_year"],
                        doc_family=meta["doc_family"],
                        page_count=int(meta["count"]),
                        jsonl_path=None,
                    )
                if (
                    family_clean
                    and family_clean not in _NON_REPLACING_FAMILIES
                    and meta["doc_family"] == family_clean
                ):
                    candidates.append(
                        ExistingCorpusDocument(
                            source=meta["source"],
                            stem=meta["stem"],
                            sha256=meta["sha256"],
                            fiscal_year=meta["fiscal_year"],
                            doc_family=meta["doc_family"],
                            page_count=int(meta["count"]),
                            jsonl_path=None,
                        )
                    )
        except Exception:
            logger.debug("Qdrant counterpart scroll failed", exc_info=True)

    if not candidates:
        return None

    # Return the newest existing counterpart by fiscal_year_rank
    candidates.sort(
        key=lambda c: (
            fiscal_year_rank(c.fiscal_year) or 0,
            c.page_count,
        ),
        reverse=True,
    )
    return candidates[0]


def _delete_source_from_qdrant(
    retriever: Any,
    source_name: str,
    *,
    keep_sha256: str = "",
) -> bool:
    """Delete Qdrant points whose ``source`` matches *source_name* (excluding *keep_sha256* when set)."""
    client = getattr(retriever, "_client", None) if retriever is not None else None
    if client is None or not source_name:
        return False
    collection_name = getattr(retriever, "collection_name", None) or QDRANT_COLLECTION
    try:
        from qdrant_client import models

        must_not = (
            [
                models.FieldCondition(
                    key="source_sha256",
                    match=models.MatchValue(value=keep_sha256),
                )
            ]
            if keep_sha256
            else None
        )
        selector = models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="source",
                        match=models.MatchValue(value=source_name),
                    )
                ],
                must_not=must_not,
            )
        )
        client.delete(collection_name=collection_name, points_selector=selector)
        return True
    except Exception:
        logger.warning("Failed to delete superseded source %s from Qdrant", source_name, exc_info=True)
        return False


def _build_canonical_chunks(sandbox_doc: SandboxDocument) -> list[dict[str, Any]]:
    """Build canonical chunk dictionaries with dense post-filter indices for both Qdrant and JSONL."""
    canonical: list[dict[str, Any]] = []
    for raw_idx, raw_chunk in enumerate(sandbox_doc.chunks):
        text = str(raw_chunk.get("text") or "").strip()
        if not text:
            continue
        dense_idx = len(canonical)
        raw_page = raw_chunk.get("page", raw_idx + 1)
        section = str(raw_chunk.get("section") or f"Page {raw_page}").strip()
        heading_trail = [section] if section else []
        chunk_id = _stable_id(sandbox_doc.filename, dense_idx, text)
        prefix = f"[Document: {sandbox_doc.stem}]"
        canonical.append(
            {
                "text": text,
                "embed_text": f"{prefix}\n\n{text}",
                "contextual_prefix": prefix,
                "heading_trail": heading_trail,
                "source": sandbox_doc.filename,
                "source_url": sandbox_doc.url,
                "source_sha256": sandbox_doc.sha256,
                "fiscal_year": sandbox_doc.fiscal_year,
                "doc_family": sandbox_doc.doc_family,
                "doc_type": "pdf_chunk",
                "section": " > ".join(heading_trail),
                "page": "",
                "chunk_id": chunk_id,
                "chunk_index": dense_idx,
                "char_count": len(text),
                "ingested_via": "ura_web_auto_sync",
            }
        )
    return canonical


def _embed_and_upsert_chunks(
    sandbox_doc: SandboxDocument,
    retriever: Any,
    *,
    canonical_chunks: list[dict[str, Any]] | None = None,
) -> int:
    """Embed canonical chunks for *sandbox_doc* and upsert deterministic points into Qdrant."""
    client = getattr(retriever, "_client", None) if retriever is not None else None
    docs_to_index = canonical_chunks if canonical_chunks is not None else _build_canonical_chunks(sandbox_doc)
    if client is None or not docs_to_index:
        return 0
    collection_name = getattr(retriever, "collection_name", None) or QDRANT_COLLECTION
    dense_model = getattr(retriever, "_dense_model", None)
    sparse_encoder = getattr(retriever, "_sparse_encoder", None) or getattr(retriever, "_bm25", None)

    embed_texts = [_embedding_text(d) for d in docs_to_index]
    dense_vecs: list[list[float]] | None = None
    if dense_model is not None:
        try:
            encoded = dense_model.encode(embed_texts, show_progress_bar=False)
            dense_vecs = [
                row.tolist() if hasattr(row, "tolist") else list(row)
                for row in encoded
            ]
        except Exception:
            logger.warning("Dense encoding failed during corpus_sync upsert", exc_info=True)
            raise

    from qdrant_client import models

    points: list[models.PointStruct] = []
    for i, doc in enumerate(docs_to_index):
        vectors: dict[str, Any] = {}
        if dense_vecs is not None and i < len(dense_vecs):
            vectors["dense"] = dense_vecs[i]
        if sparse_encoder is not None and hasattr(sparse_encoder, "encode_document"):
            try:
                s_idx, s_val = sparse_encoder.encode_document(_embedding_text(doc))
                if s_idx:
                    vectors["sparse"] = models.SparseVector(indices=list(s_idx), values=list(s_val))
            except Exception:
                logger.debug("Sparse encoding failed for chunk %d", i, exc_info=True)

        payload = {
            k: v
            for k, v in doc.items()
            if k not in {"embed_text", "chunk_index", "char_count"}
        }
        points.append(
            models.PointStruct(
                id=deterministic_point_id(doc),
                vector=vectors,
                payload=payload,
            )
        )

    client.upsert(collection_name=collection_name, points=points)
    return len(points)


def _write_canonical_pdf_jsonl(
    sandbox_doc: SandboxDocument,
    pdf_jsonl_dir: Path,
    *,
    canonical_chunks: list[dict[str, Any]] | None = None,
    removed_source: str = "",
) -> Path:
    """Write ``{sandbox_doc.stem}.jsonl`` in canonical :mod:`app.pdf_corpus` schema."""
    pdf_jsonl_dir.mkdir(parents=True, exist_ok=True)
    target = pdf_jsonl_dir / f"{sandbox_doc.stem}.jsonl"
    tmp = target.with_suffix(".jsonl.tmp")
    built = canonical_chunks if canonical_chunks is not None else _build_canonical_chunks(sandbox_doc)
    records: list[dict[str, Any]] = [
        {
            "schema_version": PDF_JSONL_SCHEMA_VERSION,
            "record_type": "pdf_chunk",
            "chunk_id": item["chunk_id"],
            "text": item["text"],
            "contextual_prefix": item["contextual_prefix"],
            "heading_trail": item["heading_trail"],
            "source": item["source"],
            "source_url": item["source_url"],
            "source_sha256": item["source_sha256"],
            "fiscal_year": item["fiscal_year"],
            "doc_family": item["doc_family"],
            "chunk_index": item["chunk_index"],
            "char_count": item["char_count"],
        }
        for item in built
    ]

    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=True) + "\n")
    tmp.replace(target)
    _update_pdf_manifest_if_present(
        pdf_jsonl_dir,
        sandbox_doc=sandbox_doc,
        record_count=len(records),
        total_chars=sum(r["char_count"] for r in records),
        removed_source=removed_source,
    )
    return target


def _update_pdf_manifest_if_present(
    pdf_jsonl_dir: Path,
    *,
    sandbox_doc: SandboxDocument,
    record_count: int,
    total_chars: int,
    removed_source: str = "",
) -> None:
    """Update ``pdf_corpus_manifest.json`` in *pdf_jsonl_dir* if present."""
    manifest_path = pdf_jsonl_dir / PDF_MANIFEST_NAME
    if not manifest_path.is_file():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list):
            return
        sources: list[dict[str, Any]] = [
            s
            for s in manifest["sources"]
            if isinstance(s, dict)
            and s.get("pdf") not in {sandbox_doc.filename, removed_source}
        ]
        sources.append(
            {
                "pdf": sandbox_doc.filename,
                "jsonl": f"{sandbox_doc.stem}.jsonl",
                "sha256": sandbox_doc.sha256,
                "records": record_count,
                "fiscal_year": sandbox_doc.fiscal_year,
                "chars": total_chars,
            }
        )
        sources.sort(key=lambda s: str(s.get("pdf") or ""))
        manifest["sources"] = sources
        manifest["source_count"] = len(sources)
        manifest["record_count"] = sum(int(s.get("records") or 0) for s in sources)
        tmp = manifest_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        tmp.replace(manifest_path)
    except Exception:
        logger.debug("Optional pdf_corpus_manifest.json update skipped", exc_info=True)


def _archive_superseded_jsonl(
    existing: ExistingCorpusDocument,
    pdf_jsonl_dir: Path,
    archive_dir: Path,
) -> Path | None:
    """Move a superseded ``.jsonl`` file from *pdf_jsonl_dir* to *archive_dir*."""
    src_path = existing.jsonl_path or (pdf_jsonl_dir / f"{existing.stem}.jsonl")
    if not src_path.is_file():
        return None
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest_path = archive_dir / src_path.name
    shutil.move(str(src_path), str(dest_path))
    return dest_path


def _record_ledger_entry(
    *,
    topic_key: str,
    doc_family: str,
    source_uri: str,
    ura_url: str,
    pdf_sha256: str,
    fiscal_year: str,
    action_taken: str,
    superseded_source: str = "",
    ttl_seconds: int = WEB_SYNC_TTL_SECONDS,
) -> dict[str, Any]:
    """Persist a sync outcome row into ``web_sync_ledger``."""
    from . import database as db

    return db.upsert_web_sync_entry(
        topic_key=topic_key or doc_family or source_uri,
        doc_family=doc_family or "general_tax",
        source_uri=source_uri,
        ura_url=ura_url,
        pdf_sha256=pdf_sha256,
        fiscal_year=fiscal_year,
        action_taken=action_taken,
        superseded_source=superseded_source,
        ttl_seconds=float(max(60, ttl_seconds)),
    )


def should_skip_web_search_for_family(
    *,
    doc_family: str = "",
    topic_key: str = "",
) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(True, ledger_entry)`` when ``web_sync_ledger`` has a fresh verification for *doc_family* or *topic_key*."""
    fam = (doc_family or "").strip()
    top = (topic_key or "").strip()
    if not fam and not top:
        return False, None
    if fam in _NON_REPLACING_FAMILIES and not top:
        return False, None
    try:
        from . import database as db

        query_family = fam if (fam and fam not in _NON_REPLACING_FAMILIES) else None
        entry = db.get_active_web_sync_entry(
            doc_family=query_family,
            topic_key=top or None,
        )
        if entry is not None:
            return True, entry
    except Exception:
        logger.debug("web_sync_ledger lookup failed in should_skip_web_search_for_family", exc_info=True)
    return False, None


def compare_and_sync_ura_document(
    sandbox_doc: SandboxDocument,
    retriever: Any = None,
    *,
    topic_key: str = "",
    pdf_jsonl_dir: Path | None = None,
    archive_dir: Path | None = None,
    freshness_snapshot_path: Path | None = None,
    freshness_roots: list[Path] | None = None,
    persist_files: bool = True,
) -> SyncOutcome:
    """Compare *sandbox_doc* against Qdrant/JSONL, hot-swap or add chunks, and update ``web_sync_ledger``."""
    validate_qdrant_sync_origin(sandbox_doc.url)

    target_jsonl_dir = pdf_jsonl_dir if pdf_jsonl_dir is not None else PDF_JSONL_DIR
    target_archive_dir = archive_dir if archive_dir is not None else ARCHIVE_DIR
    family = (sandbox_doc.doc_family or infer_doc_family(sandbox_doc.filename) or "general_tax").strip()
    effective_topic = (topic_key or family or sandbox_doc.stem).strip()

    with _family_sync_lock(family) as locked:
        if not locked:
            return SyncOutcome(
                action_taken="locked_skip",
                doc_family=family,
                source_uri=sandbox_doc.filename,
                ura_url=sandbox_doc.url,
                fiscal_year=sandbox_doc.fiscal_year,
            )

        existing = find_existing_qdrant_counterpart(
            family,
            incoming_sha256=sandbox_doc.sha256,
            retriever=retriever,
            pdf_jsonl_dir=target_jsonl_dir,
        )

        # Outcome 1: Byte-identical document already in Qdrant / pdf_jsonl
        if existing is not None and existing.sha256 and existing.sha256.lower() == sandbox_doc.sha256.lower():
            ledger = _record_ledger_entry(
                topic_key=effective_topic,
                doc_family=family,
                source_uri=existing.source,
                ura_url=sandbox_doc.url,
                pdf_sha256=sandbox_doc.sha256,
                fiscal_year=sandbox_doc.fiscal_year or existing.fiscal_year,
                action_taken="verified_current",
            )
            return SyncOutcome(
                action_taken="verified_current",
                doc_family=family,
                source_uri=existing.source,
                ura_url=sandbox_doc.url,
                fiscal_year=sandbox_doc.fiscal_year or existing.fiscal_year,
                ledger_entry=ledger,
            )

        incoming_fy_rank = fiscal_year_rank(sandbox_doc.fiscal_year)
        existing_fy_rank = fiscal_year_rank(existing.fiscal_year) if existing is not None else None

        # Outcome 2: Discovered PDF is an older fiscal year than the active Qdrant counterpart
        if (
            existing is not None
            and incoming_fy_rank is not None
            and existing_fy_rank is not None
            and incoming_fy_rank < existing_fy_rank
        ):
            ledger = _record_ledger_entry(
                topic_key=effective_topic,
                doc_family=family,
                source_uri=existing.source,
                ura_url=sandbox_doc.url,
                pdf_sha256=sandbox_doc.sha256,
                fiscal_year=existing.fiscal_year,
                action_taken="skipped_older",
                superseded_source=sandbox_doc.filename,
            )
            return SyncOutcome(
                action_taken="skipped_older",
                doc_family=family,
                source_uri=existing.source,
                ura_url=sandbox_doc.url,
                fiscal_year=existing.fiscal_year,
                superseded_source=sandbox_doc.filename,
                ledger_entry=ledger,
            )

        canonical_chunks = _build_canonical_chunks(sandbox_doc)

        # Outcome 3: Newer full guide replaces outdated counterpart
        min_pages = max(1, int(0.5 * existing.page_count)) if existing is not None else 1
        if (
            existing is not None
            and sandbox_doc.doc_kind == "full_guide"
            and family not in _NON_REPLACING_FAMILIES
            and incoming_fy_rank is not None
            and existing_fy_rank is not None
            and incoming_fy_rank > existing_fy_rank
            and sandbox_doc.page_count >= min_pages
        ):
            from . import database as db

            # Upsert the new chunks into Qdrant first so an embedding/Qdrant failure
            # never leaves the collection without a guide for this family.
            upserted = _embed_and_upsert_chunks(
                sandbox_doc,
                retriever,
                canonical_chunks=canonical_chunks,
            )
            if upserted <= 0:
                raise RuntimeError(
                    f"Refusing to supersede {existing.source}: no chunks of {sandbox_doc.filename} were indexed."
                )

            same_source = existing.source.strip().lower() == sandbox_doc.filename.strip().lower()
            tomb_id = ""
            if not same_source:
                tomb = db.add_corpus_tombstone(
                    source_uri=existing.source,
                    reason=(
                        f"Auto-superseded by {sandbox_doc.filename} ({sandbox_doc.fiscal_year}) "
                        f"from {sandbox_doc.url}"
                    ),
                    superseded_by=sandbox_doc.filename,
                    created_by="corpus_sync",
                )
                tomb_id = str(tomb.get("id") or "")
            deleted = _delete_source_from_qdrant(
                retriever,
                existing.source,
                keep_sha256=sandbox_doc.sha256 if same_source else "",
            )

            written_jsonl = ""
            archived_jsonl = ""
            if persist_files:
                archived_path = _archive_superseded_jsonl(existing, target_jsonl_dir, target_archive_dir)
                if archived_path is not None:
                    archived_jsonl = str(archived_path)
                new_path = _write_canonical_pdf_jsonl(
                    sandbox_doc,
                    target_jsonl_dir,
                    canonical_chunks=canonical_chunks,
                    removed_source=existing.source,
                )
                written_jsonl = str(new_path)
                snap_target = freshness_snapshot_path or SNAPSHOT_PATH
                roots = freshness_roots if freshness_roots is not None else [target_jsonl_dir]
                if freshness_snapshot_path is not None or snap_target.is_file():
                    with contextlib.suppress(Exception):
                        write_snapshot(path=snap_target, roots=roots)

            ledger = _record_ledger_entry(
                topic_key=effective_topic,
                doc_family=family,
                source_uri=sandbox_doc.filename,
                ura_url=sandbox_doc.url,
                pdf_sha256=sandbox_doc.sha256,
                fiscal_year=sandbox_doc.fiscal_year,
                action_taken="replaced_outdated",
                superseded_source=existing.source,
            )
            return SyncOutcome(
                action_taken="replaced_outdated",
                doc_family=family,
                source_uri=sandbox_doc.filename,
                ura_url=sandbox_doc.url,
                fiscal_year=sandbox_doc.fiscal_year,
                superseded_source=existing.source,
                upserted_points=upserted,
                deleted_old_source=deleted,
                tombstone_id=tomb_id,
                jsonl_path=written_jsonl,
                archived_jsonl_path=archived_jsonl,
                ledger_entry=ledger,
            )

        # Guard: if a single-edition counterpart exists but replacement preconditions were
        # not met (e.g. missing fiscal year, same fiscal year with different hash, or truncated
        # page count), do not add a conflicting second edition of the same handbook.
        if existing is not None and family not in _NON_REPLACING_FAMILIES and sandbox_doc.doc_kind != "public_notice":
            return SyncOutcome(
                action_taken="needs_review",
                doc_family=family,
                source_uri=sandbox_doc.filename,
                ura_url=sandbox_doc.url,
                fiscal_year=sandbox_doc.fiscal_year,
                superseded_source=existing.source,
            )

        # Outcome 4: Additive upsert (public notice or net-new document family)
        upserted = _embed_and_upsert_chunks(
            sandbox_doc,
            retriever,
            canonical_chunks=canonical_chunks,
        )
        written_jsonl = ""
        if persist_files:
            new_path = _write_canonical_pdf_jsonl(
                sandbox_doc,
                target_jsonl_dir,
                canonical_chunks=canonical_chunks,
            )
            written_jsonl = str(new_path)
            snap_target = freshness_snapshot_path or SNAPSHOT_PATH
            roots = freshness_roots if freshness_roots is not None else [target_jsonl_dir]
            if freshness_snapshot_path is not None or snap_target.is_file():
                with contextlib.suppress(Exception):
                    write_snapshot(path=snap_target, roots=roots)

        ledger = _record_ledger_entry(
            topic_key=effective_topic,
            doc_family=family,
            source_uri=sandbox_doc.filename,
            ura_url=sandbox_doc.url,
            pdf_sha256=sandbox_doc.sha256,
            fiscal_year=sandbox_doc.fiscal_year,
            action_taken="added_new",
        )
        return SyncOutcome(
            action_taken="added_new",
            doc_family=family,
            source_uri=sandbox_doc.filename,
            ura_url=sandbox_doc.url,
            fiscal_year=sandbox_doc.fiscal_year,
            upserted_points=upserted,
            jsonl_path=written_jsonl,
            ledger_entry=ledger,
        )


def serialize_sync_outcome(outcome: SyncOutcome) -> dict[str, Any]:
    """Serialize a :class:`SyncOutcome` to a JSON-compatible dictionary."""
    return asdict(outcome)
