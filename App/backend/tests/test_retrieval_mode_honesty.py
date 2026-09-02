"""The reported retrieval mode must match the leg that actually ran.

Issue #430: the live HF Space answered verbatim FAQ questions and abstained
on natural paraphrases of the *same* indexed rows.  Measured locally, bge-m3
ranks every one of those paraphrases #1 with a reranker score of 0.98-1.00,
so the retrieval stack was never the problem — the deployed image has no
sentence-transformers, so ``HybridRetriever`` drops the embedder and the
reranker and serves BM25 alone (``retriever.py``, the ImportError branch).

Every chat response nevertheless reported ``"hybrid"``, which is the one
field an operator would have read to notice.  ``/ready`` had already been
fixed to resolve the real mode; the chat path had not, so both now share
``active_retrieval_mode``.
"""

from __future__ import annotations

import unittest

from app.retriever import active_retrieval_mode


class _Retriever:
    def __init__(self, *, sparse_only: bool = False, vectorize: bool = False) -> None:
        self._sparse_only = sparse_only
        self._vectorize_mode = vectorize


class ActiveRetrievalModeTests(unittest.TestCase):
    def test_a_sparse_only_retriever_is_not_called_hybrid(self) -> None:
        """The exact deployed-Space shape: BM25 doing all the work."""
        self.assertEqual(
            active_retrieval_mode(_Retriever(sparse_only=True), ready=True), "sparse"
        )

    def test_vectorize_outranks_the_sparse_flag(self) -> None:
        """A Vectorize deployment keeps sparse-only Qdrant state as a fallback.

        Reporting "sparse" there would be the same false signal in reverse.
        """
        self.assertEqual(
            active_retrieval_mode(_Retriever(sparse_only=True, vectorize=True), ready=True),
            "vector",
        )

    def test_a_full_retriever_is_still_hybrid(self) -> None:
        self.assertEqual(active_retrieval_mode(_Retriever(), ready=True), "hybrid")

    def test_an_unready_or_missing_retriever_is_keyword(self) -> None:
        self.assertEqual(active_retrieval_mode(_Retriever(), ready=False), "keyword")
        self.assertEqual(active_retrieval_mode(None, ready=True), "keyword")

    def test_an_unknown_retriever_shape_does_not_crash(self) -> None:
        """getattr defaults keep a mock retriever (no private flags) on "hybrid"."""
        self.assertEqual(active_retrieval_mode(object(), ready=True), "hybrid")


if __name__ == "__main__":
    unittest.main()
