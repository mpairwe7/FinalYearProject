"""P1-6 tests: BM25 corpus-hash binding + deterministic point ids.

The Qdrant round-trip itself is exercised in integration; here we cover the
pure logic that makes desync detectable and reindexing idempotent.
"""

from __future__ import annotations

import unittest

from app.retriever import (
    DENSE_DIM,
    DENSE_MODEL_NAME,
    BM25SparseEncoder,
    HybridRetriever,
    bm25_binding_sentinel_id,
    compute_corpus_hash,
    deterministic_point_id,
)


class CorpusHashTest(unittest.TestCase):
    def test_deterministic_and_order_sensitive(self) -> None:
        a = compute_corpus_hash(["alpha", "beta", "gamma"])
        self.assertEqual(a, compute_corpus_hash(["alpha", "beta", "gamma"]))
        # Order matters — BM25 token ids are first-seen-order dependent.
        self.assertNotEqual(a, compute_corpus_hash(["beta", "alpha", "gamma"]))
        # Content matters.
        self.assertNotEqual(a, compute_corpus_hash(["alpha", "beta", "delta"]))

    def test_no_collision_on_boundary(self) -> None:
        # Length-prefixing prevents ["ab","c"] hashing like ["a","bc"].
        self.assertNotEqual(
            compute_corpus_hash(["ab", "c"]), compute_corpus_hash(["a", "bc"])
        )


class DeterministicPointIdTest(unittest.TestCase):
    def test_stable_for_same_content(self) -> None:
        doc = {"source": "vat.pdf", "page": 3, "section": "rates", "text": "VAT is 18%."}
        self.assertEqual(deterministic_point_id(doc), deterministic_point_id(dict(doc)))

    def test_changes_with_text_or_source(self) -> None:
        base = {"source": "vat.pdf", "page": 3, "section": "rates", "text": "VAT is 18%."}
        diff_text = {**base, "text": "VAT is 20%."}
        diff_src = {**base, "source": "paye.pdf"}
        self.assertNotEqual(deterministic_point_id(base), deterministic_point_id(diff_text))
        self.assertNotEqual(deterministic_point_id(base), deterministic_point_id(diff_src))

    def test_is_valid_uuid(self) -> None:
        import uuid as _uuid

        pid = deterministic_point_id({"source": "x", "text": "y"})
        # Must be a parseable UUID (Qdrant requires uuid or unsigned int ids).
        self.assertEqual(str(_uuid.UUID(pid)), pid)

    def test_sentinel_id_stable_and_collection_scoped(self) -> None:
        self.assertEqual(bm25_binding_sentinel_id("kb"), bm25_binding_sentinel_id("kb"))
        self.assertNotEqual(bm25_binding_sentinel_id("kb"), bm25_binding_sentinel_id("kb2"))


class EncoderRoundTripTest(unittest.TestCase):
    def test_fit_sets_hash_and_roundtrips(self) -> None:
        docs = ["the vat rate is eighteen percent", "paye is computed in bands"]
        enc = BM25SparseEncoder().fit(docs)
        self.assertEqual(enc.corpus_hash, compute_corpus_hash(docs))
        restored = BM25SparseEncoder.from_dict(enc.to_dict())
        self.assertEqual(restored.corpus_hash, enc.corpus_hash)
        # Encoding still works after a round-trip.
        idx, val = restored.encode_query("vat rate")
        self.assertTrue(idx and all(v > 0 for v in val))

    def test_legacy_state_without_hash_loads(self) -> None:
        legacy = {"vocab": {"a": 0}, "idf": {"0": 1.0}, "avg_dl": 1.0, "next_id": 1}
        enc = BM25SparseEncoder.from_dict(legacy)
        self.assertEqual(enc.corpus_hash, "")


