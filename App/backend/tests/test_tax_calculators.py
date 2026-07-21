"""Unit tests for the deterministic tax calculator tools.

Figures pin the FY2025-26 statutory formulas — in particular the resident
PAYE top band flat portion (25,000 + 30% x (10,000,000 - 410,000) =
2,902,000), which was previously off by 500.
"""

from __future__ import annotations

import unittest

from app.tools import ToolRegistry


class PAYECalculatorTests(unittest.TestCase):
    def _paye(self, gross: float, **kwargs) -> dict:
        return ToolRegistry.call("calculate_paye", {"monthly_gross": gross, **kwargs})

    def test_below_threshold_pays_nothing(self) -> None:
        result = self._paye(235_000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["paye"], 0.0)

    def test_ten_percent_band(self) -> None:
        result = self._paye(300_000)
        self.assertEqual(result["paye"], 6_500.0)  # 10% x 65,000

    def test_thirty_percent_band(self) -> None:
        result = self._paye(1_000_000)
        self.assertEqual(result["paye"], 202_000.0)  # 25,000 + 30% x 590,000
        self.assertEqual(result["net_take_home"], 798_000.0)

    def test_top_band_flat_matches_statutory_formula(self) -> None:
        # flat at 10M boundary must be 25,000 + 30% x 9,590,000 = 2,902,000
        result = self._paye(12_000_000)
        self.assertEqual(result["paye"], 3_702_000.0)  # 2,902,000 + 40% x 2,000,000

    def test_non_resident_top_band(self) -> None:
        result = self._paye(12_000_000, residency="non_resident")
        self.assertEqual(result["paye"], 3_725_500.0)  # 2,925,500 + 40% x 2,000,000

    def test_negative_salary_rejected(self) -> None:
        result = self._paye(-1)
        self.assertFalse(result["ok"])


class VATCalculatorTests(unittest.TestCase):
    def test_add_direction(self) -> None:
        result = ToolRegistry.call("calculate_vat", {"amount": 1_000_000})
        self.assertTrue(result["ok"])
        self.assertEqual(result["vat"], 180_000.0)
        self.assertEqual(result["gross"], 1_180_000.0)

    def test_extract_direction(self) -> None:
        result = ToolRegistry.call(
            "calculate_vat", {"amount": 1_180_000, "direction": "extract"}
        )
        self.assertEqual(result["net"], 1_000_000.0)
        self.assertEqual(result["vat"], 180_000.0)


class RentalIncomeTaxTests(unittest.TestCase):
    def test_individual_above_threshold(self) -> None:
        result = ToolRegistry.call(
            "calculate_rental_tax", {"annual_gross_rent": 12_000_000}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["landlord_type"], "individual")
        self.assertEqual(result["taxable_amount"], 9_180_000.0)
        self.assertEqual(result["tax"], 1_101_600.0)  # 12% x 9,180,000

    def test_individual_below_threshold_pays_nothing(self) -> None:
        result = ToolRegistry.call(
            "calculate_rental_tax", {"annual_gross_rent": 2_000_000}
        )
        self.assertEqual(result["tax"], 0.0)
        self.assertIn("threshold", result["explanation"])

    def test_company_expense_cap_enforced(self) -> None:
        result = ToolRegistry.call(
            "calculate_rental_tax",
            {
                "landlord_type": "company",
                "annual_gross_rent": 50_000_000,
                "allowable_expenses": 30_000_000,  # over the 50% cap
            },
        )
        self.assertEqual(result["allowable_expenses"], 25_000_000.0)  # capped
        self.assertEqual(result["chargeable_income"], 25_000_000.0)
        self.assertEqual(result["tax"], 7_500_000.0)  # 30%

    def test_company_expenses_within_cap(self) -> None:
        result = ToolRegistry.call(
            "calculate_rental_tax",
            {
                "landlord_type": "company",
                "annual_gross_rent": 50_000_000,
                "allowable_expenses": 10_000_000,
            },
        )
        self.assertEqual(result["chargeable_income"], 40_000_000.0)
        self.assertEqual(result["tax"], 12_000_000.0)


class WithholdingTaxTests(unittest.TestCase):
    def test_services_rate(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding", {"payment_type": "services", "amount": 2_000_000}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["withholding_tax"], 120_000.0)  # 6%
        self.assertEqual(result["net_payable"], 1_880_000.0)

    def test_dividend_rate(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding", {"payment_type": "dividend", "amount": 1_000_000}
        )
        self.assertEqual(result["withholding_tax"], 150_000.0)  # 15%

    def test_unknown_type_rejected(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding", {"payment_type": "royalty", "amount": 100_000}
        )
        self.assertFalse(result["ok"])
        self.assertIn("payment_type", result["error"])


if __name__ == "__main__":
    unittest.main()
