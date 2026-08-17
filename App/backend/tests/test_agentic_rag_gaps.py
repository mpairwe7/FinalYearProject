"""Phase A/B gap closures: G18 translate-retrieve, G19 provenance, G21/G23 handoff, G24."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.graphs.main_graph import node_act, node_observe, node_reflect  # noqa: E402
from app.agents.graphs.state import AgentGraphState  # noqa: E402
from app.agents.prompts import detail_level_prompt  # noqa: E402
from app.query import english_retrieval_query  # noqa: E402
from app.retriever import HybridRetriever, _provenance_fields  # noqa: E402


class EnglishRetrievalQueryTests(unittest.TestCase):
    def test_english_is_unchanged(self) -> None:
        self.assertEqual(english_retrieval_query("VAT rate this year", "en"), "VAT rate this year")

    def test_failed_or_identical_translation_keeps_original(self) -> None:
        with patch("app.sunbird.translate_to_english", return_value="VAT rate this year"):
            self.assertEqual(
                english_retrieval_query("VAT rate this year", "lg"),
                "VAT rate this year",
            )
        with patch("app.sunbird.translate_to_english", side_effect=RuntimeError("down")):
            self.assertEqual(english_retrieval_query("essomero", "lg"), "essomero")

    def test_luganda_is_translated(self) -> None:
        with patch("app.sunbird.translate_to_english", return_value="How do I get a TIN?"):
            self.assertEqual(
                english_retrieval_query("Nfunira ntya TIN?", "lg"),
                "How do I get a TIN?",
            )


class ProvenanceTests(unittest.TestCase):
    def test_payload_url_and_effective_date(self) -> None:
        fields = _provenance_fields(
            {
                "url": "https://ura.go.ug/en/vat",
                "fiscal_year": "FY2026-27",
                "title": "VAT guide",
                "crawled_at": "2026-08-01",
            }
        )
        self.assertEqual(fields["url"], "https://ura.go.ug/en/vat")
        self.assertEqual(fields["effective_date"], "FY2026-27")
        self.assertEqual(fields["title"], "VAT guide")

    def test_build_citations_surfaces_url(self) -> None:
        cites = HybridRetriever.build_citations(
            [
                {
                    "source": "crawl.jsonl",
                    "url": "https://ura.go.ug/notice",
                    "effective_date": "FY2026-27",
                    "title": "Notice",
                    "text": "VAT is 18 percent on taxable supplies.",
                }
            ]
        )
        self.assertEqual(cites[0]["url"], "https://ura.go.ug/notice")
        self.assertEqual(cites[0]["effective_date"], "FY2026-27")
        self.assertEqual(cites[0]["title"], "Notice")


class DetailLevelTests(unittest.TestCase):
    def test_beginner_and_expert_are_distinct(self) -> None:
        beginner = detail_level_prompt("beginner")
        expert = detail_level_prompt("expert")
        self.assertIn("acronym", beginner.lower())
        self.assertIn("statutory", expert.lower())
        self.assertNotEqual(beginner, expert)

    def test_intermediate_and_unknown_add_nothing(self) -> None:
        self.assertEqual(detail_level_prompt("intermediate"), "")
        self.assertEqual(detail_level_prompt("hostile-injection"), "")


class SpecialistHandoffTests(unittest.TestCase):
    def test_empty_tools_handoff_to_retrieve_once(self) -> None:
        state = AgentGraphState(query="What is the VAT rate?", plan=[])
        self.assertEqual(node_act(state).next_node, "observe")
        result = node_observe(state)
        self.assertEqual(result.next_node, "retrieve")
        self.assertEqual(state.handoff_count, 1)
        self.assertEqual(state.handoff_from, "observe")

    def test_handoff_is_bounded(self) -> None:
        state = AgentGraphState(query="VAT", plan=[], handoff_count=1, max_handoffs=1)
        self.assertEqual(node_act(state).next_node, "observe")
        result = node_observe(state)
        self.assertEqual(result.next_node, "synthesize")
        self.assertEqual(state.handoff_count, 1)

    def test_usable_observation_skips_handoff(self) -> None:
        state = AgentGraphState(query="VAT", plan=[])
        state.observations.append({"ok": True, "explanation": "18%"})
        self.assertEqual(node_act(state).next_node, "observe")
        result = node_observe(state)
        self.assertEqual(result.next_node, "synthesize")
        self.assertEqual(state.handoff_count, 0)

    def test_reasoning_miss_retrieves_once_even_if_query_does_not_expand(self) -> None:
        state = AgentGraphState(
            query="How do I register for a TIN in Uganda?",
            reply="The weather in Kampala is sunny today.",
            hits=[{"text": "Register for a TIN on the URA portal.", "answer": "Use the portal."}],
            faithfulness=0.9,
        )
        with patch("app.corrective_rag._expand_query", return_value=state.query):
            result = node_reflect(state)
        self.assertEqual(result.next_node, "retrieve")
        self.assertEqual(state.reflect_count, 1)

    def test_low_faithfulness_retrieves_once_even_if_query_does_not_expand(self) -> None:
        state = AgentGraphState(
            query="What is the VAT rate?",
            reply="VAT is charged at 18 percent.",
            hits=[{"text": "unrelated", "answer": "unrelated"}],
            faithfulness=0.1,
        )
        with (
            patch(
                "app.retriever.HybridRetriever.compute_faithfulness",
                return_value=0.1,
            ),
            patch("app.corrective_rag._expand_query", return_value=state.query),
        ):
            result = node_reflect(state)
        self.assertEqual(result.next_node, "retrieve")
        self.assertEqual(state.reflect_count, 1)


if __name__ == "__main__":
    unittest.main()
