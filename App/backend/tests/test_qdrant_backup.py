"""Retention and restore-drill coverage for the Qdrant backup worker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import qdrant_backup


class _SnapshotClient:
    def __init__(self) -> None:
        self.created = 0
        self.deleted_snapshots: list[tuple[str, str]] = []
        self.recoveries: list[tuple[str, str]] = []
        self.deleted_collections: list[str] = []

    def create_snapshot(self, _collection: str):
        self.created += 1
        return SimpleNamespace(name=f"snapshot-{self.created}.snapshot")

    def delete_snapshot(self, collection: str, name: str):
        self.deleted_snapshots.append((collection, name))

    def recover_snapshot(self, collection: str, *, location: str):
        self.recoveries.append((collection, location))

    def delete_collection(self, collection: str):
        self.deleted_collections.append(collection)


def _download(*, target: Path, **_kwargs) -> tuple[str, int]:
    target.write_bytes(b"snapshot payload")
    return "abc123", len(b"snapshot payload")


def test_retention_prunes_old_downloaded_and_remote_snapshots(tmp_path: Path) -> None:
    client = _SnapshotClient()
    with (
        mock.patch.object(qdrant_backup, "QDRANT_COLLECTION", "ura_active"),
        mock.patch.object(qdrant_backup, "_client", return_value=client),
        mock.patch.object(qdrant_backup, "alias_target", side_effect=["ura__old", "ura__new"]),
        mock.patch.object(qdrant_backup, "binding_payload", return_value={"source_corpus_hash": "source"}),
        mock.patch.object(qdrant_backup, "_download_snapshot", side_effect=_download),
        mock.patch.object(qdrant_backup, "write_backup_status"),
    ):
        first = qdrant_backup.create_backup(backup_dir=tmp_path, keep=1)
        second = qdrant_backup.create_backup(backup_dir=tmp_path, keep=1)

    assert Path(first["path"]).exists() is False
    assert Path(second["path"]).exists()
    assert client.deleted_snapshots == [("ura__old", "snapshot-1.snapshot")]


def test_restore_drill_uses_downloaded_snapshot_and_never_touches_alias(
    tmp_path: Path,
    monkeypatch,
) -> None:
    snapshot = tmp_path / "ura--snapshot.snapshot"
    snapshot.write_bytes(b"snapshot payload")
    metadata = {
        "collection": "ura__build",
        "alias": "ura_active",
        "snapshot": "snapshot.snapshot",
        "source_corpus_hash": "source",
        "sha256": hashlib.sha256(b"snapshot payload").hexdigest(),
    }
    snapshot.with_suffix(snapshot.suffix + ".json").write_text(json.dumps(metadata), encoding="utf-8")
    client = _SnapshotClient()
    monkeypatch.setenv("QDRANT_RESTORE_SNAPSHOT_DIR", "/qdrant/snapshots/restore")

    with (
        mock.patch.object(qdrant_backup, "QDRANT_COLLECTION", "ura_active"),
        mock.patch.object(qdrant_backup, "_client", return_value=client),
        mock.patch.object(qdrant_backup, "binding_payload", return_value={"source_corpus_hash": "source"}),
        mock.patch.object(qdrant_backup, "write_backup_status"),
    ):
        result = qdrant_backup.restore_latest_backup(backup_dir=tmp_path)

    assert result["restore_verified"]
    assert client.recoveries[0][1] == "file:///qdrant/snapshots/restore/ura--snapshot.snapshot"
    assert client.deleted_collections == [result["restore_collection"]]
