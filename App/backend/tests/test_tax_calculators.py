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


class TakeHomePayTests(unittest.TestCase):
    """`net_take_home` must not be read as a payslip figure it is not.

    PAYE alone leaves the employee's 5% NSSF contribution in the number,
    so a reply labelled "take-home" overstated actual pay for every
    formal-sector employee who asked.
    """

    def _paye(self, gross: float, **kwargs) -> dict:
        return ToolRegistry.call(
            "calculate_paye", {"monthly_gross": gross, "fiscal_year": FY26, **kwargs}
        )

    def test_paye_only_is_the_default_and_says_so(self) -> None:
        result = self._paye(1_000_000)
        self.assertFalse(result["nssf_included"])
        self.assertEqual(result["nssf_employee"], 0.0)
        self.assertEqual(result["net_take_home"], 1_000_000 - result["paye"])
        self.assertIn("net of PAYE only", result["deductions_note"])

    def test_nssf_is_deducted_after_paye_not_before(self) -> None:
        gross = 1_000_000.0
        without = self._paye(gross)
        with_nssf = self._paye(gross, include_nssf=True)
        # NSSF changes take-home but must not change the tax: the
        # employee's own contribution is not deductible against
        # employment income.
        self.assertEqual(with_nssf["paye"], without["paye"])
        self.assertEqual(with_nssf["nssf_employee"], 50_000.0)  # 5% x 1,000,000
        self.assertEqual(
            with_nssf["net_take_home"], gross - without["paye"] - 50_000.0
        )

    def test_nssf_contribution_is_cited_in_the_provenance(self) -> None:
        basis = self._paye(1_000_000, include_nssf=True)["rate_basis"]
        self.assertIn("nssf_employee_contribution", basis["legal_basis"])
        self.assertIn("not a URA tax", basis["legal_basis"]["nssf_employee_contribution"])

    def test_paye_only_result_omits_the_nssf_basis(self) -> None:
        basis = self._paye(1_000_000)["rate_basis"]
        self.assertNotIn("nssf_employee_contribution", basis.get("legal_basis", {}))


class ExciseDutyTests(unittest.TestCase):
    def test_mobile_money_cash_withdrawal(self) -> None:
        result = ToolRegistry.call(
            "calculate_excise_duty",
            {"excise_type": "mobile_money_withdrawal", "amount": 1_000_000, "fiscal_year": FY26},
        )
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["excise_duty"], 5_000.0)  # 0.5%
        self.assertIn("0.5%", result["explanation"])

    def test_telecom_data_and_voice(self) -> None:
        for etype in ("telecom_data", "telecom_voice"):
            result = ToolRegistry.call(
                "calculate_excise_duty",
                {"excise_type": etype, "amount": 50_000, "fiscal_year": FY26},
            )
            self.assertTrue(result["ok"])
            self.assertEqual(result["excise_duty"], 6_000.0)  # 12%

    def test_fuel_specific_rates(self) -> None:
        petrol = ToolRegistry.call(
            "calculate_excise_duty",
            {"excise_type": "fuel_petrol", "amount": 50, "fiscal_year": FY26},
        )
        self.assertEqual(petrol["excise_duty"], 87_500.0)  # 50 * 1750 (FY2026-27)

        diesel = ToolRegistry.call(
            "calculate_excise_duty",
            {"excise_type": "fuel_diesel", "amount": 100, "fiscal_year": FY26},
        )
        self.assertEqual(diesel["excise_duty"], 143_000.0)  # 100 * 1430 (FY2026-27)

        petrol_prev = ToolRegistry.call(
            "calculate_excise_duty",
            {"excise_type": "fuel_petrol", "amount": 50, "fiscal_year": "FY2025-26"},
        )
        self.assertEqual(petrol_prev["excise_duty"], 72_500.0)  # 50 * 1450 (FY2025-26)

    def test_beer_malt(self) -> None:
        result = ToolRegistry.call(
            "calculate_excise_duty",
            {"excise_type": "beer_malt", "amount": 100_000, "fiscal_year": FY26},
        )
        self.assertEqual(result["excise_duty"], 60_000.0)  # 60%