class AsymmetricEncodingTest(unittest.TestCase):
    """The document and query halves must not both carry IDF (see the class
    docstring on ``BM25SparseEncoder``): squaring it cost 8.9pp of Hit@1 on
    short questions."""

    DOCS = [
        "the vat rate is eighteen percent on taxable supplies",
        "paye is computed in bands on employment income",
    ]

    def test_query_weights_are_idf_alone(self) -> None:
        enc = BM25SparseEncoder().fit(self.DOCS)
        for text in ("vat", "vat vat vat"):
            idx, val = enc.encode_query(text)
            self.assertEqual(
                [round(enc._idf[t], 6) for t in idx],
                val,
                "query weight must be IDF — no term saturation, no document "
                f"length normalisation ({text!r})",
            )

    def test_document_weights_carry_no_idf(self) -> None:
        enc = BM25SparseEncoder().fit(self.DOCS)
        idx, val = enc.encode_document(self.DOCS[0])
        tokens = enc._tokenize(self.DOCS[0])
        expected = {
            enc._vocab[tok]: round(enc._saturation(tokens.count(tok), len(tokens)), 6)
            for tok in set(tokens)
        }
        self.assertEqual(dict(zip(idx, val)), expected)

    def test_dot_product_is_the_bm25_score(self) -> None:
        """What Qdrant computes for a sparse query must equal textbook BM25."""
        enc = BM25SparseEncoder().fit(self.DOCS)
        q_idx, q_val = enc.encode_query("vat rate")
        d_idx, d_val = enc.encode_document(self.DOCS[0])
        doc = dict(zip(d_idx, d_val))
        dot = sum(w * doc[t] for t, w in zip(q_idx, q_val) if t in doc)

        tokens = enc._tokenize(self.DOCS[0])
        expected = sum(
            enc._idf[enc._vocab[tok]] * enc._saturation(tokens.count(tok), len(tokens))
            for tok in ("vat", "rate")
        )
        self.assertAlmostEqual(dot, expected, places=4)

    def test_version_is_stamped_and_legacy_state_keeps_v1_scoring(self) -> None:
        enc = BM25SparseEncoder().fit(self.DOCS)
        self.assertEqual(enc.to_dict()["encoding_version"], BM25SparseEncoder.ENCODING_VERSION)

        legacy = enc.to_dict()
        legacy.pop("encoding_version")
        old = BM25SparseEncoder.from_dict(legacy)
        # A collection built by v1 code stores IDF in its document vectors, so
        # its query vectors must keep the v1 shape or the two halves disagree.
        idx, val = old.encode_query("vat")
        tid = old._vocab["vat"]
        self.assertNotEqual(val, [round(old._idf[tid], 6)])
        self.assertAlmostEqual(val[0], round(old._idf[tid] * old._saturation(1, 1), 6), places=5)


class _FakePoint:
    def __init__(self, corpus_hash: str) -> None:
        self.payload = {"_meta": "bm25_binding", "corpus_hash": corpus_hash}


class _FakeQdrant:
    def __init__(self, remote_hash: str | None) -> None:
        self._remote = remote_hash

    def retrieve(self, **kwargs):  # noqa: ANN003
        if self._remote is None:
            return []
        return [_FakePoint(self._remote)]


class BindingVerificationTest(unittest.TestCase):
    def _retriever(self, *, local_hash: str, remote_hash: str | None) -> HybridRetriever:
        r = HybridRetriever()
        r._sparse_encoder._corpus_hash = local_hash
        r._client = _FakeQdrant(remote_hash)
        return r

    def test_match_keeps_sparse_enabled(self) -> None:
        r = self._retriever(local_hash="abc123", remote_hash="abc123")
        r._verify_bm25_binding()
        self.assertTrue(r._sparse_ok)

    def test_mismatch_disables_sparse(self) -> None:
        r = self._retriever(local_hash="abc123", remote_hash="zzz999")
        r._verify_bm25_binding()
        self.assertFalse(r._sparse_ok)

    def test_missing_sentinel_leaves_sparse_enabled(self) -> None:
        r = self._retriever(local_hash="abc123", remote_hash=None)
        r._verify_bm25_binding()
        self.assertTrue(r._sparse_ok)

    def test_legacy_local_hash_skips_check(self) -> None:
        # No local hash → cannot verify → stay enabled (backward compatible).
        r = self._retriever(local_hash="", remote_hash="anything")
        r._verify_bm25_binding()
        self.assertTrue(r._sparse_ok)


class _FakeEmbedderPoint:
    def __init__(self, payload: dict) -> None:
        self.payload = payload


class _FakeEmbedderQdrant:
    def __init__(self, payload: dict | None) -> None:
        self._payload = payload

    def retrieve(self, **kwargs):  # noqa: ANN003
        return [] if self._payload is None else [_FakeEmbedderPoint(self._payload)]


