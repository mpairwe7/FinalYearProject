"""Tests for the effective-dated URA rate-table registry."""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.tax import money, tables


class RateTableLoadingTests(unittest.TestCase):
    def test_shipped_tables_load_and_are_ordered(self) -> None:
        years = tables.list_fiscal_years()
        self.assertIn("FY2025-26", years)
        self.assertIn("FY2026-27", years)
        self.assertEqual(years, sorted(years, key=lambda fy: tables.get_table(fy).effective_from))

    def test_every_table_declares_sources_and_a_status(self) -> None:
        for fy in tables.list_fiscal_years():
            table = tables.get_table(fy)
            with self.subTest(fiscal_year=fy):
                self.assertTrue(table.sources, f"{fy} has no sources")
                self.assertIn(table.status, (tables.STATUS_CONFIRMED, tables.STATUS_PROVISIONAL))

    def test_every_paye_band_set_is_contiguous_and_open_topped(self) -> None:
        for fy in tables.list_fiscal_years():
            table = tables.get_table(fy)
            for key, value in table.rates.items():
                if not key.startswith("paye_bands"):
                    continue
                with self.subTest(fiscal_year=fy, key=key):
                    self.assertEqual(value[0][0], 0)
                    self.assertIsNone(value[-1][1])
                    for earlier, later in zip(value, value[1:], strict=False):
                        self.assertEqual(earlier[1], later[0])


class BandLoadValidationTests(unittest.TestCase):
    """A malformed table must fail at load, not at the first taxpayer."""

    def _load(self, bands: list) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "FY2030-31.json"
            path.write_text(
                json.dumps(
                    {
                        "fiscal_year": "FY2030-31",
                        "effective_from": "2030-07-01",
                        "effective_to": "2031-06-30",
                        "status": "confirmed",
                        "rates": {"paye_bands_resident": bands},
                        "sources": [{"id": "x", "title": "test"}],
                    }
                )
            )
            tables._load_table(path)

    def test_gap_between_bands_is_rejected(self) -> None:
        with self.assertRaises(tables.RateTableError) as ctx:
            self._load([[0, 100, 0.0], [200, None, 0.1]])
        self.assertIn("contiguous", str(ctx.exception))

    def test_closed_top_band_is_rejected(self) -> None:
        with self.assertRaises(tables.RateTableError):
            self._load([[0, 100, 0.0], [100, 200, 0.1]])

    def test_out_of_range_rate_is_rejected(self) -> None:
        with self.assertRaises(tables.RateTableError):
            self._load([[0, 100, 0.0], [100, None, 1.5]])


class ResolutionTests(unittest.TestCase):
    def test_date_inside_a_year_resolves_to_it(self) -> None:
        self.assertEqual(tables.resolve_fiscal_year(dt.date(2025, 12, 1)), "FY2025-26")
        self.assertEqual(tables.resolve_fiscal_year(dt.date(2026, 7, 1)), "FY2026-27")

    def test_boundary_days_belong_to_the_right_year(self) -> None:
        self.assertEqual(tables.resolve_fiscal_year(dt.date(2026, 6, 30)), "FY2025-26")
        self.assertEqual(tables.resolve_fiscal_year(dt.date(2026, 7, 1)), "FY2026-27")

    def test_dates_outside_every_table_clamp_to_the_nearest(self) -> None:
        self.assertEqual(tables.resolve_fiscal_year(dt.date(2001, 1, 1)), "FY2025-26")
        self.assertEqual(tables.resolve_fiscal_year(dt.date(2099, 1, 1)), "FY2026-27")

    def test_unknown_year_names_the_known_ones(self) -> None:
        with self.assertRaises(tables.RateTableError) as ctx:
            tables.get_table("FY1999-00")
        self.assertIn("FY2025-26", str(ctx.exception))


