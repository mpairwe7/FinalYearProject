"""Closing-courtesy flow: gratitude and farewell turns end naturally.

"thank you!" used to fall through to keyword retrieval and answer with FAQ
guidance — an unnatural close. These tests pin the always-on short-circuit
on both the REST and streaming paths, and that mixed messages ("thanks for
nothing!!", "thanks, but it still fails") still reach retrieval + distress
handling instead of a sign-off.
"""

from __future__ import annotations

import unittest

from app.text_signals import FAREWELL_REPLY, GRATITUDE_REPLY


class ClosingCourtesyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import database as db

        db.init_db()
        from app import service

        cls.model = service.ChatModel()

    def test_gratitude_short_circuits_rest(self) -> None:
        for msg in ("thank you!", "Thanks!", "thanks so much", "webale", "asante"):
            with self.subTest(msg=msg):
                out = self.model.generate(message=msg)
                self.assertEqual(out["reply"], GRATITUDE_REPLY, msg)
                self.assertEqual(out["retrieval_mode"], "greeting")
                self.assertIsNone(out["faithfulness_score"])
                self.assertTrue(out["next_actions"])

    def test_farewell_short_circuits_rest(self) -> None:
        for msg in ("goodbye", "bye!", "thanks bye"):
            with self.subTest(msg=msg):
                out = self.model.generate(message=msg)
                self.assertEqual(out["reply"], FAREWELL_REPLY, msg)
                self.assertEqual(out["retrieval_mode"], "greeting")

    def test_streaming_parity(self) -> None:
        out = self.model.generate_retrieval_only(message="thank you so much!")
        self.assertEqual(out["reply"], GRATITUDE_REPLY)
        self.assertEqual(out["retrieval_mode"], "greeting")
        self.assertEqual(out["_hits"], [])

    def test_mixed_messages_fall_through(self) -> None:
        for msg in (
            "thanks for nothing!!",
            "thanks, but the portal still fails",
            "thank you, and how do I file a VAT return?",
        ):
            with self.subTest(msg=msg):
                out = self.model.generate(message=msg)
                self.assertNotEqual(out["reply"], GRATITUDE_REPLY, msg)
                self.assertNotEqual(out["reply"], FAREWELL_REPLY, msg)


if __name__ == "__main__":
    unittest.main()
