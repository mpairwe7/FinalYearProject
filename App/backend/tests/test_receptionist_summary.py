"""Unit tests for call summary parsing and fallback generation."""

from __future__ import annotations

import unittest

from app.receptionist.summary import _fallback_summary, _parse_summary_json


class TestReceptionistSummary(unittest.TestCase):
    def test_json_parse_clean(self):
        raw = """{
            "subject": "TIN Application",
            "summary": "Taxpayer asked how to register for a personal TIN.",
            "caller_intent": "Register for TIN",
            "resolution": "answered",
            "key_facts": ["Instant TIN available online"],
            "follow_ups": ["Visit URA portal"],
            "sentiment": "positive",
            "ai_handling_notes": "Single turn resolution."
        }"""
        res = _parse_summary_json(raw)
        self.assertIsNotNone(res)
        self.assertEqual(res["subject"], "TIN Application")
        self.assertEqual(res["resolution"], "answered")
        self.assertEqual(len(res["key_facts"]), 1)

    def test_json_parse_with_markdown_fences(self):
        raw = """```json
        {
            "subject": "VAT Filing",
            "summary": "Caller inquired about monthly VAT return due date.",
            "caller_intent": "Filing deadline",
            "resolution": "answered",
            "key_facts": ["15th of the following month"],
            "follow_ups": [],
            "sentiment": "neutral",
            "ai_handling_notes": ""
        }
        ```"""
        res = _parse_summary_json(raw)
        self.assertIsNotNone(res)
        self.assertEqual(res["subject"], "VAT Filing")
        self.assertEqual(res["resolution"], "answered")

    def test_fallback_summary(self):
        turns = [
            {"speaker": "caller", "text": "How do I register for a business TIN?"},
            {"speaker": "assistant", "kind": "answer", "text": "You need URSB registration."},
        ]
        call = {
            "status": "ended",
            "transferred": 0,
            "end_reason": "caller_hangup",
        }
        res = _fallback_summary(turns, call)
        self.assertIsNotNone(res)
        self.assertIn("register for a business TIN", res["summary"])
        self.assertEqual(res["resolution"], "answered")

    def test_fallback_summary_transferred(self):
        turns = [
            {"speaker": "caller", "text": "I want to dispute my customs tax"},
        ]
        call = {
            "status": "transferring",
            "transferred": 1,
            "transfer_reason": "caller_requested",
            "end_reason": "transfer",
        }
        res = _fallback_summary(turns, call)
        self.assertEqual(res["resolution"], "transferred")
