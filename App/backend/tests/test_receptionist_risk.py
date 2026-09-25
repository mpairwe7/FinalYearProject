"""Tests for live call risk signals (Call Desk Phase 3)."""

from __future__ import annotations

import json
import time
import unittest
from unittest.mock import patch

from app import database as db
from app.receptionist import risk
from app.receptionist.hub import hub
from app.receptionist.store import create_call, create_turn, init_receptionist_schema


class TestReceptionistRisk(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_receptionist_schema()
        db.execute("DELETE FROM voice_calls")
        db.execute("DELETE FROM voice_call_turns")
        hub._lobby.clear()

    def test_calm_call_has_no_risk(self):
        create_call("call_calm_1", status="ai")
        create_turn("call_calm_1", seq=1, speaker="caller", kind="utterance", text="What is the VAT rate on salt?")
        create_turn("call_calm_1", seq=2, speaker="assistant", kind="answer", text="Salt is exempt from VAT.")

        res = risk.evaluate_call_risk("call_calm_1")
        self.assertEqual(res["level"], "none")
        self.assertEqual(res["signals"], [])

    def test_multiple_clarifications_triggers_watch(self):
        create_call("call_clarify_1", status="ai")
        create_turn("call_clarify_1", seq=1, speaker="assistant", kind="clarify", text="Did you say import?")
        create_turn("call_clarify_1", seq=2, speaker="assistant", kind="clarify", text="Did you say export?")

        res = risk.evaluate_call_risk("call_clarify_1")
        self.assertEqual(res["level"], "watch")
        self.assertIn("multiple_clarifications", res["signals"])

    def test_distress_triggers_at_risk(self):
        create_call("call_distress_1", status="ai")
        create_turn("call_distress_1", seq=1, speaker="caller", kind="utterance", text="I am very frustrated, this penalty is completely unfair!!")

        res = risk.evaluate_call_risk("call_distress_1")
        self.assertEqual(res["level"], "at_risk")
        self.assertIn("distress", res["signals"])

    def test_repeated_question_detected(self):
        create_call("call_repeat_1", status="ai")
        create_turn("call_repeat_1", seq=1, speaker="caller", kind="utterance", text="How do I get my TIN certificate?")
        create_turn("call_repeat_1", seq=2, speaker="assistant", kind="answer", text="Log into the e-services portal.")
        create_turn("call_repeat_1", seq=3, speaker="caller", kind="utterance", text="How do I get my TIN certificate online?")

        res = risk.evaluate_call_risk("call_repeat_1")
        self.assertIn("repeated_question", res["signals"])

    def test_risk_change_publishes_lobby_event(self):
        events = []
        with patch.object(hub, "publish_lobby", side_effect=lambda ev, data: events.append((ev, data))):
            create_call("call_event_risk", status="ai")
            create_turn("call_event_risk", seq=1, speaker="caller", kind="utterance", text="I am so worried my business is going to close!")

            risk.evaluate_call_risk("call_event_risk")
            self.assertGreaterEqual(len(events), 1)
            self.assertEqual(events[0][0], "call.risk")
            self.assertEqual(events[0][1]["call_id"], "call_event_risk")
            self.assertEqual(events[0][1]["level"], "at_risk")
