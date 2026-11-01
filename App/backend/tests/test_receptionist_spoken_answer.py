"""What the caller hears: a numbered procedure is spoken whole, not cut at "2."."""

from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from app import database as db
from app.receptionist.brain import UraReceptionistBrain, _split_into_sentences
from app.receptionist.state import CallRoom, CallState
from app.receptionist.store import init_receptionist_schema, list_turns

# The guided TIN reply exactly as the 2026-09-30 call replay received it.
TIN_REPLY = (
    "Happy to help you register for a TIN!\n\nTo register for a TIN with URA in Uganda: "
    "1. Visit the official URA web portal at https://ura.go.ug 2. Navigate to **eServices** > "
    "**TIN Registration** 3. Complete the online application form and submit.\nAre you "
    "registering as an **individual** (requires your National ID/NIN) or an **organisation** "
    "(requires Certificate of Incorporation)?"
)


class SentenceSplitTests(unittest.TestCase):
    def test_a_numbered_procedure_stays_with_the_sentence_that_introduces_it(self):
        sentences = _split_into_sentences(TIN_REPLY)
        self.assertEqual(len(sentences), 3)
        self.assertTrue(sentences[1].startswith("To register for a TIN with URA in Uganda: 1. Visit"))
        self.assertTrue(sentences[1].endswith("application form and submit."))
        self.assertTrue(sentences[2].startswith("Are you registering"))

    def test_a_figure_still_ends_a_sentence(self):
        self.assertEqual(
            _split_into_sentences("The VAT rate is 18. Businesses must register."),
            ["The VAT rate is 18.", "Businesses must register."],
        )

    def test_a_list_on_its_own_lines(self):
        self.assertEqual(
            _split_into_sentences("1. Visit the portal.\n2. Open TIN Registration."),
            ["1. Visit the portal.", "2. Open TIN Registration."],
        )

    def test_abbreviations_do_not_end_a_sentence(self):
        self.assertEqual(
            _split_into_sentences("Pay by e.g. mobile money under Sec. 12. Keep the receipt."),
            ["Pay by e.g. mobile money under Sec. 12.", "Keep the receipt."],
        )

    def test_italic_assumptions_line_retains_closing_underscore(self):
        sentences = _split_into_sentences("- Gross salary: UGX 2,000,000\n- _Assumptions: gross monthly salary._")
        self.assertEqual(
            sentences,
            ["- Gross salary: UGX 2,000,000.", "- _Assumptions: gross monthly salary._"],
        )

    def test_unpunctuated_multiline_numbered_steps_split_into_discrete_sentences(self):
        raw = "1. Visit the URA web portal\n2. Select eServices and TIN Registration\n3. Submit the online form"
        self.assertEqual(
            _split_into_sentences(raw),
            [
                "1. Visit the URA web portal.",
                "2. Select eServices and TIN Registration.",
                "3. Submit the online form.",
            ],
        )


class SpokenAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        db.init_db()
        init_receptionist_schema()
        self.call_id = f"call_spoken_{uuid.uuid4().hex[:8]}"
        state = CallState(call_id=self.call_id, conversation_id=f"conv_{self.call_id}", mode="ai")
        chat_model = MagicMock()
        chat_model.generate.return_value = {"reply": TIN_REPLY, "sources": ["TIN guide"], "confidence": 0.9}
        self.brain = UraReceptionistBrain(room=CallRoom(call_id=self.call_id, state=state), chat_model=chat_model)
        self.brain.push_frame = AsyncMock()

    async def test_every_step_of_the_procedure_is_spoken(self):
        with patch("app.receptionist.brain.flags.is_enabled", return_value=False):
            await self.brain.handle_external_question("How do I register for a TIN?", [])
        spoken = [t["text"] for t in list_turns(self.call_id) if t["speaker"] == "assistant"][-1]
        self.assertIn("Complete the online application form and submit", spoken)
        self.assertIn("Are you registering as an individual", spoken)
        self.assertNotIn("portal at", spoken)  # the address is dropped with its "at"
        self.assertNotIn("more detail", spoken)  # nothing was cut

    async def test_compact_calculator_breakdown_speaks_payable_tax_in_full(self):
        calc_reply = (
            "**Capital gains calculation (FY2026-27)**\n\n"
            "- Sale price: UGX 100,000,000\n"
            "- Cost base: UGX 60,000,000\n"
            "- Gain: UGX 40,000,000\n"
            "- Tax at 30%: **UGX 12,000,000**\n"
            "- Statutory Basis: Income Tax Act"
        )
        spoken = self.brain._spoken_answer(calc_reply, offer_officer=False)
        self.assertIn("Tax at 30%: **UGX 12,000,000**.", spoken)
        self.assertNotIn("Would you like more detail?", spoken)

    async def test_vernacular_written_footers_are_stripped_from_spoken_answer(self):
        sw_reply = (
            "Kiwango cha VAT nchini Uganda ni 18%.\n\n"
            "Ikiwa utakabiliwa na changamoto katika hatua yoyote, URA iko tayari kukusaidia: piga 0800 117 000.\n\n"
            "Unaweza pia kutaka kujua: Ninawezaje kuwasilisha marejesho ya VAT?"
        )
        spoken = self.brain._spoken_answer(sw_reply, offer_officer=False)
        self.assertNotIn("Ikiwa utakabiliwa", spoken)
        self.assertNotIn("Unaweza pia kutaka kujua", spoken)
        self.assertIn("18%", spoken)

    async def test_seven_item_ura_services_list_is_spoken_in_full_without_truncating_after_two_items(self):
        services_reply = (
            "Uganda Revenue Authority (URA) serves as the country's central tax and customs authority. "
            "Based on our provided information, our core services include:\n\n"
            "- **Taxpayer Registration**: Providing instant Tax Identification Numbers (TIN).\n"
            "- **Domestic Tax Administration**: Managing VAT, PAYE, corporation tax, rental income tax, and withholding tax.\n"
            "- **Customs Services**: Handling import and export clearance, valuation, and baggage processing.\n"
            "- **E-Services & Solutions**: Implementing EFRIS e-invoicing and Digital Tax Stamps.\n"
            "- **Exemption & Compliance Support**: Offering tax exemption and compliance guidance.\n"
            "- **Taxpayer Education & Guidance**: Providing the Taxpayer Starter Pack and Taxation Handbook.\n"
            "- **Online Account Management**: Enabling online payments and tax account management via the URA web portal."
        )
        spoken = self.brain._spoken_answer(services_reply, offer_officer=False)
        self.assertIn("Taxpayer Registration", spoken)
        self.assertIn("Online Account Management", spoken)
        self.assertNotIn("Would you like more detail?", spoken)


if __name__ == "__main__":
    unittest.main()
