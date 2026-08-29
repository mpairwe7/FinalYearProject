"""initialize() must not let a sparse-only Qdrant collection preempt Vectorize.

Regression test for a live-prod bug: Crane Cloud and the HF Space bake a
SPARSE-ONLY Qdrant collection into the image at build time (no torch, so no
dense vectors can be computed there). `_init_qdrant()` returns True for that
collection — it IS a working Qdrant connection — so `initialize()` used to
stop right there and never attempt the Cloudflare Vectorize dense fallback,
even when Vectorize was fully configured and seeded. Vectorize-backed hybrid
retrieval was confirmed working end-to-end in prod before the sparse-only
sidecar shipped (see the HF Space's memory), then silently regressed to
sparse/BM25-only once it did.

These tests drive `HybridRetriever.initialize()` directly, with `_init_qdrant`
and `_init_vectorize_mode` replaced by fakes that set the same state the real
methods set, so the assertions exercise the actual branching in `initialize()`
rather than re-testing Qdrant or Cloudflare connectivity.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retriever import HybridRetriever  # noqa: E402


def _fake_init_qdrant(retriever: HybridRetriever, *, sparse_only: bool):
    def _init() -> bool:
        retriever._client = object()  # anything non-None marks Qdrant "connected"
        retriever._sparse_only = sparse_only
        retriever._ready = True
        return True

    return _init


def _fake_init_qdrant_fails(retriever: HybridRetriever):
    def _init() -> bool:
        return False

    return _init


def _fake_init_vectorize(retriever: HybridRetriever, *, succeeds: bool):
    def _init() -> bool:
        if not succeeds:
            return False
        retriever._vectorize_mode = True
        retriever._ready = True
        return True

    return _init


class InitializePriorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_env = os.environ.get("QDRANT_ENABLED")
        os.environ["QDRANT_ENABLED"] = "true"
        import app.retriever as R
        self._orig_flag = R.QDRANT_ENABLED
        R.QDRANT_ENABLED = True

    def tearDown(self) -> None:
        import app.retriever as R
        R.QDRANT_ENABLED = self._orig_flag
        if self._orig_env is None:
            os.environ.pop("QDRANT_ENABLED", None)
        else:
            os.environ["QDRANT_ENABLED"] = self._orig_env

    def test_dense_qdrant_wins_immediately_without_trying_vectorize(self) -> None:
        """The normal (GPU/dev) case is unaffected: real dense Qdrant is the
        richest backend and initialize() must not even look at Vectorize."""
        r = HybridRetriever()
        r._init_qdrant = _fake_init_qdrant(r, sparse_only=False)
        vectorize_calls = []
        r._init_vectorize_mode = lambda: vectorize_calls.append(1) or False

        self.assertTrue(r.initialize())
        self.assertEqual(vectorize_calls, [], "dense Qdrant must not fall through to Vectorize")
        self.assertFalse(r._vectorize_mode)

    def test_sparse_only_qdrant_defers_to_working_vectorize(self) -> None:
        """The prod bug: sparse-only Qdrant must not be the final answer when
        real dense retrieval (Vectorize) is available."""
        r = HybridRetriever()
        r._init_qdrant = _fake_init_qdrant(r, sparse_only=True)
        r._init_vectorize_mode = _fake_init_vectorize(r, succeeds=True)

        self.assertTrue(r.initialize())
        self.assertTrue(r._vectorize_mode, "Vectorize must win over sparse-only Qdrant")

    def test_sparse_only_qdrant_survives_as_fallback_when_vectorize_unavailable(self) -> None:
        """Vectorize not configured/reachable must not throw away the working
        sparse-only Qdrant connection initialize() already has in hand."""
        r = HybridRetriever()
        r._init_qdrant = _fake_init_qdrant(r, sparse_only=True)
        r._init_vectorize_mode = _fake_init_vectorize(r, succeeds=False)

        self.assertTrue(r.initialize())
        self.assertFalse(r._vectorize_mode)
        self.assertTrue(r._sparse_only)
        self.assertIsNotNone(r._client, "sparse-only Qdrant state must not be discarded")
        self.assertTrue(r._ready)

    def test_qdrant_unavailable_still_falls_through_to_vectorize(self) -> None:
        """Pre-existing behaviour, unchanged: a hard Qdrant failure (not
        sparse-only — genuinely unreachable) still tries Vectorize."""
        r = HybridRetriever()
        r._init_qdrant = _fake_init_qdrant_fails(r)
        r._init_vectorize_mode = _fake_init_vectorize(r, succeeds=True)

        self.assertTrue(r.initialize())
        self.assertTrue(r._vectorize_mode)

    def test_nothing_available_returns_false(self) -> None:
        r = HybridRetriever()
        r._init_qdrant = _fake_init_qdrant_fails(r)
        r._init_vectorize_mode = _fake_init_vectorize(r, succeeds=False)

        self.assertFalse(r.initialize())


if __name__ == "__main__":
    unittest.main()
