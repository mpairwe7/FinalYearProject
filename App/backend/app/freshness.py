"""Index freshness — detect source drift against the last successful index.

G27: when URA updates a FAQ CSV or a PDF export, Qdrant still returns the
old passage until someone re-indexes. This module hashes every corpus
source the indexer reads, compares against a snapshot written at the end
of a successful ``build_index``, and exits non-zero when they diverge.

No Qdrant. No auto-reindex. CI and a nightly cron can both run
``python -m app.freshness --check``. Optional ``--notify`` posts to
``FRESHNESS_SLACK_WEBHOOK`` (https only). Optional ``--enqueue`` writes
a request file; it never starts ``indexer --recreate``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen

from ._root import APP_DATA_ROOT, PROJECT_ROOT

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
        "generated_at": datetime.now(timezone.utc).isoformat(),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "corpus_hash": self.corpus_hash,
            "previous_hash": self.previous_hash,
            "added": self.added,
            "removed": self.removed,
            "changed": self.changed,
            "snapshot_missing": self.snapshot_missing,
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


def write_status(report: FreshnessReport, path: Path | None = None) -> Path:
    """Persist the last check so /ready does not re-hash the corpus."""
    target = path or STATUS_PATH
    payload = {
        **report.to_dict(),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "reindex_hint": "python -m app.indexer --recreate",
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
        f"~{len(report.changed)}. Re-index in an ops window: "
        "python -m app.indexer --recreate"
    )
    body = json.dumps({"text": text}).encode()
    req = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=5) as resp:  # noqa: S310 — https-only above
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

    Auto-recreate is out of scope: an ops window still has to run
    ``python -m app.indexer --recreate``.
    """
    if report.ok or report.snapshot_missing:
        return None
    target = path or REINDEX_REQUEST_PATH
    payload = {
        **report.to_dict(),
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "auto_reindex": False,
        "reindex_hint": "python -m app.indexer --recreate",
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
    args = parser.parse_args()
    path = Path(args.snapshot)

    if args.write:
        write_snapshot(path=path)
        return 0
    if not args.check:
        parser.error("specify --check or --write")

    report = check(snapshot_path=path)
    if args.write_status:
        write_status(report)
    if args.notify:
        notify_drift(report)
    if args.enqueue:
        enqueue_reindex_request(report)
    print(json.dumps(report.to_dict(), indent=2))
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