class CustomsDutyExtendedTests(unittest.TestCase):
    def test_raw_materials_zero_duty(self) -> None:
        result = ToolRegistry.call(
            "calculate_customs_duty",
            {"cif_value": 10_000_000, "goods_category": "raw_materials", "fiscal_year": FY26},
        )
        self.assertEqual(result["duty"], 0.0)
        self.assertEqual(result["vat"], 1_800_000.0)  # 18% of 10m
        self.assertEqual(result["landed_cost"], 11_800_000.0)

    def test_used_vehicle_levy_and_import_wht(self) -> None:
        result = ToolRegistry.call(
            "calculate_customs_duty",
            {
                "cif_value": 20_000_000,
                "goods_category": "used_vehicle_5_to_8_years",
                "include_wht": True,
                "fiscal_year": FY26,
            },
        )
        self.assertEqual(result["duty"], 5_000_000.0)  # 25% of 20m
        self.assertEqual(result["environmental_levy"], 7_000_000.0)  # 35% of 20m
        # VAT = (20m CIF + 5m duty + 7m levy) * 18% = 32m * 0.18 = 5,760,000
        self.assertEqual(result["vat"], 5_760_000.0)
        self.assertEqual(result["wht"], 1_200_000.0)  # 6% of 20m
        self.assertEqual(result["landed_cost"], 20_000_000 + 5_000_000 + 7_000_000 + 5_760_000 + 1_200_000)


