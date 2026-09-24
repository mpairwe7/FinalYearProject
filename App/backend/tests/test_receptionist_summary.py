"""Unit tests for call summary parsing and fallback generation."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from app.receptionist import summary as summary_mod
from app.receptionist.summary import _call_languages, _fallback_summary, _parse_summary_json


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


class TestSummaryLanguages(unittest.TestCase):
    """Officers read English summaries; Luganda calls are summarised by Sunflower first."""

    def test_languages_come_from_the_call_metrics(self):
        call = {"locale": "lg", "metrics": {"language": {"final": "lg", "used": ["en", "lg"]}}}
        self.assertEqual(_call_languages(call, []), ("lg", ["en", "lg"]))

    def test_a_mostly_luganda_transcript_is_a_luganda_call(self):
        turns = [{"speaker": "caller", "text": "Nsaba okumanya ku omusolo gwa TIN yange"}]
        self.assertEqual(_call_languages({"locale": "en"}, turns), ("lg", ["en", "lg"]))

    def test_english_stays_english(self):
        turns = [{"speaker": "caller", "text": "How do I register for a TIN?"}]
        self.assertEqual(_call_languages({"locale": "en"}, turns), ("en", ["en"]))

    def _summarise(self, call, turns, local, gemini):
        with patch.object(summary_mod, "get_call", return_value=call), \
                patch.object(summary_mod, "list_turns", return_value=turns), \
                patch.object(summary_mod, "update_call"), \
                patch.object(summary_mod, "_summarise_local", side_effect=local) as loc, \
                patch.object(summary_mod, "_summarise_gemini", side_effect=gemini) as gem:
            result = summary_mod.generate_call_summary("c1")
        return result, loc, gem

    def test_luganda_calls_try_sunflower_first(self):
        parsed = {"subject": "TIN", "summary": "Caller asked about TIN registration."}
        call = {"locale": "lg", "metrics": {"language": {"final": "lg", "used": ["en", "lg"]}}}
        turns = [{"speaker": "caller", "text": "Nsaba okumanya ku TIN"}]
        result, loc, gem = self._summarise(call, turns, [dict(parsed)], [None])
        loc.assert_called_once()
        gem.assert_not_called()
        self.assertEqual((result["language"], result["languages_used"]), ("lg", ["en", "lg"]))
        self.assertIn("Luganda", loc.call_args.args[0])

    def test_english_calls_try_gemini_first(self):
        parsed = {"subject": "TIN", "summary": "Caller asked about TIN registration."}
        turns = [{"speaker": "caller", "text": "How do I register for a TIN?"}]
        result, loc, gem = self._summarise({"locale": "en"}, turns, [None], [dict(parsed)])
        gem.assert_called_once()
        loc.assert_not_called()
        self.assertEqual(result["language"], "en")

    def test_the_prompt_demands_english(self):
        self.assertIn("write every field in English", summary_mod.SUMMARY_SYSTEM)

