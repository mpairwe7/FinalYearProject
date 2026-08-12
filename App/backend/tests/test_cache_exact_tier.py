"""Tests for the model-free exact tier of the response cache.

Both cache backends used to return ``None`` from ``get`` the moment no dense
model was present, and ``service.py`` only calls ``set_model`` when the retriever
has one. Every deployment shipping without torch — Crane Cloud, the HF Space —
therefore ran with a cache that missed on every single request and never reused
anything. The exact tier is what makes it work there, so these tests all
construct the cache with **no** model.
"""

from __future__ import annotations

import unittest

from app.cache import SemanticCache, exact_cache_key


class ExactCacheKeyTests(unittest.TestCase):
    def test_case_punctuation_and_whitespace_collapse(self) -> None:
        canonical = exact_cache_key("What is the VAT rate?")
        for variant in (
            "what is the vat rate",
            "WHAT IS THE VAT RATE?!",
            "  What   is  the VAT   rate ?  ",
        ):
            self.assertEqual(exact_cache_key(variant), canonical, variant)

    def test_locale_is_part_of_the_key(self) -> None:
        self.assertNotEqual(
            exact_cache_key("What is the VAT rate?", "en"),
            exact_cache_key("What is the VAT rate?", "lg"),
        )

    def test_different_questions_do_not_collide(self) -> None:
        self.assertNotEqual(
            exact_cache_key("What is the VAT rate?"),
            exact_cache_key("What is the PAYE rate?"),
        )

    def test_empty_and_none_are_handled(self) -> None:
        self.assertEqual(exact_cache_key(""), "en\x1f")
        self.assertEqual(exact_cache_key(None), "en\x1f")  # type: ignore[arg-type]


class ExactTierWithoutAnEmbedderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = SemanticCache()  # no dense model, as in the no-torch image
        self.assertIsNone(self.cache._dense_model)

    def test_a_repeated_question_is_served_from_cache(self) -> None:
        self.assertIsNone(self.cache.get("What is the VAT rate?", "en"))
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        self.assertEqual(
            self.cache.get("What is the VAT rate?", "en"),
            {"reply": "18 percent", "locale": "en"},
        )

    def test_normalised_variants_hit_the_same_entry(self) -> None:
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        for variant in ("what is the vat rate", "  What is the VAT rate ?  "):
            self.assertIsNotNone(self.cache.get(variant, "en"), variant)

    def test_a_different_locale_does_not_hit(self) -> None:
        """An English answer must not be served for a Luganda question."""
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        self.assertIsNone(self.cache.get("What is the VAT rate?", "lg"))

    def test_a_different_question_does_not_hit(self) -> None:
        self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
        self.assertIsNone(self.cache.get("What is the PAYE rate?", "en"))

    def test_the_entry_locale_comes_from_the_response(self) -> None:
        """put() has no locale argument, so it must key on the response's own
        locale or a Luganda answer would be filed under English."""
        self.cache.put("Omusolo gwa VAT guli gwa?", {"reply": "18%", "locale": "lg"})
        self.assertIsNotNone(self.cache.get("Omusolo gwa VAT guli gwa?", "lg"))
        self.assertIsNone(self.cache.get("Omusolo gwa VAT guli gwa?", "en"))

    def test_hits_and_misses_are_counted(self) -> None:
        self.cache.get("cold", "en")
        self.cache.put("warm", {"reply": "x", "locale": "en"})
        self.cache.get("warm", "en")
        stats = self.cache.stats
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["size"], 1)

    def test_expired_exact_entries_are_evicted(self) -> None:
        import app.cache as cache_module

        original = cache_module.CACHE_TTL_SECONDS
        cache_module.CACHE_TTL_SECONDS = 0
        try:
            self.cache.put("What is the VAT rate?", {"reply": "18 percent", "locale": "en"})
            self.assertIsNone(self.cache.get("What is the VAT rate?", "en"))
        finally:
            cache_module.CACHE_TTL_SECONDS = original

    def test_the_exact_tier_respects_the_size_cap(self) -> None:
        import app.cache as cache_module

        original = cache_module.CACHE_MAX_SIZE
        cache_module.CACHE_MAX_SIZE = 3
        try:
            for i in range(6):
                self.cache.put(f"question number {i}", {"reply": str(i), "locale": "en"})
            self.assertLessEqual(len(self.cache._exact), 3)
            self.assertGreater(self.cache.stats["evictions"], 0)
        finally:
            cache_module.CACHE_MAX_SIZE = original


if __name__ == "__main__":
    unittest.main()
