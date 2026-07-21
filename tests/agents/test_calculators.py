"""Arithmetic + edge-case tests for the 5 calculator tools."""

from __future__ import annotations

import pytest

from app.tools import ToolRegistry


# ---------------------------------------------------------------------------
# VAT
# ---------------------------------------------------------------------------
class TestVAT:
    def test_add_standard_rate(self, fresh_registry):
        r = fresh_registry.call("calculate_vat",
                                {"amount": 100_000, "direction": "add"})
        assert r["ok"] is True
        assert r["net"] == 100_000
        assert r["vat"] == 18_000
        assert r["gross"] == 118_000
        assert r["rate"] == 0.18
        assert r["fiscal_year"] == "FY2025-26"

    def test_extract_from_gross(self, fresh_registry):
        r = fresh_registry.call("calculate_vat",
                                {"amount": 118_000, "direction": "extract"})
        assert r["ok"] is True
        assert r["gross"] == 118_000
        assert r["net"] == 100_000
        assert r["vat"] == 18_000

    def test_extract_non_round(self, fresh_registry):
        """118000 / 1.18 = 100000 exactly; odd numbers should still sum back."""
        r = fresh_registry.call("calculate_vat",
                                {"amount": 12_345, "direction": "extract"})
        assert r["ok"] is True
        assert abs(r["net"] + r["vat"] - 12_345) < 0.01

    def test_custom_rate_override(self, fresh_registry):
        r = fresh_registry.call("calculate_vat",
                                {"amount": 1000, "direction": "add", "rate": 0.0})
        assert r["vat"] == 0
        assert r["gross"] == 1000

    def test_negative_amount_rejected(self, fresh_registry):
        r = fresh_registry.call("calculate_vat", {"amount": -100})
        assert r["ok"] is False
        assert "non-negative" in r["error"]

    def test_invalid_direction_rejected(self, fresh_registry):
        r = fresh_registry.call("calculate_vat",
                                {"amount": 100, "direction": "sideways"})
        assert r["ok"] is False
        assert "direction" in r["error"]


# ---------------------------------------------------------------------------
# PAYE — progressive bands must land on the right marginal rate
# ---------------------------------------------------------------------------
class TestPAYE:
    @pytest.mark.parametrize("gross,expected_paye,expected_band_rate", [
        (200_000,     0.0,        0.00),   # below 235k threshold (band 0)
        (300_000,     6_500.0,    0.10),   # 10% band  (300k-235k)*0.10 = 6500
        (370_000,     17_000.0,   0.20),   # 20% band: flat 0 + 20%*(370-335) = 7000? no — actually flat 10000 + 20%*(370-335) = 10000+7000 = 17000
        (500_000,     52_000.0,   0.30),   # 30% band: flat 25000 + 30%*(500-410) = 25000+27000 = 52000
        (2_000_000,   502_000.0,  0.30),   # 30% band: 25000 + 30%*(2M - 410k) = 25000 + 477000
        (1_500_000,   352_000.0,  0.30),   # 25000 + 30%*(1.5M - 410k) = 25000 + 327000
        # 40% band flat = 25,000 + 30% x (10M - 410k) = 2,902,000 (ITA Third
        # Schedule); 2,902,000 + 40% x (15M - 10M) = 4,902,000.
        (15_000_000,  4_902_000.0, 0.40),
    ])
    def test_resident_progressive_bands(self, fresh_registry, gross, expected_paye, expected_band_rate):
        r = fresh_registry.call("calculate_paye", {"monthly_gross": gross})
        assert r["ok"] is True
        assert r["monthly_gross"] == gross
        assert r["paye"] == expected_paye
        assert r["net_take_home"] == gross - expected_paye
        assert r["band"]["marginal_rate"] == expected_band_rate

    def test_residency_non_resident_differs(self, fresh_registry):
        """Non-resident rates start at 10% from 0 (no 235k threshold)."""
        r = fresh_registry.call("calculate_paye",
                                {"monthly_gross": 300_000, "residency": "non_resident"})
        assert r["ok"] is True
        assert r["residency"] == "non_resident"
        # Non-resident first band (0..335k) is flat 10% → 30000
        assert r["paye"] == 30_000

    def test_zero_gross(self, fresh_registry):
        r = fresh_registry.call("calculate_paye", {"monthly_gross": 0})
        assert r["ok"] is True
        assert r["paye"] == 0

    def test_negative_rejected(self, fresh_registry):
        r = fresh_registry.call("calculate_paye", {"monthly_gross": -1})
        assert r["ok"] is False

    def test_top_band_upper_is_none_not_inf(self, fresh_registry):
        """JSON response must not contain infinity (not valid JSON)."""
        import json
        r = fresh_registry.call("calculate_paye", {"monthly_gross": 50_000_000})
        assert r["ok"] is True
        assert r["band"]["upper"] is None
        # Round-trip through JSON
        json.dumps(r)


