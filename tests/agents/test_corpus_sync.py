"""Unit tests for Phase E: self-updating Qdrant comparator, hot-swap engine, and web_sync_ledger skip."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import corpus_sync as cs
from app import database as db
from app.ephemeral_pdf_sandbox import SandboxDocument
from app.pdf_corpus import PDF_JSONL_SCHEMA_VERSION, _stable_id


@pytest.fixture()
def isolated_db_and_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Configure an isolated SQLite database and temporary corpus/archive directories."""
    db_file = tmp_path / "analytics.db"
    monkeypatch.setattr(db, "_DB_DIR", tmp_path)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None
    db.init_db()

    jsonl_dir = tmp_path / "pdf_jsonl"
    archive_dir = tmp_path / "archive"
    freshness_path = tmp_path / "index_freshness.json"
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)
    yield {
        "jsonl_dir": jsonl_dir,
        "archive_dir": archive_dir,
        "freshness_path": freshness_path,
    }
    if hasattr(db._local, "conn") and db._local.conn is not None:
        db._local.conn.close()
        db._local.conn = None


def _seed_existing_jsonl(
    jsonl_dir: Path,
    *,
    stem: str,
    source: str,
    sha256: str,
    fiscal_year: str,
    doc_family: str,
    texts: list[str],
) -> Path:
    """Create a canonical PDF JSONL file in *jsonl_dir* representing an existing indexed document."""
    path = jsonl_dir / f"{stem}.jsonl"
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for idx, text in enumerate(texts):
            rec = {
                "schema_version": PDF_JSONL_SCHEMA_VERSION,
                "record_type": "pdf_chunk",
                "chunk_id": _stable_id(source, idx, text),
                "text": text,
                "contextual_prefix": f"[Document: {stem}]",
                "heading_trail": [f"Section {idx + 1}"],
                "source": source,
                "source_sha256": sha256,
                "fiscal_year": fiscal_year,
                "doc_family": doc_family,
                "chunk_index": idx,
                "char_count": len(text),
            }
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    return path


class _FakeQdrantClient:
    """In-memory stub for QdrantClient tracking delete and upsert calls."""

    def __init__(self) -> None:
        self.deleted_selectors: list[tuple[str, Any]] = []
        self.upserted_batches: list[tuple[str, list[Any]]] = []

    def delete(self, *, collection_name: str, points_selector: Any) -> None:
        """Record delete call."""
        self.deleted_selectors.append((collection_name, points_selector))

    def upsert(self, *, collection_name: str, points: list[Any]) -> None:
        """Record upsert call."""
        self.upserted_batches.append((collection_name, list(points)))

    def scroll(self, **_kwargs: Any) -> tuple[list[Any], None]:
        """Return empty scroll result."""
        return ([], None)


class _FakeDenseModel:
    """Stub SentenceTransformer returning deterministic 4-dim vectors."""

    def encode(self, texts: list[str], show_progress_bar: bool = False) -> list[list[float]]:
        """Encode each text into a fixed 4-dim float list."""
        assert show_progress_bar is False
        return [[0.25, 0.50, 0.75, float(i + 1)] for i in range(len(texts))]


class _FakeSparseEncoder:
    """Stub BM25SparseEncoder returning deterministic sparse indices and weights."""

    def encode_document(self, text: str) -> tuple[list[int], list[float]]:
        """Encode text into sparse vector."""
        return ([1, 7], [1.25, float(max(1, len(text) % 5))])


class _FakeRetriever:
    """Stub HybridRetriever bundling Qdrant client, dense model, and sparse encoder."""

    collection_name = "ura_knowledge_base_test"

    def __init__(self) -> None:
        self._client = _FakeQdrantClient()
        self._dense_model = _FakeDenseModel()
        self._sparse_encoder = _FakeSparseEncoder()


