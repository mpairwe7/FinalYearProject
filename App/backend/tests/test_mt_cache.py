"""The machine-translation memo, and what it refuses to remember.

A non-English turn used to pay for translation three times — the router
translates the question, the hybrid retriever translates the same question
again for the corpus, and the reply is translated on the way out — each one a
local generation pass or a Sunbird round trip. That is why a Luganda question
took two to three times as long as the identical English one. Two of those
three calls translate the same string, and a taxpayer assistant is asked the
same questions repeatedly besides.

The cache is per-process and holds only a digest of the text, because taxpayer
questions reach it and can carry a TIN or a name.
"""

from __future__ import annotations

import unittest

from app import mt


class FigureFidelityTest(unittest.TestCase):
    def setUp(self) -> None:
        mt.cache.clear()

    def test_the_same_rate_marked_differently_still_matches(self):
        """Luganda writes a rate as parts per hundred, with no percent sign.

        Comparing money and percentages as separate categories rejected this —
        the source's percentage vanished, the translation grew an amount — so
        a translation that is exactly right was thrown away and the taxpayer
        got English.
        """
        self.assertTrue(
            mt.figures_survived(
                "The standard VAT rate in Uganda is 18% on taxable supplies.",
                "Omusolo gwa VAT mu Uganda guli ebitundu 18 ku buli kikumi.",
            )
        )

    def test_a_changed_digit_is_caught(self):
        self.assertFalse(
            mt.figures_survived("PAYE due is UGX 235,000.", "PAYE ye UGX 253,000.")
        )

    def test_grouping_and_suffix_are_the_same_number(self):
        self.assertTrue(
            mt.figures_survived(
                "The registration threshold is 150m shillings.",
                "Ekipimo ky’okwewandiisa kiri 150,000,000.",
            )
        )

    def test_an_invented_figure_is_caught(self):
        self.assertFalse(
            mt.figures_survived("Visit any URA office.", "Genda mu ofiisi 5 eza URA.")
        )

    def test_prose_with_no_figures_passes(self):
        self.assertTrue(
            mt.figures_survived("Visit any URA office.", "Genda mu ofiisi ya URA yonna.")
        )


class CacheTest(unittest.TestCase):
    def setUp(self) -> None:
        mt.cache.clear()

    def test_a_hit_does_not_run_the_backend(self):
        calls = []

        def _translate():
            calls.append(1)
            return "Genda ku ura.go.ug."

        first = mt.translate_cached("Go to ura.go.ug.", "en", "lg", _translate)
        second = mt.translate_cached("Go to ura.go.ug.", "en", "lg", _translate)
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1)

    def test_direction_is_part_of_the_key(self):
        mt.cache.put("lg", "en", "Nkola ntya", "How do I do it")
        self.assertIsNone(mt.cache.get("en", "lg", "Nkola ntya"))

    def test_a_failed_translation_is_never_pinned(self):
        """A Sunbird timeout must not stick for the life of the process."""
        outcomes = iter([None, "Genda ku ura.go.ug."])
        results = [
            mt.translate_cached("Go to ura.go.ug.", "en", "lg", lambda: next(outcomes))
            for _ in range(2)
        ]
        self.assertEqual(results, [None, "Genda ku ura.go.ug."])

    def test_a_figure_mangling_translation_is_never_pinned(self):
        out = mt.translate_cached("PAYE is UGX 235,000.", "en", "lg", lambda: "PAYE ye UGX 253,000.")
        self.assertEqual(out, "PAYE ye UGX 253,000.")
        self.assertIsNone(mt.cache.get("en", "lg", "PAYE is UGX 235,000."))

    def test_the_cache_is_bounded(self):
        cache = mt._TranslationCache(max_entries=2)
        for index in range(5):
            cache.put("en", "lg", f"question {index}", f"ekibuuzo {index}")
        self.assertEqual(cache.stats()["entries"], 2)
        # Oldest evicted, newest kept.
        self.assertIsNone(cache.get("en", "lg", "question 0"))
        self.assertEqual(cache.get("en", "lg", "question 4"), "ekibuuzo 4")

    def test_the_text_itself_is_not_retained(self):
        """The key is a digest — this cache must not become a second copy of
        the conversation sitting in memory."""
        question_with_a_tin = "My TIN is 1000123456, what do I owe?"
        mt.cache.put("en", "lg", question_with_a_tin, "translated")
        stored_keys = [key for key in mt.cache._entries]
        self.assertTrue(stored_keys)
        self.assertNotIn(question_with_a_tin, str(stored_keys))

    def test_an_oversized_reply_is_not_cached(self):
        long_text = "x" * (mt.MT_CACHE_MAX_CHARS + 1)
        mt.cache.put("en", "lg", long_text, "short")
        self.assertIsNone(mt.cache.get("en", "lg", long_text))

    def test_a_disabled_cache_is_inert(self):
        cache = mt._TranslationCache(max_entries=0)
        cache.put("en", "lg", "anything", "kintu")
        self.assertIsNone(cache.get("en", "lg", "anything"))