# ---------------------------------------------------------------------------
# Corporation tax
# ---------------------------------------------------------------------------
class TestCorporationTax:
    def test_straight_30_percent(self, fresh_registry):
        r = fresh_registry.call("calculate_corporation_tax",
                                {"chargeable_income": 100_000_000})
        assert r["ok"] is True
        assert r["tax"] == 30_000_000
        assert r["after_tax"] == 70_000_000
        assert r["rate"] == 0.30

    def test_zero_income(self, fresh_registry):
        r = fresh_registry.call("calculate_corporation_tax",
                                {"chargeable_income": 0})
        assert r["ok"] is True
        assert r["tax"] == 0

    def test_negative_rejected(self, fresh_registry):
        r = fresh_registry.call("calculate_corporation_tax",
                                {"chargeable_income": -1_000_000})
        assert r["ok"] is False


# ---------------------------------------------------------------------------
# Capital gains (corporate)
# ---------------------------------------------------------------------------
class TestCapitalGains:
    def test_straightforward_gain(self, fresh_registry):
        r = fresh_registry.call("calculate_capital_gains",
                                {"sale_price": 10_000_000, "cost_base": 4_000_000})
        assert r["ok"] is True
        assert r["gain"] == 6_000_000
        assert r["tax"] == 1_800_000   # 30% × 6M
        assert r["net_proceeds"] == 10_000_000 - 1_800_000

    def test_loss_returns_zero_tax(self, fresh_registry):
        r = fresh_registry.call("calculate_capital_gains",
                                {"sale_price": 3_000_000, "cost_base": 5_000_000})
        assert r["ok"] is True
        assert r["tax"] == 0
        assert r["gain"] == -2_000_000

    def test_exact_break_even(self, fresh_registry):
        r = fresh_registry.call("calculate_capital_gains",
                                {"sale_price": 5_000_000, "cost_base": 5_000_000})
        assert r["ok"] is True
        assert r["tax"] == 0

    def test_negative_inputs_rejected(self, fresh_registry):
        r = fresh_registry.call("calculate_capital_gains",
                                {"sale_price": -1, "cost_base": 100})
        assert r["ok"] is False


# ---------------------------------------------------------------------------
# Customs duty
# ---------------------------------------------------------------------------
class TestCustomsDuty:
    def test_cif_plus_duty_plus_vat_landed_cost(self, fresh_registry):
        """CIF 1M + 10% duty = 100k + 18% VAT on (1M + 100k) = 198k → 1.298M."""
        r = fresh_registry.call("calculate_customs_duty",
                                {"cif_value": 1_000_000, "duty_rate": 0.1})
        assert r["ok"] is True
        assert r["cif_value"] == 1_000_000
        assert r["duty"] == 100_000
        assert r["vat"] == 198_000
        assert r["landed_cost"] == 1_298_000

    def test_without_vat(self, fresh_registry):
        r = fresh_registry.call("calculate_customs_duty",
                                {"cif_value": 1_000_000, "duty_rate": 0.25,
                                 "include_vat": False})
        assert r["ok"] is True
        assert r["vat"] == 0
        assert r["landed_cost"] == 1_250_000

    def test_zero_duty_rate(self, fresh_registry):
        r = fresh_registry.call("calculate_customs_duty",
                                {"cif_value": 1_000_000, "duty_rate": 0.0})
        assert r["duty"] == 0
        # Just the VAT on the CIF
        assert r["vat"] == 180_000
        assert r["landed_cost"] == 1_180_000

    def test_default_duty_rate_25pct(self, fresh_registry):
        """Default duty_rate comes from FY rate table = 0.25."""
        r = fresh_registry.call("calculate_customs_duty",
                                {"cif_value": 100_000})
        assert r["duty_rate"] == 0.25
        assert r["duty"] == 25_000

    def test_negative_cif_rejected(self, fresh_registry):
        r = fresh_registry.call("calculate_customs_duty",
                                {"cif_value": -1000})
        assert r["ok"] is False
