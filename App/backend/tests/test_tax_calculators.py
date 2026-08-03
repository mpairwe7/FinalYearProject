"""Unit tests for the deterministic tax calculator tools.

Every figure here pins a statutory formula to a **named fiscal year**.
Calls that omit ``fiscal_year`` resolve to whichever table is in force
today, so a test that asserts a rate must say which year's rate it
means — otherwise it silently starts testing next year's law the
morning a new table lands.
"""

from __future__ import annotations

import unittest

from app.tools import ToolRegistry

FY25 = "FY2025-26"
FY26 = "FY2026-27"


class PAYEFY2025Tests(unittest.TestCase):
    """FY2025-26 bands: 0/235k, 10%/335k, 20%/410k, 30%/10m, 40% above."""

    def _paye(self, gross: float, **kwargs) -> dict:
        return ToolRegistry.call(
            "calculate_paye", {"monthly_gross": gross, "fiscal_year": FY25, **kwargs}
        )

    def test_below_threshold_pays_nothing(self) -> None:
        result = self._paye(235_000)
        self.assertTrue(result["ok"])
        self.assertEqual(result["paye"], 0.0)

    def test_ten_percent_band(self) -> None:
        result = self._paye(300_000)
        self.assertEqual(result["paye"], 6_500.0)  # 10% x 65,000

    def test_thirty_percent_band(self) -> None:
        result = self._paye(1_000_000)
        self.assertEqual(result["paye"], 202_000.0)  # 10,000 + 15,000 + 30% x 590,000
        self.assertEqual(result["net_take_home"], 798_000.0)

    def test_top_band_integrates_lower_bands(self) -> None:
        # Cumulative tax at the 10M boundary is 2,902,000; the top band
        # adds 40% of the excess.  Integrated from the bands, not read
        # off a hand-maintained constant.
        result = self._paye(12_000_000)
        self.assertEqual(result["paye"], 3_702_000.0)

    def test_non_resident_top_band(self) -> None:
        result = self._paye(12_000_000, residency="non_resident")
        self.assertEqual(result["paye"], 3_725_500.0)

    def test_negative_salary_rejected(self) -> None:
        result = self._paye(-1)
        self.assertFalse(result["ok"])

    def test_band_breakdown_sums_to_total(self) -> None:
        result = self._paye(1_000_000)
        self.assertEqual(
            round(sum(b["tax_in_band"] for b in result["bands_applied"]), 2), result["paye"]
        )

    def test_result_is_stamped_as_confirmed(self) -> None:
        basis = self._paye(1_000_000)["rate_basis"]
        self.assertEqual(basis["fiscal_year"], FY25)
        self.assertEqual(basis["status"], "confirmed")
        self.assertIn("paye_bands_resident", basis["legal_basis"])
        self.assertTrue(basis["sources"])


class PAYEFY2026Tests(unittest.TestCase):
    """FY2026-27 bands: threshold up to 335k, new 25% band 410k-485k."""

    def _paye(self, gross: float, **kwargs) -> dict:
        return ToolRegistry.call(
            "calculate_paye", {"monthly_gross": gross, "fiscal_year": FY26, **kwargs}
        )

    def test_raised_threshold_is_tax_free(self) -> None:
        self.assertEqual(self._paye(335_000)["paye"], 0.0)

    def test_ten_percent_band_starts_at_new_threshold(self) -> None:
        self.assertEqual(self._paye(400_000)["paye"], 6_500.0)  # 10% x 65,000

    def test_new_twenty_five_percent_band(self) -> None:
        # 10% x 75,000 = 7,500, then 25% x 40,000 = 10,000
        result = self._paye(450_000)
        self.assertEqual(result["paye"], 17_500.0)
        self.assertEqual(result["band"]["marginal_rate"], 0.25)

    def test_thirty_percent_band_resumes_above_485k(self) -> None:
        # 7,500 + 25% x 75,000 = 26,250, then 30% x 15,000 = 4,500
        self.assertEqual(self._paye(500_000)["paye"], 30_750.0)

    def test_provisional_table_warns(self) -> None:
        result = self._paye(1_000_000)
        self.assertEqual(result["rate_basis"]["status"], "provisional")
        self.assertIn("provisional", result["verification_warning"])

    def test_non_resident_bands_are_flagged_as_carried_forward(self) -> None:
        result = self._paye(12_000_000, residency="non_resident")
        self.assertIn("paye_bands_non_resident", result["rate_basis"]["carried_forward"])