class FigureProtectionTest(unittest.TestCase):
    """Digits are masked before translation, so MT never sees one to rewrite.

    The mechanism these replace (``heal_vernacular_figures``) tried to repair
    the output afterwards. Repairing means guessing where a number belonged,
    and the narrowed version that could no longer guess could no longer fire
    at all: a figure only counted as missing once its digits were absent, and
    every insertion path it had left required those digits to be present.
    """

    SOURCE = "The VAT rate is 18% and the threshold is UGX 150,000,000."

    def test_masking_and_restoring_is_the_identity(self):
        text = "Pay UGX 150,000,000 or 1.5m by the 15th; call 0800 117 000."
        masked, mapping = mt.protect_figures(text)
        restored, residue = mt.restore_figures(masked, mapping)
        self.assertEqual(restored, text)
        self.assertEqual(residue, 0)

    def test_only_the_digits_are_masked(self):
        """The currency code and percent sign stay: they are the language cue.

        Luganda renders a rate as "ebitundu 18 ku buli kikumi", which it can
        only do if it can still see that the figure was a percentage.
        """
        masked, mapping = mt.protect_figures(self.SOURCE)
        self.assertIn("%", masked)
        self.assertIn("UGX", masked)
        self.assertNotIn("18", masked)
        self.assertNotIn("150,000,000", masked)
        self.assertEqual(sorted(mapping.values()), ["150,000,000", "18"])

    def test_a_translator_that_rewrites_digits_cannot_touch_a_masked_figure(self):
        """The guarantee. Digit transposition is the failure that motivated this."""
        masked, mapping = mt.protect_figures(self.SOURCE)
        # A translator that mangles every digit it is given; it is given none.
        mangled = "".join("0" if ch.isdigit() else ch for ch in masked)
        restored, residue = mt.restore_figures(mangled, mapping)
        self.assertEqual(residue, 0)
        self.assertTrue(mt.figures_survived(self.SOURCE, restored))
        self.assertIn("150,000,000", restored)
        self.assertIn("18%", restored)

    def test_repeated_figures_get_distinct_sentinels(self):
        """Restoration is positional, so a reorder cannot swap two figures."""
        masked, mapping = mt.protect_figures("18% here and 18% there")
        self.assertEqual(len(mapping), 2)
        self.assertEqual(len(set(mapping)), 2)
        self.assertEqual(masked, "#NMBRA#% here and #NMBRB#% there")

    def test_labels_stay_distinct_past_the_alphabet(self):
        """``#NMBRA#`` must not match inside ``#NMBRAA#``."""
        text = " ".join(str(n) for n in range(1, 31))
        masked, mapping = mt.protect_figures(text)
        self.assertEqual(len(mapping), 30)
        self.assertIn("#NMBRAA#", masked)
        restored, residue = mt.restore_figures(masked, mapping)
        self.assertEqual(restored, text)
        self.assertEqual(residue, 0)

    def test_space_grouped_digits_are_one_span(self):
        """"1 500 000" and "0800 117 000" are one figure, not three.

        The grouping branch is what keeps a phone number intact through
        translation. Consecutive three-digit numbers are read the same way,
        which costs nothing: the whole run is masked and restored verbatim.
        """
        masked, mapping = mt.protect_figures("Call 0800 117 000 about UGX 1 500 000.")
        self.assertEqual(sorted(mapping.values()), ["0800 117 000", "1 500 000"])
        restored, residue = mt.restore_figures(masked, mapping)
        self.assertEqual(restored, "Call 0800 117 000 about UGX 1 500 000.")
        self.assertEqual(residue, 0)

    def test_restoration_tolerates_what_translators_do_to_a_sentinel(self):
        _, mapping = mt.protect_figures(self.SOURCE)
        for mangled in (
            "Omusolo guli #nmbra#% ne ekkomo UGX #NMBRB#.",   # lowercased
            "Omusolo guli # NMBRA #% ne ekkomo UGX #NMBRB#.",  # spaced out
            "Omusolo guli NMBRA% ne ekkomo UGX NMBRB.",        # hashes stripped
            "Omusolo guli omuNMBRA#% ne ekkomo UGX #NMBRB#.",  # prefix glued on
        ):
            with self.subTest(mangled=mangled):
                restored, residue = mt.restore_figures(mangled, mapping)
                self.assertEqual(residue, 0)
                self.assertTrue(mt.figures_survived(self.SOURCE, restored))

    def test_an_echoed_sentinel_is_reported_as_residue(self):
        """Visible garbage in a taxpayer's answer; the caller must not ship it."""
        _, mapping = mt.protect_figures(self.SOURCE)
        restored, residue = mt.restore_figures(
            "Guli #NMBRA# ne #NMBRA# ne UGX #NMBRB#.", mapping
        )
        self.assertEqual(residue, 1)
        self.assertIn("NMBR", restored)

    def test_a_dropped_sentinel_leaves_no_residue_and_is_caught_by_the_guard(self):
        """Masking stops mutation; it cannot stop omission, which still falls back."""
        _, mapping = mt.protect_figures(self.SOURCE)
        restored, residue = mt.restore_figures("Omusolo guli waggulu.", mapping)
        self.assertEqual(residue, 0)
        self.assertFalse(mt.figures_survived(self.SOURCE, restored))

    def test_text_without_figures_is_untouched(self):
        """The common case pays nothing and skips restoration entirely."""
        text = "Genda mu ofiisi ya URA yonna."
        masked, mapping = mt.protect_figures(text)
        self.assertEqual(masked, text)
        self.assertEqual(mapping, {})

    def test_ordinary_prose_is_never_read_as_residue(self):
        """The sentinel core is not a substring of any English word."""
        restored, residue = mt.restore_figures(
            "Check the figure in the config for that number.", {}
        )
        self.assertEqual(residue, 0)
        self.assertEqual(restored, "Check the figure in the config for that number.")


if __name__ == "__main__":
    unittest.main()
