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
    """FY2026-27 bands, from Schedule 4 Part I as substituted by the 2026 Act.

    The Act states ANNUAL chargeable income; these are the monthly
    equivalents. Two cumulative figures appear verbatim in the Act's own
    table and are asserted below as an independent check that the
    annual-to-monthly conversion is right:

        UGX   180,000 of tax at   4,920,000 annual (= 410,000/month)
        UGX   405,000 of tax at   5,820,000 annual (= 485,000/month)
    """

    def _paye(self, gross: float, **kwargs) -> dict:
        return ToolRegistry.call(
            "calculate_paye", {"monthly_gross": gross, "fiscal_year": FY26, **kwargs}
        )

    def test_raised_threshold_is_tax_free(self) -> None:
        self.assertEqual(self._paye(335_000)["paye"], 0.0)

    def test_twenty_percent_band_starts_at_the_new_threshold(self) -> None:
        # Schedule 4 Part I: 20% of the excess over 4,020,000 annual.
        # Secondary summaries reported this band as 10%; the Act says 20%.
        self.assertEqual(self._paye(400_000)["paye"], 13_000.0)  # 20% x 65,000

    def test_annual_cumulative_at_the_second_boundary_matches_the_act(self) -> None:
        result = self._paye(410_000)
        self.assertEqual(result["annual_paye"], 180_000.0)

    def test_new_twenty_five_percent_band(self) -> None:
        # 20% x 75,000 = 15,000, then 25% x 40,000 = 10,000
        result = self._paye(450_000)
        self.assertEqual(result["paye"], 25_000.0)
        self.assertEqual(result["band"]["marginal_rate"], 0.25)

    def test_annual_cumulative_at_the_third_boundary_matches_the_act(self) -> None:
        result = self._paye(485_000)
        self.assertEqual(result["annual_paye"], 405_000.0)

    def test_thirty_percent_band_resumes_above_485k(self) -> None:
        # 15,000 + 25% x 75,000 = 33,750, then 30% x 15,000 = 4,500
        self.assertEqual(self._paye(500_000)["paye"], 38_250.0)

    def test_top_band_adds_the_additional_ten_percent(self) -> None:
        # Above 120,000,000 annual (10,000,000 monthly) the Act adds a
        # further 10%, giving an effective 40% marginal rate.
        self.assertEqual(self._paye(12_000_000)["band"]["marginal_rate"], 0.4)

    def test_confirmed_table_does_not_caveat_a_verified_figure(self) -> None:
        result = self._paye(1_000_000)
        self.assertEqual(result["rate_basis"]["status"], "confirmed")
        self.assertNotIn("verification_warning", result)

    def test_result_cites_the_amending_act(self) -> None:
        basis = self._paye(1_000_000)["rate_basis"]["legal_basis"]
        self.assertIn("Income Tax (Amendment) Act 2026", basis["paye_bands_resident"])

    def test_non_resident_bands_were_not_amended(self) -> None:
        # The Act substitutes only Part I, which applies to residents, so
        # Part II continues to apply and is not a stale carry-forward.
        result = self._paye(12_000_000, residency="non_resident")
        self.assertEqual(result["paye"], 3_725_500.0)
        self.assertNotIn("carried_forward", result["rate_basis"])
        self.assertIn("not amended", result["rate_basis"]["legal_basis"]["paye_bands_non_resident"])


class UnverifiedFigureTests(unittest.TestCase):
    """A confirmed table can still hold a figure from a secondary source."""

    def test_an_unverified_figure_is_caveated(self) -> None:
        result = ToolRegistry.call(
            "check_vat_registration",
            {"annual_turnover": 400_000_000, "fiscal_year": FY26},
        )
        self.assertTrue(result["ok"])
        self.assertIn("vat_registration_threshold_annual", result["rate_basis"]["unverified"])
        self.assertIn("not yet reconciled", result["verification_warning"])

    def test_a_verified_figure_in_the_same_table_is_not_caveated(self) -> None:
        result = ToolRegistry.call(
            "calculate_withholding",
            {"payment_type": "public_entertainer", "amount": 1_000_000, "fiscal_year": FY26},
        )
        self.assertNotIn("verification_warning", result)
        self.assertNotIn("unverified", result["rate_basis"])


class WithholdingFY2026Act(unittest.TestCase):
    """Rates introduced by the 2026 Act, each traced to its Schedule 4 Part."""

    def test_rates_match_the_act(self) -> None:
        for payment_type, rate in (
            ("public_entertainer", 0.06),   # Part XVI, s.135B
            ("betting_winnings", 0.15),     # Part XI, s.131
            ("telecom_commission", 0.10),   # Part XIII, s.133
            ("non_business_asset", 0.06),   # Part X item 4, s.130(3)
            ("foreign_interest", 0.05),     # Part 2 para 2A, s.82(5)
        ):
            with self.subTest(payment_type=payment_type):
                result = ToolRegistry.call(
                    "calculate_withholding",
                    {"payment_type": payment_type, "amount": 1_000_000, "fiscal_year": FY26},
                )
                self.assertTrue(result["ok"], result.get("error"))
                self.assertEqual(result["rate"], rate)

    def test_new_categories_fail_closed_for_the_previous_year(self) -> None:
        for payment_type in ("telecom_commission", "non_business_asset"):
            with self.subTest(payment_type=payment_type):
                result = ToolRegistry.call(
                    "calculate_withholding",
                    {"payment_type": payment_type, "amount": 1_000, "fiscal_year": FY25},
                )
                self.assertFalse(result["ok"])
                self.assertIn(FY26, result["error"])


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