class FiscalYearResolutionTests(unittest.TestCase):
    def test_as_of_selects_the_year_in_force(self) -> None:
        march = ToolRegistry.call(
            "calculate_paye", {"monthly_gross": 300_000, "as_of": "2026-03-01"}
        )
        july = ToolRegistry.call(
            "calculate_paye", {"monthly_gross": 300_000, "as_of": "2026-07-01"}
        )
        self.assertEqual(march["fiscal_year"], FY25)
        self.assertEqual(july["fiscal_year"], FY26)
        # The same salary is taxed under FY2025-26 and tax-free under
        # FY2026-27 — the whole point of dating the table.
        self.assertEqual(march["paye"], 6_500.0)
        self.assertEqual(july["paye"], 0.0)

    def test_unknown_fiscal_year_is_rejected_with_the_known_ones(self) -> None:
        result = ToolRegistry.call(
            "calculate_vat", {"amount": 1000, "fiscal_year": "FY1999-00"}
        )
        self.assertFalse(result["ok"])
        self.assertIn(FY25, result["known_fiscal_years"])

    def test_malformed_as_of_is_rejected(self) -> None:
        result = ToolRegistry.call("calculate_vat", {"amount": 1000, "as_of": "last July"})
        self.assertFalse(result["ok"])
        self.assertIn("ISO date", result["error"])


class VATCalculatorTests(unittest.TestCase):
    def test_add_direction(self) -> None:
        result = ToolRegistry.call("calculate_vat", {"amount": 1_000_000, "fiscal_year": FY25})
        self.assertTrue(result["ok"])
        self.assertEqual(result["vat"], 180_000.0)
        self.assertEqual(result["gross"], 1_180_000.0)

    def test_extract_direction(self) -> None:
        result = ToolRegistry.call(
            "calculate_vat", {"amount": 1_180_000, "direction": "extract", "fiscal_year": FY25}
        )
        self.assertEqual(result["net"], 1_000_000.0)
        self.assertEqual(result["vat"], 180_000.0)

    def test_decimal_amount_does_not_drift(self) -> None:
        # 0.1 + 0.2 arithmetic in binary floats leaks into rounded
        # totals; the Decimal path must reproduce the exact figure.
        result = ToolRegistry.call("calculate_vat", {"amount": 1234.56, "fiscal_year": FY25})
        self.assertEqual(result["vat"], 222.22)
        self.assertEqual(result["gross"], 1456.78)

    def test_percent_shaped_rate_is_rejected(self) -> None:
        result = ToolRegistry.call(
            "calculate_vat", {"amount": 1000, "rate": 18, "fiscal_year": FY25}
        )
        self.assertFalse(result["ok"])
        self.assertIn("decimal fraction", result["error"])

    def test_bad_direction_rejected(self) -> None:
        result = ToolRegistry.call(
            "calculate_vat", {"amount": 1000, "direction": "sideways", "fiscal_year": FY25}
        )
        self.assertFalse(result["ok"])


class VATRegistrationTests(unittest.TestCase):
    def test_threshold_rose_in_fy2026(self) -> None:
        args = {"annual_turnover": 200_000_000}
        self.assertTrue(
            ToolRegistry.call("check_vat_registration", {**args, "fiscal_year": FY25})[
                "registration_required"
            ]
        )
        self.assertFalse(
            ToolRegistry.call("check_vat_registration", {**args, "fiscal_year": FY26})[
                "registration_required"
            ]
        )

    def test_headroom_reported_when_below(self) -> None:
        result = ToolRegistry.call(
            "check_vat_registration", {"annual_turnover": 250_000_000, "fiscal_year": FY26}
        )
        self.assertEqual(result["headroom"], 50_000_000.0)


class RentalIncomeTaxTests(unittest.TestCase):
    def test_individual_above_threshold(self) -> None:
        result = ToolRegistry.call(
            "calculate_rental_tax", {"annual_gross_rent": 12_000_000, "fiscal_year": FY25}
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["landlord_type"], "individual")
        self.assertEqual(result["taxable_amount"], 9_180_000.0)
        self.assertEqual(result["tax"], 1_101_600.0)  # 12% x 9,180,000

    def test_individual_below_threshold_pays_nothing(self) -> None:
        result = ToolRegistry.call(
            "calculate_rental_tax", {"annual_gross_rent": 2_000_000, "fiscal_year": FY25}
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
                "fiscal_year": FY25,
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
                "fiscal_year": FY25,
            },
        )
        self.assertEqual(result["chargeable_income"], 40_000_000.0)
        self.assertEqual(result["tax"], 12_000_000.0)


