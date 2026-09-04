"""Unit and integration tests for rolling context management, multi-turn history,
entity extraction, and query rewriting."""

from __future__ import annotations

import unittest

from app.context_manager import (
    RollingContextManager,
    extract_conversation_entities,
    normalize_history_turns,
    summarize_older_turns,
)
from app.database import (
    get_conversation_context,
    init_db,
    log_conversation,
)
from app.llm import _build_messages
from app.query import rewrite_with_history


class NormalizeHistoryTurnsTests(unittest.TestCase):
    def test_empty_history(self) -> None:
        self.assertEqual(normalize_history_turns([]), [])
        self.assertEqual(normalize_history_turns(None), [])

    def test_standard_turn_dicts(self) -> None:
        raw = [
            {"user_message": "How do I pay PAYE?", "bot_reply": "You can pay via URA portal."},
            {"user_message": "What is the rate?", "bot_reply": "PAYE is graduated up to 40%."},
        ]
        norm = normalize_history_turns(raw)
        self.assertEqual(len(norm), 2)
        self.assertEqual(norm[0]["user_message"], "How do I pay PAYE?")
        self.assertEqual(norm[0]["bot_reply"], "You can pay via URA portal.")

    def test_flat_role_messages(self) -> None:
        raw = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hello! How can I help with URA taxes?"},
            {"role": "user", "content": "I need a TIN"},
            {"role": "assistant", "content": "You can register for a TIN online."},
        ]
        norm = normalize_history_turns(raw)
        self.assertEqual(len(norm), 2)
        self.assertEqual(norm[0]["user_message"], "Hello")
        self.assertEqual(norm[0]["bot_reply"], "Hello! How can I help with URA taxes?")
        self.assertEqual(norm[1]["user_message"], "I need a TIN")
        self.assertEqual(norm[1]["bot_reply"], "You can register for a TIN online.")

    def test_mixed_or_trailing_user_turn(self) -> None:
        raw = [
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Trailing question without answer"},
        ]
        norm = normalize_history_turns(raw)
        self.assertEqual(len(norm), 2)
        self.assertEqual(norm[1]["user_message"], "Trailing question without answer")
        self.assertEqual(norm[1]["bot_reply"], "")


class EntityExtractionTests(unittest.TestCase):
    def test_extract_tax_types_and_taxpayer_type(self) -> None:
        turns = [
            {"user_message": "I am a resident individual earning 2.5m monthly", "bot_reply": "Okay."},
            {"user_message": "How much PAYE and local service tax do I pay?", "bot_reply": "Let me calculate."},
        ]
        entities = extract_conversation_entities(turns)
        self.assertTrue(any("PAYE" in t for t in entities.tax_topics))
        self.assertTrue(any("Resident" in t for t in entities.taxpayer_types))
        self.assertTrue(any("Individual" in t for t in entities.taxpayer_types))
        self.assertTrue(len(entities.amounts) > 0 or any("2.5m" in a for a in entities.amounts))

    def test_extract_identifiers(self) -> None:
        turns = [
            {"user_message": "My TIN is 1000123456 and PRN is 224000123456", "bot_reply": "Checking PRN status."},
        ]
        entities = extract_conversation_entities(turns)
        self.assertIn("1000123456", entities.reference_numbers)
        self.assertIn("224000123456", entities.reference_numbers)


class RollingContextManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RollingContextManager(recent_limit=3, max_total_turns=10)

    def test_short_conversation_no_summary(self) -> None:
        turns = [
            {"user_message": "What is VAT?", "bot_reply": "Value Added Tax is 18%."},
            {"user_message": "When is it due?", "bot_reply": "Due by the 15th."},
        ]
        result = self.manager.build_context(turns)
        self.assertEqual(len(result.recent_turns), 2)
        self.assertEqual(result.context_summary, "")
        self.assertEqual(result.total_turns, 2)

    def test_long_conversation_generates_summary(self) -> None:
        turns = [
            {"user_message": "I run a company in Kampala with 100m revenue", "bot_reply": "Noted company in Kampala."},
            {"user_message": "What is corporate income tax rate?", "bot_reply": "CIT is 30% for resident companies."},
            {"user_message": "Do we need to pay provisional tax?", "bot_reply": "Yes, provisional returns are required."},
            {"user_message": "What about withholding tax on legal fees?", "bot_reply": "WHT is 6% on professional fees."},
            {"user_message": "And VAT on exports?", "bot_reply": "Exports are zero-rated (0%)."},
        ]
        result = self.manager.build_context(turns)
        # Recent limit is 3, so 3 recent turns and 2 summarized turns
        self.assertEqual(len(result.recent_turns), 3)
        self.assertTrue(len(result.context_summary) > 0)
        self.assertEqual(result.total_turns, 5)
        self.assertIn("Corporation", result.context_summary)

    def test_summarize_older_turns_function(self) -> None:
        turns = [
            {"user_message": "What is rental income tax?", "bot_reply": "Rental tax is 12% for individuals."},
            {"user_message": "Can I claim building maintenance expenses?", "bot_reply": "Individuals get a standard 75% deduction."},
        ]
        summary = summarize_older_turns(turns)
        self.assertIn("Rental", summary)
        self.assertIn("Tax domains", summary)


