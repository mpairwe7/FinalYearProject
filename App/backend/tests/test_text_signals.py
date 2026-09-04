from __future__ import annotations

import unittest

from app.text_signals import (
    ABSTENTION_REPLY,
    CLARIFICATION_PROMPT,
    CONTACT_FOOTER,
    ESCALATION_REPLY_LEAD,
    FAREWELL_REPLY,
    GRATITUDE_REPLY,
    GREETING_REPLY,
    GROUNDED_REVISION_PREAMBLE,
    NO_HITS_REPLY,
    content_tokens,
    detect_user_distress,
    empathy_ack,
    is_courtesy_sentence,
    split_sentences,
)


class CourtesySentenceTests(unittest.TestCase):
    def test_greetings_and_thanks_are_courtesy(self) -> None:
        for sentence in (
            "Hello, and welcome!",
            "Good morning",
            "Thank you for reaching out.",
            "Great question!",
            "You're welcome.",
            "I'm the URA Digital Assistant.",
        ):
            self.assertTrue(is_courtesy_sentence(sentence), sentence)

    def test_offers_of_help_and_followups_are_courtesy(self) -> None:
        for sentence in (
            "I can help with tax registration, filing returns, payments, customs and more — how can I help you today?",
            "Please don't hesitate to ask if anything is unclear.",
            "I hope this helps!",
            "You might also want to know about VAT registration thresholds.",
            "Is there anything else I can do for you?",
        ):
            self.assertTrue(is_courtesy_sentence(sentence), sentence)

    def test_contact_footer_with_hotlines_is_courtesy(self) -> None:
        footer = (
            "For help, contact URA at https://ura.go.ug, toll-free 0800 117 000 / "
            "0800 217 000, or WhatsApp 0772 140 000."
        )
        self.assertTrue(is_courtesy_sentence(footer))

    def test_meta_preambles_are_courtesy(self) -> None:
        for sentence in (
            "Based on the URA guidance I retrieved:",
            "Here's the most relevant guidance I found in official URA sources:",
            "I couldn't find a specific answer in the URA knowledge base.",
            "Could you share a little more detail about your question?",
        ):
            self.assertTrue(is_courtesy_sentence(sentence), sentence)

    def test_every_empathy_ack_is_courtesy(self) -> None:
        # Every opener is prefixed to a scored reply, so it must be
        # filtered out of faithfulness and claim verification — otherwise
        # adding a kind silently dilutes the grounding gate.
        for kind in ("frustration", "anxiety", "urgency", "hardship", "confusion"):
            ack = empathy_ack(kind)
            with self.subTest(kind=kind):
                self.assertTrue(ack)
                self.assertTrue(is_courtesy_sentence(ack), ack)

    def test_in_answer_constants_are_fully_courtesy(self) -> None:
        # These appear INSIDE grounded, scored replies — every sentence
        # (including URL-split fragments) must be filtered, or future copy
        # edits would silently re-dilute faithfulness.
        for constant in (CONTACT_FOOTER, GROUNDED_REVISION_PREAMBLE):
            for sentence in split_sentences(constant):
                self.assertTrue(is_courtesy_sentence(sentence), sentence)

    def test_standalone_reply_constants_open_courteously(self) -> None:
        for constant in (
            GREETING_REPLY,
            GRATITUDE_REPLY,
            FAREWELL_REPLY,
            CLARIFICATION_PROMPT,
            ABSTENTION_REPLY,
            NO_HITS_REPLY,
            ESCALATION_REPLY_LEAD,
        ):
            first = split_sentences(constant)[0]
            self.assertTrue(is_courtesy_sentence(first), first)

    def test_sentences_with_figures_are_never_courtesy(self) -> None:
        for sentence in (
            "Please contact URA within 30 days.",
            "The penalty is UGX 200,000.",
            "The standard VAT rate is 18 percent.",
            "Thank you for filing before 31 March.",
        ):
            self.assertFalse(is_courtesy_sentence(sentence), sentence)

    def test_factual_sentences_are_not_courtesy(self) -> None:
        for sentence in (
            "TIN registration can be completed through the URA online portal.",
            "Go to ura.go.ug and click Get a TIN.",
            "VAT returns are filed monthly through the EFRIS portal.",
        ):
            self.assertFalse(is_courtesy_sentence(sentence), sentence)


class DistressDetectorTests(unittest.TestCase):
    def test_calm_message_returns_empty(self) -> None:
        self.assertEqual(detect_user_distress("What is the VAT rate in Uganda?"), "")

    def test_frustration_detected(self) -> None:
        self.assertEqual(
            detect_user_distress("I'm so frustrated, registration still doesn't work"),
            "frustration",
        )

    def test_repeated_exclamations_read_as_frustration(self) -> None:
        self.assertEqual(detect_user_distress("Why is the portal down!!"), "frustration")

    def test_anxiety_outranks_urgency(self) -> None:
        self.assertEqual(
            detect_user_distress("I'm worried about the penalty deadline"),
            "anxiety",
        )

    def test_urgency_detected(self) -> None:
        self.assertEqual(
            detect_user_distress("The filing deadline is tomorrow, please advise"),
            "urgency",
        )

    def test_hardship_detected_and_outranks_frustration(self) -> None:
        # A frustrated message that also describes losing a business is
        # hardship: it needs options and a person, not a fixed process.
        self.assertEqual(
            detect_user_distress("This is useless, I'm going to lose my business"),
            "hardship",
        )
        self.assertEqual(detect_user_distress("I cannot afford to pay"), "hardship")

    def test_neutral_medical_queries_are_not_hardship(self) -> None:
        self.assertEqual(detect_user_distress("Are hospital expenses deductible?"), "")
        self.assertEqual(detect_user_distress("What is the tax treatment of sick leave pay?"), "")
        self.assertEqual(detect_user_distress("Tax exemptions for medical equipment supplies"), "")

    def test_genuine_medical_hardship_detected(self) -> None:
        self.assertEqual(detect_user_distress("I am sick and cannot afford my taxes"), "hardship")
        self.assertEqual(detect_user_distress("My child is sick and I have no money to pay"), "hardship")

    def test_comprehension_trouble_is_confusion_not_anxiety(self) -> None:
        self.assertEqual(
            detect_user_distress("I don't understand what chargeable income means"),
            "confusion",
        )

    def test_genuine_worry_still_reads_as_anxiety(self) -> None:
        self.assertEqual(
            detect_user_distress("I'm worried and confused about my return"),
            "anxiety",
        )

    def test_unknown_ack_kind_is_empty(self) -> None:
        self.assertEqual(empathy_ack(""), "")
        self.assertEqual(empathy_ack("calm"), "")


class TokenHelperTests(unittest.TestCase):
    def test_split_sentences_matches_scorer_rule(self) -> None:
        self.assertEqual(
            split_sentences("First sentence here. Tiny. Second one follows!"),
            ["First sentence here", "Second one follows"],
        )

    def test_content_tokens_drop_stopwords_keep_figures(self) -> None:
        tokens = content_tokens("The penalty is UGX 200,000 for late filing")
        self.assertNotIn("the", tokens)
        self.assertNotIn("is", tokens)
        self.assertIn("ugx", tokens)
        self.assertIn("200", tokens)
        self.assertIn("penalty", tokens)


if __name__ == "__main__":
    unittest.main()