class PhaseFResidencyBikFxAndRuleToolTests(unittest.TestCase):
    """Phase F.4 tests: expectations computed dynamically from rate-table values, never hardcoded literals."""

    def setUp(self) -> None:
        from app.tax.tables import get_table

        self.table = get_table(FY26)

    def test_determine_residency_edges_and_contract_months(self) -> None:
        thresh_days = int(self.table["residency_days_threshold"])
        thresh_avg = int(self.table["residency_avg_days_threshold"])

        # 0 days with secondary tests negative -> non_resident
        r0 = ToolRegistry.call(
            "determine_residency",
            {
                "days_in_uganda": 0,
                "permanent_home": False,
                "avg_days_current_and_two_prior": 0,
                "gov_employee_posted_abroad": False,
                "fiscal_year": FY26,
            },
        )
        self.assertTrue(r0["ok"])
        self.assertEqual(r0["status"], "non_resident")

        # threshold - 1 (182 days) with secondary tests negative -> non_resident
        r_below = ToolRegistry.call(
            "determine_residency",
            {
                "days_in_uganda": thresh_days - 1,
                "permanent_home": False,
                "avg_days_current_and_two_prior": 0,
                "gov_employee_posted_abroad": False,
                "fiscal_year": FY26,
            },
        )
        self.assertEqual(r_below["status"], "non_resident")

        # threshold - 1 (182 days) alone (secondary tests unknown) -> undetermined + likely_non_resident
        r_partial = ToolRegistry.call(
            "determine_residency", {"days_in_uganda": thresh_days - 1, "fiscal_year": FY26}
        )
        self.assertEqual(r_partial["status"], "undetermined")
        self.assertTrue(r_partial["likely_non_resident"])
        self.assertTrue(r_partial["missing_facts"])

        # exact threshold (183 days) -> resident
        r_exact = ToolRegistry.call(
            "determine_residency", {"days_in_uganda": thresh_days, "fiscal_year": FY26}
        )
        self.assertEqual(r_exact["status"], "resident")
        self.assertTrue(r_exact["likely_resident"])

        # 3-year average at threshold (122 days) -> resident
        r_avg = ToolRegistry.call(
            "determine_residency",
            {"avg_days_current_and_two_prior": thresh_avg, "fiscal_year": FY26},
        )
        self.assertEqual(r_avg["status"], "resident")

        # permanent home or government employee posted abroad -> resident
        self.assertEqual(
            ToolRegistry.call(
                "determine_residency", {"permanent_home": True, "fiscal_year": FY26}
            )["status"],
            "resident",
        )
        self.assertEqual(
            ToolRegistry.call(
                "determine_residency", {"gov_employee_posted_abroad": True, "fiscal_year": FY26}
            )["status"],
            "resident",
        )

        # contract_months=8 alone -> undetermined + likely_resident=True + missing_facts
        r_contract = ToolRegistry.call(
            "determine_residency",
            {
                "contract_months": 8,
                "period_start": "2026-03-01",
                "period_end": "2026-10-31",
                "fiscal_year": FY26,
            },
        )
        self.assertEqual(r_contract["status"], "undetermined")
        self.assertTrue(r_contract["likely_resident"])
        self.assertIn("days physically present in Uganda", r_contract["missing_facts"])
        self.assertEqual(r_contract["years_of_income_affected"], ["FY2025-26", "FY2026-27"])

    def test_value_benefit_in_kind_formulas_and_unverified_flags(self) -> None:
        from decimal import Decimal

        car_inc = Decimal(str(self.table["bik_motor_vehicle_inclusion_rate"]))
        car_dep = Decimal(str(self.table["bik_motor_vehicle_depreciation_rate"]))
        std_days = int(self.table["bik_days_in_year_standard"])
        housing_rate = Decimal(str(self.table["bik_housing_employment_income_rate"]))
        loan_thresh = Decimal(str(self.table["bik_loan_principal_threshold_ugx"]))
        loan_stat_rate = Decimal(str(self.table["bik_loan_statutory_interest_rate"]))
        de_minimis = Decimal(str(self.table["bik_de_minimis_monthly_ugx"]))

        # 1. Car in year 2 (years_since_first_provided=1)
        mv = Decimal("100000000")
        expected_car_annual = float(car_inc * (mv * (Decimal(1) - car_dep)) * Decimal(std_days) / Decimal(std_days))
        car_res = ToolRegistry.call(
            "value_benefit_in_kind",
            {
                "kind": "car",
                "market_value": float(mv),
                "years_since_first_provided": 1,
                "days_private_use": std_days,
                "days_in_year": std_days,
                "employee_payment": 0,
                "fiscal_year": FY26,
            },
        )
        self.assertTrue(car_res["ok"])
        self.assertTrue(car_res["verified"])
        self.assertAlmostEqual(car_res["annual_value"], expected_car_annual, places=2)
        self.assertEqual(car_res["direction"], "added_to_employment_income")

        # 2. Housing benefit -> lesser of (a) and 15% of (employment_income + (a)); verified=False
        monthly_rent = Decimal("4000000")
        annual_rent = monthly_rent * Decimal(12)
        annual_salary = Decimal("180000000")
        expected_housing_annual = float(min(annual_rent, housing_rate * (annual_salary + annual_rent)))
        housing_res = ToolRegistry.call(
            "value_benefit_in_kind",
            {
                "kind": "housing",
                "market_rent": float(monthly_rent),
                "employment_income_annual": float(annual_salary),
                "fiscal_year": FY26,
            },
        )
        self.assertTrue(housing_res["ok"])
        self.assertFalse(housing_res["verified"])
        self.assertIn("bik_housing_employment_income_rate", housing_res["rate_basis"]["unverified"])
        self.assertAlmostEqual(housing_res["annual_value"], expected_housing_annual, places=2)

        # 3. Low-interest loan at exact threshold (1,000,000) vs above threshold
        loan_at_thresh = ToolRegistry.call(
            "value_benefit_in_kind",
            {
                "kind": "low_interest_loan",
                "loan_principal": float(loan_thresh),
                "interest_rate_paid": 0.02,
                "fiscal_year": FY26,
            },
        )
        self.assertTrue(loan_at_thresh["ok"])
        self.assertEqual(loan_at_thresh["annual_value"], 0.0)
        self.assertTrue(loan_at_thresh["verified"])

        loan_above = ToolRegistry.call(
            "value_benefit_in_kind",
            {
                "kind": "low_interest_loan",
                "loan_principal": float(loan_thresh * 10),
                "interest_rate_paid": 0.02,
                "fiscal_year": FY26,
            },
        )
        expected_loan_annual = float((loan_thresh * 10) * (loan_stat_rate - Decimal("0.02")))
        self.assertFalse(loan_above["verified"])
        self.assertAlmostEqual(loan_above["annual_value"], expected_loan_annual, places=2)

        # 4. De-minimis boundary (< 10,000/month excluded, == 10,000/month included)
        below_dm = ToolRegistry.call(
            "value_benefit_in_kind",
            {"kind": "meals", "monthly_cost": float(de_minimis - 1), "fiscal_year": FY26},
        )
        self.assertTrue(below_dm["de_minimis_excluded"])
        self.assertEqual(below_dm["monthly_value"], 0.0)

        at_dm = ToolRegistry.call(
            "value_benefit_in_kind",
            {"kind": "meals", "monthly_cost": float(de_minimis), "fiscal_year": FY26},
        )
        self.assertFalse(at_dm["de_minimis_excluded"])
        self.assertEqual(at_dm["monthly_value"], float(de_minimis))

    def test_extended_paye_verified_vs_conditional_and_residency_comparison(self) -> None:
        from decimal import Decimal
        from app.tax.money import apply_bands

        gross = Decimal("20000000")
        car_monthly = Decimal("1000000")
        housing_monthly = Decimal("2500000")

        res_bands = self.table["paye_bands_resident"]
        nonres_bands = self.table["paye_bands_non_resident"]
        exp_verified, _ = apply_bands(gross + car_monthly, res_bands)
        exp_conditional, _ = apply_bands(gross + car_monthly + housing_monthly, res_bands)
        exp_nonres, _ = apply_bands(gross + car_monthly, nonres_bands)

        result = ToolRegistry.call(
            "calculate_paye",
            {
                "monthly_gross": float(gross),
                "residency": "resident",
                "compare_residency": True,
                "benefits": [
                    {"kind": "car", "monthly_value": float(car_monthly), "verified": True},
                    {"kind": "housing", "monthly_value": float(housing_monthly), "verified": False},
                ],
                "fiscal_year": FY26,
            },
        )
        self.assertTrue(result["ok"])
        # Verified PAYE excludes the unverified housing benefit
        self.assertEqual(result["paye"], float(exp_verified))
        self.assertEqual(result["paye_verified"], float(exp_verified))
        self.assertEqual(result["paye_conditional"], float(exp_conditional))
        self.assertEqual(result["unverified_benefits"], ["housing"])
        self.assertEqual(
            result["comparison"]["non_resident_minus_resident_monthly"],
            float(exp_nonres - exp_verified),
        )

    def test_convert_currency_asks_for_rate_when_missing_and_uses_user_rate(self) -> None:
        missing = ToolRegistry.call(
            "convert_currency", {"amount": 6000, "currency": "USD", "fiscal_year": FY26}
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["needs"], "exchange_rate")

        supplied = ToolRegistry.call(
            "convert_currency",
            {"amount": 6000, "currency": "USD", "exchange_rate": 3750, "fiscal_year": FY26},
        )
        self.assertTrue(supplied["ok"])
        self.assertEqual(supplied["ugx_amount"], 6000 * 3750)
        self.assertEqual(supplied["rate_source"], "user_supplied")

    def test_per_head_rule_tools_and_capital_loss_rate_key(self) -> None:
        # 1. objection_requirements
        dep_rate = float(self.table["tax_appeals_tribunal_deposit_rate"])
        obj = ToolRegistry.call(
            "objection_requirements", {"disputed_tax_ugx": 50_000_000, "fiscal_year": FY26}
        )
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["deposit_amount"], 50_000_000 * dep_rate)
        self.assertEqual(obj["objection_deadline_days"], int(self.table["objection_timeline_days"]))
        self.assertEqual(
            obj["tat_deadline_days"], int(self.table["tax_appeals_tribunal_deadline_days"])
        )

        # 2. penalty_late_filing (minimum > 2% on 3m)
        pen = ToolRegistry.call(
            "penalty_late_filing",
            {"tax_payable_ugx": 3_000_000, "months_late": 1, "fiscal_year": FY26},
        )
        self.assertTrue(pen["ok"])
        self.assertEqual(
            pen["penal_tax"],
            max(
                float(self.table["penal_tax_late_filing_minimum_ugx"]),
                3_000_000 * float(self.table["penal_tax_late_filing_monthly_rate"]),
            ),
        )

        # 3. vat_input_claim_eligibility (manual invoice disallowed)
        vat_elig = ToolRegistry.call(
            "vat_input_claim_eligibility",
            {"amount_net_ugx": 40_000_000, "has_efris_einvoice": False, "fiscal_year": FY26},
        )
        self.assertTrue(vat_elig["ok"])
        self.assertFalse(vat_elig["eligible"])
        self.assertEqual(vat_elig["claimable_input_vat"], 0.0)

        # 4. wht_non_resident_service (service vs employment exclusion)
        wht_srv = ToolRegistry.call(
            "wht_non_resident_service",
            {"amount_ugx": 30_000_000, "is_employment_contract": False, "fiscal_year": FY26},
        )
        self.assertTrue(wht_srv["wht_applicable"])
        self.assertEqual(
            wht_srv["withholding_tax"],
            30_000_000 * float(self.table["withholding_non_resident_service_contract"]),
        )
        wht_emp = ToolRegistry.call(
            "wht_non_resident_service",
            {"amount_ugx": 10_000_000, "is_employment_contract": True, "fiscal_year": FY26},
        )
        self.assertFalse(wht_emp["wht_applicable"])
        self.assertEqual(wht_emp["withholding_tax"], 0.0)

        # 5. calculate_capital_gains on capital loss includes 'rate' key (fixes HTTP 500 in format_calc_reply)
        from app.calculator_router import format_calc_reply

        cgt_loss = ToolRegistry.call(
            "calculate_capital_gains",
            {"sale_price": 100_000_000, "cost_base": 140_000_000, "fiscal_year": FY26},
        )
        self.assertTrue(cgt_loss["ok"])
        self.assertEqual(cgt_loss["tax"], 0.0)
        self.assertEqual(cgt_loss["rate"], float(self.table["capital_gains_corporate"]))
        self.assertIn("no CGT is payable", cgt_loss["explanation"])
        formatted = format_calc_reply("calculate_capital_gains", cgt_loss, [])
        self.assertIn("UGX 0", formatted)