def test_origin_invariant_rejects_non_tier1_or_insecure_urls(
    isolated_db_and_dirs: dict[str, Path],
) -> None:
    """Verify only HTTPS URLs from ura.go.ug / ulii.org can modify Qdrant."""
    for bad_url in (
        "http://ura.go.ug/en/wp-content/uploads/2026/07/Guide.pdf",
        "https://pwc.com/ug/en/tax-guide-2026.pdf",
        "https://www.kra.go.ke/en/publications/guide.pdf",
    ):
        doc = SandboxDocument(
            url=bad_url,
            filename="Guide.pdf",
            stem="Guide",
            sha256="a" * 64,
            page_count=6,
            fiscal_year="FY2026-27",
            doc_family="taxation_handbook",
            doc_kind="full_guide",
            chunks=[{"text": "Sample text", "page": 1, "section": "Section 1"}],
        )
        with pytest.raises(cs.CorpusSyncOriginRejected):
            cs.compare_and_sync_ura_document(
                doc,
                pdf_jsonl_dir=isolated_db_and_dirs["jsonl_dir"],
                archive_dir=isolated_db_and_dirs["archive_dir"],
            )


def test_verified_current_and_skipped_older_do_not_mutate_qdrant(
    isolated_db_and_dirs: dict[str, Path],
) -> None:
    """Verify Outcome 1 (verified_current) and Outcome 2 (skipped_older) stamp ledger without mutating Qdrant."""
    jsonl_dir = isolated_db_and_dirs["jsonl_dir"]
    archive_dir = isolated_db_and_dirs["archive_dir"]
    existing_sha = "b" * 64
    _seed_existing_jsonl(
        jsonl_dir,
        stem="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25",
        source="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf",
        sha256=existing_sha,
        fiscal_year="FY2024-25",
        doc_family="sector_guide_real_estate",
        texts=["Page 1 content", "Page 2 content", "Page 3 content", "Page 4 content"],
    )
    retriever = _FakeRetriever()

    # 1. Byte-identical SHA-256 -> verified_current
    same_doc = SandboxDocument(
        url="https://ura.go.ug/en/wp-content/uploads/2024/07/TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf",
        filename="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25.pdf",
        stem="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25",
        sha256=existing_sha,
        page_count=4,
        fiscal_year="FY2024-25",
        doc_family="sector_guide_real_estate",
        doc_kind="full_guide",
        chunks=[{"text": "Page 1 content", "page": 1, "section": "Page 1"}],
    )
    out_current = cs.compare_and_sync_ura_document(
        same_doc,
        retriever,
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
    )
    assert out_current.action_taken == "verified_current"
    assert out_current.upserted_points == 0
    assert retriever._client.deleted_selectors == []
    assert retriever._client.upserted_batches == []

    # 2. Older fiscal year (FY2022-23 < FY2024-25) -> skipped_older
    older_doc = SandboxDocument(
        url="https://ura.go.ug/en/wp-content/uploads/2022/07/TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2022-23.pdf",
        filename="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2022-23.pdf",
        stem="TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2022-23",
        sha256="c" * 64,
        page_count=6,
        fiscal_year="FY2022-23",
        doc_family="sector_guide_real_estate",
        doc_kind="full_guide",
        chunks=[{"text": "Old edition page 1", "page": 1, "section": "Page 1"}],
    )
    out_older = cs.compare_and_sync_ura_document(
        older_doc,
        retriever,
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
    )
    assert out_older.action_taken == "skipped_older"
    assert out_older.superseded_source == "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2022-23.pdf"
    assert retriever._client.deleted_selectors == []
    assert retriever._client.upserted_batches == []


