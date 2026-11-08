"""The local dense leg against a real PostgreSQL + pgvector (ADR-0034).

Skipped unless ``LOCAL_DENSE_TEST_DSN`` names a superuser connection to a
database called ``ura_dense``; ``local-dense-pgvector.yml`` provides one from the
same digest-pinned pgvector image the HF Space layer copies the extension from.
The model is not involved: the artifact here is synthetic, so the test covers
the SQL (COPY, HNSW, grants, filters, iterative scan) and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import TYPE_CHECKING

import numpy as np
import pytest
from app import dense_cpu, pgvector_dense

if TYPE_CHECKING:
    from pathlib import Path

DSN = os.getenv("LOCAL_DENSE_TEST_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="LOCAL_DENSE_TEST_DSN is not set (CI provides pgvector)")


def _artifact(directory: Path, count: int = 60) -> tuple[list[str], np.ndarray]:
    rng = np.random.default_rng(7)
    vectors = rng.normal(size=(count, dense_cpu.DIM)).astype(np.float32)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(count)]
    np.save(directory / "vectors.npy", vectors.astype(np.float16))
    manifest = {"schema": pgvector_dense.ARTIFACT_SCHEMA, "model": dense_cpu.MODEL_REPO,
                "revision": dense_cpu.MODEL_REVISION, "dim": dense_cpu.DIM, "count": count, "ids": ids,
                "id_set_sha256": hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest(),
                "vectors_sha256": pgvector_dense._vectors_digest(vectors.astype(np.float16), ids),
                "payload_sha256": [pgvector_dense._payload_digest(_payload(i)) for i in range(count)]}
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return ids, vectors


def _payload(i: int) -> dict[str, str]:
    return {"text": f"passage {i}", "doc_type": "faq" if i % 2 else "pdf_chunk",
            "fiscal_year": ["FY2025-26", "FY2026-27", ""][i % 3]}


class _Query:
    def __init__(self, vector: np.ndarray) -> None:
        self.vector = vector

    def embed_query(self, _text: str) -> np.ndarray:
        return self.vector


@pytest.fixture
def loaded(tmp_path: Path) -> tuple[list[str], np.ndarray, str]:
    import psycopg

    ids, vectors = _artifact(tmp_path)
    with psycopg.connect(DSN) as conn:
        report = pgvector_dense.load_into_postgres(conn, tmp_path, [(pid, _payload(i)) for i, pid in enumerate(ids)],
                                          binding={"corpus_hash": "ci-corpus"})
        assert report["loaded"] == len(ids)
    # The CI database trusts loopback connections, so the serving role needs no password.
    reader = psycopg.conninfo.make_conninfo(DSN, user=pgvector_dense.READER_ROLE)
    return ids, vectors, reader


def test_a_loaded_table_answers_as_the_serving_role(loaded: tuple) -> None:
    ids, vectors, reader = loaded
    target = 17
    store = pgvector_dense.LocalDenseStore(reader, embedder=_Query(vectors[target]))
    assert store.open(), store.reason
    assert store.status() == f"pgvector multilingual-e5-small, {len(ids)} vectors"
    hits = store.search("q", 5)
    assert hits[0][0] == ids[target]
    assert hits[0][2] == pytest.approx(1.0, abs=2e-3)
    assert [h[2] for h in hits] == sorted((h[2] for h in hits), reverse=True)


def test_filters_hold_in_sql(loaded: tuple) -> None:
    ids, vectors, reader = loaded
    store = pgvector_dense.LocalDenseStore(reader, embedder=_Query(vectors[17]))
    assert store.open(), store.reason
    faq = store.search("q", 10, {"doc_type": "faq"})
    assert faq
    assert all(h[1]["doc_type"] == "faq" for h in faq)
    dated = store.search("q", 30, {"fiscal_year": "FY2026-27"})
    # The edition asked for, plus undated rows, as the Qdrant path filters (G293).
    assert dated
    assert {h[1]["fiscal_year"] for h in dated} <= {"FY2026-27", ""}
    either = store.search("q", 30, {"doc_type": ["faq", "pdf_chunk"]})
    assert len(either) == 30


def test_the_serving_role_cannot_write(loaded: tuple) -> None:
    import psycopg

    _ids, _vectors, reader = loaded
    with psycopg.connect(reader) as conn, conn.cursor() as cur, pytest.raises(psycopg.errors.InsufficientPrivilege):
        cur.execute(f"DELETE FROM {pgvector_dense.TABLE}")  # noqa: S608 - table name is a module constant


def test_loading_again_replaces_the_table(loaded: tuple, tmp_path: Path) -> None:
    import psycopg

    ids, _vectors = _artifact(tmp_path, count=10)
    with psycopg.connect(DSN) as conn:
        pgvector_dense.load_into_postgres(conn, tmp_path, [(pid, _payload(i)) for i, pid in enumerate(ids)],
                                          binding={"corpus_hash": "ci-corpus"})
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {pgvector_dense.TABLE}")  # noqa: S608 - table name is a module constant
            assert cur.fetchone()[0] == 10
            cur.execute(f"SELECT value FROM {pgvector_dense.META_TABLE} WHERE key = 'count'")  # noqa: S608 - table name is a module constant
            assert cur.fetchone()[0] == "10"
