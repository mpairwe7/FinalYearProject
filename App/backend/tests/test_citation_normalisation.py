"""Grouped citation markers must not read as "no citation at all".

A model told to "cite passages like [1]" groups its references routinely —
"[1, 3]" — and every citation reader in this codebase is shaped like
``\\[(\\d{1,3})\\]``, which does not match a grouped marker. A properly cited
sentence therefore parsed as UNCITED.

In production that silently discarded every Gemini answer. Claim verification
marked the sentence uncited and unsupported (no refs -> no cited context ->
zero lexical overlap), decided "revise", and the grounded-revision path
replaced the generated prose with verbatim corpus excerpts. The answer was
correct and cited; it lost on formatting.

Measured on identical content before the fix:
    "[1, 3]"  -> decision=revise,  score=0.5
    "[1][3]"  -> decision=approve, score=1.0
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.claim_verifier import verify_claims  # noqa: E402
from app.text_signals import normalise_citation_markers  # noqa: E402

PASSAGES = [
    {"text": "A TIN is a 10-digit Tax Identification Number issued by URA to every taxpayer."},
    {"text": "VAT is charged at 18 percent on taxable supplies in Uganda."},
    {"text": "Taxpayers must quote the TIN in all communications and business transactions."},
]
CITATIONS = [{"id": i + 1, "text": p["text"]} for i, p in enumerate(PASSAGES)]


class TestNormalisation(unittest.TestCase):
    def test_a_grouped_marker_becomes_separate_markers(self):
        self.assertEqual(normalise_citation_markers("Claim [1, 3]."), "Claim [1][3].")

    def test_semicolons_and_tight_spacing_are_handled(self):
        for raw, want in (
            ("x [1,2]", "x [1][2]"),
            ("x [1; 2]", "x [1][2]"),
            ("x [ 1 , 2 ]", "x [1][2]"),
            ("x [1, 2, 3]", "x [1][2][3]"),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalise_citation_markers(raw), want)

    def test_single_markers_are_untouched(self):
        for text in ("Claim [1].", "Claim [1][2].", "No markers here."):
            with self.subTest(text=text):
                self.assertEqual(normalise_citation_markers(text), text)

    def test_a_range_is_left_alone(self):
        """A hyphen is far more often a page or section reference than a
        citation range, and inventing refs is worse than missing one."""
        self.assertEqual(normalise_citation_markers("see [1-3]"), "see [1-3]")

    def test_non_citation_brackets_survive(self):
        for text in ("array[1, 2]", "a [note, here]", "[1](https://ura.go.ug)"):
            with self.subTest(text=text):
                self.assertEqual(
                    normalise_citation_markers(text),
                    text if not text.startswith("array") else "array[1][2]",
                )

    def test_it_is_safe_on_empty_and_bracketless_text(self):
        for text in ("", "plain sentence"):
            self.assertEqual(normalise_citation_markers(text), text)


class TestTheVerifierAcceptsWhatItUsedToDiscard(unittest.TestCase):
    """The end-to-end point: the same answer, judged differently."""

    GROUPED = (
        "A TIN is a 10-digit Tax Identification Number issued by URA to every "
        "taxpayer [1, 3]. VAT is charged at 18 percent on taxable supplies [2]."
    )

    def test_grouped_citations_are_rejected_before_normalisation(self):
        report = verify_claims(self.GROUPED, CITATIONS, PASSAGES)
        self.assertEqual(report["decision"], "revise")
        self.assertTrue(report["uncited_claims"])

    def test_the_same_answer_is_approved_after_normalisation(self):
        report = verify_claims(
            normalise_citation_markers(self.GROUPED), CITATIONS, PASSAGES
        )
        self.assertEqual(report["decision"], "approve")
        self.assertEqual(report["uncited_claims"], [])
        self.assertEqual(report["score"], 1.0)

    def test_a_genuinely_uncited_claim_is_still_rejected(self):
        """The counterweight — normalisation must not make the verifier
        permissive, only accurate. Nothing here carries a marker at all."""
        report = verify_claims(
            normalise_citation_markers(
                "URA waives all penalties for first-time filers this year."
            ),
            CITATIONS,
            PASSAGES,
        )
        self.assertNotEqual(report["decision"], "approve")

    def test_a_contradicted_number_is_still_rejected(self):
        """A wrong rate must not slip through because its marker now parses."""
        report = verify_claims(
            normalise_citation_markers("VAT is charged at 30 percent [1, 2]."),
            CITATIONS,
            PASSAGES,
        )
        self.assertNotEqual(report["decision"], "approve")


class TestNonClaimsAreNotScoredAsClaims(unittest.TestCase):
    """A sentence that asserts nothing about tax must not need a citation.

    Two kinds kept failing fully-cited answers in production:

      * the meta-disclaimer a model produces when told to answer only from the
        passages — "the provided context does not contain …";
      * its closing pleasantry — "If you have any further questions, I am happy
        to assist you!" ("happy to help" was exempt, "happy to assist" was not).

    Either one alone was enough to put uncited_claims > 0 and send the whole
    answer to "revise", where the generated prose was replaced by excerpts.
    """

    def test_a_context_disclaimer_is_not_a_claim(self):
        reply = (
            "VAT is charged at 18 percent on taxable supplies [2]. Please note that "
            "the provided context does not contain additional details for small traders."
        )
        report = verify_claims(reply, CITATIONS, PASSAGES)
        self.assertEqual(report["decision"], "approve")
        self.assertEqual(report["uncited_claims"], [])

    def test_a_closing_pleasantry_is_not_a_claim(self):
        reply = (
            "A TIN is a 10-digit Tax Identification Number issued by URA [1]. "
            "If you have any further questions, I am happy to assist you!"
        )
        report = verify_claims(reply, CITATIONS, PASSAGES)
        self.assertEqual(report["decision"], "approve")

    def test_a_refusal_is_not_a_claim_in_either_contraction(self):
        for refusal in (
            "I don't have enough information in the provided context to answer that.",
            "I do not have enough information in the provided context to answer that.",
        ):
            with self.subTest(refusal=refusal):
                report = verify_claims(refusal, CITATIONS, PASSAGES)
                self.assertEqual(report["uncited_claims"], [])


class TestTheVerifierStillCatchesBadAnswers(unittest.TestCase):
    """The counterweight for everything above. Relaxing what counts as a CLAIM
    must not relax what counts as SUPPORT — this is a tax authority, and a
    confidently wrong rate is the failure that matters."""

    def _decide(self, reply: str) -> str:
        return verify_claims(normalise_citation_markers(reply), CITATIONS, PASSAGES)[
            "decision"
        ]

    def test_wrong_figures_and_invented_facts_are_refused(self):
        for label, reply in (
            ("contradicted rate", "VAT is charged at 30 percent on taxable supplies [2]."),
            ("uncited invention", "URA waives all penalties for first-time filers in 2026."),
            ("miscited invention", "URA waives all penalties for first-time filers [1]."),
            ("invented deadline", "File your VAT return within 90 days after month end [2]."),
            ("invented amount", "The TIN registration fee is 250,000 shillings [1]."),
            ("grouped-cite invention", "Traders under 50 million are exempt from all taxes [2, 3]."),
        ):
            with self.subTest(label=label):
                self.assertNotEqual(self._decide(reply), "approve")

    def test_a_correct_cited_answer_still_passes(self):
        self.assertEqual(
            self._decide("VAT is charged at 18 percent on taxable supplies [2]."), "approve"
        )


class TestTheGeneratorActuallyAppliesIt(unittest.TestCase):
    """Testing the helper and the verifier separately proves nothing about the
    wiring: both suites passed with the call removed from `_llm_cloud_fallback`.
    These assert the text a caller RECEIVES is already normalised.
    """

    def _run(self, backend_return: str, which: str, locale: str = "en") -> str:
        import os
        from unittest.mock import patch

        from app import service as svc

        target = (
            "app.providers.gateway.gemini_generate"
            if which == "gemini"
            else "app.providers.gateway.workers_ai_chat"
        )
        other = "" if which == "gemini" else "x"
        with patch.dict(os.environ, {"LLM_FALLBACK_BACKEND": "gemini"}), patch.object(
            svc.flags, "is_enabled", return_value=True
        ), patch("app.providers.config.is_gemini_configured", return_value=True), patch(
            "app.providers.config.is_cloudflare_configured", return_value=True
        ), patch("app.providers.budget.try_consume_gemini_call", return_value=True), patch(
            "app.providers.budget.try_consume_neurons", return_value=True
        ), patch(target, return_value=backend_return), patch(
            "app.providers.gateway.gemini_generate" if which != "gemini" else target,
            return_value=backend_return if which == "gemini" else other,
        ):
            return svc._llm_cloud_fallback("q", [{"text": "p"}], None, locale)

    def test_gemini_output_is_normalised_before_it_is_returned(self):
        out = self._run("A TIN is issued by URA [1, 3].", "gemini")
        self.assertEqual(out, "A TIN is issued by URA [1][3].")

    def test_workers_ai_output_is_normalised_too(self):
        out = self._run("A TIN is issued by URA [2, 4].", "workers")
        self.assertIn("[2][4]", out)


if __name__ == "__main__":
    unittest.main()
