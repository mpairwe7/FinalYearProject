"""The local dense retrieval leg: pgvector beside the sparse-only Qdrant collection (ADR-0034).

Postgres and the ONNX model are faked here; the HF Space image check
(``hf-space-image-check.yml``) starts the real Postgres and pgvector.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from app import dense_cpu, pgvector_dense
from app.retriever import (
    BM25SparseEncoder,
    HybridRetriever,
    active_retrieval_mode,
    deterministic_point_id,
)

ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- fakes


class _Encoding:
    def __init__(self, ids: list[int], mask: list[int]) -> None:
        self.ids, self.attention_mask = ids, mask


class _Tokenizer:
    """Tokens are words; padding to the longest in the batch, as `tokenizers` does."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.max_length = 0

    def enable_truncation(self, max_length: int) -> None:
        self.max_length = max_length

    def enable_padding(self) -> None:
        pass

    def encode_batch(self, texts: list[str]) -> list[_Encoding]:
        self.seen.extend(texts)
        words = [t.split()[: self.max_length] for t in texts]
        width = max(len(w) for w in words)
        return [_Encoding([len(x) for x in w] + [0] * (width - len(w)), [1] * len(w) + [0] * (width - len(w)))
                for w in words]


class _Input:
    def __init__(self, name: str) -> None:
        self.name = name