def test_replaced_outdated_deletes_old_tombstones_upserts_new_archives_and_skips_turn2(
    isolated_db_and_dirs: dict[str, Path],
) -> None:
    """Verify Outcome 3 (replaced_outdated) hot-swaps Qdrant, tombstones old guide, archives JSONL, and skips Turn 2."""
    jsonl_dir = isolated_db_and_dirs["jsonl_dir"]
    archive_dir = isolated_db_and_dirs["archive_dir"]
    freshness_path = isolated_db_and_dirs["freshness_path"]

    old_stem = "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2024-25"
    old_source = f"{old_stem}.pdf"
    old_jsonl = _seed_existing_jsonl(
        jsonl_dir,
        stem=old_stem,
        source=old_source,
        sha256="d" * 64,
        fiscal_year="FY2024-25",
        doc_family="sector_guide_real_estate",
        texts=[
            "FY2024-25 real estate guide page 1",
            "FY2024-25 real estate guide page 2",
            "FY2024-25 real estate guide page 3",
            "FY2024-25 real estate guide page 4",
        ],
    )
    assert old_jsonl.exists()

    retriever = _FakeRetriever()
    new_stem = "TAXATION-OF-THE-REAL-ESTATE-SECTOR-FY-2026-27"
    new_source = f"{new_stem}.pdf"
    new_url = f"https://ura.go.ug/en/wp-content/uploads/2026/07/{new_source}"
    new_doc = SandboxDocument(
        url=new_url,
        filename=new_source,
        stem=new_stem,
        sha256="e" * 64,
        page_count=6,
        fiscal_year="FY2026-27",
        doc_family="sector_guide_real_estate",
        doc_kind="full_guide",
        chunks=[
            {"text": "FY2026-27 Real Estate Guide Section 1: Rental Tax is 12% above UGX 2,820,000.", "page": 1, "section": "Section 1"},
            {"text": "FY2026-27 Real Estate Guide Section 2: Corporate landlords pay 30% with 50% cap.", "page": 2, "section": "Section 2"},
            {"text": "FY2026-27 Real Estate Guide Section 3: EFRIS e-receipts mandatory.", "page": 3, "section": "Section 3"},
        ],
    )

    outcome = cs.compare_and_sync_ura_document(
        new_doc,
        retriever,
        topic_key="rental_tax_real_estate_2026",
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
        freshness_snapshot_path=freshness_path,
        freshness_roots=[jsonl_dir],
    )
    assert outcome.action_taken == "replaced_outdated"
    assert outcome.superseded_source == old_source
    assert outcome.deleted_old_source is True
    assert outcome.upserted_points == 3
    assert outcome.tombstone_id.startswith("tomb_")

    # Verify Qdrant delete + upsert occurred
    assert len(retriever._client.deleted_selectors) == 1
    assert len(retriever._client.upserted_batches) == 1
    coll_name, points = retriever._client.upserted_batches[0]
    assert coll_name == "ura_knowledge_base_test"
    assert len(points) == 3
    assert points[0].payload["source"] == new_source
    assert points[0].payload["source_url"] == new_url
    assert points[0].payload["fiscal_year"] == "FY2026-27"
    assert points[0].payload["ingested_via"] == "ura_web_auto_sync"

    # Verify corpus tombstone was created for the old source
    tombstones = db.get_active_corpus_tombstones()
    assert any(t["source_uri"] == old_source and t["superseded_by"] == new_source for t in tombstones)

    # Verify old JSONL was moved to archive_dir and new JSONL was written + freshness updated
    assert not old_jsonl.exists()
    assert (archive_dir / f"{old_stem}.jsonl").is_file()
    assert (jsonl_dir / f"{new_stem}.jsonl").is_file()
    assert freshness_path.is_file()

    # Verify Turn 2 fast-path skip returns True from web_sync_ledger
    skip, ledger_row = cs.should_skip_web_search_for_family(doc_family="sector_guide_real_estate")
    assert skip is True
    assert ledger_row is not None
    assert ledger_row["action_taken"] == "replaced_outdated"
    assert ledger_row["source_uri"] == new_source


def test_added_new_public_notice_does_not_delete_existing_guides(
    isolated_db_and_dirs: dict[str, Path],
) -> None:
    """Verify Outcome 4 (added_new) upserts public notices additively without deleting existing guides."""
    jsonl_dir = isolated_db_and_dirs["jsonl_dir"]
    archive_dir = isolated_db_and_dirs["archive_dir"]
    existing_jsonl = _seed_existing_jsonl(
        jsonl_dir,
        stem="TAXATION-HANDBOOK-FY-2025-26-1",
        source="TAXATION-HANDBOOK-FY-2025-26-1.pdf",
        sha256="f" * 64,
        fiscal_year="FY2025-26",
        doc_family="taxation_handbook",
        texts=["Handbook page 1", "Handbook page 2"],
    )

    retriever = _FakeRetriever()
    notice_doc = SandboxDocument(
        url="https://ura.go.ug/en/wp-content/uploads/2026/08/Public_Notice_EFRIS_Waiver.pdf",
        filename="Public_Notice_EFRIS_Waiver.pdf",
        stem="Public_Notice_EFRIS_Waiver",
        sha256="9" * 64,
        page_count=2,
        fiscal_year="FY2026-27",
        doc_family="public_notice",
        doc_kind="public_notice",
        chunks=[
            {"text": "PUBLIC NOTICE: URA waives EFRIS late penalty until 30 September 2026.", "page": 1, "section": "Notice"}
        ],
    )
    outcome = cs.compare_and_sync_ura_document(
        notice_doc,
        retriever,
        topic_key="efris_penalty_waiver_aug_2026",
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
    )
    assert outcome.action_taken == "added_new"
    assert outcome.deleted_old_source is False
    assert outcome.upserted_points == 1
    assert retriever._client.deleted_selectors == []
    assert existing_jsonl.is_file()
    assert (jsonl_dir / "Public_Notice_EFRIS_Waiver.jsonl").is_file()

    # Additive family (public_notice) must NOT skip based on doc_family alone
    skip_fam_only, _ = cs.should_skip_web_search_for_family(doc_family="public_notice")
    assert skip_fam_only is False
    skip_with_topic, row = cs.should_skip_web_search_for_family(
        doc_family="public_notice",
        topic_key="efris_penalty_waiver_aug_2026",
    )
    assert skip_with_topic is True
    assert row is not None


