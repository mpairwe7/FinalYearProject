"""Tone-hint threading: the per-turn empathy instruction must reach the
system prompt on every LLM path, and stay absent for calm turns."""

from __future__ import annotations

import unittest

from app.llm import SYSTEM_PROMPT, _build_messages, _build_tool_messages


class ToneHintMessageTests(unittest.TestCase):
    def test_system_prompt_has_tone_rules(self) -> None:
        self.assertIn("## Tone", SYSTEM_PROMPT)
        self.assertIn("empathetic sentence", SYSTEM_PROMPT)

    def test_build_messages_appends_tone_hint(self) -> None:
        messages = _build_messages(
            "what is vat?",
            passages=[],
            tone_hint="The user sounds worried. Begin with one short reassuring sentence.",
        )
        system = messages[0]
        self.assertEqual(system["role"], "system")
        self.assertIn("## This turn", system["content"])
        self.assertIn("sounds worried", system["content"])

    def test_build_messages_without_hint_is_unchanged(self) -> None:
        messages = _build_messages("what is vat?", passages=[])
        self.assertNotIn("## This turn", messages[0]["content"])

    def test_build_tool_messages_appends_tone_hint(self) -> None:
        messages = _build_tool_messages(
            "what is vat?",
            passages=None,
            conversation_history=None,
            locale="en",
            tone_hint="The user sounds frustrated.",
        )
        self.assertIn("## This turn", messages[0]["content"])
        self.assertIn("sounds frustrated", messages[0]["content"])


class DistressToToneHintTests(unittest.TestCase):
    def test_detected_distress_maps_to_hint(self) -> None:
        from app.text_signals import detect_user_distress, tone_hint_for

        kind = detect_user_distress("I'm so frustrated, the portal still doesn't work")
        self.assertEqual(kind, "frustration")
        self.assertIn("frustrated", tone_hint_for(kind))
        self.assertEqual(tone_hint_for(""), "")


if __name__ == "__main__":
    unittest.main()