class MultiTurnQueryRewritingTests(unittest.TestCase):
    def test_rewrite_with_entity_from_older_turn(self) -> None:
        history = [
            {"user_message": "I want to know about withholding tax for non residents", "bot_reply": "Non resident WHT is 15% on management fees."},
            {"user_message": "How do I file the return?", "bot_reply": "You file via the e-services portal."},
        ]
        rewritten = rewrite_with_history("What is the penalty if it is late?", history)
        self.assertTrue(
            "withholding" in rewritten.lower() or "return" in rewritten.lower() or "penalty" in rewritten.lower()
        )
        self.assertIn("penalty", rewritten.lower())

    def test_rewrite_preserves_standalone_queries(self) -> None:
        history = [
            {"user_message": "Tell me about VAT rates", "bot_reply": "Standard rate is 18%."},
        ]
        query = "How do I register a new business for corporate tax in Uganda?"
        rewritten = rewrite_with_history(query, history)
        self.assertEqual(rewritten, query)

    def test_rewrite_elliptical_followup(self) -> None:
        history = [
            {"user_message": "What is PAYE threshold?", "bot_reply": "The threshold is UGX 235,000 per month."},
        ]
        rewritten = rewrite_with_history("And what about for non-residents?", history)
        self.assertTrue("paye" in rewritten.lower() or "non-resident" in rewritten.lower())

    def test_rewrite_preserves_demonstrative_determiners(self) -> None:
        history = [
            {"user_message": "Tell me about rental tax", "bot_reply": "Rental tax is charged on rental income."},
        ]
        q1 = "How much is it for this year?"
        rewritten1 = rewrite_with_history(q1, history)
        self.assertIn("this year", rewritten1)
        self.assertNotIn("Rental Income Tax year", rewritten1)
        self.assertIn("Rental", rewritten1)

        q2 = "How do I pay it if this is my first time?"
        rewritten2 = rewrite_with_history(q2, history)
        self.assertIn("this is my first time", rewritten2)
        self.assertNotIn("Rental Income Tax is my first time", rewritten2)

    def test_rewrite_possessive_pronouns(self) -> None:
        history = [
            {"user_message": "Tell me about rental tax", "bot_reply": "Rental tax is charged on rental income."},
        ]
        rewritten = rewrite_with_history("What is its deadline?", history)
        self.assertIn("'s", rewritten)
        self.assertIn("deadline", rewritten)


class DatabaseContextRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        init_db()

    def test_get_conversation_context(self) -> None:
        session_id = "test-session-ctx-123"
        conv_id = "conv-ctx-xyz"

        # Log 8 turns
        for i in range(1, 9):
            log_conversation(
                session_id=session_id,
                conversation_id=conv_id,
                user_message=f"Question {i} about VAT registration step {i}",
                bot_reply=f"Answer {i} regarding VAT registration",
            )

        context = get_conversation_context(
            session_id=session_id,
            conversation_id=conv_id,
            recent_limit=4,
            max_history=20,
        )

        self.assertGreaterEqual(context["total_turns"], 8)
        self.assertLessEqual(len(context["recent_turns"]), 4)
        self.assertTrue(len(context["context_summary"]) > 0)
        self.assertIn("VAT", context["context_summary"])


class LLMContextInjectionTests(unittest.TestCase):
    def test_build_messages_with_context_summary(self) -> None:
        summary = "Prior conversation summary (turns 1-5): Discussed VAT registration for Kampala retailer."
        history = [
            {"user_message": "What is the threshold?", "bot_reply": "UGX 150m annual turnover."},
        ]
        query = "Can I register voluntarily if below that?"

        messages = _build_messages(
            query=query,
            passages=[],
            conversation_history=history,
            context_summary=summary,
        )

        self.assertGreater(len(messages), 1)
        system_content = messages[0]["content"]
        self.assertIn("Prior conversation context", system_content)
        self.assertIn("Discussed VAT registration", system_content)
        # Verify history turn is present
        user_turns = [m for m in messages if m["role"] == "user"]
        self.assertEqual(len(user_turns), 2)  # 1 historical user + 1 current query