def test_same_filename_replacement_and_needs_review_guard(
    isolated_db_and_dirs: dict[str, Path],
) -> None:
    """Verify same-filename replacements avoid self-tombstoning and conflicting same-year guides return needs_review."""
    jsonl_dir = isolated_db_and_dirs["jsonl_dir"]
    archive_dir = isolated_db_and_dirs["archive_dir"]

    # Seed an existing guide whose filename has no FY suffix, but whose content is FY2024-25
    shared_stem = "A-GUIDE-TO-TAXATION-OF-AGRICULTURAL-SECTOR"
    shared_source = f"{shared_stem}.pdf"
    _seed_existing_jsonl(
        jsonl_dir,
        stem=shared_stem,
        source=shared_source,
        sha256="1" * 64,
        fiscal_year="FY2024-25",
        doc_family="sector_guide_agriculture",
        texts=["Agri guide p1", "Agri guide p2", "Agri guide p3", "Agri guide p4"],
    )
    retriever = _FakeRetriever()

    # 1. Truncated guide (only 1 page vs 4 existing pages -> < 0.5 * 4) returns needs_review without upserting
    truncated_doc = SandboxDocument(
        url=f"https://ura.go.ug/en/wp-content/uploads/2026/07/{shared_source}",
        filename=shared_source,
        stem=shared_stem,
        sha256="2" * 64,
        page_count=1,
        fiscal_year="FY2026-27",
        doc_family="sector_guide_agriculture",
        doc_kind="full_guide",
        chunks=[{"text": "Truncated 1-page guide", "page": 1, "section": "Section 1"}],
    )
    out_review = cs.compare_and_sync_ura_document(
        truncated_doc,
        retriever,
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
    )
    assert out_review.action_taken == "needs_review"
    assert retriever._client.upserted_batches == []

    # 2. Full same-filename replacement (FY2026-27 > FY2024-25) replaces without self-tombstoning
    new_sha = "3" * 64
    updated_same_name_doc = SandboxDocument(
        url=f"https://ura.go.ug/en/wp-content/uploads/2026/07/{shared_source}",
        filename=shared_source,
        stem=shared_stem,
        sha256=new_sha,
        page_count=4,
        fiscal_year="FY2026-27",
        doc_family="sector_guide_agriculture",
        doc_kind="full_guide",
        chunks=[
            {"text": "FY2026-27 Agri Guide p1", "page": 1, "section": "Section 1"},
            {"text": "FY2026-27 Agri Guide p2", "page": 2, "section": "Section 2"},
        ],
    )
    out_same = cs.compare_and_sync_ura_document(
        updated_same_name_doc,
        retriever,
        pdf_jsonl_dir=jsonl_dir,
        archive_dir=archive_dir,
    )
    assert out_same.action_taken == "replaced_outdated"
    assert out_same.tombstone_id == ""
    assert not any(t["source_uri"] == shared_source for t in db.get_active_corpus_tombstones())
    assert len(retriever._client.deleted_selectors) == 1
    _, selector = retriever._client.deleted_selectors[0]
    assert selector.filter.must_not is not None
    assert (jsonl_dir / f"{shared_stem}.jsonl").is_file()

