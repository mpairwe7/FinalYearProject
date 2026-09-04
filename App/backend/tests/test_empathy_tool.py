"""Tests for the emotional-intelligence tool."""

from __future__ import annotations

import unittest

from app.text_signals import empathy_ack, is_courtesy_sentence
from app.tools import ToolRegistry
from app.tools.empathy import assess


def _call(message: str) -> dict:
    return ToolRegistry.call("assess_emotional_tone", {"message": message})


class ClassificationTests(unittest.TestCase):
    def test_each_kind_is_recognised(self) -> None:
        cases = {
            "I am really worried about the VAT threshold, please help me": "anxiety",
            "This is the THIRD time I have tried and it still does not work!!": "frustration",
            "My deadline is tomorrow and I will be fined": "urgency",
            "I can't afford to pay, I'm going to lose my business": "hardship",
            "I do not understand what chargeable income means": "confusion",
            "What is the VAT rate?": "",
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                self.assertEqual(assess(message)["kind"], expected)

    def test_comprehension_trouble_is_confusion_not_anxiety(self) -> None:
        # text_signals folds "don't understand" into anxiety; for tone
        # selection it matters that this wants the explanation rebuilt,
        # not reassurance.
        result = assess("I don't understand what chargeable income means")
        self.assertEqual(result["kind"], "confusion")
        self.assertFalse(result["lead_with_acknowledgement"])

    def test_genuine_worry_still_beats_confusion(self) -> None:
        self.assertEqual(assess("I'm worried and confused about my return")["kind"], "anxiety")

    def test_hardship_outranks_frustration(self) -> None:
        # Both read frustrated and describe losing a business; the second
        # is the one that should change how the assistant answers.
        result = assess("This is useless, I'm going to lose my business over this")
        self.assertEqual(result["kind"], "hardship")

    def test_enforcement_hardship_triggers_hardship(self) -> None:
        for phrasing in [
            "My bank account is frozen by URA",
            "They issued an agency notice to my bank",
            "URA seized my goods at the border",
            "They sealed my shop yesterday",
            "I lost my job and cannot pay this assessment",
        ]:
            res = assess(phrasing)
            self.assertEqual(res["kind"], "hardship", f"Failed for {phrasing}")
            self.assertTrue(res["offer_human_handoff"], f"Failed for {phrasing}")


class IntensityTests(unittest.TestCase):
    def test_more_cues_raise_intensity(self) -> None:
        mild = assess("I am worried about my return")
        loud = assess("I am REALLY worried about my return, please help!!")
        self.assertEqual(mild["intensity"], "low")
        self.assertEqual(loud["intensity"], "high")

    def test_neutral_message_has_no_intensity(self) -> None:
        self.assertEqual(assess("What is the corporation tax rate?")["intensity"], "none")

    def test_hardship_is_always_high(self) -> None:
        self.assertEqual(assess("I cannot afford to pay")["intensity"], "high")


class GuidanceTests(unittest.TestCase):
    def test_hardship_offers_a_human(self) -> None:
        self.assertTrue(assess("I can't afford this")["offer_human_handoff"])

    def test_mild_frustration_does_not_offer_a_human(self) -> None:
        # Offering too early reads as a brush-off.
        self.assertFalse(assess("this is annoying")["offer_human_handoff"])

    def test_high_anxiety_offers_human(self) -> None:
        loud = assess("I am VERY scared, terrified and worried about this audit")
        self.assertEqual(loud["kind"], "anxiety")
        self.assertEqual(loud["intensity"], "high")
        self.assertTrue(loud["offer_human_handoff"])

    def test_neutral_message_gets_no_empathy_opener(self) -> None:
        result = assess("What is the VAT rate?")
        self.assertEqual(result["acknowledgement"], "")
        self.assertFalse(result["lead_with_acknowledgement"])

    def test_every_kind_carries_actionable_guidance(self) -> None:
        for message in (
            "I am worried",
            "this is useless",
            "my deadline is tomorrow",
            "I can't afford it",
            "I don't understand",
            "what is the VAT rate",
        ):
            with self.subTest(message=message):
                self.assertTrue(assess(message)["avoid"])


class GroundingSafetyTests(unittest.TestCase):
    """An empathy opener must never dilute the grounding gate."""

    def test_every_acknowledgement_is_classed_as_courtesy(self) -> None:
        for message in (
            "I am worried about this",
            "this is useless and frustrating",
            "my deadline is tomorrow",
            "I can't afford to pay",
        ):
            ack = assess(message)["acknowledgement"]
            with self.subTest(message=message):
                self.assertTrue(ack)
                # Courtesy sentences are excluded from faithfulness and
                # claim verification, so the opener cannot inflate a score.
                self.assertTrue(is_courtesy_sentence(ack), ack)

    def test_delegates_to_text_signals_rather_than_redefining(self) -> None:
        # One definition of each kind; the tool must not drift from the
        # copy the fixed reply paths emit.
        self.assertEqual(assess("I am worried")["acknowledgement"], empathy_ack("anxiety"))


class ToolContractTests(unittest.TestCase):
    def test_registered_and_read_only(self) -> None:
        tool = ToolRegistry.get("assess_emotional_tone")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.schema.namespace, "empathy")
        self.assertEqual(tool.schema.risk, "low")
        self.assertTrue(tool.schema.read_only)
        self.assertFalse(tool.schema.destructive)

    def test_declares_an_output_schema(self) -> None:
        descriptor = ToolRegistry.get("assess_emotional_tone").to_mcp_tool()
        self.assertIn("outputSchema", descriptor)
        self.assertIn("kind", descriptor["outputSchema"]["properties"])

    def test_blank_message_is_rejected(self) -> None:
        result = _call("   ")
        self.assertFalse(result["ok"])
        self.assertIn("message", result["error"])

    def test_result_is_deterministic(self) -> None:
        message = "I am worried about the penalty"
        self.assertEqual(_call(message), _call(message))


if __name__ == "__main__":
    unittest.main()
