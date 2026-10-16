"""Phase 39 signals: crisis recall, the check-in rule, voice affect, workflow loops."""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest import mock

import numpy as np
from app.receptionist.prosody import analyze_utterance_prosody
from app.text_signals import (
    CRISIS_LINES,
    checkin_already_given,
    crisis_support_reply,
    detect_crisis,
    needs_wellbeing_checkin,
    wellbeing_checkin_reply,
)
from app.workflows.registry import WorkflowRegistry, WorkflowSession


class CrisisRecallTests(unittest.TestCase):
    def test_passive_ideation_is_a_crisis(self) -> None:
        for text in (
            "I have nothing to live for",
            "My family would be better off without me",
            "I don't want to live anymore",
            "I'm tired of living",
            "I can't go on living like this",
        ):
            self.assertTrue(detect_crisis(text), text)

    def test_everyday_tiredness_is_not(self) -> None:
        for text in (
            "I'm tired of this portal",
            "We would be better off without the penalty",
            "I can't go on paying these fees",
            "I don't want to file late anymore",
        ):
            self.assertFalse(detect_crisis(text), text)


class CheckInTests(unittest.TestCase):
    def test_hopelessness_needs_hardship_or_enforcement_beside_it(self) -> None:
        self.assertFalse(needs_wellbeing_checkin("I can't cope anymore"))
        self.assertFalse(needs_wellbeing_checkin("I give up on this portal"))
        self.assertTrue(needs_wellbeing_checkin("I can't afford the penalty and I can't cope anymore"))
        # Across turns: the hardship came first.
        self.assertTrue(needs_wellbeing_checkin("I give up", ["What is PAYE?", "URA froze my bank account"]))
        self.assertTrue(needs_wellbeing_checkin("Nimechoka na maisha", ["Sina pesa ya kulipa kodi"]))

    def test_an_explicit_statement_goes_to_the_crisis_path_instead(self) -> None:
        self.assertFalse(needs_wellbeing_checkin("I can't afford it and I want to die"))

    def test_once_per_conversation(self) -> None:
        self.assertTrue(checkin_already_given(["…", wellbeing_checkin_reply()]))
        self.assertFalse(checkin_already_given(["Here is how PAYE works."]))

    def test_both_replies_give_the_same_verified_lines(self) -> None:
        self.assertIn(CRISIS_LINES, crisis_support_reply())
        self.assertIn(CRISIS_LINES, wellbeing_checkin_reply())


def _pcm(seconds: float, amplitude: float) -> bytes:
    rng = np.random.default_rng(7)
    samples = rng.normal(0.0, amplitude, int(16000 * seconds)).clip(-1, 1)
    return (samples * 32767).astype(np.int16).tobytes()


class VoiceAffectTests(unittest.TestCase):
    """Loudness or pace alone does not name a feeling (EU AI Act Art. 3(39) is the reference)."""

    LOUD_FAST = (_pcm(1.0, 0.5), "what is the vat rate for my small shop in kampala please")
    SLOW = (_pcm(4.0, 0.05), "vat rate")

    def test_a_loud_fast_caller_is_not_called_frustrated(self) -> None:
        pcm, text = self.LOUD_FAST
        self.assertEqual(analyze_utterance_prosody(pcm, text=text).caller_affect, "calm")

    def test_a_slow_caller_is_not_called_confused(self) -> None:
        pcm, text = self.SLOW
        self.assertEqual(analyze_utterance_prosody(pcm, text=text).caller_affect, "calm")

    def test_words_still_count_and_the_voice_intensifies_them(self) -> None:
        pcm, _ = self.LOUD_FAST
        profile = analyze_utterance_prosody(pcm, text="this is ridiculous, nothing works, I am so frustrated")
        self.assertEqual(profile.caller_affect, "urgent_distress")

    def test_acoustic_only_labels_return_only_when_opted_in(self) -> None:
        pcm, text = self.LOUD_FAST
        with mock.patch.dict(os.environ, {"PROSODY_ACOUSTIC_ONLY_AFFECT": "true"}):
            self.assertEqual(analyze_utterance_prosody(pcm, text=text).caller_affect, "frustrated")


class WorkflowLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        from pathlib import Path

        from app.workflows.loader import load_workflow

        flows = Path(__file__).resolve().parents[1] / "app" / "workflows" / "flows"
        WorkflowRegistry.register(load_workflow(flows / "return_filing.yaml"))

    def _at_tax_type(self) -> WorkflowSession:
        session = WorkflowRegistry.create_session("return_filing")
        WorkflowRegistry.advance(session, "")
        WorkflowRegistry.advance(session, "company")
        return session

    def test_wrong_answers_are_counted_and_a_right_one_clears_the_count(self) -> None:
        session = self._at_tax_type()
        self.assertEqual(WorkflowRegistry.advance(session, "banana").failed_attempts, 1)
        self.assertEqual(WorkflowRegistry.advance(session, "pineapple").failed_attempts, 2)
        turn = WorkflowRegistry.advance(session, "vat")
        self.assertEqual(turn.failed_attempts, 0)
        self.assertNotIn("_invalid_count", session.slots)


class InternalKeysTests(unittest.TestCase):
    def test_engine_state_is_never_what_the_taxpayer_gave(self) -> None:
        from app.officer_handoff import given_slot_names

        session: dict[str, Any] = {"slots": {"tax_type": "vat", "_tangent_count": 1, "_invalid_count": 2}}
        self.assertEqual(given_slot_names(session), ["tax_type"])

    def test_engine_state_never_reaches_the_slot_bank(self) -> None:
        from app.memory.resolution_ledger import ResolutionLedger

        written: dict[str, Any] = {}
        with mock.patch.multiple(
            "app.database",
            has_active_consent=lambda *a, **k: True,
            get_working_memory=lambda *a, **k: {},
            upsert_working_memory=lambda user_id, tenant, fields, ttl_seconds=0: written.update(fields) or True,
        ):
            ResolutionLedger().store_verified_slots("u-1", {"tax_type": "vat", "_invalid_step": "collect_period"})
        self.assertEqual(written["verified_slot_bank"], {"tax_type": "vat"})


if __name__ == "__main__":
    unittest.main()
