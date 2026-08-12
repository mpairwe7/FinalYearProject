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


if __name__ == "__main__":
    unittest.main()