class WithholdingTaxTests(unittest.TestCase):
    def test_services_rate(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding",
            {"payment_type": "services", "amount": 2_000_000, "fiscal_year": FY25},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["withholding_tax"], 120_000.0)  # 6%
        self.assertEqual(result["net_payable"], 1_880_000.0)

    def test_dividend_rate(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding",
            {"payment_type": "dividend", "amount": 1_000_000, "fiscal_year": FY25},
        )
        self.assertEqual(result["withholding_tax"], 150_000.0)  # 15%

    def test_fy2026_categories(self) -> None:
        for payment_type, expected in (
            ("royalty", 150_000.0),
            ("public_entertainer", 60_000.0),
            ("betting_winnings", 150_000.0),
            ("foreign_interest", 50_000.0),
        ):
            with self.subTest(payment_type=payment_type):
                result = ToolRegistry.call(
                    "calculate_withholding",
                    {"payment_type": payment_type, "amount": 1_000_000, "fiscal_year": FY26},
                )
                self.assertTrue(result["ok"], result.get("error"))
                self.assertEqual(result["withholding_tax"], expected)

    def test_fy2026_category_fails_closed_for_fy2025(self) -> None:
        # A category the 2026 amendments introduced must not be answered
        # at some other year's rate for a 2025 question.
        result = ToolRegistry.call(
            "calculate_withholding",
            {"payment_type": "royalty", "amount": 100_000, "fiscal_year": FY25},
        )
        self.assertFalse(result["ok"])
        self.assertIn("not defined", result["error"])
        self.assertIn(FY26, result["error"])

    def test_unknown_type_rejected(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding", {"payment_type": "vibes", "amount": 100_000}
        )
        self.assertFalse(result["ok"])
        self.assertIn("payment_type", result["error"])


class CustomsDutyTests(unittest.TestCase):
    def test_duty_and_vat_stack(self) -> None:
        result = ToolRegistry.call(
            "calculate_customs_duty", {"cif_value": 1_000_000, "fiscal_year": FY25}
        )
        self.assertEqual(result["duty"], 250_000.0)  # 25%
        self.assertEqual(result["vat"], 225_000.0)  # 18% of 1,250,000
        self.assertEqual(result["landed_cost"], 1_475_000.0)

    def test_used_clothing_levy_doubled_in_fy2026(self) -> None:
        args = {"cif_value": 1_000_000, "goods_category": "used_clothing", "include_vat": False}
        fy25 = ToolRegistry.call("calculate_customs_duty", {**args, "fiscal_year": FY25})
        fy26 = ToolRegistry.call("calculate_customs_duty", {**args, "fiscal_year": FY26})
        self.assertEqual(fy25["environmental_levy"], 150_000.0)  # 15% of CIF
        self.assertEqual(fy26["environmental_levy"], 300_000.0)  # 30% of CIF

    def test_levy_is_in_the_vat_base(self) -> None:
        result = ToolRegistry.call(
            "calculate_customs_duty",
            {"cif_value": 1_000_000, "goods_category": "used_clothing", "fiscal_year": FY26},
        )
        # VAT on CIF + duty (250,000) + levy (300,000)
        self.assertEqual(result["vat"], 279_000.0)


class CapitalGainsTests(unittest.TestCase):
    def test_gain_taxed_at_corporate_rate(self) -> None:
        result = ToolRegistry.call(
            "calculate_capital_gains",
            {"sale_price": 50_000_000, "cost_base": 20_000_000, "fiscal_year": FY25},
        )
        self.assertEqual(result["gain"], 30_000_000.0)
        self.assertEqual(result["tax"], 9_000_000.0)

    def test_loss_pays_nothing(self) -> None:
        result = ToolRegistry.call(
            "calculate_capital_gains",
            {"sale_price": 10_000_000, "cost_base": 20_000_000, "fiscal_year": FY25},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["tax"], 0.0)


if __name__ == "__main__":
    unittest.main()
