"""Index freshness — detect source drift against the serving Qdrant index.

G27: when URA updates a FAQ CSV or a PDF export, Qdrant still returns the
old passage until someone re-indexes. This module hashes every corpus
source the indexer reads, compares against a snapshot written at the end
of a successful ``build_index``, and exits non-zero when they diverge.

CI and a nightly cron can run ``python -m app.freshness --check``. Add
``--verify-qdrant`` to compare the shipped corpus hash with the hash stamped
into the serving Qdrant collection. Optional ``--notify`` posts to
``FRESHNESS_SLACK_WEBHOOK`` (https only). Optional ``--enqueue`` writes
a request file; rebuild orchestration lives in :mod:`app.index_lifecycle`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
try:
    from datetime import UTC, datetime
except ImportError:
    from datetime import datetime, timezone

    UTC = timezone.utc  # type: ignore[assignment]
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from ._root import APP_DATA_ROOT, PROJECT_ROOT

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(
    os.getenv(
        "INDEX_FRESHNESS_PATH",
        str(APP_DATA_ROOT.parent / "Model" / "index_freshness.json"),
    )
)
STATUS_PATH = Path(
    os.getenv(
        "INDEX_FRESHNESS_STATUS_PATH",
        str(SNAPSHOT_PATH.with_name("index_freshness_status.json")),
    )
)
LIFECYCLE_STATUS_PATH = Path(
    os.getenv(
        "INDEX_LIFECYCLE_STATUS_PATH",
        str(SNAPSHOT_PATH.with_name("index_lifecycle_status.json")),
    )
)
BACKUP_STATUS_PATH = Path(
    os.getenv(
        "QDRANT_BACKUP_STATUS_PATH",
        str(SNAPSHOT_PATH.with_name("qdrant_backup_status.json")),
    )
)
REINDEX_REQUEST_PATH = Path(
    os.getenv(
        "INDEX_REINDEX_REQUEST_PATH",
        str(SNAPSHOT_PATH.with_name("index_reindex_requested.json")),
    )
)

_SOURCE_DIRS: tuple[Path, ...] = (
    Path(os.getenv("DATA_DIR", str(APP_DATA_ROOT / "dataset"))),
    Path(os.getenv("FAQ_JSONL_DIR", str(APP_DATA_ROOT / "faq_jsonl"))),
    Path(os.getenv("TEACHER_QA_DIR", str(APP_DATA_ROOT / "teacher_qa"))),
    Path(os.getenv("PDF_JSONL_DIR", str(APP_DATA_ROOT / "pdf_jsonl"))),
    Path(os.getenv("CRAWL_JSONL_DIR", str(APP_DATA_ROOT / "crawl_jsonl"))),
)
_SOURCE_SUFFIXES = {".csv", ".jsonl", ".pdf"}


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def iter_source_files(roots: Iterable[Path] | None = None) -> list[Path]:
    """Corpus files the indexer would ingest, sorted for stable hashing."""
    found: list[Path] = []
    for root in roots or _SOURCE_DIRS:
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES:
                found.append(path)
    return sorted(found, key=lambda p: _rel(p))


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 256), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_sources(roots: Iterable[Path] | None = None) -> dict[str, Any]:
    """Hash every current source file into a comparable snapshot."""
    files: dict[str, dict[str, Any]] = {}
    digest = hashlib.sha256()
    for path in iter_source_files(roots):
        rel = _rel(path)
        sha = file_digest(path)
        files[rel] = {
            "sha256": sha,
            "bytes": path.stat().st_size,
        }
        digest.update(rel.encode())
        digest.update(b"\x00")
        digest.update(sha.encode())
        digest.update(b"\x01")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "corpus_hash": digest.hexdigest(),
        "file_count": len(files),
        "files": files,
    }


@dataclass
class FreshnessReport:
    """Delta between the live tree and the last indexed snapshot."""

    ok: bool
    corpus_hash: str
    previous_hash: str = ""
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    snapshot_missing: bool = False
    index_corpus_hash: str = ""
    index_snapshot_missing: bool = False
    index_drift: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "corpus_hash": self.corpus_hash,
            "previous_hash": self.previous_hash,
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "snapshot_missing": self.snapshot_missing,
            "index_corpus_hash": self.index_corpus_hash,
            "index_snapshot_missing": self.index_snapshot_missing,
            "index_drift": self.index_drift,
            "drift_count": len(self.added) + len(self.removed) + len(self.changed),
        }


def load_snapshot(path: Path | None = None) -> dict[str, Any] | None:
    target = path or SNAPSHOT_PATH
    if not target.is_file():
        return None
    with target.open() as fh:
        return json.load(fh)


def write_snapshot(
    snapshot: dict[str, Any] | None = None,
    *,
    path: Path | None = None,
    roots: Iterable[Path] | None = None,
) -> Path:
    """Persist a snapshot after a successful index (or ``--write``)."""
    target = path or SNAPSHOT_PATH
    payload = snapshot or snapshot_sources(roots)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(target)
    logger.info(
        "Wrote index freshness snapshot %s files=%d hash=%s",
        target,
        payload.get("file_count", 0),
        str(payload.get("corpus_hash", ""))[:12],
    )
    return target


def compare(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
) -> FreshnessReport:
    current_files = current.get("files") or {}
    if previous is None:
        return FreshnessReport(
            ok=False,
            corpus_hash=str(current.get("corpus_hash") or ""),
            snapshot_missing=True,
            added=sorted(current_files),
        )
    previous_files = previous.get("files") or {}
    added = sorted(set(current_files) - set(previous_files))
    removed = sorted(set(previous_files) - set(current_files))
    changed = sorted(
        rel
        for rel in set(current_files) & set(previous_files)
        if current_files[rel].get("sha256") != previous_files[rel].get("sha256")
    )
    return FreshnessReport(
        ok=not (added or removed or changed),
        corpus_hash=str(current.get("corpus_hash") or ""),
        previous_hash=str(previous.get("corpus_hash") or ""),
        added=added,
        removed=removed,
        changed=changed,
    )


def check(
    *,
    snapshot_path: Path | None = None,
    roots: Iterable[Path] | None = None,
) -> FreshnessReport:
    """Compare the live corpus to the last indexed snapshot."""
    return compare(snapshot_sources(roots), load_snapshot(snapshot_path))


def qdrant_index_hash(
    *,
    url: str | None = None,
    collection: str | None = None,
    api_key: str | None = None,
) -> str:
    """Read the source hash committed with the serving Qdrant collection.

    The binding sentinel lives in the same collection (or Qdrant alias) used
    by the retriever. An unavailable server or missing sentinel is represented
    by an empty hash so callers can report that condition explicitly.
    """
    from qdrant_client import QdrantClient

    from .retriever import bm25_binding_sentinel_id

    index_url = url or os.getenv("QDRANT_URL", "http://localhost:6333")
    index_collection = collection or os.getenv("QDRANT_COLLECTION", "ura_knowledge_base")
    client = QdrantClient(
        url=index_url,
        api_key=api_key if api_key is not None else (os.getenv("QDRANT_API_KEY") or None),
        timeout=5,
    )
    points = client.retrieve(
        collection_name=index_collection,
        ids=[bm25_binding_sentinel_id(index_collection)],
        with_payload=True,
        with_vectors=False,
    )
    if not points:
        return ""
    return str((points[0].payload or {}).get("source_corpus_hash") or "")


def compare_index_hash(report: FreshnessReport, index_corpus_hash: str) -> FreshnessReport:
    """Add Qdrant binding state to a source-snapshot freshness report."""
    report.index_corpus_hash = index_corpus_hash
    report.index_snapshot_missing = not bool(index_corpus_hash)
    report.index_drift = bool(index_corpus_hash) and index_corpus_hash != report.corpus_hash
    report.ok = report.ok and not report.index_snapshot_missing and not report.index_drift
    return report


def write_status(report: FreshnessReport, path: Path | None = None) -> Path:
    """Persist the last check so /ready does not re-hash the corpus."""
    target = path or STATUS_PATH
    payload = {
        **report.to_dict(),
        "checked_at": datetime.now(UTC).isoformat(),
        "reindex_hint": "python -m app.index_lifecycle --rebuild",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(target)
    return target


def load_status(path: Path | None = None) -> dict[str, Any] | None:
    target = path or STATUS_PATH
    if not target.is_file():
        return None
    with target.open() as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else None


def _write_operational_status(target: Path, payload: dict[str, Any]) -> Path:
    """Atomically persist a small operational status record for `/metrics`."""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(target)
    return target


def _load_operational_status(target: Path) -> dict[str, Any] | None:
    if not target.is_file():
        return None
    with target.open() as fh:
        data = json.load(fh)
    return data if isinstance(data, dict) else None


def write_lifecycle_status(
    *,
    ok: bool,
    reindexed: bool = False,
    collection: str = "",
    source_corpus_hash: str = "",
    error: str = "",
    path: Path | None = None,
) -> Path:
    """Record the last staged rebuild result for Prometheus and operations."""
    return _write_operational_status(
        path or LIFECYCLE_STATUS_PATH,
        {
            "ok": ok,
            "reindexed": reindexed,
            "collection": collection,
            "source_corpus_hash": source_corpus_hash,
            "error": error,
            "last_attempt_at": datetime.now(UTC).isoformat(),
        },
    )


def load_lifecycle_status(path: Path | None = None) -> dict[str, Any] | None:
    """Return the last staged-rebuild result, if the indexer has run."""
    return _load_operational_status(path or LIFECYCLE_STATUS_PATH)


def write_backup_status(
    *,
    ok: bool,
    collection: str = "",
    snapshot: str = "",
    error: str = "",
    restore_drill_ok: bool | None = None,
    restore_drill_error: str = "",
    path: Path | None = None,
) -> Path:
    """Record the last retained-Qdrant-backup result for Prometheus."""
    target = path or BACKUP_STATUS_PATH
    payload = _load_operational_status(target) or {}
    payload.update(
        {
            "ok": ok,
            "collection": collection,
            "snapshot": snapshot,
            "error": error,
            "last_attempt_at": datetime.now(UTC).isoformat(),
        }
    )
    if restore_drill_ok is not None:
        payload.update(
            {
                "restore_drill_ok": restore_drill_ok,
                "restore_drill_error": restore_drill_error,
                "last_restore_drill_at": datetime.now(UTC).isoformat(),
            }
        )
    return _write_operational_status(
        target,
        payload,
    )


def load_backup_status(path: Path | None = None) -> dict[str, Any] | None:
    """Return the last backup result, if scheduled backup is enabled."""
    return _load_operational_status(path or BACKUP_STATUS_PATH)


def slack_webhook_url(raw: str | None = None) -> str:
    """Return an https Slack webhook, or empty if unset / not https."""
    url = (raw if raw is not None else os.getenv("FRESHNESS_SLACK_WEBHOOK") or "").strip()
    if url.startswith("https://"):
        return url
    return ""


def notify_drift(report: FreshnessReport, *, webhook: str | None = None) -> bool:
    """POST a short Slack payload on drift. Never logs the URL. Best-effort.

    Returns True only when a request was accepted. Missing snapshot, a
    matching corpus, or a missing/non-https webhook are silent no-ops.
    """
    if report.ok or report.snapshot_missing:
        return False
    url = slack_webhook_url(webhook)
    if not url:
        return False
    text = (
        f"URA index drift: +{len(report.added)} -{len(report.removed)} "
        f"~{len(report.changed)}; qdrant_index_drift={report.index_drift}. "
        "Run the safe rebuild: python -m app.index_lifecycle --rebuild"
    )
    body = json.dumps({"text": text}).encode()
    req = Request(  # noqa: S310 -- the URL is restricted to HTTPS below.
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=5) as resp:  # nosec B310 # noqa: S310 — https-only above
            return 200 <= getattr(resp, "status", 200) < 300
    except (URLError, TimeoutError, OSError):
        logger.warning("Freshness Slack notify failed (webhook not logged)")
        return False


def enqueue_reindex_request(
    report: FreshnessReport,
    *,
    path: Path | None = None,
) -> Path | None:
    """Write a reindex *request* file. Does not run the indexer.

    An ops window still has to run the staged, alias-based rebuild.
    """
    if report.ok or report.snapshot_missing:
        return None
    target = path or REINDEX_REQUEST_PATH
    payload = {
        **report.to_dict(),
        "requested_at": datetime.now(UTC).isoformat(),
        "auto_reindex": False,
        "reindex_hint": "python -m app.index_lifecycle --rebuild",
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    tmp.replace(target)
    logger.info("Wrote reindex request %s (indexer not started)", target)
    return target


def main() -> int:  # pragma: no cover - CLI
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Check or write the index freshness snapshot")
    parser.add_argument("--check", action="store_true", help="Exit 1 when sources drifted")
    parser.add_argument("--write", action="store_true", help="Write a snapshot of the current tree")
    parser.add_argument(
        "--write-status",
        action="store_true",
        help="Write index_freshness_status.json for the /v1/index/freshness probe",
    )
    parser.add_argument("--snapshot", type=str, default=str(SNAPSHOT_PATH))
    parser.add_argument(
        "--notify",
        action="store_true",
        help="On drift, POST to FRESHNESS_SLACK_WEBHOOK (https only; no-op if unset)",
    )
    parser.add_argument(
        "--enqueue",
        action="store_true",
        help="On drift, write a reindex request file (does not run the indexer)",
    )
    parser.add_argument(
        "--verify-qdrant",
        action="store_true",
        help="Compare the source hash with the one stamped in the serving Qdrant collection",
    )
    args = parser.parse_args()
    path = Path(args.snapshot)

    if args.write:
        write_snapshot(path=path)
        return 0
    if not args.check:
        parser.error("specify --check or --write")

    report = check(snapshot_path=path)
    if args.verify_qdrant:
        try:
            compare_index_hash(report, qdrant_index_hash())
        except Exception:
            logger.warning("Could not read the Qdrant index hash", exc_info=True)
            compare_index_hash(report, "")
    if args.write_status:
        write_status(report)
    if args.notify:
        notify_drift(report)
    if args.enqueue:
        enqueue_reindex_request(report)
    sys.stdout.write(json.dumps(report.to_dict(), indent=2) + "\n")
    if report.snapshot_missing:
        logger.error("No freshness snapshot at %s — run indexer or --write", path)
        return 2
    if not report.ok:
        logger.error(
            "Index drift: +%d -%d ~%d (re-index required)",
            len(report.added),
            len(report.removed),
            len(report.changed),
        )
        return 1
    logger.info("Index sources match snapshot %s", report.corpus_hash[:12])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
