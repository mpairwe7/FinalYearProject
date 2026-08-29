"""Retained Qdrant collection snapshots for the local persistent deployment.

The index lifecycle keeps the previous collection for a fast rollback, but an
alias alone is not a disaster-recovery backup. This module creates a Qdrant
snapshot of the active physical collection, downloads it to a separate volume,
records a SHA-256 checksum, and retains only the requested number of backups.

The embedded CPU image is immutable and has no persistent volume, so its
rollback unit is the previous signed image rather than this scheduler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
try:
    from datetime import UTC, datetime
except ImportError:
    from datetime import datetime, timezone

    UTC = timezone.utc  # type: ignore[assignment]
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .freshness import BACKUP_STATUS_PATH, write_backup_status
from .index_lifecycle import alias_target, binding_payload
from .indexer import QDRANT_API_KEY, QDRANT_COLLECTION, QDRANT_URL

logger = logging.getLogger(__name__)

BACKUP_DIR = Path(
    os.getenv("QDRANT_SNAPSHOT_DIR", str(BACKUP_STATUS_PATH.with_name("qdrant_snapshots")))
)
DEFAULT_KEEP = max(1, int(os.getenv("QDRANT_SNAPSHOT_KEEP", "7")))
DEFAULT_INTERVAL_SECONDS = max(60, int(os.getenv("QDRANT_SNAPSHOT_INTERVAL_SECONDS", "86400")))
DEFAULT_RETRY_SECONDS = max(60, int(os.getenv("QDRANT_SNAPSHOT_RETRY_SECONDS", "300")))
DEFAULT_RESTORE_DRILL_INTERVAL_SECONDS = max(
    DEFAULT_INTERVAL_SECONDS,
    int(os.getenv("QDRANT_RESTORE_DRILL_INTERVAL_SECONDS", "2592000")),
)


class QdrantBackupError(RuntimeError):
    """A Qdrant backup could not be created or verified."""


def _client() -> Any:
    from qdrant_client import QdrantClient

    return QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=60)


def _safe_backup_name(collection: str, snapshot_name: str) -> str:
    """Make a stable local name while preserving the Qdrant snapshot suffix."""
    safe_collection = "".join(char if char.isalnum() or char in "-_" else "_" for char in collection)
    return f"{safe_collection}--{snapshot_name}"


def _metadata_path(snapshot_path: Path) -> Path:
    return snapshot_path.with_suffix(snapshot_path.suffix + ".json")


def _download_snapshot(*, collection: str, snapshot_name: str, target: Path) -> tuple[str, int]:
    """Download a snapshot atomically and return its SHA-256 and byte count."""
    url = (
        f"{QDRANT_URL.rstrip('/')}/collections/{quote(collection, safe='-_.')}/snapshots/"
        f"{quote(snapshot_name, safe='-_.')}"
    )
    headers = {"api-key": QDRANT_API_KEY} if QDRANT_API_KEY else {}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    digest = hashlib.sha256()
    byte_count = 0
    try:
        with httpx.stream("GET", url, headers=headers, timeout=120.0) as response:
            response.raise_for_status()
            with temporary.open("wb") as handle:
                for chunk in response.iter_bytes():
                    digest.update(chunk)
                    byte_count += len(chunk)
                    handle.write(chunk)
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return digest.hexdigest(), byte_count


def _backup_records(backup_dir: Path, alias: str) -> list[tuple[Path, dict[str, Any]]]:
    """Return every durable backup for one serving alias, newest first.

    Physical collection names change on each staged promotion. Retention must
    therefore be scoped to the stable alias rather than the current physical
    collection, otherwise old generations would accumulate indefinitely.
    """
    records: list[tuple[Path, dict[str, Any]]] = []
    for metadata_path in backup_dir.glob("*.snapshot.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("Ignoring invalid Qdrant backup metadata %s", metadata_path)
            continue
        snapshot_path = metadata_path.with_suffix("")
        if snapshot_path.is_file() and payload.get("alias") == alias:
            records.append((snapshot_path, payload))
    return sorted(records, key=lambda item: item[0].stat().st_mtime, reverse=True)


def _prune_backups(client: Any, *, backup_dir: Path, alias: str, keep: int) -> list[str]:
    """Delete older local copies and their matching Qdrant-side snapshots."""
    removed: list[str] = []
    for snapshot_path, metadata in _backup_records(backup_dir, alias)[keep:]:
        snapshot_path.unlink(missing_ok=True)
        _metadata_path(snapshot_path).unlink(missing_ok=True)
        remote_name = str(metadata.get("snapshot") or "")
        source_collection = str(metadata.get("collection") or "")
        if remote_name and source_collection:
            try:
                client.delete_snapshot(source_collection, remote_name)
            except Exception:
                # The durable copy has already been pruned. A missing remote
                # file is safe; a failed remote cleanup is retried next run.
                logger.warning("Could not delete old Qdrant snapshot %s", remote_name, exc_info=True)
        removed.append(snapshot_path.name)
    return removed


def create_backup(*, backup_dir: Path = BACKUP_DIR, keep: int = DEFAULT_KEEP) -> dict[str, Any]:
    """Snapshot the active alias target, download it, checksum it, and retain it."""
    client = _client()
    collection = alias_target(client, QDRANT_COLLECTION)
    if not collection:
        raise QdrantBackupError(f"Qdrant alias {QDRANT_COLLECTION!r} has no active collection")
    description = client.create_snapshot(collection)
    snapshot_name = str(getattr(description, "name", "") or "")
    if not snapshot_name:
        raise QdrantBackupError(f"Qdrant did not return a snapshot name for {collection!r}")

    snapshot_path = backup_dir / _safe_backup_name(collection, snapshot_name)
    checksum, byte_count = _download_snapshot(
        collection=collection,
        snapshot_name=snapshot_name,
        target=snapshot_path,
    )
    source_hash = str(binding_payload(client, collection, QDRANT_COLLECTION).get("source_corpus_hash") or "")
    metadata = {
        "created_at": datetime.now(UTC).isoformat(),
        "collection": collection,
        "alias": QDRANT_COLLECTION,
        "snapshot": snapshot_name,
        "source_corpus_hash": source_hash,
        "sha256": checksum,
        "bytes": byte_count,
    }
    metadata_path = _metadata_path(snapshot_path)
    temporary = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    temporary.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(metadata_path)
    removed = _prune_backups(client, backup_dir=backup_dir, alias=QDRANT_COLLECTION, keep=keep)
    write_backup_status(ok=True, collection=collection, snapshot=snapshot_name)
    return {**metadata, "path": str(snapshot_path), "pruned": removed}


def verify_latest_backup(*, backup_dir: Path = BACKUP_DIR) -> dict[str, Any]:
    """Checksum the newest durable backup before a scheduled restore drill."""
    records = _backup_records(backup_dir, QDRANT_COLLECTION)
    if not records:
        raise QdrantBackupError(f"No Qdrant snapshots found in {backup_dir}")
    snapshot_path, metadata = records[0]
    if not snapshot_path.is_file():
        raise QdrantBackupError(f"Snapshot payload missing for {snapshot_path.name}")
    digest = hashlib.sha256()
    with snapshot_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != metadata.get("sha256"):
        raise QdrantBackupError(f"Snapshot checksum mismatch for {snapshot_path.name}")
    return {**metadata, "path": str(snapshot_path), "verified": True}


def _qdrant_restore_location(snapshot_path: Path) -> str:
    """Return the Qdrant-visible location of a backup mounted into its container."""
    configured = os.getenv("QDRANT_RESTORE_SNAPSHOT_DIR", "").strip()
    if not configured:
        raise QdrantBackupError(
            "QDRANT_RESTORE_SNAPSHOT_DIR must name the backup mount inside the Qdrant container"
        )
    restore_dir = Path(configured)
    return f"file://{restore_dir / snapshot_path.name}"


def restore_latest_backup(*, backup_dir: Path = BACKUP_DIR) -> dict[str, Any]:
    """Restore the newest backup to a disposable collection and validate it.

    This is a non-serving restore drill. It reads the downloaded durable copy,
    checks the binding sentinel after recovery, then removes only the temporary
    collection. The active alias is never changed.
    """
    metadata = verify_latest_backup(backup_dir=backup_dir)
    source_hash = str(metadata.get("source_corpus_hash") or "")
    if not source_hash:
        raise QdrantBackupError("Backup metadata has no source corpus hash")
    client = _client()
    drill_collection = (
        f"{metadata['collection']}__restore_drill_{int(time.time())}"
    )
    location = _qdrant_restore_location(Path(str(metadata["path"])))
    try:
        client.recover_snapshot(drill_collection, location=location)
        payload = binding_payload(client, drill_collection, str(metadata["alias"]))
        if payload.get("source_corpus_hash") != source_hash:
            raise QdrantBackupError("Restored collection has no matching source-hash sentinel")
    finally:
        try:
            client.delete_collection(drill_collection)
        except Exception:
            logger.warning("Could not remove restore-drill collection %s", drill_collection, exc_info=True)
    write_backup_status(
        ok=True,
        collection=str(metadata["collection"]),
        snapshot=str(metadata["snapshot"]),
        restore_drill_ok=True,
    )
    return {**metadata, "restore_collection": drill_collection, "restore_verified": True}


def _restore_drill_due(interval_seconds: int) -> bool:
    from .freshness import load_backup_status

    status = load_backup_status()
    raw = str((status or {}).get("last_restore_drill_at") or "")
    if not raw:
        return True
    try:
        elapsed = datetime.now(UTC) - datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    return elapsed.total_seconds() >= interval_seconds


def _run_once(*, backup_dir: Path, keep: int, verify_only: bool, restore_drill: bool = False) -> int:
    try:
        if restore_drill:
            result = restore_latest_backup(backup_dir=backup_dir)
        elif verify_only:
            result = verify_latest_backup(backup_dir=backup_dir)
        else:
            result = create_backup(backup_dir=backup_dir, keep=keep)
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 0
    except Exception as exc:
        write_backup_status(
            ok=False,
            error=str(exc),
            restore_drill_ok=False if restore_drill else None,
            restore_drill_error=str(exc) if restore_drill else "",
        )
        logger.exception("Qdrant backup failed")
        return 1


def main() -> int:  # pragma: no cover - command wiring
    parser = argparse.ArgumentParser(description="Create and retain Qdrant snapshots")
    parser.add_argument("--loop", action="store_true", help="Run continuously at the configured interval")
    parser.add_argument("--interval-seconds", type=int, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument(
        "--retry-seconds",
        type=int,
        default=DEFAULT_RETRY_SECONDS,
        help="In --loop mode, delay before retrying a failed backup",
    )
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP)
    parser.add_argument("--backup-dir", type=Path, default=BACKUP_DIR)
    parser.add_argument("--verify-latest", action="store_true", help="Checksum the newest backup only")
    parser.add_argument(
        "--restore-drill",
        action="store_true",
        help="Restore the newest backup to a disposable collection and validate it",
    )
    parser.add_argument(
        "--restore-drill-interval-seconds",
        type=int,
        default=DEFAULT_RESTORE_DRILL_INTERVAL_SECONDS,
        help="In --loop mode, run a restore drill when the prior drill is older than this",
    )
    args = parser.parse_args()
    if args.keep < 1:
        parser.error("--keep must be at least one")
    if args.interval_seconds < 60:
        parser.error("--interval-seconds must be at least 60")
    if args.retry_seconds < 60:
        parser.error("--retry-seconds must be at least 60")
    if (args.verify_latest or args.restore_drill) and args.loop:
        parser.error("--verify-latest and --restore-drill cannot be combined with --loop")
    if args.verify_latest and args.restore_drill:
        parser.error("--verify-latest cannot be combined with --restore-drill")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not args.loop:
        return _run_once(
            backup_dir=args.backup_dir,
            keep=args.keep,
            verify_only=args.verify_latest,
            restore_drill=args.restore_drill,
        )

    while True:
        backup_exit = _run_once(backup_dir=args.backup_dir, keep=args.keep, verify_only=False)
        if backup_exit == 0 and _restore_drill_due(args.restore_drill_interval_seconds):
            drill_exit = _run_once(
                backup_dir=args.backup_dir,
                keep=args.keep,
                verify_only=False,
                restore_drill=True,
            )
            delay = args.interval_seconds if drill_exit == 0 else args.retry_seconds
        else:
            delay = args.interval_seconds if backup_exit == 0 else args.retry_seconds
        time.sleep(delay)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
