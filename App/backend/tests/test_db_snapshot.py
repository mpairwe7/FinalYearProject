"""The analytics database survives a container restart as a snapshot (G616, ADR-0063).

A "restart" here is what the HF Space does to the container's disk: the
database, its WAL files and the snapshot status file are all gone, and only the
snapshot target remains.
"""

from __future__ import annotations

import json
import os
import selectors
import signal
import sqlite3
import subprocess
import sys
import time
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
    loop = subprocess.Popen([*run, "loop"], env=env, stderr=subprocess.PIPE)
    try:
        ready = selectors.DefaultSelector()
        ready.register(loop.stderr, selectors.EVENT_READ)
        seen, deadline = b"", time.monotonic() + 60
        while b"snapshot loop running" not in seen:
            assert time.monotonic() < deadline, f"the loop never started: {seen!r}"
            if ready.select(timeout=1):
                chunk = os.read(loop.stderr.fileno(), 4096)
                assert chunk, f"the loop exited early: {seen!r}"
                seen += chunk
        loop.send_signal(signal.SIGTERM)
        loop.wait(timeout=60)
    finally:
        loop.kill()
    manifest = json.loads((tmp_path / "bucket" / snapshot.MANIFEST).read_text())
    assert manifest["tables"] == {"tickets": 1}


@pytest.mark.parametrize("damage", [b"", b"null", b"{not json"])
def test_a_damaged_manifest_is_a_failed_restore_not_an_empty_one(container, damage):
    """Read as "no snapshot", the next export would replace the pointer to the real dump."""
    _, bucket, restart = container
    snapshot.restore()
    _ticket("keep me")
    snapshot.export()
    (bucket / snapshot.MANIFEST).write_bytes(damage)
    restart()
    assert snapshot.restore() == "failed"


def test_an_export_removes_dumps_a_failed_upload_or_delete_left_behind(container):
    _, bucket, _ = container
    snapshot.restore()
    _ticket("one")
    snapshot.export()
    stray = bucket / "snapshots" / f"{'0' * 64}.sql.gz"
    stray.write_bytes(b"left behind")
    latest = snapshot.export()
    assert sorted(p.name for p in (bucket / "snapshots").iterdir()) == [Path(latest["file"]).name]


def test_a_local_dump_is_readable_by_its_owner_only(container):
    _, bucket, _ = container
    snapshot.restore()
    latest = snapshot.export()
    for path in (bucket / latest["file"], bucket / snapshot.MANIFEST):
        assert path.stat().st_mode & 0o077 == 0, path


def test_a_restore_killed_part_way_keeps_exports_off(container, monkeypatch):
    """A killed restore lets the server create an empty database; that must not read as "kept"."""
    _, bucket, restart = container
    snapshot.restore()
    _ticket("keep me")
    good = snapshot.export()
    restart()

    def killed(*_args):
        raise SystemExit("killed by timeout")

    monkeypatch.setattr(snapshot, "_get", killed)
    with pytest.raises(SystemExit):
        snapshot.restore()
    monkeypatch.undo()
    monkeypatch.setattr(db, "_DB_PATH", container[0])
    monkeypatch.setenv("DB_SNAPSHOT_TARGET", f"file://{bucket}")
    db.init_db()  # the first role's server starts on an empty database
    assert snapshot.restore() == "failed", "the second role must not decide kept"
    with pytest.raises(snapshot.SnapshotError):
        snapshot.export()
    assert json.loads((bucket / snapshot.MANIFEST).read_text())["sha256"] == good["sha256"]


def test_an_export_never_deletes_the_dump_the_published_manifest_names(container, monkeypatch):
    """Another container may publish its manifest between this export's upload and its prune."""
    _, bucket, _ = container
    snapshot.restore()
    _ticket("one")
    theirs = snapshot.export()
    _ticket("two")
    real_list = snapshot._list

    def list_after_their_publish(target, folder):
        names = real_list(target, folder)
        (bucket / snapshot.MANIFEST).write_text(json.dumps(theirs))  # they publish again
        return names

    monkeypatch.setattr(snapshot, "_list", list_after_their_publish)
    snapshot.export()
    assert (bucket / theirs["file"]).exists()
