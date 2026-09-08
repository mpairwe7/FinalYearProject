"""Regression tests for domain spelling correction (backend/app/query.py).

The corrector rewrote correctly-spelled words. ``_CORRECTIONS`` maps
``"withholdin" -> "withholding"`` and the substitution was a bare
``re.sub(re.escape(wrong), right, ...)`` with no word boundary, so the substring
inside the *correct* word "withholding" was replaced and left a stray letter:

    "What is withholding tax?"  ->  "What is withholdingg tax?"

That drops "withholding" from the BM25 query entirely. Measured against the live
corpus, a direct query ranks Withholding-Tax-FY-2024-25-1.pdf first with full
term coverage; through the rewrite, the service instead retrieved VAT and EFRIS
documents and answered a withholding-tax question with "VAT is collected at
different stages in the production and distribution of a good or service."

Any correction key that is a prefix of its own replacement has this shape, so the
tests below pin the invariant rather than just the one word.
"""

from __future__ import annotations

import unittest

from app.query import _CORRECTIONS, correct_spelling, rewrite


class SpellingCorrectionBoundaryTests(unittest.TestCase):
    def test_the_reported_case_is_left_alone(self) -> None:
        self.assertEqual(correct_spelling("What is withholding tax?"), "What is withholding tax?")
        self.assertEqual(rewrite("What is withholding tax?"), "What is withholding tax?")

    def test_no_correction_key_corrupts_its_own_replacement(self) -> None:
        """The general invariant: applying the corrector to an already-correct
        term must be a no-op, for every entry in the table."""
        for wrong, right in _CORRECTIONS.items():
            self.assertEqual(
                correct_spelling(right).lower(),
                right.lower(),
                f"{wrong!r} -> {right!r} corrupts the correct spelling",
            )

    def test_every_misspelling_is_still_corrected(self) -> None:
        """Anchoring must not cost the corrector its job."""
        for wrong, right in _CORRECTIONS.items():
            self.assertEqual(correct_spelling(wrong).lower(), right.lower(), wrong)

    def test_corrections_apply_inside_a_sentence(self) -> None:
        self.assertEqual(
            correct_spelling("how do I pay withholdin tax"),
            "how do I pay withholding tax",
        )

    def test_correction_is_case_insensitive_at_a_boundary(self) -> None:
        self.assertEqual(correct_spelling("WITHHOLDIN tax").lower(), "withholding tax")

    def test_a_correction_key_inside_a_longer_unrelated_word_is_not_replaced(self) -> None:
        """Word boundaries mean substrings of other words are untouched."""
        for wrong in _CORRECTIONS:
            padded = f"xx{wrong}xx"
            self.assertEqual(correct_spelling(padded), padded, wrong)

    def test_user_syntax_errors_and_slips_are_understood(self) -> None:
        """Users make syntax errors, typos, and SMS-style chat slips."""
        cases = [
            ("hw do i regstr for a tin?", "how do i register for a tin?"),
            ("wat is the vat rat?", "what is the vat rate?"),
            ("penlaty for late fillling of vat retun", "penalty for late filing of vat return"),
            ("incometax for comapny", "income tax for company"),
            ("can u claryfy efris invoyce requrments?", "can you clarify efris invoice requirements?"),
            ("statment and clearnce for tcc", "statement and clearance for tcc"),
            ("what is the dedline for filing?", "what is the deadline for filing?"),
            ("is vat compulsary or voluntery?", "is vat compulsory or voluntary?"),
        ]
        for noisy, expected in cases:
            with self.subTest(noisy=noisy):
                self.assertEqual(correct_spelling(noisy).lower(), expected.lower())

    def test_fuzzy_distance_one_correction_works(self) -> None:
        """Unseen minor typos (edit distance 1) against domain terms are automatically resolved."""
        self.assertEqual(correct_spelling("doucment").lower(), "document")
        self.assertEqual(correct_spelling("individuls").lower(), "individuals")
        self.assertEqual(correct_spelling("penaltys").lower(), "penalties")

    def test_general_english_syntax_and_misspellings(self) -> None:
        """Handles normal English syntax errors, glued words, inverted questions, and everyday misspellings."""
        cases = [
            ("how i can get my tin number?", "how can i get my tin number?"),
            ("where i pay my tax?", "where do i pay my tax?"),
            ("i want know whatis vat", "i want to know what is vat"),
            ("howmuch is the servise fee?", "how much is the service fee?"),
            ("am having a problm with my calender", "i am having a problem with my calendar"),
            ("pleaaase helpme to recieve infomation from goverment offise", "please help me to receive information from government office"),
            ("is it posible to procede with diffrent adress?", "is it possible to proceed with different address?"),
            ("pls tel me wich document is neccessary", "please tell me which document is necessary"),
            ("did not filed my tax return", "did not file my tax return"),
        ]
        for noisy, expected in cases:
            with self.subTest(noisy=noisy):
                self.assertEqual(correct_spelling(noisy).lower(), expected.lower())


if __name__ == "__main__":
    unittest.main()
