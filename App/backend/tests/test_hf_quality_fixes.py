"""Quality fixes for the deployed app: rate questions answer with the real
figure, TIN registration clarifies individual vs organisation, and the
grounded-revision fallback never shows orphan footnote digits."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from app.calculator_router import format_rate_reply, plan_rate_lookup


class PlanRateLookupTests(unittest.TestCase):
    def test_vat_rate_question_maps_to_standard_rate(self) -> None:
        plan = plan_rate_lookup("What is the current VAT rate in Uganda?")
        self.assertIsNotNone(plan)
        self.assertEqual(plan.tax_type, "vat_standard")

    def test_short_ask_and_summaries(self) -> None:
        self.assertEqual(plan_rate_lookup("vat rate?").tax_type, "vat_standard")
        self.assertEqual(plan_rate_lookup("what is the PAYE rate").summary, "paye")
        self.assertEqual(plan_rate_lookup("rental income tax rate").summary, "rental")
        self.assertEqual(
            plan_rate_lookup("what is the withholding rate on dividends").tax_type,
            "withholding_dividend",
        )

    def test_calculations_and_non_rate_asks_are_left_alone(self) -> None:
        self.assertIsNone(plan_rate_lookup("calculate VAT on 1m"))  # amount → calc
        self.assertIsNone(plan_rate_lookup("how do I register for VAT?"))
        self.assertIsNone(plan_rate_lookup("what is a TIN?"))

    def test_format_uses_real_rate_table(self) -> None:
        from app.tools.calculators import _get_rates

        reply, actions = format_rate_reply(plan_rate_lookup("current VAT rate"), _get_rates())
        self.assertIn("18%", reply)
        self.assertIn("FY2025-26", reply)
        self.assertTrue(actions)

        paye_reply, _ = format_rate_reply(plan_rate_lookup("PAYE rates"), _get_rates())
        self.assertIn("UGX 235,000", paye_reply)
        self.assertIn("40%", paye_reply)


class GroundedRevisionDigitTests(unittest.TestCase):
    def test_trailing_footnote_digit_and_inline_ref_removed(self) -> None:
        from app import service

        hits = [{
            "source": "taxation_handbook.pdf",
            "text": (
                "Value Added Tax is an indirect tax on consumption applied to "
                "the value added to goods and services; VAT-registered businesses "
                "collect it from customers and remit to URA. 1"
            ),
        }]
        citations = [{"ref": "[1]", "source": "taxation_handbook.pdf"}]
        out = service.ChatModel._build_grounded_revision(hits, citations, "vat")
        self.assertTrue(out)
        self.assertFalse(out.rstrip().endswith("1"), out[-80:])
        self.assertNotIn("[1]", out)

    def test_legitimate_trailing_numbers_survive(self) -> None:
        from app import service

        hits = [{
            "source": "contacts.csv",
            "text": (
                "For help with any registration step, contact the URA Contact "
                "Centre on the toll-free line 0800 117 000."
            ),
        }]
        out = service.ChatModel._build_grounded_revision(hits, [], "help")
        self.assertIn("0800 117 000.", out)


class CleanPassageMarkdownTests(unittest.TestCase):
    """The Vectorize-fallback corpus (backend/scripts/reindex_vectorize.py)
    embeds raw pymupdf4llm Markdown chunks and never populates a hit's
    ``answer`` field, so ``_extract_grounded_answer_text`` always falls
    through to the raw ``text`` for those hits. Reproduces the exact
    "What services does URA provide?" reply observed live on the HF Space
    deployment, where ATX headings and bold markers leaked into the
    extractive-fallback reply verbatim."""

    def test_atx_headings_and_bold_markers_are_stripped(self) -> None:
        from app.service import _clean_passage_text

        raw = (
            "omes and wealth of the rich. The revenue raised is then used to "
            "provide social services for the benefit of the society. \n\n"
            "## **8.0 About Uganda Revenue Authority** \n\n"
            "Uganda Revenue Authority (URA) is a Statutory Authority "
            "established by the Uganda Revenue Authority Act, Cap 196 with "
            "the mandate of assessment, collection and administration of "
            "taxes, fees and Non- Tax revenue in Uganda. \n\n"
            "## **8.1 Vision** \n\n"
            "A transformational Revenue Service for Uganda's Economic "
            "Independence \n\n"
            "## **8.2 Mission**"
        )
        cleaned = _clean_passage_text(raw)
        self.assertNotIn("#", cleaned)
        self.assertNotIn("**", cleaned)
        self.assertIn("8.0 About Uganda Revenue Authority", cleaned)
        self.assertIn("8.1 Vision", cleaned)

    def test_bare_hash_in_prose_is_not_a_heading(self) -> None:
        from app.service import _clean_passage_text

        self.assertEqual(
            _clean_passage_text("Room #12 is on the third floor."),
            "Room #12 is on the third floor.",
        )

    def test_grounded_revision_excludes_markdown_artifacts(self) -> None:
        from app import service

        hits = [{
            "source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf",
            "text": (
                "## **8.0 About Uganda Revenue Authority**\n\n"
                "Uganda Revenue Authority (URA) is a Statutory Authority "
                "established by the Uganda Revenue Authority Act, Cap 196 "
                "with the mandate of assessment, collection and "
                "administration of taxes, fees and Non-Tax revenue in "
                "Uganda."
            ),
        }]
        out = service.ChatModel._build_grounded_revision(hits, [], "what services does ura provide")
        self.assertNotIn("##", out)
        self.assertNotIn("**", out)


class TinClarificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service
        from app.flags import flags

        flags.set("workflows", True)
        cls._flags = flags
        cls.model = service.ChatModel()

    @classmethod
    def tearDownClass(cls):
        cls._flags.clear("workflows")

    def test_untyped_ask_clarifies_then_returns_org_steps(self) -> None:
        thread = str(uuid.uuid4())
        first = self.model.generate_retrieval_only(
            message="How do I register for a TIN?", conversation_id=thread
        )
        self.assertEqual(first["retrieval_mode"], "workflow")
        self.assertIn("individual", first["reply"].lower())
        self.assertIn("organisation", first["reply"].lower())
        self.assertEqual(first["workflow"]["pending_slot"], "taxpayer_kind")

        second = self.model.generate_retrieval_only(
            message="organisation", conversation_id=thread
        )
        self.assertEqual(second["faithfulness_score"], 1.0)
        self.assertIn("Non-Individual TIN registration", second["reply"])
        self.assertIn(
            "curated deterministic template", second["response_judge"]["reasons"]
        )

    def test_pin_typo_is_understood(self) -> None:
        result = self.model.generate_retrieval_only(
            message="how do i register for a pin?", conversation_id=str(uuid.uuid4())
        )
        self.assertEqual(result["retrieval_mode"], "workflow")
        self.assertEqual(result["workflow"]["pending_slot"], "taxpayer_kind")

    def test_typed_ask_answers_immediately(self) -> None:
        result = self.model.generate_retrieval_only(
            message="How does my company register for a TIN?",
            conversation_id=str(uuid.uuid4()),
        )
        self.assertTrue(result.get("_short_circuit"))
        self.assertEqual(result["faithfulness_score"], 1.0)
        self.assertIn("Non-Individual TIN registration", result["reply"])

    def test_vat_rate_question_end_to_end(self) -> None:
        with patch("app.tools.rates._authority_payload", return_value=(True, {})):
            result = self.model.generate_retrieval_only(
                message="What is the current VAT rate in Uganda?",
                conversation_id=str(uuid.uuid4()),
            )
        self.assertEqual(result["retrieval_mode"], "calculator")
        self.assertIn("18%", result["reply"])
        self.assertEqual(result["response_judge"]["confidence_band"], "high")
        self.assertIn("official rate table", result["response_judge"]["reasons"])


if __name__ == "__main__":
    unittest.main()
