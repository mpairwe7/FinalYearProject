"""P1-5 (score normalization/calibration) + P1-8 (contradiction grounding).

P1-5: abstention/corrective gating uses a normalized [0,1] reranker scale, and
treats reranker-absent (RRF-only) as a degraded mode instead of abstaining on
an incomparable raw score.

P1-8: a claim whose percentage conflicts with the cited context is flagged as a
contradiction so the response judge escalates rather than disclaiming.
"""

from __future__ import annotations

import unittest
import unittest.mock as mock


class NormalizationTest(unittest.TestCase):
    def test_sigmoid_bounds_and_monotonic(self) -> None:
        from app.retriever import normalize_rerank_score

        self.assertAlmostEqual(normalize_rerank_score(0.0), 0.5, places=6)
        self.assertGreater(normalize_rerank_score(8.0), 0.99)
        self.assertLess(normalize_rerank_score(-8.0), 0.01)
        # No overflow on extreme logits.
        self.assertTrue(0.0 <= normalize_rerank_score(1e6) <= 1.0)
        self.assertTrue(0.0 <= normalize_rerank_score(-1e6) <= 1.0)

    def test_hit_relevance_priority_and_degraded(self) -> None:
        from app.retriever import hit_relevance

        self.assertEqual(hit_relevance({"score_norm": 0.7}), 0.7)
        self.assertAlmostEqual(hit_relevance({"score_rerank": 0.0}), 0.5, places=6)
        # RRF-only → degraded (None), since RRF is not comparable to the reranker.
        self.assertIsNone(hit_relevance({"score_rrf": 0.016}))
        self.assertIsNone(hit_relevance({}))
        self.assertIsNone(hit_relevance({"score_norm": "bad"}))


class AbstainTest(unittest.TestCase):
    def test_uses_normalized_scale_and_degraded_mode(self) -> None:
        from app.guardrails import OutputGuard

        self.assertTrue(OutputGuard.should_abstain([]))  # no hits
        self.assertFalse(OutputGuard.should_abstain([{"score_norm": 0.8}]))  # confident
        self.assertTrue(OutputGuard.should_abstain([{"score_norm": 0.1}]))  # too low
        # Degraded (RRF-only): do NOT abstain on an incomparable raw score —
        # this is the over-abstention bug P1-5 fixes.
        self.assertFalse(OutputGuard.should_abstain([{"score_rrf": 0.01}]))


class CorrectTest(unittest.TestCase):
    def test_uses_normalized_scale_and_degraded_mode(self) -> None:
        import app.corrective_rag as cr

        with mock.patch.object(cr, "CORRECTIVE_ENABLED", True):
            self.assertTrue(cr.should_correct([]))  # no hits → correct
            self.assertFalse(cr.should_correct([{"score_norm": 0.9}]))  # strong
            self.assertTrue(cr.should_correct([{"score_norm": 0.2}]))  # weak
            # Degraded (RRF-only): don't spuriously correct on every query.
            self.assertFalse(cr.should_correct([{"score_rrf": 0.01}]))


class NumericContradictionTest(unittest.TestCase):
    def test_percentage_conflict_detection(self) -> None:
        from app.entailment import is_contradicted, numeric_contradiction

        self.assertTrue(numeric_contradiction("VAT is 20%", "VAT is 18%"))
        self.assertTrue(numeric_contradiction("VAT is 20 percent", "VAT is 18 percent"))
        self.assertTrue(numeric_contradiction("rate is 20%", "rate is 18 percent"))  # mixed forms
        self.assertFalse(numeric_contradiction("VAT is 18%", "VAT is 18 percent"))  # same value
        self.assertFalse(numeric_contradiction("Register at the portal", "VAT is 18%"))  # no claim %
        self.assertFalse(numeric_contradiction("VAT is 18%", "Register at the portal"))  # no ctx %
        self.assertTrue(is_contradicted("VAT is 20%", ["VAT is 18%", "extra ctx"]))
        self.assertFalse(is_contradicted("", ["VAT is 18%"]))


class VerifyClaimsContradictionTest(unittest.TestCase):
    def test_contradicted_claim_escalates(self) -> None:
        from app.claim_verifier import verify_claims

        hits = [{"text": "The standard VAT rate in Uganda is 18%."}]
        citations = [{"ref": "[1]", "passage": hits[0]["text"]}]
        report = verify_claims("The standard VAT rate is 20%. [1]", citations, hits)
        self.assertEqual(report["decision"], "escalate")
        self.assertTrue(report["contradicted_claims"])
        self.assertTrue(report["unsupported_claims"])  # contradicted is also unsupported

    def test_supported_claim_not_contradicted(self) -> None:
        from app.claim_verifier import verify_claims

        hits = [{"text": "The standard VAT rate in Uganda is 18% for taxable supplies."}]
        citations = [{"ref": "[1]", "passage": hits[0]["text"]}]
        report = verify_claims("The standard VAT rate is 18%. [1]", citations, hits)
        self.assertEqual(report["decision"], "approve")
        self.assertEqual(report["contradicted_claims"], [])


if __name__ == "__main__":
    unittest.main()
