"""The local dense retrieval leg: pgvector beside the sparse Qdrant collection (ADR-0034).

Three stages, one module, so they cannot drift apart:

1. **CI** (``build-artifact``): embed every corpus document with the pinned CPU
   model and write ``vectors.npy`` plus a manifest of their Qdrant point ids.
   Cached on the corpus, so an unchanged corpus is not embedded again.
2. **Space build** (``load``): read the baked Qdrant collection, require its
   point ids to be exactly the manifest's, and load ids, Qdrant's own payloads
   and the vectors into Postgres with an HNSW index. A mismatch refuses the
   load, so the leg can never serve vectors for a different corpus.
3. **Runtime** (:class:`LocalDenseStore`): embed the query, search the HNSW
   index, return hits for the retriever to fuse with BM25 by RRF.

Postgres listens on a Unix socket only, inside the container. The serving
role can only read the table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import dense_cpu

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

logger = logging.getLogger(__name__)

ARTIFACT_SCHEMA = 1
#: The SQL below spells these names out rather than interpolating them, so no
#: statement is built by formatting; test_local_dense_leg keeps them in step.
TABLE = "dense_passages"
META_TABLE = "dense_meta"
READER_ROLE = "ura_dense_reader"
#: Socket-only DSNs: the serving role reads, the build role writes.
DSN = os.getenv("LOCAL_DENSE_DSN", f"postgresql:///ura_dense?host=/app/pgrun&user={READER_ROLE}")
BUILD_DSN = os.getenv("LOCAL_DENSE_BUILD_DSN", "postgresql:///ura_dense?host=/app/pgrun&user=postgres")
BACKEND = os.getenv("LOCAL_DENSE_BACKEND", "").strip().lower()
#: How long startup waits for Postgres, which supervisord starts beside uvicorn.
CONNECT_WAIT_S = float(os.getenv("LOCAL_DENSE_CONNECT_WAIT_S", "20"))
#: Server-side ceiling on one dense query, and how long a search waits for the store.
#: A stalled Postgres must cost a query its dense half, never block BM25 (PR 707 review).
QUERY_TIMEOUT_MS = int(os.getenv("LOCAL_DENSE_QUERY_TIMEOUT_MS", "1500"))
LOCK_WAIT_S = float(os.getenv("LOCAL_DENSE_LOCK_WAIT_S", "0.5"))
#: Candidates the HNSW scan keeps; pgvector's default of 40 is low for a fused leg.
EF_SEARCH = int(os.getenv("LOCAL_DENSE_EF_SEARCH", "80"))


class ArtifactError(RuntimeError):
    """The vectors artifact is missing, malformed, or for another model."""


class LoadRefused(RuntimeError):
    """The artifact does not describe the corpus in the baked Qdrant collection."""


# --------------------------------------------------------------------------- artifact (CI)


def _id_set_digest(ids: Iterable[str]) -> str:
    return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()


def _vectors_digest(vectors: Any, ids: list[str]) -> str:
    """SHA-256 over the ordered ids and the float16 vector bytes, row by row."""
    import numpy as np

    digest = hashlib.sha256()
    rows = np.ascontiguousarray(vectors.astype(np.float16))
    for pid, row in zip(ids, rows, strict=True):
        digest.update(pid.encode())
        digest.update(b"\x00")
        digest.update(row.tobytes())
    return digest.hexdigest()


def build_artifact(out_dir: Path, *, documents: list[dict[str, Any]] | None = None,
                   embedder: Any = None) -> dict[str, Any]:
    """Embed the corpus into *out_dir* (``vectors.npy``, ``manifest.json``); returns the manifest."""
    import numpy as np

    from .indexer import _embedding_text, load_documents
    from .point_ids import deterministic_point_id

    docs = load_documents() if documents is None else documents
    # Qdrant upserts by id, so a repeated id keeps its last document; do the same.
    by_id: dict[str, dict[str, Any]] = {}
    for doc in docs:
        by_id[deterministic_point_id(doc)] = doc
    ids = list(by_id)
    if embedder is None:
        embedder = dense_cpu.OnnxEmbedder(dense_cpu.model_dir(out_dir))
    started = time.perf_counter()
    vectors = embedder.embed_passages([_embedding_text(by_id[i]) for i in ids])
    out_dir.mkdir(parents=True, exist_ok=True)
    stored = np.ascontiguousarray(vectors.astype(np.float16))
    np.save(out_dir / "vectors.npy", stored)
    manifest = {
        "schema": ARTIFACT_SCHEMA,
        "model": dense_cpu.MODEL_REPO,
        "revision": dense_cpu.MODEL_REVISION,
        "dim": dense_cpu.DIM,
        "count": len(ids),
        "id_set_sha256": _id_set_digest(ids),
        # Binds the rows to the ordered ids: a replaced or reordered vectors.npy
        # of the same shape would otherwise pair vectors with the wrong payloads.
        "vectors_sha256": _vectors_digest(stored, ids),
        "ids": ids,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "embed_seconds": round(time.perf_counter() - started, 1),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def read_artifact(directory: Path) -> tuple[dict[str, Any], Any]:
    """The manifest and vectors in *directory*, checked against the pinned model."""
    import numpy as np

    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        vectors = np.load(directory / "vectors.npy")
    except (OSError, ValueError) as exc:
        raise ArtifactError(f"no readable dense artifact in {directory}: {exc}") from exc
    expected = {"schema": ARTIFACT_SCHEMA, "model": dense_cpu.MODEL_REPO,
                "revision": dense_cpu.MODEL_REVISION, "dim": dense_cpu.DIM}
    wrong = {k: manifest.get(k) for k, v in expected.items() if manifest.get(k) != v}
    if wrong:
        raise ArtifactError(f"artifact was built for another model or schema: {wrong}")
    ids = manifest.get("ids") or []
    if vectors.shape != (len(ids), dense_cpu.DIM) or manifest.get("count") != len(ids):
        raise ArtifactError(f"artifact shape {vectors.shape} does not match its {len(ids)} ids")
    if len(set(ids)) != len(ids) or manifest.get("id_set_sha256") != _id_set_digest(ids):
        raise ArtifactError("artifact ids are repeated or do not match their digest")
    if manifest.get("vectors_sha256") != _vectors_digest(vectors, ids):
        raise ArtifactError("vectors.npy does not match the manifest (replaced, reordered or corrupted)")
    return manifest, vectors


# --------------------------------------------------------------------------- load (Space build)


def qdrant_points(client: Any, collection: str,
                  binding: dict[str, str] | None = None) -> Iterator[tuple[str, dict[str, Any]]]:
    """Every document point in *collection* as ``(id, payload)``.

    The BM25 binding sentinel is skipped; its corpus hash goes into *binding*,
    so the loaded table records which collection it was built against.
    """
    offset = None
    while True:
        points, offset = client.scroll(collection_name=collection, limit=512, offset=offset,
                                       with_payload=True, with_vectors=False)
        for point in points:
            payload = point.payload or {}
            if payload.get("_meta") == "bm25_binding":
                if binding is not None:
                    binding["corpus_hash"] = str(payload.get("source_corpus_hash") or payload.get("corpus_hash") or "")
                continue
            yield str(point.id), payload
        if offset is None:
            return


def _vector_literal(vector: Any) -> str:
    return "[" + ",".join(f"{float(x):.6g}" for x in vector) + "]"


def load_into_postgres(conn: Any, artifact_dir: Path, points: Iterable[tuple[str, dict[str, Any]]],
                       *, binding: dict[str, str] | None = None) -> dict[str, Any]:
    """Replace the table with the artifact's vectors and Qdrant's payloads. Raises :class:`LoadRefused`.

    *binding* holds the collection's corpus hash once *points* is consumed (see
    :func:`qdrant_points`); the runtime turns the leg off when the serving
    collection's hash differs, so a promoted corpus never meets stale vectors.
    """
    manifest, vectors = read_artifact(artifact_dir)
    row_of = {pid: i for i, pid in enumerate(manifest["ids"])}
    payloads = dict(points)
    corpus_hash = str((binding or {}).get("corpus_hash") or "")
    if not corpus_hash:
        raise LoadRefused("the collection has no BM25 binding sentinel, so the vectors cannot be bound to it")
    missing = sorted(set(payloads) - set(row_of))
    extra = sorted(set(row_of) - set(payloads))
    if missing or extra:
        # Point ids are derived from source, page, section, chunk id and text, so
        # equal sets mean the same documents; anything else is another corpus.
        raise LoadRefused(f"artifact and Qdrant disagree: {len(missing)} points without a vector "
                          f"(e.g. {missing[:3]}), {len(extra)} vectors without a point (e.g. {extra[:3]})")
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("DROP TABLE IF EXISTS dense_passages")
        cur.execute("CREATE TABLE dense_passages (id text PRIMARY KEY, payload jsonb NOT NULL, "
                    "embedding vector(384) NOT NULL)")
        with cur.copy("COPY dense_passages (id, payload, embedding) FROM STDIN") as copy:
            for pid, payload in payloads.items():
                copy.write_row((pid, json.dumps(payload, ensure_ascii=False), _vector_literal(vectors[row_of[pid]])))
        cur.execute("CREATE INDEX dense_passages_hnsw ON dense_passages USING hnsw (embedding vector_cosine_ops) "
                    "WITH (m = 16, ef_construction = 64)")
        cur.execute("DROP TABLE IF EXISTS dense_meta")
        cur.execute("CREATE TABLE dense_meta (key text PRIMARY KEY, value text NOT NULL)")
        meta = {"model": manifest["model"], "revision": manifest["revision"], "dim": str(manifest["dim"]),
                "count": str(len(payloads)), "id_set_sha256": manifest["id_set_sha256"],
                "qdrant_corpus_hash": corpus_hash,
                "artifact_created_at": str(manifest.get("created_at") or ""),
                "loaded_at": datetime.now(UTC).isoformat(timespec="seconds")}
        cur.executemany("INSERT INTO dense_meta (key, value) VALUES (%s, %s)", list(meta.items()))
        cur.execute("ANALYZE dense_passages")
        cur.execute("DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ura_dense_reader') "
                    "THEN CREATE ROLE ura_dense_reader LOGIN; END IF; END $$")
        cur.execute("GRANT CONNECT ON DATABASE ura_dense TO ura_dense_reader")
        cur.execute("GRANT USAGE ON SCHEMA public TO ura_dense_reader")
        cur.execute("GRANT SELECT ON dense_passages, dense_meta TO ura_dense_reader")
    conn.commit()
    return {"loaded": len(payloads), **{k: meta[k] for k in ("model", "revision", "id_set_sha256")}}


# --------------------------------------------------------------------------- runtime


#: The search, around a WHERE clause made only of the constant fragments in _filter_sql.
_SEARCH_HEAD = ("WITH hits AS MATERIALIZED (SELECT id, payload, embedding <=> %s::vector AS distance "
                "FROM dense_passages")
_SEARCH_TAIL = " ORDER BY distance LIMIT %s) SELECT id, payload, distance FROM hits ORDER BY distance"
_CONTAINS = "payload @> %s::jsonb"


def _filter_sql(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    """Qdrant's payload filter semantics as jsonb containment, parameterised.

    A list matches any of its values, and an empty list matches nothing;
    ``fiscal_year`` also matches undated rows (``""``), as the Qdrant path does
    (G293); a value matches the field or an element of an array field.
    """
    clauses, params = [], []
    for key, value in (filters or {}).items():
        if isinstance(value, list):
            options = value
        elif key == "fiscal_year" and isinstance(value, str) and value:
            options = [value, ""]
        else:
            options = [value]
        if not options:
            clauses.append("FALSE")  # Qdrant's MatchAny([]) matches nothing too
            continue
        # A value matches the field itself or, as in Qdrant, an element of an array field.
        clauses.append("".join(("(", " OR ".join([_CONTAINS] * (2 * len(options))), ")")))
        for option in options:
            params.extend((json.dumps({key: option}), json.dumps({key: [option]})))
    return ("".join((" WHERE ", " AND ".join(clauses))) if clauses else ""), params


class LocalDenseStore:
    """Query-time access to the pgvector table. Never raises from :meth:`search`."""

    def __init__(self, dsn: str = DSN, *, embedder: Any = None, connect: Any = None) -> None:
        self._dsn = dsn
        self._embedder = embedder
        self._connect = connect
        self._conn: Any = None
        self._lock = threading.Lock()
        self.meta: dict[str, str] = {}
        self.reason = "not opened"

    def _connection(self) -> Any:
        if self._conn is None or getattr(self._conn, "closed", False):
            if self._connect is not None:
                self._conn = self._connect(self._dsn)
            else:
                import psycopg

                self._conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=3)
            with self._conn.cursor() as cur:
                cur.execute(f"SET statement_timeout = {int(QUERY_TIMEOUT_MS)}")
                cur.execute(f"SET hnsw.ef_search = {int(EF_SEARCH)}")
                # pgvector 0.8: keep scanning when a filter rejects the nearest rows.
                cur.execute("SET hnsw.iterative_scan = relaxed_order")
        return self._conn

    def open(self) -> bool:
        """Check the table is for the pinned model and load the embedder. Sets :attr:`reason` on failure."""
        try:
            with self._lock, self._connection().cursor() as cur:
                cur.execute("SELECT key, value FROM dense_meta")
                self.meta = {str(k): str(v) for k, v in cur.fetchall()}
            expected = {"model": dense_cpu.MODEL_REPO, "revision": dense_cpu.MODEL_REVISION, "dim": str(dense_cpu.DIM)}
            wrong = {k: self.meta.get(k) for k, v in expected.items() if self.meta.get(k) != v}
            if wrong:
                self.reason = f"table built for another model: {wrong}"
                return False
            if int(self.meta.get("count") or 0) <= 0:
                self.reason = "table is empty"
                return False
            if not self.meta.get("qdrant_corpus_hash"):
                self.reason = "table records no Qdrant corpus binding; reload it"
                return False
            if self._embedder is None:
                self._embedder = dense_cpu.OnnxEmbedder(dense_cpu.model_dir())
        except Exception as exc:  # noqa: BLE001 - a missing leg must not stop retrieval
            self.reason = f"{type(exc).__name__}: {str(exc)[:160]}"
            self._conn = None
            return False
        self.reason = ""
        return True

    def status(self) -> str:
        if self.reason:
            return f"unavailable ({self.reason})"
        return f"pgvector {self.meta.get('model', '?').split('/')[-1]}, {self.meta.get('count', '?')} vectors"

    def search(self, query: str, limit: int, filters: dict[str, Any] | None = None) -> list[tuple[str, dict[str, Any], float]]:
        """``(id, payload, cosine similarity)`` best first; ``[]`` on any failure."""
        vector = _vector_literal(self._embedder.embed_query(query))
        where, params = _filter_sql(filters)
        sql = "".join((_SEARCH_HEAD, where, _SEARCH_TAIL))
        if not self._lock.acquire(timeout=LOCK_WAIT_S):
            # Another search holds the connection, probably stalled: give this
            # query no dense half rather than queue behind it.
            raise TimeoutError("local dense store busy")
        try:
            with self._connection().cursor() as cur:
                cur.execute(sql, [vector, *params, int(limit)])
                rows = cur.fetchall()
        except Exception:
            self._conn = None  # reconnect next time
            raise
        finally:
            self._lock.release()
        out = []
        for pid, payload, distance in rows:
            if isinstance(payload, str):
                payload = json.loads(payload)
            out.append((str(pid), dict(payload or {}), 1.0 - float(distance)))
        return out


# --------------------------------------------------------------------------- CLI


def _selftest(conn: Any) -> None:
    """pgvector loads, an HNSW index builds, and a nearest-neighbour query answers."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("CREATE TEMP TABLE t (id int, v vector(3))")
        cur.execute("INSERT INTO t VALUES (1, '[1,0,0]'), (2, '[0,1,0]'), (3, '[0,0,1]')")
        cur.execute("CREATE INDEX ON t USING hnsw (v vector_cosine_ops)")
        cur.execute("SELECT id FROM t ORDER BY v <=> '[0.1,0.9,0]' LIMIT 1")
        assert cur.fetchone()[0] == 2, "pgvector returned the wrong neighbour"
    conn.rollback()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - command wiring
    parser = argparse.ArgumentParser(prog="python -m app.pgvector_dense")
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build-artifact", help="CI: download the pinned model and embed the corpus")
    build.add_argument("--out", type=Path, default=dense_cpu.DENSE_DIR)
    build.add_argument("--skip-download", action="store_true")
    load = sub.add_parser("load", help="Space build: load the artifact beside the baked Qdrant collection")
    load.add_argument("--artifact", type=Path, default=dense_cpu.DENSE_DIR)
    sub.add_parser("selftest", help="check pgvector works in this Postgres")
    sub.add_parser("check", help="open the store as the serving role and run one query")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # psycopg only where a database is used: CI's build-artifact runs without it.
    if args.command == "build-artifact":
        if not args.skip_download:
            dense_cpu.download_model(dense_cpu.model_dir(args.out))
        manifest = build_artifact(args.out)
        print(json.dumps({k: v for k, v in manifest.items() if k != "ids"}))  # noqa: T201 - CLI output
    elif args.command == "selftest":
        import psycopg

        with psycopg.connect(BUILD_DSN) as conn:
            _selftest(conn)
        print("pgvector selftest passed")  # noqa: T201 - CLI output
    elif args.command == "load":
        import psycopg
        from qdrant_client import QdrantClient

        client = QdrantClient(url=os.getenv("QDRANT_URL", "http://127.0.0.1:6333"), timeout=60)
        collection = os.getenv("QDRANT_COLLECTION", "ura_knowledge_base_active")
        binding: dict[str, str] = {}
        with psycopg.connect(BUILD_DSN) as conn:
            report = load_into_postgres(conn, args.artifact, qdrant_points(client, collection, binding),
                                        binding=binding)
            print(json.dumps(report))  # noqa: T201 - CLI output
    elif args.command == "check":
        store = LocalDenseStore()
        if not store.open():
            print(f"local dense store unavailable: {store.reason}", file=sys.stderr)  # noqa: T201 - CLI output
            return 1
        hits = store.search("How do I register for VAT?", 3)
        if len(hits) != 3:
            print(f"expected 3 hits, got {len(hits)}", file=sys.stderr)  # noqa: T201 - CLI output
            return 1
        print(f"{store.status()}; top hit {hits[0][0]} at {hits[0][2]:.3f}")  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