class StrictModeTests(unittest.TestCase):
    def test_provisional_table_fails_closed_when_confirmed_is_required(self) -> None:
        # Every shipped table is confirmed, so the guard is exercised
        # against a synthetic provisional year rather than a real one.
        real = tables.get_table("FY2025-26")
        fake = dataclasses.replace(
            real,
            fiscal_year="FY2099-00",
            status=tables.STATUS_PROVISIONAL,
            verification_note="synthetic",
        )
        with mock.patch.object(tables, "_tables", {**tables._all_tables(), "FY2099-00": fake}), \
                mock.patch.dict(os.environ, {"TAX_RATES_REQUIRE_CONFIRMED": "true"}):
            tables.get_table("FY2025-26")  # confirmed — still fine
            with self.assertRaises(tables.RateTableError) as ctx:
                tables.get_table("FY2099-00")
        self.assertIn("provisional", str(ctx.exception))

    def test_every_shipped_table_is_confirmed(self) -> None:
        for fy in tables.list_fiscal_years():
            with self.subTest(fiscal_year=fy):
                self.assertTrue(tables.get_table(fy).confirmed)

    def test_production_requires_confirmed_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"APP_ENV": "production"}, clear=False):
            os.environ.pop("TAX_RATES_REQUIRE_CONFIRMED", None)
            self.assertTrue(tables.require_confirmed())

    def test_development_allows_provisional_by_default(self) -> None:
        with mock.patch.dict(os.environ, {"APP_ENV": "development"}, clear=False):
            os.environ.pop("TAX_RATES_REQUIRE_CONFIRMED", None)
            self.assertFalse(tables.require_confirmed())


class ProvenanceTests(unittest.TestCase):
    def test_provenance_cites_only_the_keys_used(self) -> None:
        basis = tables.get_table("FY2025-26").provenance("vat_standard")
        self.assertEqual(list(basis["legal_basis"]), ["vat_standard"])

    def test_unverified_keys_are_flagged_per_figure(self) -> None:
        # The table is confirmed as a whole; individual figures sourced
        # from secondary reporting are still called out.
        table = tables.get_table("FY2026-27")
        basis = table.provenance("vat_registration_threshold_annual")
        self.assertEqual(basis["status"], "confirmed")
        self.assertEqual(basis["unverified"], ["vat_registration_threshold_annual"])
        self.assertIn("not yet reconciled", basis["unverified_note"])

    def test_a_verified_key_carries_no_unverified_flag(self) -> None:
        basis = tables.get_table("FY2026-27").provenance("paye_bands_resident")
        self.assertNotIn("unverified", basis)

    def test_every_unverified_key_exists_in_the_table(self) -> None:
        for fy in tables.list_fiscal_years():
            table = tables.get_table(fy)
            for key in table.unverified:
                with self.subTest(fiscal_year=fy, key=key):
                    self.assertIn(key, table.rates)

    def test_compare_reports_the_2026_changes(self) -> None:
        threshold = tables.compare(
            "vat_registration_threshold_annual", older="FY2025-26", newer="FY2026-27"
        )
        self.assertTrue(threshold["changed"])
        self.assertEqual(threshold["newer"]["value"], 300_000_000)
        self.assertFalse(
            tables.compare("corporation_tax", older="FY2025-26", newer="FY2026-27")["changed"]
        )


class MoneyTests(unittest.TestCase):
    def test_bool_is_not_an_amount(self) -> None:
        with self.assertRaises(money.AmountError):
            money.to_decimal(True, field="amount")

    def test_infinity_is_rejected(self) -> None:
        with self.assertRaises(money.AmountError):
            money.to_decimal(float("inf"), field="amount")

    def test_float_does_not_pick_up_binary_expansion(self) -> None:
        self.assertEqual(str(money.to_decimal(1.15, field="amount")), "1.15")

    def test_bands_below_the_first_threshold_produce_no_tax(self) -> None:
        bands = tables.get_table("FY2026-27")["paye_bands_resident"]
        tax, breakdown = money.apply_bands(money.to_decimal(100, field="x"), bands)
        self.assertEqual(money.to_float(tax), 0.0)
        # The zero-rate band is still reported so a reply can show the
        # working ("the first UGX 335,000 is tax free").
        self.assertEqual([b["tax_in_band"] for b in breakdown], [0.0])

    def test_breakdown_stops_at_the_taxpayer_income(self) -> None:
        bands = tables.get_table("FY2026-27")["paye_bands_resident"]
        _tax, breakdown = money.apply_bands(money.to_decimal(450_000, field="x"), bands)
        self.assertEqual(breakdown[-1]["upper"], 485_000.0)
        self.assertEqual(breakdown[-1]["taxable_in_band"], 40_000.0)


if __name__ == "__main__":
    unittest.main()
