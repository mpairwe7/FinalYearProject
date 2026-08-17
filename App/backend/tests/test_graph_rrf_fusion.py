"""Statutory graph is a third RRF leg, not an unconditional prepend."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.retriever import RRF_K, rrf_fuse_ranked_lists  # noqa: E402
from app.service import ChatModel  # noqa: E402


class RrfFuseTests(unittest.TestCase):
    def test_empty_lists_yield_empty(self) -> None:
        self.assertEqual(rrf_fuse_ranked_lists([], []), [])

    def test_graph_only_survives_empty_passages(self) -> None:
        graph = [{"id": "graph:statutory", "text": "VAT 18%", "doc_type": "graph", "score_norm": 0.84}]
        fused = rrf_fuse_ranked_lists([], graph)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["id"], "graph:statutory")
        self.assertAlmostEqual(fused[0]["score_rrf"], 1.0 / (RRF_K + 0))

    def test_shared_id_accumulates_both_ranks(self) -> None:
        passages = [{"id": "a", "text": "p", "score_norm": 0.4}]
        graph = [{"id": "a", "text": "g", "doc_type": "graph", "score_norm": 0.84}]
        fused = rrf_fuse_ranked_lists(passages, graph)
        self.assertEqual(len(fused), 1)
        self.assertEqual(fused[0]["doc_type"], "graph")
        self.assertAlmostEqual(fused[0]["score_rrf"], 2.0 / (RRF_K + 0))

    def test_graph_does_not_unconditionally_outrank_a_strong_passage(self) -> None:
        """Prepend always put the graph first. Fusion must not."""
        passages = [
            {
                "id": "chunk-1",
                "text": "strong passage about tin registration steps",
                "score_norm": 0.95,
                "score_rrf": 0.02,
            }
        ]
        graph = [
            {
                "id": "graph:statutory",
                "text": "VAT 18%",
                "doc_type": "graph",
                "score_norm": 0.84,
            }
        ]
        fused = rrf_fuse_ranked_lists(passages, graph)
        self.assertEqual(fused[0]["id"], "chunk-1")
        self.assertEqual(fused[1]["id"], "graph:statutory")


class GraphHitShapeTests(unittest.TestCase):
    def test_rate_question_returns_a_fusable_hit(self) -> None:
        from app.graph.shadow import GRAPH_HIT_ID, graph_hit_for

        hit = graph_hit_for("What is the VAT rate in Uganda?")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["id"], GRAPH_HIT_ID)
        self.assertEqual(hit["doc_type"], "graph")
        self.assertIn("score_norm", hit)
        self.assertGreater(float(hit["score_norm"]), 0.5)
        self.assertIn("18", hit["answer"])

    def test_unrelated_question_returns_nothing(self) -> None:
        from app.graph.shadow import graph_hit_for

        self.assertIsNone(graph_hit_for("How do I bake banana bread?"))


class FuseGraphLegFlagTests(unittest.TestCase):
    def test_flags_off_leaves_hits_unchanged(self) -> None:
        hits = [{"id": "p", "text": "passage"}]
        with patch("app.flags.flags.is_enabled", return_value=False):
            out, fused = ChatModel._fuse_graph_leg("What is the VAT rate?", hits)
        self.assertFalse(fused)
        self.assertIs(out, hits)

    def test_flags_on_fuses_a_graph_hit(self) -> None:
        hits = [
            {
                "id": "chunk-1",
                "text": "a long passage about something else entirely here",
                "score_norm": 0.4,
            }
        ]
        fake = {
            "id": "graph:statutory",
            "text": "VAT is 18%",
            "doc_type": "graph",
            "score_norm": 0.84,
        }
        with (
            patch("app.flags.flags.is_enabled", return_value=True),
            patch("app.graph.shadow.graph_hit_for", return_value=fake),
        ):
            out, fused = ChatModel._fuse_graph_leg("What is the VAT rate?", hits)
        self.assertTrue(fused)
        ids = [h["id"] for h in out]
        self.assertIn("graph:statutory", ids)
        self.assertIn("chunk-1", ids)


if __name__ == "__main__":
    unittest.main()
