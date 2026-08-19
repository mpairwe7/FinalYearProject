"""Acceptance coverage for issue #300's local and embedded Qdrant lifecycle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from app.analytics import MetricsStore
from app.freshness import FreshnessReport, compare_index_hash
from app.index_lifecycle import (
    IndexLifecycleError,
    candidate_collection_name,
    promote_alias,
    rebuild_if_needed,
)


class _AliasClient:
    def __init__(self, aliases: list[object] | None = None, source_hash: str = "old") -> None:
        self.aliases = aliases or []
        self.source_hash = source_hash
        self.updated: list[object] = []

    def get_collections(self):
        return SimpleNamespace(collections=[])

    def get_aliases(self):
        return SimpleNamespace(aliases=self.aliases)

    def retrieve(self, **_kwargs):
        return [SimpleNamespace(payload={"source_corpus_hash": self.source_hash})]

    def update_collection_aliases(self, *, change_aliases_operations):
        self.updated = list(change_aliases_operations)
        return True


def test_candidate_name_is_stable_and_revision_scoped() -> None:
    first = candidate_collection_name("ura_active", "a" * 64)
    assert first == candidate_collection_name("ura_active", "a" * 64)
    assert first != candidate_collection_name("ura_active", "b" * 64)
    assert "aaaaaaaaaaaaaaaa" in first


def test_candidate_name_rejects_a_missing_hash() -> None:
    with pytest.raises(RuntimeError, match="source corpus hash"):
        candidate_collection_name("ura_active", "")


def test_alias_swap_is_one_qdrant_request() -> None:
    previous = SimpleNamespace(alias_name="ura_active", collection_name="ura__old")
    client = _AliasClient([previous])
    promote_alias(client, alias="ura_active", collection="ura__new", previous="ura__old")

    assert len(client.updated) == 2
    assert client.updated[0].delete_alias.alias_name == "ura_active"
    assert client.updated[1].create_alias.collection_name == "ura__new"


def test_build_failure_never_updates_the_serving_alias() -> None:
    client = _AliasClient(
        [SimpleNamespace(alias_name="ura_knowledge_base", collection_name="ura__old")]
    )
    snapshot = {"corpus_hash": "new" * 22, "files": {}, "file_count": 0}

    with (
        mock.patch("qdrant_client.QdrantClient", return_value=client),
        mock.patch("app.index_lifecycle.snapshot_sources", return_value=snapshot),
        mock.patch("app.index_lifecycle.load_documents", return_value=[{"text": "x"}]),
        mock.patch("app.index_lifecycle.build_index", side_effect=RuntimeError("embedding failed")),
        mock.patch("app.index_lifecycle.QDRANT_COLLECTION", "ura_knowledge_base"),
        pytest.raises(RuntimeError, match="embedding failed"),
    ):
        rebuild_if_needed(force=True)

    assert client.updated == []


def test_matching_active_hash_is_idempotent() -> None:
    current_hash = "a" * 64
    client = _AliasClient(
        [SimpleNamespace(alias_name="ura_knowledge_base", collection_name="ura__old")],
        source_hash=current_hash,
    )
    snapshot = {"corpus_hash": current_hash, "files": {}, "file_count": 0}

    with (
        mock.patch("qdrant_client.QdrantClient", return_value=client),
        mock.patch("app.index_lifecycle.snapshot_sources", return_value=snapshot),
        mock.patch("app.index_lifecycle._write_fresh_status"),
        mock.patch("app.index_lifecycle.build_index") as build,
        mock.patch("app.index_lifecycle.QDRANT_COLLECTION", "ura_knowledge_base"),
    ):
        result = rebuild_if_needed()

    assert not result["reindexed"]
    build.assert_not_called()
    assert client.updated == []


def test_candidate_quality_failure_never_updates_the_serving_alias() -> None:
    client = _AliasClient(
        [SimpleNamespace(alias_name="ura_knowledge_base", collection_name="ura__old")]
    )
    snapshot = {"corpus_hash": "new" * 22, "files": {}, "file_count": 0}
    candidate_payload = {
        "source_corpus_hash": snapshot["corpus_hash"],
        "bm25_state_zlib": "present",
    }

    with (
        mock.patch("qdrant_client.QdrantClient", return_value=client),
        mock.patch("app.index_lifecycle.snapshot_sources", side_effect=[snapshot, snapshot]),
        mock.patch("app.index_lifecycle.load_documents", return_value=[{"text": "VAT"}]),
        mock.patch("app.index_lifecycle.build_index", return_value={"total_upserted": 1}),
        mock.patch(
            "app.index_lifecycle.binding_payload",
            side_effect=[{"source_corpus_hash": "old"}, candidate_payload],
        ),
        mock.patch("app.index_lifecycle.validate_candidate_retrieval", side_effect=IndexLifecycleError("miss")),
        mock.patch("app.index_lifecycle.QDRANT_COLLECTION", "ura_knowledge_base"),
        pytest.raises(IndexLifecycleError, match="miss"),
    ):
        rebuild_if_needed(force=True)

    assert client.updated == []


def test_qdrant_source_hash_mismatch_is_explicit_drift() -> None:
    report = FreshnessReport(ok=True, corpus_hash="source-hash")
    compare_index_hash(report, "index-hash")

    assert not report.ok
    assert report.index_drift
    assert report.index_corpus_hash == "index-hash"


def test_missing_qdrant_binding_is_never_reported_as_fresh() -> None:
    report = FreshnessReport(ok=True, corpus_hash="source-hash")
    compare_index_hash(report, "")

    assert not report.ok
    assert report.index_snapshot_missing


def test_cpu_image_build_triggers_for_every_shipped_corpus_source() -> None:
    workflow = (
        Path(__file__).resolve().parents[3] / ".github/workflows/ura-chatbot-build-push.yml"
    ).read_text(encoding="utf-8")

    for source in ("Data/dataset/**", "Data/teacher_qa/**", "Data/pdfs/**", "Data/crawl/**"):
        assert source in workflow


def test_local_compose_runs_the_staged_indexer_before_api() -> None:
    compose = Path(__file__).resolve().parents[2] / "docker-compose.local-retrieval.yml"
    body = compose.read_text(encoding="utf-8")

    assert "qdrant-indexer:" in body
    assert "app.index_lifecycle" in body
    assert "service_completed_successfully" in body
    assert "ura_knowledge_base_jsonl_active" in body
    assert "./Data/pdf_jsonl:/app/Data/pdf_jsonl:ro" in body
    assert "./Data/crawl_jsonl:/app/Data/crawl_jsonl:ro" in body
    assert "../Data/pdfs:/app/Data/pdfs:ro" in body
    assert "../Data/crawl/pages:/app/Data/crawl/pages:ro" in body
    assert "qdrant-backup:" in body
    assert "app.qdrant_backup" in body
    assert "qdrant_backups" in body


def test_cpu_image_emits_supply_chain_attestations() -> None:
    workflow = (
        Path(__file__).resolve().parents[3] / ".github/workflows/ura-chatbot-build-push.yml"
    ).read_text(encoding="utf-8")

    assert "provenance: mode=max" in workflow
    assert "sbom: true" in workflow


def test_durable_index_lifecycle_status_is_exported_to_prometheus() -> None:
    with (
        mock.patch(
            "app.freshness.load_status",
            return_value={"ok": True, "checked_at": "2026-08-19T12:00:00+00:00"},
        ),
        mock.patch(
            "app.freshness.load_lifecycle_status",
            return_value={"ok": False, "last_attempt_at": "2026-08-19T12:01:00+00:00"},
        ),
        mock.patch(
            "app.freshness.load_backup_status",
            return_value={
                "ok": True,
                "last_attempt_at": "2026-08-19T12:02:00+00:00",
                "restore_drill_ok": True,
                "last_restore_drill_at": "2026-08-19T12:03:00+00:00",
            },
        ),
        mock.patch.dict("os.environ", {"QDRANT_BACKUP_REQUIRED": "true"}),
    ):
        output = MetricsStore().to_prometheus()

    assert "ura_qdrant_index_fresh 1" in output
    assert "ura_qdrant_rebuild_failed 1" in output
    assert "ura_qdrant_backup_required 1" in output
    assert "ura_qdrant_backup_failed 0" in output
    assert "ura_qdrant_restore_drill_failed 0" in output
