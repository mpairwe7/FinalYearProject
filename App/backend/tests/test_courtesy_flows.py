"""Closing-courtesy flow: gratitude and farewell turns end naturally.

"thank you!" used to fall through to keyword retrieval and answer with FAQ
guidance — an unnatural close. These tests pin the always-on short-circuit
on both the REST and streaming paths, and that mixed messages ("thanks for
nothing!!", "thanks, but it still fails") still reach retrieval + distress
handling instead of a sign-off.
"""

from __future__ import annotations

import unittest
import uuid
from unittest import mock

from app.text_signals import (
    ABSTENTION_REPLY,
    CLARIFICATION_PROMPT,
    CONTACT_FOOTER,
    ESCALATION_REPLY_FOOTER,
    ESCALATION_REPLY_LEAD,
    FAREWELL_REPLY,
    GRATITUDE_REPLY,
    GRATITUDE_REPLY_BY_LOCALE,
    GREETING_REPLY,
    GREETING_REPLY_BY_LOCALE,
    LOCALIZED_CONTACT_FOOTERS,
)
from app.tools.education import LEVELS, explain, format_education_reply


class ClosingCourtesyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.db import database as db

        db.init_db()
        from app.rag import pipeline

        cls.model = pipeline.ChatModel()

    def test_gratitude_short_circuits_rest(self) -> None:
        for msg in ("thank you!", "Thanks!", "thanks so much", "webale", "asante"):
            with self.subTest(msg=msg):
                out = self.model.generate(message=msg)
                self.assertEqual(out["reply"], GRATITUDE_REPLY, msg)
                self.assertEqual(out["retrieval_mode"], "greeting")
                self.assertIsNone(out["faithfulness_score"])
                self.assertTrue(out["next_actions"])

    def test_farewell_short_circuits_rest(self) -> None:
        for msg in ("goodbye", "bye!", "thanks bye"):
            with self.subTest(msg=msg):
                out = self.model.generate(message=msg)
                self.assertEqual(out["reply"], FAREWELL_REPLY, msg)
                self.assertEqual(out["retrieval_mode"], "greeting")

    def test_streaming_parity(self) -> None:
        out = self.model.generate_retrieval_only(message="thank you so much!")
        self.assertEqual(out["reply"], GRATITUDE_REPLY)
        self.assertEqual(out["retrieval_mode"], "greeting")
        self.assertEqual(out["_hits"], [])

    def test_mixed_messages_fall_through(self) -> None:
        for msg in (
            "thanks for nothing!!",
            "thanks, but the portal still fails",
            "thank you, and how do I file a VAT return?",
        ):
            with self.subTest(msg=msg):
                out = self.model.generate(message=msg)
                self.assertNotEqual(out["reply"], GRATITUDE_REPLY, msg)
                self.assertNotEqual(out["reply"], FAREWELL_REPLY, msg)


#: G191's closed list of declines, typed the way a taxpayer might.
DECLINES = (
    "No", "no.", "No thanks", "no, thank you!", "Nope", "nothing", "Nothing else.",
    "That's all", "that is all", "I’m good", "All good!",
)
#: Each other locale's bare "no", after that locale's own offers.
LOCALE_DECLINES = {"lg": ("Nedda",), "sw": ("hapana",), "nyn": ("ngaaha", "Apana."), "ach": ("pe",)}

#: One turn of each kind whose reply ends on an offer of more help.
OPEN_OFFERS = {
    "greeting": ("Hello", GREETING_REPLY),
    "gratitude": ("thank you", GRATITUDE_REPLY),
    "contact footer": (
        "What is the VAT rate?",
        f"VAT is charged at 18% on taxable supplies.\n\n{CONTACT_FOOTER}",
    ),
    "anything else": (
        "When is the VAT return due?",
        "The VAT return is due by the 15th of the following month. "
        "Is there anything else I can help you with?",
    ),
    "how can I help": (
        "Where is my TIN?",
        "Your TIN is on the certificate URA emailed you. How can I help you further?",
    ),
    # G191's own wording, also the receptionist's "offer_declined" phrase.
    "what else can I help": (
        "How do I pay?",
        "You pay with a PRN at any URA-designated bank. What else can I help you with?",
    ),
}


