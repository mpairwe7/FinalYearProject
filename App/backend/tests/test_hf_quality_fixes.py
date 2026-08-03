"""Quality fixes for the deployed app: rate questions answer with the real
figure, TIN registration clarifies individual vs organisation, and the
grounded-revision fallback never shows orphan footnote digits."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import patch

from app.calculator_router import format_rate_reply, plan_rate_lookup
from app.tax.tables import get_table as get_table_in_force


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
        from app.tax.tables import get_table

        # Pinned year: the figures asserted here are FY2025-26 statute.
        fy25 = get_table("FY2025-26")
        reply, actions = format_rate_reply(plan_rate_lookup("current VAT rate"), fy25)
        self.assertIn("18%", reply)
        self.assertIn("FY2025-26", reply)
        self.assertTrue(actions)

        paye_reply, _ = format_rate_reply(plan_rate_lookup("PAYE rates"), fy25)
        self.assertIn("UGX 235,000", paye_reply)
        self.assertIn("40%", paye_reply)

    def test_format_names_whichever_year_is_in_force(self) -> None:
        current = get_table_in_force()
        reply, _ = format_rate_reply(plan_rate_lookup("current VAT rate"), current)
        self.assertIn(current.fiscal_year, reply)
        # A table compiled ahead of the gazetted Act must say so in the
        # reply itself, not only in the tool payload.
        self.assertEqual("provisional" in reply, not current.confirmed)


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

    def test_headings_become_their_own_paragraph(self) -> None:
        """Stripping the "##"/"**" markers must not collapse a multi-section
        chunk into one run-on paragraph — each former heading should still
        start a new paragraph so the excerpt stays readable."""
        from app.service import _clean_passage_text

        raw = (
            "Intro sentence about revenue. \n\n"
            "## **8.0 About Uganda Revenue Authority** \n\n"
            "URA body text here. \n\n"
            "## **8.1 Vision** \n\n"
            "Vision body text here."
        )
        cleaned = _clean_passage_text(raw)
        self.assertEqual(
            cleaned,
            "Intro sentence about revenue.\n\n"
            "8.0 About Uganda Revenue Authority\n\n"
            "URA body text here.\n\n"
            "8.1 Vision\n\n"
            "Vision body text here.",
        )

    def test_bare_hash_in_prose_is_not_a_heading(self) -> None:
        from app.service import _clean_passage_text

        self.assertEqual(
            _clean_passage_text("Room #12 is on the third floor."),
            "Room #12 is on the third floor.",
        )

    def test_mojibake_replacement_char_between_digits_becomes_a_period(self) -> None:
        """A lossy PDF-extraction encoding step corrupted section numbers like
        "8.0" into "8�0" in the live corpus; any other stray U+FFFD carries
        no recoverable meaning and is dropped outright."""
        from app.service import _clean_passage_text

        cleaned = _clean_passage_text(
            "Section 8�0 covers the tax base, split into 5�1 and 5�2. "
            "A stray�character elsewhere is just dropped."
        )
        self.assertNotIn("�", cleaned)
        self.assertIn("8.0", cleaned)
        self.assertIn("5.1", cleaned)
        self.assertIn("5.2", cleaned)
        self.assertIn("straycharacter", cleaned)

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

    def test_near_duplicate_excerpts_across_editions_are_skipped(self) -> None:
        """Different handbook fiscal-year editions often carry near-identical
        wording for the same section. The top-2 ranked hits repeating that
        passage must not both make it into the reply — the second slot
        should fall through to the next genuinely distinct hit."""
        from app import service

        hit_a = {
            "source": "Taxation-handbook-FY2023-24.pdf",
            "text": (
                "The revenue raised is then used to provide social services "
                "for the benefit of the society. \n\n"
                "## **8.0 About Uganda Revenue Authority** \n\n"
                "Uganda Revenue Authority (URA) is a Statutory Authority "
                "established by the Uganda Revenue Authority Act, Cap 196 "
                "with the mandate of assessment, collection and "
                "administration of taxes, fees and Non-Tax revenue in "
                "Uganda."
            ),
        }
        hit_b = {
            "source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf",
            "text": (
                "The revenue raised is then used to provide social services "
                "for the benefit of society. \n\n"
                "## **8�0 About Uganda Revenue Authority** \n\n"
                "Uganda Revenue Authority (URA) is a Statutory Authority "
                "established by the Uganda Revenue Authority Act, Cap 196, "
                "with the mandate of assessment, collection, and "
                "administration of taxes, fees, and non-tax revenue in "
                "Uganda."
            ),
        }
        hit_c = {
            "source": "ura_vat_faqs.csv",
            "answer": (
                "VAT registration is compulsory once taxable turnover "
                "exceeds UGX 150 million in any 12 consecutive months, or "
                "UGX 37.5 million in any 3 consecutive months."
            ),
        }
        out = service.ChatModel._build_grounded_revision(
            [hit_a, hit_b, hit_c], [], "what services does ura provide"
        )
        self.assertEqual(out.count("Statutory Authority"), 1)

    def test_near_duplicate_detection_handles_asymmetric_length(self) -> None:
        """Live miss: a plain Jaccard-over-union check missed this pair
        (0.53, just under the 0.6 cutoff) because the longer excerpt's
        unique tail content (Vision/Mission/Core Values sections) dilutes
        the union, even though the shorter excerpt is ~97% contained in the
        longer one. A containment ratio over the shorter excerpt's own
        token count must still catch it."""
        from app import service

        hit_a = {
            "source": "Taxation-handbook-FY2023-24.pdf",
            "text": (
                "omes and wealth of the rich. The revenue raised is then "
                "used to provide social services for the benefit of the "
                "society. \n\n"
                "## **8.0 About Uganda Revenue Authority** \n\n"
                "Uganda Revenue Authority (URA) is a Statutory Authority "
                "established by the Uganda Revenue Authority Act, Cap 196 "
                "with the mandate of assessment, collection and "
                "administration of taxes, fees and Non- Tax revenue in "
                "Uganda. \n\n"
                "## **8.1 Vision** \n\n"
                "A transformational Revenue Service for Uganda's Economic "
                "Independence \n\n"
                "## **8.2 Mission** \n\n"
                "Mobilize revenue for National Development in a "
                "Transparent and Efficient manner. \n\n"
                "## **8.3 Core Values** \n\n"
                "- Patriotism \n\n- Integrity \n\n- Professionalism \n\n"
                "## **8.4 Client Value Proposition**"
            ),
        }
        hit_b = {
            "source": "TAXATION-HANDBOOK-FY-2025-26-1.pdf",
            "text": (
                "incomes and wealth of the rich. The revenue raised is "
                "then used to provide social services for the benefit of "
                "society. \n\n"
                "## **8�0 About Uganda Revenue Authority** \n\n"
                "Uganda Revenue Authority (URA) is a Statutory Authority "
                "established by the Uganda Revenue Authority Act, Cap 196, "
                "with the mandate of assessment, collection, and "
                "administration of taxes, fees, and non-tax revenue in "
                "Uganda."
            ),
        }
        hit_c = {
            "source": "ura_vat_faqs.csv",
            "answer": (
                "VAT registration is compulsory once taxable turnover "
                "exceeds UGX 150 million in any 12 consecutive months, or "
                "UGX 37.5 million in any 3 consecutive months."
            ),
        }
        out = service.ChatModel._build_grounded_revision(
            [hit_a, hit_b, hit_c], [], "what services does ura provide"
        )
        self.assertEqual(out.count("Statutory Authority"), 1)
        self.assertIn("VAT registration", out)
        self.assertIn("VAT registration", out)


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
