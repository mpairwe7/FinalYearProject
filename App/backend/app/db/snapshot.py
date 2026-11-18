"""Keep the SQLite analytics database across container restarts (G616, ADR-0063).

A container without a persistent disk, such as the HF Space, starts every run on
an empty ``analytics.db``: tickets, consent receipts, the audit ledger and memory
are gone. With ``DB_SNAPSHOT_TARGET`` set, the database is kept as a gzipped SQL
dump at that target and restored from it before the API starts::

    python -m app.db.snapshot restore   # uvicorn-role.sh, before each role's server
    python -m app.db.snapshot loop      # supervisord: every interval, and on SIGTERM
    python -m app.db.snapshot export    # once, by hand

``DB_SNAPSHOT_TARGET`` is ``hf://buckets/<owner>/<bucket>[/<prefix>]`` (a private
bucket; ``HF_TOKEN`` needs write access to it) or ``file:///<absolute dir>``.
The dump is stored under its SHA-256 and ``latest.json`` names it, so a reader
never sees half an upload. Each export then deletes every other dump, so an
erased taxpayer leaves the target with the next successful export.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import json
import logging
import os
import signal
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import database

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

MANIFEST = "latest.json"
DUMPS = "snapshots"
#: A restore outcome after which exporting cannot replace data this database lacks.
EXPORTABLE = frozenset({"restored", "empty", "kept"})


class SnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class Target:
    scheme: str  # "file" or "hf"
    root: str  # a directory, or "<owner>/<bucket>"
    prefix: str = ""

    def path(self, name: str) -> str:
        return f"{self.prefix}/{name}" if self.prefix else name


def configured_target(raw: str | None = None) -> Target | None:
    raw = (os.getenv("DB_SNAPSHOT_TARGET", "") if raw is None else raw).strip()
    if not raw:
        return None
    if raw.startswith("file://") and Path(raw[len("file://") :]).is_absolute():
        return Target("file", raw[len("file://") :])
    if raw.startswith("hf://buckets/"):
        owner, _, rest = raw[len("hf://buckets/") :].partition("/")
        bucket, _, prefix = rest.partition("/")
        if owner and bucket:
            return Target("hf", f"{owner}/{bucket}", prefix.strip("/"))
    raise SnapshotError(
        f"DB_SNAPSHOT_TARGET must be hf://buckets/<owner>/<bucket>[/<prefix>] or file:///<dir>, got {raw!r}"
    )


def _file_put(target: Target, name: str, data: bytes) -> None:
    path = Path(target.root) / target.path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Owner-only from the first byte: the dump holds taxpayers' records.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "wb") as out:
        out.write(data)
    os.replace(tmp, path)


def _file_get(target: Target, name: str) -> bytes | None:
    path = Path(target.root) / target.path(name)
    return path.read_bytes() if path.exists() else None


def _file_delete(target: Target, name: str) -> None:
    (Path(target.root) / target.path(name)).unlink(missing_ok=True)


def _file_list(target: Target, folder: str) -> list[str]:
    directory = Path(target.root) / target.path(folder)
    return (
        sorted(f"{folder}/{p.name}" for p in directory.glob("*.sql.gz"))
        if directory.is_dir()
        else []
    )


def _hf_api() -> Any:
    from huggingface_hub import HfApi

    return HfApi()


def _hf_put(target: Target, name: str, data: bytes) -> None:
    api = _hf_api()
    # The dump holds taxpayers' tickets and memory; a public bucket would publish them.
    if not api.bucket_info(target.root).private:
        raise SnapshotError(f"refusing to write a database snapshot to public bucket {target.root}")
    api.batch_bucket_files(target.root, add=[(data, target.path(name))])


def _hf_get(target: Target, name: str) -> bytes | None:
    with tempfile.TemporaryDirectory() as tmp:
        local = Path(tmp) / "file"
        _hf_api().download_bucket_files(
            target.root, [(target.path(name), local)], raise_on_missing_files=False
        )
        return local.read_bytes() if local.exists() else None


def _hf_delete(target: Target, name: str) -> None:
    _hf_api().batch_bucket_files(target.root, delete=[target.path(name)])


def _hf_list(target: Target, folder: str) -> list[str]:
    full = target.path(folder) + "/"
    paths = (
        item.path for item in _hf_api().list_bucket_tree(target.root, prefix=full, recursive=True)
    )
    return sorted(f"{folder}/{p[len(full) :]}" for p in paths if p.endswith(".sql.gz"))


TRANSPORTS = {
    "file": (_file_put, _file_get, _file_delete, _file_list),
    "hf": (_hf_put, _hf_get, _hf_delete, _hf_list),
}


def _put(target: Target, name: str, data: bytes) -> None:
    TRANSPORTS[target.scheme][0](target, name, data)


def _get(target: Target, name: str) -> bytes | None:
    return TRANSPORTS[target.scheme][1](target, name)


def _delete(target: Target, name: str) -> None:
    TRANSPORTS[target.scheme][2](target, name)


def _list(target: Target, folder: str) -> list[str]:
    return TRANSPORTS[target.scheme][3](target, folder)


def _db_path() -> Path:
    return Path(database._DB_PATH)


def _status_path() -> Path:
    # On the container's own disk, like the database: one restore decision per container start.
    return _db_path().with_name(_db_path().name + ".snapshot-status")


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    """One restore or export at a time, across the roles, the loop and a manual run."""
    db_path = _db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with open(db_path.with_name(db_path.name + ".snapshot-lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def restore_status() -> str:
    path = _status_path()
    words = path.read_text().split(maxsplit=1) if path.exists() else []
    return words[0] if words else ""


def _decide(status: str, detail: str = "") -> str:
    _status_path().write_text(f"{status} {detail}".strip() + "\n")
    return status


def _manifest(target: Target) -> dict[str, Any] | None:
    """The published manifest; None only when there is none. A damaged one raises."""
    raw = _get(target, MANIFEST)
    if raw is None:
        return None
    try:
        manifest = json.loads(raw)
    except ValueError:
        manifest = None
    if not isinstance(manifest, dict) or not {"file", "sha256"} <= manifest.keys():
        raise SnapshotError(f"{MANIFEST} is damaged")
    return manifest


def restore() -> str:
    """Restore the latest snapshot into a missing database; never fails the start.

    Both API roles call this as they start. The first to take the lock decides for
    the container (``restored``, ``empty``, ``kept`` or ``failed``) and the second,
    or a later program restart, finds the decision already made.
    """
    target = configured_target()
    if target is None:
        return "off"
    db_path = _db_path()
    with _locked():
        if restore_status():
            return restore_status()
        if db_path.exists() and db_path.stat().st_size > 0:
            return _decide("kept")
        # Until the outcome replaces it: a restore killed part-way (uvicorn-role.sh's
        # timeout) lets a server create an empty database, which must not read as "kept".
        _decide("failed", "the restore did not finish")
        try:
            manifest = _manifest(target)
            if manifest is None:
                return _decide("empty")
            data = _get(target, manifest["file"])
            if data is None or hashlib.sha256(data).hexdigest() != manifest["sha256"]:
                raise SnapshotError(f"{manifest['file']} is missing or does not match {MANIFEST}")
            staging = db_path.with_name(db_path.name + ".restoring")
            staging.unlink(missing_ok=True)
            with contextlib.closing(sqlite3.connect(staging)) as conn:
                conn.executescript(gzip.decompress(data).decode())
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise SnapshotError("the restored database fails PRAGMA integrity_check")
            os.replace(staging, db_path)
            logger.info(
                "restored analytics database from %s (%s)",
                manifest["file"],
                manifest.get("created_at"),
            )
            return _decide("restored", manifest["sha256"])
        except Exception as exc:  # a bad snapshot must not stop the API; export then stays off
            logger.error(
                "database snapshot restore failed; exports are off for this container: %s", exc
            )
            return _decide("failed", str(exc)[:300])


def _dump(db_path: Path) -> tuple[str, dict[str, int]]:
    """A consistent SQL dump of the live database and the rows it holds per table."""
    # shortcut: the dump is built in memory; stream it through a temp file once the database outgrows a few hundred MB.
    with (
        contextlib.closing(sqlite3.connect(db_path, timeout=10)) as live,
        contextlib.closing(sqlite3.connect(":memory:")) as copy,
    ):
        live.backup(copy)  # one consistent read while the API keeps writing
        statements = list(copy.iterdump())
        tables = {
            name: 0
            for (name,) in copy.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        }
    for statement in statements:  # iterdump writes one INSERT INTO "<table>" per row
        if statement.startswith('INSERT INTO "'):
            name = statement[len('INSERT INTO "') :].split('"', 1)[0]
            if name in tables:
                tables[name] += 1
    return "\n".join(statements), tables


def export() -> dict[str, Any] | None:
    """Write a consistent dump of the live database to the target."""
    target = configured_target()
    if target is None:
        return None
    with _locked():
        if restore_status() not in EXPORTABLE:
            raise SnapshotError(
                f"not exporting: this container's restore status is {restore_status() or 'missing'}, "
                "so the snapshot it would replace may hold data this database lacks"
            )
        db_path = _db_path()
        if not db_path.exists():
            return None
        sql, tables = _dump(db_path)
        data = gzip.compress(sql.encode(), mtime=0)
        digest = hashlib.sha256(data).hexdigest()
        manifest = {
            "format": 1,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "file": f"{DUMPS}/{digest}.sql.gz",
            "sha256": digest,
            "bytes": len(data),
            "tables": tables,
        }
        _put(target, manifest["file"], data)
        _put(target, MANIFEST, json.dumps(manifest, indent=1).encode())
        # Every other dump goes, including one a failed delete or upload left behind, but
        # never the one the manifest names now: another container may have just published.
        stale = _list(target, DUMPS)
        published = _manifest(target)
        keep = {manifest["file"], published["file"] if published else manifest["file"]}
        for name in stale:
            if name not in keep:
                _delete(target, name)
        return manifest


def _export_logged(final: bool = False) -> None:
    if final:
        logger.info("final export begins")
    try:
        manifest = export()
    except Exception as exc:
        logger.error("database snapshot export failed: %s", exc)
        return
    if manifest:
        logger.info(
            "exported analytics database (%d bytes, %d tables)",
            manifest["bytes"],
            len(manifest["tables"]),
        )


def loop(interval_s: float | None = None) -> None:
    """Export every interval and once more on SIGTERM (supervisord stops the API first)."""
    if interval_s is None:
        interval_s = max(60.0, float(os.getenv("DB_SNAPSHOT_INTERVAL_SECONDS", "300")))
    stops = {signal.SIGTERM, signal.SIGINT}
    signal.pthread_sigmask(
        signal.SIG_BLOCK, stops
    )  # taken by sigtimedwait below, never by a handler
    enabled = configured_target() is not None
    logger.info("snapshot loop running every %ss (%s)", int(interval_s), "on" if enabled else "off")
    while signal.sigtimedwait(stops, interval_s) is None:
        if enabled:
            _export_logged()
    if enabled:
        _export_logged(final=True)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s db-snapshot %(message)s"
    )
    command = argparse.ArgumentParser(prog="python -m app.db.snapshot")
    command.add_argument("action", choices=["export", "restore", "loop"])
    action = command.parse_args(argv).action
    if action == "restore":
        sys.stdout.write(restore() + "\n")
        return 0
    if action == "loop":
        loop()
        return 0
    try:
        manifest = export()
    except SnapshotError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write((json.dumps(manifest) if manifest else "off") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
