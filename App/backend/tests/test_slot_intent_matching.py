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


class MisspellingTests(unittest.TestCase):
    """Answers typed quickly on a phone are still answers.

    Everything above assumes the words are spelled the way the option is.
    "individul" and "organistion" are not wrong answers; refusing them repeats
    the original failure with a different cause.
    """

    def test_common_typos_resolve(self):
        for reply, spec, expected in [
            ("individul", KIND, "individual"),
            ("indavidual", KIND, "individual"),
            ("organistion", KIND, "organisation"),
            ("organisatoin", KIND, "organisation"),
            ("compnay", ENTITY, "company"),
            ("corporaton tax", TAX, "corporation tax"),
            ("witholding tax", TAX, "withholding tax"),
        ]:
            with self.subTest(reply=reply):
                ok, value, _ = validate_slot(reply, spec)
                self.assertTrue(ok, f"{reply!r} should resolve to {expected!r}")
                self.assertEqual(value, expected)

    def test_a_dropped_letter_picks_the_nearer_option(self):
        """"mport" is import (0.91) not export (0.73) — the margin decides."""
        ok, value, _ = validate_slot("mport", TRADE)
        self.assertTrue(ok)
        self.assertEqual(value, "import")

    def test_equidistant_typo_is_refused(self):
        """"port" scores 0.80 against both import and export. Do not pick."""
        ok, _, error = validate_slot("port", TRADE)
        self.assertFalse(ok)
        self.assertIn("import", error)

    def test_near_identical_options_are_never_split_by_a_hair(self):
        ok, _, error = validate_slot("individua", "enum[individual,individuals]")
        self.assertFalse(ok)
        self.assertIn("Did you mean", error)

    def test_short_replies_do_not_drift_into_an_option(self):
        ok, _, _ = validate_slot("abc", KIND)
        self.assertFalse(ok)


class SemanticResolverTests(unittest.TestCase):
    """The layer the rules cannot reach — and the leash on it.

    Rules only cover phrasings someone anticipated. "sole trader", or an answer
    given in Luganda, needs meaning rather than string distance. That is what
    the resolver is for, and it is injected rather than imported so these tests
    need no model.

    The important property is not that it helps. It is that it cannot hurt: its
    answer is re-validated against the option list, so the model can restate a
    reply but never introduce a value.
    """

    def test_it_is_not_consulted_when_the_rules_already_decided(self):
        calls = []
        def resolver(reply, options):
            calls.append(reply)
            return "organisation"
        ok, value, _ = validate_slot("as an individual", KIND, resolver)
        self.assertTrue(ok)
        self.assertEqual(value, "individual")
        self.assertEqual(calls, [], "an inference call on the common path is waste")

    def test_it_places_a_reply_no_rule_covers(self):
        ok, value, _ = validate_slot("sole trader", KIND, lambda r, o: "individual")
        self.assertTrue(ok)
        self.assertEqual(value, "individual")

    def test_it_handles_another_language(self):
        """"nedda" is Luganda for no; no English word list will ever hold it."""
        ok, value, _ = validate_slot("nedda", "boolean", lambda r, o: "no")
        self.assertTrue(ok)
        self.assertIs(value, False)

    def test_the_models_answer_is_read_as_tolerantly_as_a_persons(self):
        """A phrased answer from the model counts, exactly as it would from a user.

        This path used to require `== "yes"`, so "No." was accepted from a person
        and rejected from the model. Qwen answers "unclear" for words it does not
        know — which must still fall through — but it should not be punished for
        a full stop.
        """
        for answer, expected in [("No.", False), ("the answer is no", False), ("Yes", True)]:
            with self.subTest(answer=answer):
                ok, value, _ = validate_slot("hmm", "boolean", lambda r, o, a=answer: a)
                self.assertTrue(ok, answer)
                self.assertIs(value, expected)

    def test_unclear_from_the_model_still_falls_through(self):
        """Observed live: Qwen3-8B answers "unclear" for Luganda "nedda"."""
        ok, _, error = validate_slot("nedda", "boolean", lambda r, o: "unclear")
        self.assertFalse(ok)
        self.assertIn("yes or no", error)

    def test_unclear_falls_through_to_asking_again(self):
        ok, _, error = validate_slot("hmm not sure", KIND, lambda r, o: "unclear")
        self.assertFalse(ok)
        self.assertIn("individual", error)

    def test_a_value_outside_the_options_is_rejected(self):
        """The model cannot introduce a taxpayer classification of its own."""
        ok, _, error = validate_slot("something", KIND, lambda r, o: "sole_proprietor")
        self.assertFalse(ok, "an off-list answer must not become a slot value")
        self.assertIn("Please choose one of", error)

    def test_a_chatty_answer_is_still_matched_by_the_rules(self):
        """It restates; code decides. A sentence containing the option is fine."""
        ok, value, _ = validate_slot("dunno", KIND, lambda r, o: "I think they mean individual")
        self.assertTrue(ok)
        self.assertEqual(value, "individual")

    def test_a_resolver_that_raises_does_not_break_the_flow(self):
        def boom(reply, options):
            raise RuntimeError("model down")
        ok, _, error = validate_slot("something", KIND, boom)
        self.assertFalse(ok)
        self.assertIn("Please choose one of", error)

    def test_no_resolver_behaves_exactly_as_before(self):
        self.assertEqual(validate_slot("sole trader", KIND), validate_slot("sole trader", KIND, None))


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