class EmbedderBindingVerificationTest(unittest.TestCase):
    """The dense half has no self-check: querying a collection built by another
    encoder returns confidently ranked nonsense rather than an error, so the
    encoder identity is stamped into the collection and verified at init."""

    def _retriever(self, payload: dict | None) -> HybridRetriever:
        r = HybridRetriever()
        r._client = _FakeEmbedderQdrant(payload)
        return r

    def test_matching_model_and_dim_pass(self) -> None:
        r = self._retriever({"dense_model": DENSE_MODEL_NAME, "dense_dim": DENSE_DIM})
        self.assertTrue(r._verify_embedder_binding())

    def test_a_different_encoder_is_rejected(self) -> None:
        r = self._retriever(
            {"dense_model": "sentence-transformers/all-MiniLM-L6-v2", "dense_dim": 384}
        )
        self.assertFalse(r._verify_embedder_binding())

    def test_a_dimension_change_under_the_same_name_is_rejected(self) -> None:
        r = self._retriever({"dense_model": DENSE_MODEL_NAME, "dense_dim": DENSE_DIM + 1})
        self.assertFalse(r._verify_embedder_binding())

    def test_unstamped_collection_cannot_be_verified_and_stays_enabled(self) -> None:
        """Collections built before the stamp existed must keep working."""
        self.assertTrue(self._retriever({"corpus_hash": "abc"})._verify_embedder_binding())
        self.assertTrue(self._retriever(None)._verify_embedder_binding())

    def test_a_missing_dim_stamp_still_accepts_a_matching_model(self) -> None:
        r = self._retriever({"dense_model": DENSE_MODEL_NAME})
        self.assertTrue(r._verify_embedder_binding())

    def test_a_qdrant_error_does_not_take_dense_retrieval_down(self) -> None:
        class _Boom:
            def retrieve(self, **kwargs):  # noqa: ANN003
                raise RuntimeError("qdrant unreachable")

        r = HybridRetriever()
        r._client = _Boom()
        self.assertTrue(r._verify_embedder_binding())



class _FakeCollectionConfig:
    def __init__(self, vectors) -> None:
        self.config = type("C", (), {"params": type("P", (), {"vectors": vectors})()})()


class _FakeCollectionClient:
    def __init__(self, vectors) -> None:
        self._vectors = vectors

    def get_collection(self, name):  # noqa: ANN001, ANN201
        return _FakeCollectionConfig(self._vectors)


class SparseOnlyCollectionDetectionTests(unittest.TestCase):
    """A collection built with SPARSE_ONLY_INDEX=true declares no dense vector,
    and a dense prefetch against it fails outright — Qdrant answers
    `400 Not existing vector name error: dense` and the search returns nothing.

    Detection must come from the collection, not from whether
    sentence-transformers happens to be importable: the same sparse-only
    collection can be queried by a process that does have torch (observed while
    validating the HF Space sidecar).
    """

    def _retriever(self, vectors) -> HybridRetriever:
        r = HybridRetriever()
        r._client = _FakeCollectionClient(vectors)
        return r

    def test_named_dense_vector_is_detected(self) -> None:
        self.assertTrue(self._retriever({"dense": object()})._collection_has_dense_vector())

    def test_sparse_only_collection_reports_no_dense_vector(self) -> None:
        for vectors in ({}, None):
            self.assertFalse(
                self._retriever(vectors)._collection_has_dense_vector(), repr(vectors)
            )

    def test_unnamed_single_vector_collection_counts_as_dense(self) -> None:
        self.assertTrue(self._retriever(object())._collection_has_dense_vector())

    def test_an_unreadable_config_assumes_dense_so_behaviour_is_unchanged(self) -> None:
        class _Boom:
            def get_collection(self, name):  # noqa: ANN001, ANN201
                raise RuntimeError("no such collection")

        r = HybridRetriever()
        r._client = _Boom()
        self.assertTrue(r._collection_has_dense_vector())


class SparseOnlySearchGuardTests(unittest.TestCase):
    def test_search_without_a_dense_model_is_refused_unless_sparse_only(self) -> None:
        """The dense-model guard is relaxed only for the mode that legitimately
        has no dense model — not removed outright."""
        r = HybridRetriever()
        r._ready = True
        r._client = object()
        r._dense_model = None
        self.assertEqual(r.search("what is vat"), [])

    def test_sparse_only_search_with_no_matching_vocabulary_returns_empty(self) -> None:
        """With no dense half there is nothing to fall back on, so an
        out-of-vocabulary query must return empty rather than raise."""
        r = HybridRetriever()
        r._ready = True
        r._sparse_only = True
        r._dense_model = None
        r._client = object()
        r._sparse_encoder = BM25SparseEncoder().fit(["vat is charged on taxable supplies"])
        self.assertEqual(r.search("zzzz qqqq"), [])

if __name__ == "__main__":
    unittest.main()