class DeclineAfterOfferTests(unittest.TestCase):
    """G191: a bare decline after an offer of more help ends the conversation.

    Only then. A "No" that answers any other question, or that carries one of
    its own, reaches whatever handled it before.
    """

    @classmethod
    def setUpClass(cls):
        from app.db import database as db

        db.init_db()
        from app.rag import pipeline

        cls.db = db
        cls.model = pipeline.ChatModel()
        # Localizing a reply is a model call; the suite never makes one.
        cls._no_translation = mock.patch.object(
            pipeline, "localize_reply", side_effect=lambda reply, locale, **_: reply
        )
        cls._no_translation.start()
        cls.paths = {"rest": cls.model.generate, "stream": cls.model.generate_retrieval_only}

    @classmethod
    def tearDownClass(cls):
        cls._no_translation.stop()

    def converse(self, path, turns):
        """Each message's answer on one conversation, recorded as the API records it."""
        thread, session = str(uuid.uuid4()), str(uuid.uuid4())
        answers = []
        for message in turns:
            answers.append(path(message=message, conversation_id=thread, session_id=session))
            self.db.log_conversation(
                session, thread, message, answers[-1]["reply"], locale=answers[-1]["locale"]
            )
        return answers

    def reply_after(self, path, asked, previous_reply, message, locale="en"):
        """The answer to *message* after *asked* was answered by *previous_reply* in *locale*."""
        thread, session = str(uuid.uuid4()), str(uuid.uuid4())
        self.db.log_conversation(session, thread, asked, previous_reply, locale=locale)
        return path(message=message, conversation_id=thread, session_id=session)

    def assertFarewell(self, out):
        self.assertEqual(out["reply"], FAREWELL_REPLY)
        self.assertEqual(out["retrieval_mode"], "greeting")
        self.assertEqual(out["sources"], [])

    def test_live_cases_end_the_conversation(self) -> None:
        # Both went to retrieval on the Space (image sha-6566907, 2026-11-21).
        for opening, answered_by in (("What is the VAT rate in Uganda?", "calculator"), ("Hello", "greeting")):
            for name, path in self.paths.items():
                with self.subTest(opening=opening, path=name):
                    opened, declined = self.converse(path, (opening, "No"))
                    self.assertEqual(opened["retrieval_mode"], answered_by)
                    self.assertFarewell(declined)

    def test_each_decline_after_each_open_offer(self) -> None:
        for kind, (asked, offer) in OPEN_OFFERS.items():
            for decline in DECLINES:
                for name, path in self.paths.items():
                    with self.subTest(offer=kind, decline=decline, path=name):
                        self.assertFarewell(self.reply_after(path, asked, offer, decline))

    def test_each_locale_decline_after_that_locale_offers(self) -> None:
        for locale, declines in LOCALE_DECLINES.items():
            offers = {
                "greeting": GREETING_REPLY_BY_LOCALE[locale],
                "gratitude": GRATITUDE_REPLY_BY_LOCALE[locale],
                "contact footer": LOCALIZED_CONTACT_FOOTERS[locale],
            }
            for decline in declines:
                for kind, offer in offers.items():
                    for name, path in self.paths.items():
                        with self.subTest(decline=decline, offer=kind, path=name):
                            out = self.reply_after(path, "", offer, decline, locale=locale)
                            self.assertEqual(out["locale"], locale)
                            self.assertFarewell(out)

    def test_another_languages_decline_is_unchanged_in_an_english_turn(self) -> None:
        # "PE" is also a permanent establishment. The language detector may read "ngaaha" as
        # Luganda and "apana" as Swahili (it does on CI, not on every machine), and then the
        # farewell would come back translated, so the check is the branch, not the text.
        for decline in ("PE", "pe", "ngaaha", "apana"):
            for name, path in self.paths.items():
                with self.subTest(decline=decline, path=name):
                    out = self.reply_after(path, "Hello", GREETING_REPLY, decline)
                    self.assertNotEqual(out["retrieval_mode"], "greeting")
                    self.assertNotEqual(out["reply"], FAREWELL_REPLY)

    def test_a_decline_asked_as_a_question_is_unchanged(self) -> None:
        cases = (("No?", "en"), ("Nothing?", "en"), ("that's all?", "en"), ("No?!", "en"), ("pe?", "ach"))
        for message, locale in cases:
            offer = GREETING_REPLY_BY_LOCALE[locale]
            for name, path in self.paths.items():
                with self.subTest(message=message, path=name):
                    out = self.reply_after(path, "Hello", offer, message, locale=locale)
                    self.assertNotEqual(out["reply"], FAREWELL_REPLY)

    def test_no_after_every_lesson_answers_its_knowledge_check(self) -> None:
        # A lesson ends on a question for the taxpayer, then the contact footer
        # (_maybe_handle_education); "No" may be the right answer.
        for topic in explain("")["available_topics"]:
            for level in LEVELS:
                for reveal in (False, True):
                    lesson = format_education_reply(explain(topic, level=level, reveal_answer=reveal), reveal)
                    for name, path in self.paths.items():
                        with self.subTest(topic=topic, level=level, reveal=reveal, path=name):
                            out = self.reply_after(path, topic, f"{lesson}\n\n{CONTACT_FOOTER}", "No")
                            self.assertNotEqual(out["reply"], FAREWELL_REPLY)

    def test_no_after_a_lesson_from_the_pipeline_is_unchanged(self) -> None:
        for question in ("What is EFRIS?", "What is VAT registration?"):
            for name, path in self.paths.items():
                with self.subTest(question=question, path=name):
                    lesson, answered = self.converse(path, (question, "No"))
                    self.assertEqual(lesson["retrieval_mode"], "education")
                    self.assertIn("Knowledge Check", lesson["reply"])
                    self.assertNotEqual(answered["reply"], FAREWELL_REPLY)

    def test_no_that_answers_another_question_is_unchanged(self) -> None:
        questions = {
            "yes/no: resident": "Are you a resident?",
            "yes/no: TIN": "Do you have a TIN?",
            "yes/no: vehicle": "Is the vehicle used?",
            "clarification": CLARIFICATION_PROMPT,
            "officer offer": "Would you like me to connect you to an officer?",
            "escalation": f"{ESCALATION_REPLY_LEAD}{ESCALATION_REPLY_FOOTER}",
            "abstention": ABSTENTION_REPLY,
            "answer with no offer": "VAT is charged at 18% on taxable supplies.",
            "follow-up suggestion": (
                f"VAT is charged at 18%.\n\n{CONTACT_FOOTER}\n\n"
                "You might also want to know: Who must register for VAT?"
            ),
        }
        for kind, previous in questions.items():
            for name, path in self.paths.items():
                with self.subTest(previous=kind, path=name):
                    out = self.reply_after(path, "What about VAT?", previous, "No")
                    self.assertNotEqual(out["reply"], FAREWELL_REPLY)

    def test_no_as_the_first_message_is_unchanged(self) -> None:
        for name, path in self.paths.items():
            with self.subTest(path=name):
                out = path(message="No", conversation_id=str(uuid.uuid4()), session_id=str(uuid.uuid4()))
                self.assertNotEqual(out["reply"], FAREWELL_REPLY)

    def test_a_decline_carrying_a_question_is_unchanged(self) -> None:
        for message in ("No, what about PAYE?", "no I need the VAT rate"):
            for name, path in self.paths.items():
                with self.subTest(message=message, path=name):
                    out = self.reply_after(path, "Hello", GREETING_REPLY, message)
                    self.assertNotEqual(out["reply"], FAREWELL_REPLY)

    def test_an_open_workflow_still_gets_its_no(self) -> None:
        for name, path in self.paths.items():
            with self.subTest(path=name):
                _, asked, answered = self.converse(
                    path, ("Guide me through registering my imported car", "first registration", "No")
                )
                self.assertIn("Do you have a TIN in your own name?", asked["reply"])
                self.assertIn("Get your TIN before anything else", answered["reply"])

    def test_a_pending_clarification_still_gets_its_no(self) -> None:
        for name, path in self.paths.items():
            with self.subTest(path=name):
                asked, answered = self.converse(path, ("How do I register for a TIN?", "No"))
                self.assertEqual(asked["workflow"]["pending_slot"], "taxpayer_kind")
                self.assertEqual(answered["workflow"]["pending_slot"], "taxpayer_kind")
                self.assertIn("Please choose one of: individual, organisation", answered["reply"])


if __name__ == "__main__":
    unittest.main()
