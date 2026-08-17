"""HyDE is dense-only, flag-gated, and must not rewrite BM25 input."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.flags import FeatureFlags  # noqa: E402
from app.hyde import dense_query_text, template_hypothetical  # noqa: E402


class TemplateHydeTests(unittest.TestCase):
    def test_template_contains_the_question_and_ura_context(self) -> None:
        doc = template_hypothetical("What is the VAT rate?")
        self.assertIn("What is the VAT rate?", doc)
        self.assertIn("Uganda Revenue Authority", doc)

    def test_blank_query_is_empty(self) -> None:
        self.assertEqual(template_hypothetical("   "), "")


class DenseQueryTextFlagTests(unittest.TestCase):
    def test_flag_off_returns_the_raw_query(self) -> None:
        with patch("app.flags.flags.is_enabled", return_value=False):
            self.assertEqual(dense_query_text("What is PAYE?"), "What is PAYE?")

    def test_flag_on_uses_the_template_when_llm_hyde_is_off(self) -> None:
        with (
            patch("app.flags.flags.is_enabled", return_value=True),
            patch("app.hyde.HYDE_LLM", False),
        ):
            text = dense_query_text("What is PAYE?")
        self.assertIn("What is PAYE?", text)
        self.assertIn("Uganda Revenue Authority", text)
        self.assertNotEqual(text, "What is PAYE?")

    def test_llm_hyde_falls_back_to_template_when_generation_is_empty(self) -> None:
        with (
            patch("app.flags.flags.is_enabled", return_value=True),
            patch("app.hyde.HYDE_LLM", True),
            patch("app.hyde._llm_hypothetical", return_value=""),
        ):
            text = dense_query_text("How do I register for a TIN?")
        self.assertEqual(text, template_hypothetical("How do I register for a TIN?"))

    def test_llm_hyde_uses_the_generated_document_when_present(self) -> None:
        fake = "A TIN is issued through the URA web portal after identity checks."
        with (
            patch("app.flags.flags.is_enabled", return_value=True),
            patch("app.hyde.HYDE_LLM", True),
            patch("app.hyde._llm_hypothetical", return_value=fake),
        ):
            self.assertEqual(dense_query_text("How do I get a TIN?"), fake)


class DenseOnlyContractTests(unittest.TestCase):
    def test_hyde_text_diverges_from_the_lexical_query(self) -> None:
        """BM25 and the reranker must keep the taxpayer's words.

        ``HybridRetriever.search`` embeds ``dense_query_text(query)`` and
        sparse-encodes ``query``. This pins the split so a future refactor
        cannot feed the hypothetical document to BM25.
        """
        query = "What is withholding tax?"
        with (
            patch("app.flags.flags.is_enabled", return_value=True),
            patch("app.hyde.HYDE_LLM", False),
        ):
            dense = dense_query_text(query)
        self.assertNotEqual(dense, query)
        self.assertIn(query, dense)


class HydePercentRolloutTests(unittest.TestCase):
    def test_subject_is_forwarded_to_the_flag(self) -> None:
        with patch("app.flags.flags.is_enabled", return_value=False) as enabled:
            dense_query_text("What is PAYE?", subject="user-42")
        enabled.assert_called_once()
        kwargs = enabled.call_args.kwargs
        self.assertEqual(enabled.call_args.args[0], "hyde")
        self.assertEqual(kwargs.get("subject"), "user-42")

    def _env_without_hyde_bool(self, **extra: str) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k != "FLAG_HYDE"}
        env.update(extra)
        return env

    def test_percent_canary_needs_an_unset_boolean_and_a_subject(self) -> None:
        """FLAG_HYDE=false would force everyone off and ignore the percent."""
        with patch.dict(os.environ, self._env_without_hyde_bool(FLAG_HYDE_PERCENT="100"), clear=True):
            flags = FeatureFlags()
            self.assertTrue(flags.is_enabled("hyde", subject="anyone"))
            self.assertFalse(flags.is_enabled("hyde"))

    def test_explicit_false_beats_the_percent_canary(self) -> None:
        with patch.dict(
            os.environ,
            self._env_without_hyde_bool(FLAG_HYDE="false", FLAG_HYDE_PERCENT="100"),
            clear=True,
        ):
            flags = FeatureFlags()
            self.assertFalse(flags.is_enabled("hyde", subject="anyone"))


if __name__ == "__main__":
    unittest.main()
