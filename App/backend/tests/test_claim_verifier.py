from __future__ import annotations

import unittest

from app.claim_verifier import verify_claims


class ClaimVerifierTests(unittest.TestCase):
    def test_cited_supported_claim_approves(self) -> None:
        hits = [{"text": "The standard VAT rate in Uganda is 18 percent for taxable supplies."}]
        citations = [{"ref": "[1]", "passage": hits[0]["text"]}]
        report = verify_claims("The standard VAT rate in Uganda is 18 percent. [1]", citations, hits)
        self.assertEqual(report["decision"], "approve")
        self.assertEqual(report["unsupported_claims"], [])

    def test_uncited_claim_requests_revision(self) -> None:
        hits = [{"text": "TIN registration can be completed through the URA online portal."}]
        citations = [{"ref": "[1]", "passage": hits[0]["text"]}]
        report = verify_claims("TIN registration can be completed through the URA online portal.", citations, hits)
        self.assertEqual(report["decision"], "revise")
        self.assertEqual(len(report["uncited_claims"]), 1)

    def test_unsupported_number_escalates(self) -> None:
        hits = [{"text": "The standard VAT rate in Uganda is 18 percent."}]
        citations = [{"ref": "[1]", "passage": hits[0]["text"]}]
        report = verify_claims("The standard VAT rate in Uganda is 22 percent. [1]", citations, hits)
        self.assertEqual(report["decision"], "escalate")
        self.assertEqual(len(report["unsupported_claims"]), 1)


if __name__ == "__main__":
    unittest.main()
