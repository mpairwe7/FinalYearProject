"""/ready must report the retrieval mode the retriever is actually in.

It computed `"hybrid" if model._retriever_ready else "keyword"` and never
consulted the retriever's own `_sparse_only`. A retriever that fell back —
no embedder stamp on the collection, or no sentence-transformers in the image —
still set `_retriever_ready`, so the probe said "hybrid" while BM25 did all the
work. Observed on the deployed Space, whose logs say:

    HybridRetriever ready in SPARSE-ONLY mode (url=http://127.0.0.1:6333 ...)
    Collection 'ura_knowledge_base' carries no embedder stamp

while /ready reported hybrid. That is the field an operator reads to decide
whether retrieval is healthy, and it was saying yes with dense switched off —
which also explains abstention on questions the knowledge base should cover.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import health_readiness  # noqa: E402


def _model(ready: bool, sparse_only: bool):
    return SimpleNamespace(
        _retriever_ready=ready,
        _retriever=SimpleNamespace(is_ready=ready, _sparse_only=sparse_only),
        _faq_index={"a": [], "b": []},
    )


class ReadinessRetrievalModeTests(unittest.TestCase):
    def test_dense_available_reports_hybrid(self):
        self.assertEqual(health_readiness(_model(True, False)).retrieval_mode, "hybrid")

    def test_sparse_only_is_not_reported_as_hybrid(self):
        """The bug: this said "hybrid" with dense switched off."""
        mode = health_readiness(_model(True, True)).retrieval_mode
        self.assertNotEqual(mode, "hybrid", "sparse-only must not masquerade as hybrid")
        self.assertEqual(mode, "sparse")

    def test_no_retriever_still_reports_keyword(self):
        self.assertEqual(health_readiness(_model(False, False)).retrieval_mode, "keyword")

    def test_a_retriever_without_the_attribute_is_treated_as_hybrid(self):
        """Older/stub retrievers lack _sparse_only; getattr default must not crash."""
        m = SimpleNamespace(
            _retriever_ready=True,
            _retriever=SimpleNamespace(is_ready=True),
            _faq_index={},
        )
        self.assertEqual(health_readiness(m).retrieval_mode, "hybrid")


if __name__ == "__main__":
    unittest.main()
