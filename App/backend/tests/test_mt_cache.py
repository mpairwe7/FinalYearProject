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


if __name__ == "__main__":
    unittest.main()
