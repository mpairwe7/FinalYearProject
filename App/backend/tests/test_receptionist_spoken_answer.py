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


if __name__ == "__main__":
    unittest.main()
