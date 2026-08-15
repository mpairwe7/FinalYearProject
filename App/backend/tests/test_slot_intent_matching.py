"""Slot answers are read for intent, not compared as strings.

From a real conversation, where the flow asked "Are you registering as an
**individual** or an **organisation** (company, NGO, partnership)?":

    user:      as an individual
    assistant: Please choose one of: individual, organisation
    user:      organization
    assistant: Please choose one of: individual, organisation

Both answers were correct. `_validate_enum` compared the whole reply to each
option with `==`, so an answer only counted if the person typed the option and
nothing else — no sentence framing, and British spelling only. The flow then
repeated the identical question, which reads as the assistant not listening,
and there was no way out of the loop short of guessing the exact token.

The matcher widens in steps and stops at the first that identifies exactly one
option. What it must NOT do is resolve ambiguity by picking: these answers set a
taxpayer classification that the rest of the flow depends on, so a reply that
genuinely fits two options has to ask again.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.workflows.slots import validate_slot  # noqa: E402

KIND = "enum[individual,organisation]"
ENTITY = "enum[individual,company,ngo]"
TAX = "enum[vat,paye,corporation tax,withholding tax,customs,other]"
PAY = "enum[bank,mobile money,online card,other]"
TRADE = "enum[import,export]"
WHT = "enum[services,goods,management_fees,dividend]"


class ReportedConversationTests(unittest.TestCase):
    """The two replies that were refused, verbatim."""

    def test_sentence_framing_is_understood(self):
        ok, value, _ = validate_slot("as an individual", KIND)
        self.assertTrue(ok, "'as an individual' is an answer, not a non-answer")
        self.assertEqual(value, "individual")

    def test_us_spelling_is_the_same_word(self):
        ok, value, _ = validate_slot("organization", KIND)
        self.assertTrue(ok, "refusing a US spelling is refusing the right answer")
        self.assertEqual(value, "organisation")


class PhrasingTests(unittest.TestCase):
    def test_answers_inside_a_sentence(self):
        for reply, expected in [
            ("I'm registering a company", "company"),
            ("an NGO", "ngo"),
            ("just an individual", "individual"),
            ("I want to export", "export"),
        ]:
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, ENTITY if expected != "export" else TRADE)
                self.assertTrue(ok, reply)
                self.assertEqual(value, expected)

    def test_case_and_separators_do_not_matter(self):
        for reply, spec, expected in [
            ("PAYE", TAX, "paye"),
            ("Vat", TAX, "vat"),
            ("management_fees", WHT, "management_fees"),
            ("management fees", WHT, "management_fees"),
        ]:
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, spec)
                self.assertTrue(ok, reply)
                self.assertEqual(value, expected)

    def test_a_distinctive_prefix_is_enough(self):
        """Nobody types "withholding tax" when "withholding" is unambiguous."""
        for reply, expected in [("corporation", "corporation tax"), ("withholding", "withholding tax")]:
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, TAX)
                self.assertTrue(ok, reply)
                self.assertEqual(value, expected)

    def test_local_payment_vocabulary(self):
        for reply in ("momo", "MTN mobile money", "mobile money"):
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, PAY)
                self.assertTrue(ok, reply)
                self.assertEqual(value, "mobile money")

    def test_the_longer_option_wins_over_a_substring_of_it(self):
        """"corporation tax" contains no other option, but must beat a bare match."""
        ok, value, _ = validate_slot("I pay corporation tax", TAX)
        self.assertTrue(ok)
        self.assertEqual(value, "corporation tax")


class AmbiguityTests(unittest.TestCase):
    """Guessing here writes a taxpayer classification. It has to ask instead."""

    def test_a_reply_naming_both_options_is_refused(self):
        ok, _, error = validate_slot("individual or organisation", KIND)
        self.assertFalse(ok)
        self.assertIn("individual", error)
        self.assertIn("organisation", error)

    def test_an_unrelated_reply_lists_the_options(self):
        ok, _, error = validate_slot("something else entirely", KIND)
        self.assertFalse(ok)
        self.assertIn("individual", error)

    def test_an_empty_reply_is_refused(self):
        ok, _, error = validate_slot("   ", KIND)
        self.assertFalse(ok)
        self.assertTrue(error)


class BooleanTests(unittest.TestCase):
    def test_natural_affirmatives(self):
        for reply in ("yes", "yeah", "yep", "sure", "ok", "yes please", "that's right"):
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, "boolean")
                self.assertTrue(ok, reply)
                self.assertIs(value, True)

    def test_natural_negatives(self):
        for reply in ("no", "nope", "nah", "no thanks", "cancel"):
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, "boolean")
                self.assertTrue(ok, reply)
                self.assertIs(value, False)

    def test_both_at_once_asks_again(self):
        ok, _, error = validate_slot("yes and no", "boolean")
        self.assertFalse(ok)
        self.assertIn("yes or a no", error)


class RegressionGuards(unittest.TestCase):
    """The widening must not have loosened what was already correct."""

    def test_exact_options_still_match_themselves(self):
        for spec in (KIND, ENTITY, TAX, PAY, TRADE, WHT):
            options = spec[len("enum[") : -1].split(",")
            for opt in options:
                with self.subTest(spec=spec, option=opt):
                    ok, value, _ = validate_slot(opt, spec)
                    self.assertTrue(ok, opt)
                    self.assertEqual(value, opt)

    def test_other_validators_are_untouched(self):
        ok, value, _ = validate_slot("1.5m", "number")
        self.assertTrue(ok)
        self.assertEqual(value, 1_500_000)
        ok, _, _ = validate_slot("123456789", r"regex[^\d{9}$]")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
