"""The analytics database survives a container restart as a snapshot (G616, ADR-0063).

A "restart" here is what the HF Space does to the container's disk: the
database, its WAL files and the snapshot status file are all gone, and only the
snapshot target remains.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from app.db import database as db
from app.db import snapshot

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def container(tmp_path, monkeypatch):
    """A live analytics.db with the app's schema, and a file:// snapshot target."""
    path = tmp_path / "disk" / "analytics.db"
    path.parent.mkdir()
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_DB_DIR", path.parent)
    monkeypatch.setenv("DB_SNAPSHOT_TARGET", f"file://{tmp_path / 'bucket'}")
    conns: list[sqlite3.Connection] = []

    def connect() -> sqlite3.Connection:
        if not conns:
            conn = sqlite3.connect(path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conns.append(conn)
        return conns[0]

    def restart() -> None:
        while conns:
            conns.pop().close()
        for leftover in path.parent.iterdir():
            leftover.unlink()

    monkeypatch.setattr(db, "_get_connection", connect)
    db.init_db()
    yield path, tmp_path / "bucket", restart
    restart()


def _dump(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def _ticket(reason: str) -> None:
    db.create_ticket(reason=reason, user_query="How do I file VAT?", user_id="u-1")


def test_a_restarted_container_gets_its_database_back(container):
    path, _, restart = container
    assert snapshot.restore() == "kept"
    _ticket("filing help")
    snapshot.export()
    before = _dump(path)

    restart()
    assert not path.exists()
    assert snapshot.restore() == "restored"
    assert _dump(path) == before
    assert db.list_tickets()[0]["reason"] == "filing help"


def test_the_first_start_with_no_snapshot_starts_empty_and_exports(container):
    path, bucket, restart = container
    restart()
    assert snapshot.restore() == "empty"
    db.init_db()
    _ticket("first")
    manifest = snapshot.export()
    assert manifest["tables"]["tickets"] == 1
    assert json.loads((bucket / snapshot.MANIFEST).read_text())["sha256"] == manifest["sha256"]


def test_a_damaged_snapshot_is_never_overwritten(container):
    """A failed restore keeps exports off, so the last good snapshot stays recoverable."""
    _, bucket, restart = container
    snapshot.restore()
    _ticket("keep me")
    good = snapshot.export()
    (bucket / good["file"]).write_bytes(b"not a dump")

    restart()
    assert snapshot.restore() == "failed"
    db.init_db()
    with pytest.raises(snapshot.SnapshotError, match="restore status is failed"):
        snapshot.export()
    assert snapshot.restore() == "failed", "a program restart must not reopen exports"
    assert json.loads((bucket / snapshot.MANIFEST).read_text())["sha256"] == good["sha256"]


def test_only_the_latest_dump_is_kept(container):
    """An erased taxpayer leaves the target with the next export."""
    _, bucket, _ = container
    snapshot.restore()
    _ticket("one")
    snapshot.export()
    _ticket("two")
    latest = snapshot.export()
    assert [p.name for p in (bucket / "snapshots").iterdir()] == [Path(latest["file"]).name]


def test_a_public_bucket_is_refused(container, monkeypatch):
    class PublicBucket:
        added: list = []

        def bucket_info(self, bucket_id):
            return type("Info", (), {"private": False})()

        def download_bucket_files(self, bucket_id, files, raise_on_missing_files=False):
            return None

        def batch_bucket_files(self, bucket_id, add=None, delete=None):
            self.added.extend(add or [])

    api = PublicBucket()
    monkeypatch.setattr(snapshot, "_hf_api", lambda: api)
    monkeypatch.setenv("DB_SNAPSHOT_TARGET", "hf://buckets/owner/records")
    snapshot.restore()
    with pytest.raises(snapshot.SnapshotError, match="public bucket"):
        snapshot.export()
    assert api.added == []


@pytest.mark.parametrize(
    ("raw", "target"),
    [
        ("", None),
        ("hf://buckets/owner/records", snapshot.Target("hf", "owner/records")),
        (
            "hf://buckets/owner/records/space/db/",
            snapshot.Target("hf", "owner/records", "space/db"),
        ),
        ("file:///data/snapshots", snapshot.Target("file", "/data/snapshots")),
    ],
)
def test_the_target_setting_is_parsed(raw, target):
    assert snapshot.configured_target(raw) == target


@pytest.mark.parametrize(
    "raw", ["s3://bucket", "file://relative/dir", "hf://buckets/owner", "hf://owner/records"]
)
def test_a_malformed_target_is_refused(raw):
    with pytest.raises(snapshot.SnapshotError, match="DB_SNAPSHOT_TARGET"):
        snapshot.configured_target(raw)


def test_the_loop_exports_once_more_when_stopped(tmp_path):
    """supervisord stops the loop after the API, and that last export must happen."""
    disk = tmp_path / "disk"
    disk.mkdir()
    conn = sqlite3.connect(disk / "analytics.db")
    conn.execute("CREATE TABLE tickets (id TEXT)")
    conn.execute("INSERT INTO tickets VALUES ('t-1')")
    conn.commit()
    conn.close()
    env = {
        **os.environ,
        "ANALYTICS_DB_DIR": str(disk),
        "DB_SNAPSHOT_TARGET": f"file://{tmp_path / 'bucket'}",
        "DB_SNAPSHOT_INTERVAL_SECONDS": "3600",
        "PYTHONPATH": str(BACKEND),
    }
    run = [sys.executable, "-m", "app.db.snapshot"]
    assert (
        subprocess.run(
            [*run, "restore"], env=env, capture_output=True, text=True, timeout=120
        ).stdout
        == "kept\n"
    )
    loop = subprocess.Popen([*run, "loop"], env=env, stderr=subprocess.PIPE, text=True)
    try:
        for line in loop.stderr:
            if "snapshot loop running" in line:
                break
        loop.send_signal(signal.SIGTERM)
        loop.wait(timeout=60)
    finally:
        loop.kill()
    manifest = json.loads((tmp_path / "bucket" / snapshot.MANIFEST).read_text())
    assert manifest["tables"] == {"tickets": 1}
