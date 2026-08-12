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
        idx, val = restored.encode("vat rate")
        self.assertTrue(idx and all(v > 0 for v in val))

    def test_legacy_state_without_hash_loads(self) -> None:
        legacy = {"vocab": {"a": 0}, "idf": {"0": 1.0}, "avg_dl": 1.0, "next_id": 1}
        enc = BM25SparseEncoder.from_dict(legacy)
        self.assertEqual(enc.corpus_hash, "")


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


if __name__ == "__main__":
    unittest.main()