class _Session:
    """Hidden state per token: [token id, 1, 0, …], so mean pooling is checkable."""

    def __init__(self, dim: int = dense_cpu.DIM, inputs: tuple[str, ...] = ("input_ids", "attention_mask")) -> None:
        self.dim, self._inputs, self.feeds = dim, inputs, []

    def get_inputs(self) -> list[_Input]:
        return [_Input(n) for n in self._inputs]

    def run(self, _outputs: Any, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        self.feeds.append(feed)
        ids = feed["input_ids"].astype(np.float32)
        hidden = np.zeros((*ids.shape, self.dim), dtype=np.float32)
        hidden[..., 0] = ids
        hidden[..., 1] = 1.0
        return [hidden]


def _embedder(**kwargs: Any) -> dense_cpu.OnnxEmbedder:
    return dense_cpu.OnnxEmbedder(Path("."), session=_Session(**kwargs), tokenizer=_Tokenizer())


class _FixedEmbedder:
    """One distinct unit vector per text, keyed by the text, for the artifact tests."""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        self.texts.extend(texts)
        out = np.zeros((len(texts), dense_cpu.DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            out[i, int(hashlib.sha256(text.encode()).hexdigest(), 16) % dense_cpu.DIM] = 1.0
        return out

    def embed_query(self, _text: str) -> np.ndarray:
        vector = np.zeros(dense_cpu.DIM, dtype=np.float32)
        vector[0] = 1.0
        return vector


class _Copy:
    def __init__(self, rows: list[tuple]) -> None:
        self.rows = rows

    def __enter__(self) -> _Copy:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def write_row(self, row: tuple) -> None:
        self.rows.append(row)


class _Cursor:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_exc: Any) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> None:
        if self.conn.fail_next:
            self.conn.fail_next = False
            raise RuntimeError("connection lost")
        self.conn.sql.append((sql, params))

    def executemany(self, sql: str, rows: list) -> None:
        self.conn.sql.append((sql, rows))

    def copy(self, sql: str) -> _Copy:
        self.conn.sql.append((sql, None))
        return _Copy(self.conn.copied)

    def fetchall(self) -> list:
        return self.conn.results.pop(0) if self.conn.results else []


class _Conn:
    def __init__(self, results: list | None = None) -> None:
        self.sql: list[tuple[str, Any]] = []
        self.copied: list[tuple] = []
        self.results = list(results or [])
        self.fail_next = False
        self.committed = False
        self.closed = False

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed = True


class _Point:
    def __init__(self, pid: str, payload: dict[str, Any], score: float = 0.5) -> None:
        self.id, self.payload, self.score = pid, payload, score


class _Scroller:
    def __init__(self, pages: list[list[_Point]]) -> None:
        self.pages = pages

    def scroll(self, *, offset: Any = None, **_kw: Any) -> tuple[list[_Point], Any]:
        index = offset or 0
        return self.pages[index], (index + 1 if index + 1 < len(self.pages) else None)


def _docs() -> list[dict[str, Any]]:
    return [
        {"source": "vat.csv", "text": "Question: Who registers for VAT?\nAnswer: Turnover above the threshold.",
         "doc_type": "faq", "chunk_id": "f1"},
        {"source": "efris.pdf", "text": "A taxpayer may issue invoices offline for up to 120 hours.",
         "doc_type": "pdf_chunk", "chunk_id": "p7", "page": 3},
    ]


# --------------------------------------------------------------------------- embedder


def test_embeddings_are_prefixed_mean_pooled_and_normalised() -> None:
    embedder = _embedder()
    vectors = embedder.embed_passages(["a bb", "ccc dddd eeeee"])
    assert embedder._tokenizer.seen == ["passage: ccc dddd eeeee", "passage: a bb"], "longest first"
    # Mean of [len, 1] over real tokens only; padding is excluded by the mask.
    expected = [np.array([np.mean([8, 1, 2]), 1.0]), np.array([np.mean([8, 3, 4, 5]), 1.0])]
    for row, want in zip(vectors, expected, strict=True):
        assert np.allclose(row[:2], want / np.linalg.norm(want))
        assert np.isclose(np.linalg.norm(row), 1.0)
    query = embedder.embed_query("vat")
    assert embedder._tokenizer.seen[-1] == "query: vat"
    assert query.shape == (dense_cpu.DIM,)


def test_token_type_ids_are_sent_when_the_model_takes_them() -> None:
    session = _Session(inputs=("input_ids", "attention_mask", "token_type_ids"))
    dense_cpu.OnnxEmbedder(Path("."), session=session, tokenizer=_Tokenizer()).embed_query("x")
    assert "token_type_ids" in session.feeds[0]
    assert not session.feeds[0]["token_type_ids"].any()


def test_a_model_of_another_width_is_refused() -> None:
    with pytest.raises(RuntimeError, match="dimensions"):
        _embedder(dim=1024).embed_query("x")


def test_model_files_must_match_their_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx/model.onnx").write_bytes(b"model")
    monkeypatch.setattr(dense_cpu, "MODEL_FILES", {"onnx/model.onnx": hashlib.sha256(b"model").hexdigest(),
                                                   "tokenizer.json": "0" * 64})
    assert dense_cpu.verify_model_files(tmp_path) == ["tokenizer.json: missing"]
    (tmp_path / "tokenizer.json").write_text("{}")
    assert dense_cpu.verify_model_files(tmp_path)[0].startswith("tokenizer.json: SHA-256 differs")
    with pytest.raises(RuntimeError, match="rejected"):
        dense_cpu.OnnxEmbedder(tmp_path)


# --------------------------------------------------------------------------- artifact


def test_the_artifact_carries_qdrant_point_ids(tmp_path: Path) -> None:
    docs = _docs() + [dict(_docs()[0], doc_type="faq_v2")]  # same id as the first: Qdrant keeps the last
    embedder = _FixedEmbedder()
    manifest = pgvector_dense.build_artifact(tmp_path, documents=docs, embedder=embedder)
    assert manifest["ids"] == [deterministic_point_id(_docs()[0]), deterministic_point_id(_docs()[1])]
    assert manifest["count"] == 2
    assert len(embedder.texts) == 2
    read, vectors = pgvector_dense.read_artifact(tmp_path)
    assert read["id_set_sha256"] == manifest["id_set_sha256"]
    assert vectors.dtype == np.float16
    assert vectors.shape == (2, dense_cpu.DIM)


@pytest.mark.parametrize(("change", "match"), [
    ({"revision": "0" * 40}, "another model"),
    ({"model": "BAAI/bge-m3"}, "another model"),
    ({"count": 3}, "does not match"),
    ({"id_set_sha256": "x"}, "digest"),
])
def test_an_artifact_for_anything_else_is_refused(tmp_path: Path, change: dict, match: str) -> None:
    pgvector_dense.build_artifact(tmp_path, documents=_docs(), embedder=_FixedEmbedder())
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    (tmp_path / "manifest.json").write_text(json.dumps({**manifest, **change}))
    with pytest.raises(pgvector_dense.ArtifactError, match=match):
        pgvector_dense.read_artifact(tmp_path)


def test_a_missing_artifact_is_reported(tmp_path: Path) -> None:
    with pytest.raises(pgvector_dense.ArtifactError, match="no readable"):
        pgvector_dense.read_artifact(tmp_path)


# --------------------------------------------------------------------------- load


def test_qdrant_points_skips_the_binding_sentinel_and_pages() -> None:
    client = _Scroller([[_Point("a", {"text": "x"}), _Point("s", {"_meta": "bm25_binding"})], [_Point("b", {"text": "y"})]])
    assert [pid for pid, _ in pgvector_dense.qdrant_points(client, "c")] == ["a", "b"]


def test_the_load_takes_qdrant_payloads_and_builds_hnsw(tmp_path: Path) -> None:
    manifest = pgvector_dense.build_artifact(tmp_path, documents=_docs(), embedder=_FixedEmbedder())
    points = [(pid, {"text": f"payload {pid}", "doc_type": "faq"}) for pid in manifest["ids"]]
    conn = _Conn()
    report = pgvector_dense.load_into_postgres(conn, tmp_path, points, binding={"corpus_hash": "c0ffee"})
    assert report["loaded"] == 2
    assert conn.committed
    assert [json.loads(row[1])["text"] for row in conn.copied] == [f"payload {pid}" for pid in manifest["ids"]]
    assert all(row[2].startswith("[") and row[2].count(",") == dense_cpu.DIM - 1 for row in conn.copied)
    sql = "\n".join(s for s, _ in conn.sql)
    assert "USING hnsw (embedding vector_cosine_ops)" in sql
    assert f"GRANT SELECT ON {pgvector_dense.TABLE}" in sql
    assert "INSERT" not in sql.split("GRANT")[1]
    meta = dict(next(rows for s, rows in conn.sql if s.startswith(f"INSERT INTO {pgvector_dense.META_TABLE}")))
    assert meta["revision"] == dense_cpu.MODEL_REVISION
    assert meta["count"] == "2"


@pytest.mark.parametrize("drop", ["point", "vector"])
def test_a_load_for_another_corpus_is_refused(tmp_path: Path, drop: str) -> None:
    manifest = pgvector_dense.build_artifact(tmp_path, documents=_docs(), embedder=_FixedEmbedder())
    points = [(pid, {"text": "t"}) for pid in manifest["ids"]]
    points = points[1:] if drop == "point" else points + [("not-in-artifact", {"text": "t"})]
    conn = _Conn()
    with pytest.raises(pgvector_dense.LoadRefused, match="disagree"):
        pgvector_dense.load_into_postgres(conn, tmp_path, points, binding={"corpus_hash": "c0ffee"})
    assert conn.sql == [], "nothing is written for a refused load"
    assert not conn.committed


# --------------------------------------------------------------------------- runtime store


def test_filters_follow_qdrant_semantics_and_are_parameterised() -> None:
    where, params = pgvector_dense._filter_sql({"doc_type": ["faq", "pdf_chunk"], "fiscal_year": "FY2026-27",
                                                "tag": "vat'; DROP TABLE x; --"})
    assert where.count("payload @> %s::jsonb") == 10
    assert "DROP" not in where
    # Each value matches the field itself or, as in Qdrant, an element of an array field.
    assert [json.loads(p) for p in params] == [
        {"doc_type": "faq"}, {"doc_type": ["faq"]}, {"doc_type": "pdf_chunk"}, {"doc_type": ["pdf_chunk"]},
        {"fiscal_year": "FY2026-27"}, {"fiscal_year": ["FY2026-27"]}, {"fiscal_year": ""}, {"fiscal_year": [""]},
        {"tag": "vat'; DROP TABLE x; --"}, {"tag": ["vat'; DROP TABLE x; --"]},
    ]
    assert pgvector_dense._filter_sql(None) == ("", [])


def _meta_rows(**override: str) -> list[tuple[str, str]]:
    meta = {"model": dense_cpu.MODEL_REPO, "revision": dense_cpu.MODEL_REVISION, "dim": str(dense_cpu.DIM),
            "count": "7251", "qdrant_corpus_hash": "c0ffee", **override}
    return list(meta.items())


@pytest.mark.parametrize(("meta", "reason"), [
    (_meta_rows(revision="0" * 40), "another model"),
    (_meta_rows(count="0"), "empty"),
])
def test_a_table_for_another_model_or_empty_is_not_opened(meta: list, reason: str) -> None:
    store = pgvector_dense.LocalDenseStore(embedder=_FixedEmbedder(), connect=lambda _dsn: _Conn([meta]))
    assert store.open() is False
    assert reason in store.reason
    assert store.status().startswith("unavailable")


def test_search_returns_similarity_best_first() -> None:
    conn = _Conn([_meta_rows(), [("b", {"text": "efris"}, 0.2), ("a", json.dumps({"text": "vat"}), 0.4)]])
    store = pgvector_dense.LocalDenseStore(embedder=_FixedEmbedder(), connect=lambda _dsn: conn)
    assert store.open()
    assert store.status() == "pgvector multilingual-e5-small, 7251 vectors"
    hits = store.search("offline invoices", 5, {"doc_type": "pdf_chunk"})
    assert [(h[0], round(h[2], 2)) for h in hits] == [("b", 0.8), ("a", 0.6)]
    sql, params = conn.sql[-1]
    assert "AS MATERIALIZED" in sql
    assert "embedding <=> %s::vector" in sql
    assert params[-1] == 5
    assert any("hnsw.iterative_scan = relaxed_order" in s for s, _ in conn.sql)


def test_a_lost_connection_is_reopened() -> None:
    conns = [_Conn([_meta_rows()]), _Conn([[("a", {"text": "x"}, 0.1)]])]
    store = pgvector_dense.LocalDenseStore(embedder=_FixedEmbedder(), connect=lambda _dsn: conns.pop(0))
    assert store.open()
    store._conn.fail_next = True
    with pytest.raises(RuntimeError):
        store.search("q", 1)
    assert [h[0] for h in store.search("q", 1)] == ["a"]


# --------------------------------------------------------------------------- retriever


class _FakeStore:
    def __init__(self, rows: list | None = None, error: bool = False) -> None:
        self.rows, self.error, self.queries = rows or [], error, []

    def search(self, query: str, limit: int, filters: Any = None) -> list:
        self.queries.append((query, limit, filters))
        if self.error:
            raise RuntimeError("postgres down")
        return self.rows

    def status(self) -> str:
        return "pgvector multilingual-e5-small, 2 vectors"


class _Qdrant:
    def __init__(self, points: list[_Point]) -> None:
        self.points, self.calls = points, 0

    def query_points(self, **_kw: Any) -> Any:
        self.calls += 1
        return type("R", (), {"points": self.points})()


def _retriever(points: list[_Point], store: _FakeStore | None) -> HybridRetriever:
    r = HybridRetriever()
    r._ready = True
    r._sparse_only = True
    r._dense_model = None
    r._client = _Qdrant(points)
    r._sparse_encoder = BM25SparseEncoder().fit([
        "value added tax registration threshold turnover",
        "electronic fiscal receipting offline invoices",
    ])
    r._local_dense = store
    return r


VAT = {"text": "Register for value added tax when turnover passes the threshold.", "doc_type": "faq", "source": "vat.csv"}
EFRIS = {"text": "Invoices may be issued without a connection for up to 120 hours.", "doc_type": "pdf_chunk",
         "source": "efris.pdf"}


def test_a_passage_sharing_no_word_with_the_question_is_found() -> None:
    store = _FakeStore([("efris", EFRIS, 0.86)])
    r = _retriever([], store)
    hits = r.search("how long can I keep billing while the internet is down", top_k=3)
    assert [h["id"] for h in hits] == ["efris"]
    assert hits[0]["score_dense"] == pytest.approx(0.86)
    assert r._client.calls == 0, "no BM25 term, so Qdrant is not asked"


def test_both_legs_are_fused() -> None:
    store = _FakeStore([("efris", EFRIS, 0.8), ("vat", VAT, 0.5)])
    r = _retriever([_Point("vat", VAT)], store)
    hits = r.search("value added tax registration offline invoices", top_k=4)
    assert {h["id"] for h in hits} == {"vat", "efris"}
    assert store.queries[0][1] == 20, "the dense leg is asked for the prefetch depth"


def test_a_failing_dense_leg_serves_bm25_alone_without_charging_qdrant(monkeypatch: pytest.MonkeyPatch) -> None:
    r = _retriever([_Point("vat", VAT)], _FakeStore(error=True))
    charged: list[int] = []
    monkeypatch.setattr(r._circuit, "record_failure", lambda: charged.append(1))
    for _ in range(3):
        assert [h["id"] for h in r.search("value added tax registration", top_k=2)] == ["vat"]
    assert charged == [], "a dense failure must not count against Qdrant"
    assert r._local_dense_circuit.state.name == "OPEN"


def test_blending_keeps_the_dense_leg_s_top_hit_near_the_top() -> None:
    from app.retriever import _blend_orders

    lexical = [{"id": "vat"}, {"id": "other"}, {"id": "efris"}]  # word overlap alone
    blended = _blend_orders(lexical, ["efris", "vat", "other"])  # the fused order
    assert [h["id"] for h in blended] == ["vat", "efris", "other"]


class _LexicalOnly:
    """The HF layer's CPU stand-in for a cross-encoder: word overlap only."""

    is_cpu_lexical_reranker = True
    activation_fn = type("Sigmoid", (), {})()

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        return [0.5 for _pair in pairs]


@pytest.mark.parametrize(("lexical", "blended"), [(True, True), (False, False)])
def test_search_blends_only_behind_the_cpu_lexical_reranker(monkeypatch: pytest.MonkeyPatch, lexical: bool,
                                                           blended: bool) -> None:
    from app import retriever as retriever_module

    calls: list[list[str]] = []
    real = retriever_module._blend_orders
    monkeypatch.setattr(retriever_module, "_blend_orders",
                        lambda candidates, fused: calls.append(list(fused)) or real(candidates, fused))
    r = _retriever([_Point("vat", VAT)], _FakeStore([("efris", EFRIS, 0.9)]))
    reranker = _LexicalOnly()
    if not lexical:
        reranker.is_cpu_lexical_reranker = False
    r._reranker = reranker
    r.search("value added tax registration offline invoices", top_k=3)
    assert bool(calls) is blended
    if blended:
        assert set(calls[0]) == {"vat", "efris"}, "the blend reads the fused order of both legs"


def test_readiness_names_the_leg() -> None:
    r = _retriever([], _FakeStore())
    assert active_retrieval_mode(r, ready=True) == "hybrid"
    assert r.local_dense_status() == "pgvector multilingual-e5-small, 2 vectors"
    r._local_dense = None
    r._local_dense_reason = "UndefinedTable: relation dense_meta does not exist"
    assert active_retrieval_mode(r, ready=True) == "sparse"
    assert r.local_dense_status().startswith("unavailable (UndefinedTable")


def test_the_leg_is_off_unless_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    r = HybridRetriever()
    monkeypatch.setattr(pgvector_dense, "BACKEND", "")
    assert r._init_local_dense() is False
    assert r.local_dense_status() == "off"
    monkeypatch.setattr(pgvector_dense, "BACKEND", "faiss")
    assert r._init_local_dense() is False
    assert "unrecognised" in r.local_dense_status()


def test_startup_waits_for_postgres_but_not_for_a_bad_table(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[str] = []

    class _Store:
        def __init__(self, reasons: list[str]) -> None:
            self.reasons, self.reason = reasons, ""

        def open(self) -> bool:
            self.reason = self.reasons.pop(0) if self.reasons else ""
            attempts.append(self.reason)
            return not self.reason

        def status(self) -> str:
            return "ok"

    monkeypatch.setattr(pgvector_dense, "BACKEND", "pgvector")
    monkeypatch.setattr("app.retriever.time.sleep", lambda _s: None)
    monkeypatch.setattr(pgvector_dense, "LocalDenseStore", lambda: _Store(["OperationalError: no socket"] * 2))
    r = HybridRetriever()
    assert r._init_local_dense() is True
    assert len(attempts) == 3
    attempts.clear()
    monkeypatch.setattr(pgvector_dense, "LocalDenseStore", lambda: _Store(["table built for another model: {}"]))
    assert r._init_local_dense() is False
    assert len(attempts) == 1


def test_a_sparse_only_collection_prefers_the_local_leg_over_vectorize(monkeypatch: pytest.MonkeyPatch) -> None:
    r = HybridRetriever()
    monkeypatch.setattr("app.retriever.QDRANT_ENABLED", True)

    def _qdrant() -> bool:
        r._sparse_only = True
        return True

    monkeypatch.setattr(r, "_init_qdrant", _qdrant)
    monkeypatch.setattr(r, "_init_local_dense", lambda: True)
    monkeypatch.setattr(r, "_init_vectorize_mode", lambda: pytest.fail("Vectorize must not be tried first"))
    assert r.initialize() is True


# --------------------------------------------------------------------------- packaging


def test_the_space_layer_installs_a_socket_only_pinned_pgvector() -> None:
    dockerfile = (ROOT / "App/deploy/hf-space/Dockerfile").read_text()
    pins = re.findall(r"COPY --from=pgvector/pgvector:[\w.-]+@(sha256:[0-9a-f]{64}) ", dockerfile)
    assert len(pins) == 2, "both pgvector COPYs are digest-pinned"
    assert len(set(pins)) == 1, "to one image"
    program = dockerfile[dockerfile.index("'[program:postgres]'"):]
    assert "listen_addresses= " in program
    assert "unix_socket_directories=/app/pgrun" in program
    assert "'user=postgres'" in program
    assert "--auth-host=reject" in dockerfile
    assert "LOCAL_DENSE_BACKEND=pgvector" in dockerfile
    # A refused load leaves BM25 serving: the load sits in an if, never under set -e alone.
    assert re.search(r"if /usr/sbin/runuser -u postgres -- env QDRANT_URL=.*?app\.pgvector_dense load", dockerfile, re.S)
    # The base image's PATH has no /usr/sbin: every runuser is called by its path.
    assert not re.search(r"(?<!/usr/sbin/)runuser -u", dockerfile)


def test_ci_embeds_with_the_runtime_tokenizer_and_the_base_image_ships_it() -> None:
    workflow = (ROOT / ".github/workflows/ura-chatbot-build-push.yml").read_text()
    dockerfile = (ROOT / "App/deploy/hf-space/Dockerfile").read_text()
    ci = re.search(r'"tokenizers==([\d.]+)"', workflow).group(1)
    space = re.search(r'"tokenizers==([\d.]+)"', dockerfile).group(1)
    assert ci == space, "queries must be tokenised as the passages were"
    assert "python -m app.pgvector_dense build-artifact" in workflow
    key = re.search(r"key: dense-[^\n]+", workflow).group(0)
    assert "dense_cpu.py" in key
    assert "pgvector_dense.py" in key
    assert "pdf_jsonl" in key
    assert "COPY App/Data/dense" in (ROOT / "App/Dockerfile.cranecloud").read_text()
    assert "!App/Data/dense/**" in (ROOT / "App/Dockerfile.cranecloud.dockerignore").read_text()


def test_the_sql_spells_out_the_names_it_is_built_from() -> None:
    """No statement is formatted (Bandit B608); the literals must match the constants and width."""
    source = (Path(pgvector_dense.__file__)).read_text()
    assert not re.search(r'f"[^"\n]*\b(SELECT|INSERT|CREATE|DROP|GRANT|ANALYZE|COPY|WITH)\b', source)
    for name in (pgvector_dense.TABLE, pgvector_dense.META_TABLE, pgvector_dense.READER_ROLE):
        assert name in source
    assert f"vector({dense_cpu.DIM})" in source
    assert pgvector_dense.TABLE in pgvector_dense._SEARCH_HEAD


# --------------------------------------------------------------------------- PR 707 review


def test_a_reordered_vectors_file_is_refused(tmp_path: Path) -> None:
    pgvector_dense.build_artifact(tmp_path, documents=_docs(), embedder=_FixedEmbedder())
    vectors = np.load(tmp_path / "vectors.npy")
    np.save(tmp_path / "vectors.npy", vectors[::-1])  # same shape, rows swapped
    with pytest.raises(pgvector_dense.ArtifactError, match="does not match the manifest"):
        pgvector_dense.read_artifact(tmp_path)


def test_an_empty_list_filter_matches_nothing() -> None:
    where, params = pgvector_dense._filter_sql({"tag": [], "doc_type": "faq"})
    assert "FALSE" in where
    assert [json.loads(p) for p in params] == [{"doc_type": "faq"}, {"doc_type": ["faq"]}]


def test_the_load_binds_the_table_to_the_collection(tmp_path: Path) -> None:
    manifest = pgvector_dense.build_artifact(tmp_path, documents=_docs(), embedder=_FixedEmbedder())
    sentinel = _Point("s", {"_meta": "bm25_binding", "source_corpus_hash": "c0ffee", "corpus_hash": "older"})
    client = _Scroller([[_Point(pid, {"text": "t"}) for pid in manifest["ids"]] + [sentinel]])
    binding: dict[str, str] = {}
    conn = _Conn()
    pgvector_dense.load_into_postgres(conn, tmp_path, pgvector_dense.qdrant_points(client, "c", binding),
                                      binding=binding)
    meta = dict(next(rows for s, rows in conn.sql if s.startswith("INSERT INTO dense_meta")))
    assert meta["qdrant_corpus_hash"] == "c0ffee"
    with pytest.raises(pgvector_dense.LoadRefused, match="binding"):
        pgvector_dense.load_into_postgres(_Conn(), tmp_path, [(pid, {"text": "t"}) for pid in manifest["ids"]])


def test_a_table_without_a_corpus_binding_is_not_opened() -> None:
    store = pgvector_dense.LocalDenseStore(embedder=_FixedEmbedder(),
                                           connect=lambda _dsn: _Conn([_meta_rows(qdrant_corpus_hash="")]))
    assert store.open() is False
    assert "no Qdrant corpus binding" in store.reason


def test_queries_are_bounded_server_side_and_on_the_lock(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _Conn([_meta_rows()])
    store = pgvector_dense.LocalDenseStore(embedder=_FixedEmbedder(), connect=lambda _dsn: conn)
    assert store.open()
    assert any(s.startswith("SET statement_timeout = ") for s, _ in conn.sql)
    monkeypatch.setattr(pgvector_dense, "LOCK_WAIT_S", 0.01)
    store._lock.acquire()  # a stalled search holds the connection
    try:
        with pytest.raises(TimeoutError):
            store.search("q", 1)
    finally:
        store._lock.release()


def test_building_the_artifact_needs_no_psycopg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "psycopg", None)  # CI's embed step does not install it
    monkeypatch.setattr(pgvector_dense, "build_artifact", lambda out: {"ids": [], "count": 0})
    assert pgvector_dense.main(["build-artifact", "--skip-download", "--out", str(tmp_path)]) == 0


class _Slow(_FakeStore):
    def search(self, query: str, limit: int, filters: Any = None) -> list:
        import time

        time.sleep(0.5)
        return self.rows


def _bound(store: _FakeStore, corpus_hash: str = "c0ffee") -> _FakeStore:
    store.meta = {"qdrant_corpus_hash": corpus_hash}
    return store


def test_a_promoted_collection_turns_the_leg_off() -> None:
    r = _retriever([_Point("vat", VAT)], _bound(_FakeStore([("efris", EFRIS, 0.9)]), "old-corpus"))
    r._bound_corpus_hash = "new-corpus"
    assert [h["id"] for h in r.search("value added tax registration", top_k=3)] == ["vat"]
    assert r._local_dense is None
    assert "collection changed since the vectors were loaded" in r.local_dense_status()


def test_a_stalled_dense_search_is_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    from app import retriever as retriever_module

    monkeypatch.setattr(retriever_module, "_LOCAL_DENSE_TIMEOUT_S", 0.05)
    r = _retriever([_Point("vat", VAT)], _Slow([("efris", EFRIS, 0.9)]))
    started = time.perf_counter()
    assert [h["id"] for h in r.search("value added tax registration", top_k=3)] == ["vat"]
    assert time.perf_counter() - started < 0.4, "BM25 did not wait for the stalled dense half"


def test_readiness_reports_an_open_dense_breaker() -> None:
    r = _retriever([_Point("vat", VAT)], _FakeStore(error=True))
    for _ in range(3):
        r.search("value added tax registration", top_k=2)
    assert active_retrieval_mode(r, ready=True) == "sparse"
    assert r.local_dense_status().startswith("unavailable (circuit open")


class _BrokenQdrant:
    def query_points(self, **_kw: Any) -> Any:
        raise ConnectionError("qdrant down")


@pytest.mark.parametrize("how", ["raises", "circuit_open"])
def test_qdrant_down_falls_back_to_the_dense_leg(how: str) -> None:
    from app import retriever as retriever_module

    r = _retriever([], _FakeStore([("efris", EFRIS, 0.9)]))
    r._client = _BrokenQdrant()
    if how == "circuit_open":
        for _ in range(3):
            r._circuit.record_failure()
    token = retriever_module._SEARCH_FAILED.set(False)
    try:  # the flag is per request; restore it so later tests do not inherit an outage
        hits = r.search("value added tax registration offline invoices", top_k=3)
        assert [h["id"] for h in hits] == ["efris"]
        assert retriever_module._SEARCH_FAILED.get() is True, "still an outage answer, never cached"
    finally:
        retriever_module._SEARCH_FAILED.reset(token)


class _OverlapReranker:
    """The CPU stand-in: bounded word-overlap scores in [0, 1]."""

    is_cpu_lexical_reranker = True
    activation_fn = type("Sigmoid", (), {})()

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = []
        for q, d in pairs:
            qs, ds = set(q.lower().split()), set(d.lower().replace(".", "").split())
            scores.append(len(qs & ds) / max(1, len(qs)))
        return scores


def test_a_dense_only_passage_with_no_word_in_common_cannot_carry_an_answer() -> None:
    from app.guardrails import OutputGuard
    from app.retriever import gating_relevance

    r = _retriever([], _FakeStore([("efris", EFRIS, 0.9)]))
    r._reranker = _OverlapReranker()
    hits = r.search("how long can I keep billing while the internet is down", top_k=3)
    assert [h["id"] for h in hits] == ["efris"]
    assert gating_relevance(hits[0]) == 0.0, "not a sigmoid's 0.5"
    assert OutputGuard.should_abstain(hits) is True


def test_preference_ordering_keeps_the_blended_order() -> None:
    from app.retriever import _blend_cpu_lexical, hit_relevance

    reranked = [{"id": "vat", "score_rerank": 0.6}, {"id": "other", "score_rerank": 0.4},
                {"id": "efris", "score_rerank": 0.1}]
    blended = _blend_cpu_lexical(reranked, ["efris", "vat", "other"])
    assert [h["id"] for h in blended] == ["vat", "efris", "other"]
    assert [h["id"] for h in sorted(blended, key=hit_relevance, reverse=True)] == ["vat", "efris", "other"]
    assert {h["id"]: h["score_calibrated"] for h in blended} == {"vat": 0.6, "other": 0.4, "efris": 0.1}


def test_point_ids_have_their_own_module_and_the_cache_key_sees_it() -> None:
    from app import point_ids, retriever

    assert retriever.deterministic_point_id is point_ids.deterministic_point_id
    key = re.search(r"key: dense-[^\n]+", (ROOT / ".github/workflows/ura-chatbot-build-push.yml").read_text()).group(0)
    assert "point_ids.py" in key
    assert "retriever.py" not in key, "every retriever edit would re-embed the corpus"


def test_the_serving_process_can_only_be_the_reader() -> None:
    dockerfile = (ROOT / "App/deploy/hf-space/Dockerfile").read_text()
    assert "--auth-local=peer --auth-host=reject" in dockerfile
    assert "'local   ura_dense  ura_dense_reader  peer map=dense'" in dockerfile
    assert "'dense   root      ura_dense_reader'" in dockerfile
    hba = dockerfile.split("> /app/pgdata/pg_hba.conf")[0].split("$PG/initdb")[-1]
    assert "'host" not in hba, "no TCP (host) entries in pg_hba.conf"
    assert 'echo "FAIL: root can connect as postgres' in dockerfile
    assert "trust" not in dockerfile.split("--auth-local=")[1].split(";")[0]
    smoke = (ROOT / ".github/scripts/hf_space_pgvector_smoke.sh").read_text()
    assert "-U ura_dense_reader" in smoke
    assert "read-only boundary is broken" in smoke
    assert "a missing or empty table is a failed load" in smoke


def test_sparse_only_builds_have_the_dense_directory() -> None:
    import subprocess

    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", "App/Data/dense/.keep"], cwd=ROOT,
                             capture_output=True, text=True)
    assert tracked.returncode == 0, "App/Data/dense/.keep must be tracked"
