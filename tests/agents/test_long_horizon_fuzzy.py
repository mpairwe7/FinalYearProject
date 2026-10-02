"""Tests for long-horizon context awareness, fuzzy robustness, and anti-hallucination.

Verifies:
1. Multi-turn entity and rolling context memory across extended sessions (>8 turns).
2. Typographical slip corrections on domain vocabulary (fuzzy robustness).
3. Intra-sentential subject preservation during query rewriting (does not misbind pronouns).
4. Official URA emails (@ura.go.ug) are preserved, while private emails are redacted.
5. User scenario figures (e.g. 80m turnover) do not trigger false contradiction withholding.
"""

from __future__ import annotations

import unittest

from app.claim_verifier import verify_claims
from app.context_manager import (
    RollingContextManager,
    extract_conversation_entities,
)
from app.entailment import is_contradicted, numeric_contradiction
from app.guardrails import OutputGuard, redact_pii_text
from app.premise_guard import check_false_premise
from app.query import correct_spelling, rewrite_with_history


class TestLongHorizonContextAwareness(unittest.TestCase):
    def test_ten_turn_rolling_context_and_summary(self) -> None:
        """Verify that an extended 10-turn conversation accumulates and summarizes history."""
        manager = RollingContextManager(recent_limit=4, max_total_turns=15)
        turns = [
            {"user_message": "I am an individual taxpayer in Kampala.", "bot_reply": "Welcome! How can I assist you with taxes?"},
            {"user_message": "I want to apply for a TIN.", "bot_reply": "You can get an instant TIN online at ura.go.ug."},
            {"user_message": "What documents do I need?", "bot_reply": "You need a valid National ID or Passport."},
            {"user_message": "My monthly salary is 3.5m UGX.", "bot_reply": "Let me calculate your PAYE tax on 3,500,000."},
            {"user_message": "What is the threshold for PAYE?", "bot_reply": "The first 235,000 UGX monthly is exempt."},
            {"user_message": "What about for non-residents?", "bot_reply": "Non-residents start paying from the first shilling."},
            {"user_message": "I also run a retail shop with 80m turnover.", "bot_reply": "Turnover below 150m makes VAT voluntary."},
            {"user_message": "What is EFRIS?", "bot_reply": "EFRIS is the electronic fiscal receipting system."},
            {"user_message": "Can I apply for TIN on WhatsApp?", "bot_reply": "No, TIN registration must be done on the portal."},
            {"user_message": "What is the URA helpline?", "bot_reply": "Toll-free 0800 117 000 or email services@ura.go.ug."},
        ]
        ctx = manager.build_context(turns)
        self.assertEqual(len(ctx.recent_turns), 4)
        self.assertEqual(ctx.total_turns, 10)
        self.assertTrue(len(ctx.context_summary) > 0)
        self.assertIn("PAYE", ctx.context_summary)
        self.assertIn("TIN", ctx.context_summary)

        # Entity extraction covers domains across all 10 turns
        entities = extract_conversation_entities(turns)
        self.assertTrue(any("PAYE" in t for t in entities.tax_topics))
        self.assertTrue(any("TIN" in t for t in entities.tax_topics))
        self.assertTrue(any("Individual" in s for s in entities.taxpayer_types))


class TestFuzzySpellingRobustness(unittest.TestCase):
    def test_domain_typo_corrections(self) -> None:
        """Verify common typing noise and letter transpositions are corrected."""
        test_cases = [
            ("what docuemnts do i need?", "what documents do i need?"),
            ("what is dat online aplication?", "what is that online application?"),
            ("what abt if i was non residetn?", "what about if i was non resident?"),
            ("if my crago goods are assessed hihger", "if my cargo goods are assessed higher"),
            ("how can i dispuet the assessment?", "how can i dispute the assessment?"),
            ("is vat compulsary or voluntery?", "is vat compulsory or voluntary?"),
        ]
        for noisy, expected in test_cases:
            with self.subTest(noisy=noisy):
                self.assertEqual(correct_spelling(noisy).lower(), expected.lower())


class TestCoreferenceResolution(unittest.TestCase):
    def test_query_with_own_subject_does_not_override_pronoun(self) -> None:
        """'What is EFRIS and would I be required to use it?' must bind 'it' to EFRIS, not previous turn's VAT."""
        history = [
            {"user_message": "Is VAT compulsory for my 80m shop?", "bot_reply": "No, VAT is voluntary below 150m."}
        ]
        q = "What is EFRIS and would I be required to use it?"
        rewritten = rewrite_with_history(q, history)
        # Must retain EFRIS and not mutate into "use Value Added Tax (VAT)"
        self.assertIn("efris", rewritten.lower())
        self.assertNotIn("use value added tax", rewritten.lower())

    def test_dependent_followup_attaches_active_subject(self) -> None:
        """A dependent followup like 'what are the requirements' attaches the active topic."""
        history = [
            {"user_message": "I need to get a TIN for my business", "bot_reply": "TIN registration is done online."}
        ]
        rewritten = rewrite_with_history("what documents do i need for that online application?", history)
        self.assertTrue("tin" in rewritten.lower() or "application" in rewritten.lower())


class TestOfficialEmailPrivacyIntegrity(unittest.TestCase):
    def test_official_ura_emails_preserved(self) -> None:
        raw = "Contact services@ura.go.ug or info@ura.go.ug. Taxpayer email is private.person@gmail.com."
        redacted = redact_pii_text(raw)
        self.assertIn("services@ura.go.ug", redacted)
        self.assertIn("info@ura.go.ug", redacted)
        self.assertNotIn("private.person@gmail.com", redacted)
        self.assertIn("[REDACTED_EMAIL]", redacted)

    def test_sanitize_restores_redacted_ura_email(self) -> None:
        raw = "Telephone: 0417-443-150 Email:[REDACTED_EMAIL]; [REDACTED_EMAIL] | https://ura.go.ug"
        sanitized = OutputGuard.sanitize(raw)
        self.assertIn("services@ura.go.ug", sanitized)
        self.assertNotIn("[REDACTED_EMAIL]", sanitized)


class TestNumericContradictionWithholding(unittest.TestCase):
    def test_user_scenario_figures_do_not_trigger_contradiction(self) -> None:
        """User asking about hypothetical 80m turnover against 150m statutory threshold is not contradicted."""
        query = "If I also open a side shop with annual turnover of 80m, is VAT compulsory for me?"
        claim = "With an annual turnover of 80m, VAT registration is not compulsory because the threshold is 150m."
        context = "The threshold for VAT registration is an annual turnover of over 150 million, or 37.5 million in three consecutive months."

        self.assertFalse(numeric_contradiction(claim, context, user_query=query))
        self.assertFalse(is_contradicted(claim, [context], user_query=query))

    def test_model_hallucinated_threshold_is_contradicted(self) -> None:
        """If model hallucinates a threshold not mentioned by user or passage, contradiction triggers."""
        query = "What is the VAT threshold?"
        claim = "The compulsory VAT registration threshold is 200m."
        context = "The threshold for VAT registration is an annual turnover of over 150 million."

        self.assertTrue(numeric_contradiction(claim, context, user_query=query))
        self.assertTrue(is_contradicted(claim, [context], user_query=query))

    def test_action_verbs_do_not_trigger_false_premise_guard(self) -> None:
        """Legitimate phrases like 'use Value Added Tax' do not trigger epistemic false-premise rejection."""
        query = "What is EFRIS and would I be required to use Value Added Tax?"
        res = check_false_premise(query, hits=[])
        self.assertFalse(res.is_false_premise)


if __name__ == "__main__":
    unittest.main()
