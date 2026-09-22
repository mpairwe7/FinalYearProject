"""Unit tests for the ClarifyGate and yes/no classifier (pure logic)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.receptionist.clarify import (
    ClarifyGate,
    ClarifyState,
    classify_yes_no,
)
from app.speech_service import WordConf


class TestClarifyGatePureLogic(unittest.TestCase):
    def setUp(self):
        self.gate = ClarifyGate(threshold=0.55, max_attempts=2)

    def test_classify_yes_no(self):
        self.assertEqual(classify_yes_no("Yes, that's right"), "yes")
        self.assertEqual(classify_yes_no("yep"), "yes")
        self.assertEqual(classify_yes_no("exactly"), "yes")
        self.assertEqual(classify_yes_no("No, that's wrong"), "no")
        self.assertEqual(classify_yes_no("nope"), "no")
        self.assertEqual(classify_yes_no("I want export"), "neither")

    def test_midport_to_import_clarification(self):
        # Caller says: "I want to know about group mid-port"
        words = [
            WordConf("I", 0.98),
            WordConf("want", 0.95),
            WordConf("to", 0.99),
            WordConf("know", 0.94),
            WordConf("about", 0.97),
            WordConf("group", 0.95),
            WordConf("mid-port", 0.31),  # below 0.55 threshold
        ]
        action = self.gate.assess("I want to know about group mid-port", words)
        self.assertEqual(action.action, "ask_term")
        self.assertEqual(action.candidate, "import")
        self.assertIn("import", action.prompt or "")

        # State transition: stage="term"
        state = ClarifyState(
            stage="term",
            original_question="I want to know about group mid-port",
            target_word="mid-port",
            suggested_term="import",
            previous_word="group",
            attempts=0,
        )

        # Step 2: Caller says "Yes, import"
        res_action = self.gate.resolve("Yes, import", words=None, state=state)
        self.assertEqual(res_action.action, "ask_confirm_question")
        self.assertEqual(state.stage, "confirm")
        self.assertIn("group import", res_action.prompt or "")
        self.assertEqual(res_action.corrected_text, "I want to know about group import")

        # Step 3: Caller says "Yes"
        final_action = self.gate.resolve("Yes", words=None, state=state)
        self.assertEqual(final_action.action, "answer")
        self.assertEqual(final_action.corrected_text, "I want to know about group import")

    def test_ask_repeat_when_no_lexicon_candidate(self):
        # Caller says unknown gibberish with low overall confidence
        words = [
            WordConf("I", 0.60),
            WordConf("want", 0.58),
            WordConf("to", 0.60),
            WordConf("know", 0.59),
            WordConf("about", 0.58),
            WordConf("group", 0.60),
            WordConf("xyzqqq", 0.20),
        ]
        action = self.gate.assess("I want to know about group xyzqqq", words)
        self.assertEqual(action.action, "ask_repeat")
        self.assertIn("group", action.prompt or "")

    def test_threshold_edges_and_stop_words_ignored(self):
        # Stop words below threshold must NOT trigger clarification
        words = [
            WordConf("the", 0.20),  # stop word
            WordConf("payment", 0.95),
            WordConf("of", 0.25),   # stop word
            WordConf("tax", 0.92),
        ]
        action = self.gate.assess("the payment of tax", words)
        self.assertEqual(action.action, "none")

        # Content word just above threshold (0.56 >= 0.55) -> none
        words2 = [
            WordConf("group", 0.95),
            WordConf("import", 0.56),
        ]
        action2 = self.gate.assess("group import", words2)
        self.assertEqual(action2.action, "none")

    def test_two_failures_trigger_transfer(self):
        state = ClarifyState(
            stage="term",
            original_question="I want group mid-port",
            target_word="mid-port",
            suggested_term="import",
            previous_word="group",
            attempts=0,
        )

        # Attempt 1: Caller says "No"
        act1 = self.gate.resolve("No", state=state, max_attempts=2)
        self.assertEqual(act1.action, "restart")
        self.assertEqual(state.attempts, 1)

        # Attempt 2: Caller says "No" again
        act2 = self.gate.resolve("No", state=state, max_attempts=2)
        self.assertEqual(act2.action, "transfer")
        self.assertEqual(act2.reason, "clarification_failed")

    def test_restatement_in_term_stage(self):
        state = ClarifyState(
            stage="term",
            original_question="I want to know about group mid-port",
            target_word="mid-port",
            suggested_term="import",
            previous_word="group",
            attempts=0,
        )
        # Instead of yes or no, caller clarifies: "I meant export"
        action = self.gate.resolve("export", state=state)
        self.assertEqual(action.action, "ask_confirm_question")
        self.assertEqual(action.candidate, "export")
        self.assertEqual(action.corrected_text, "I want to know about group export")

    def test_fallback_when_words_is_none(self):
        # words is None -> detect near miss in text
        action = self.gate.assess("I want to know about group midport", words=None)
        self.assertEqual(action.action, "ask_term")
        self.assertEqual(action.candidate, "import")

        # Completely clean text -> none
        clean_action = self.gate.assess("How do I register for TIN?", words=None)
        self.assertEqual(clean_action.action, "none")
