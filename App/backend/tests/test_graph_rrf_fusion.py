"""Statutory graph is a third RRF leg, not an unconditional prepend."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

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

    def test_fused_rank_is_primary_over_individual_relevance(self) -> None:
        """A consensus lower-ranked hit beats a one-leg relevance outlier."""
        one_leg = [
            {"id": "outlier", "text": "outlier", "score_norm": 0.99},
            {"id": "consensus", "text": "consensus", "score_norm": 0.20},
        ]
        second_leg = [{"id": "consensus", "text": "consensus", "score_norm": 0.20}]

        fused = rrf_fuse_ranked_lists(one_leg, second_leg)

        self.assertEqual(fused[0]["id"], "consensus")
        self.assertGreater(fused[0]["score_rrf"], fused[1]["score_rrf"])

    def test_duplicate_identity_contributes_only_once_per_leg(self) -> None:
        duplicate = {"id": "same", "text": "same", "score_norm": 0.4}
        next_hit = {"id": "next", "text": "next", "score_norm": 0.3}
        fused = rrf_fuse_ranked_lists([duplicate, dict(duplicate), next_hit])

        by_id = {hit["id"]: hit for hit in fused}
        self.assertEqual(len(fused), 2)
        self.assertAlmostEqual(by_id["same"]["score_rrf"], 1.0 / RRF_K)
        self.assertAlmostEqual(by_id["next"]["score_rrf"], 1.0 / (RRF_K + 1))

    def test_invalid_rrf_parameters_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="positive integer"):
            rrf_fuse_ranked_lists([], k=0)
        with pytest.raises(ValueError, match="non-negative integer"):
            rrf_fuse_ranked_lists([], top_k=-1)

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

    def test_current_fiscal_year_is_resolved_at_request_time(self) -> None:
        from app.graph.query import Claim, GraphAnswer
        from app.graph.shadow import graph_hit_for

        answer = GraphAnswer(
            claims=[Claim(subject="VAT", predicate="rate", value=0.18)],
            matched=True,
        )
        with (
            patch("app.query.current_fiscal_year", return_value="FY2027-28"),
            patch("app.graph.query.resolve", return_value=answer) as resolve,
        ):
            graph_hit_for("What is the VAT rate?")

        self.assertEqual(resolve.call_args.kwargs["default_fiscal_year"], "FY2027-28")


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
