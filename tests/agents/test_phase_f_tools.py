"""Mirror Phase F.4 calculator tests under tests/agents/ so CI coverage gates execute them."""

from __future__ import annotations

from App.backend.tests.test_tax_calculators import PhaseFResidencyBikFxAndRuleToolTests  # noqa: F401
from app.tools import ToolRegistry


def test_additional_per_head_calculators_fy2026() -> None:
    """Exercise presumptive/startup, stamp duty, DST, gaming, and advance vehicle tax tools."""
    pres = ToolRegistry.call(
        "calculate_presumptive_or_startup_tax",
        {"annual_turnover_ugx": 200_000_000, "investment_capital_ugx": 800_000_000, "fiscal_year": "FY2026-27"},
    )
    assert pres["ok"] is True
    assert pres["qualifies_for_presumptive"] is False
    assert pres["qualifies_for_startup_exemption"] is False

    sd_land = ToolRegistry.call(
        "calculate_stamp_duty",
        {"instrument_type": "property_transfer", "value_ugx": 150_000_000, "fiscal_year": "FY2026-27"},
    )
    assert sd_land["ok"] is True
    assert sd_land["stamp_duty"] == 1_500_000.0

    sd_mv = ToolRegistry.call(
        "calculate_stamp_duty",
        {"instrument_type": "motor_vehicle_transfer", "fiscal_year": "FY2026-27"},
    )
    assert sd_mv["ok"] is True
    assert sd_mv["stamp_duty"] == 200_000.0

    dst = ToolRegistry.call(
        "calculate_digital_services_tax",
        {"gross_uganda_revenue_ugx": 400_000_000, "fiscal_year": "FY2026-27"},
    )
    assert dst["ok"] is True
    assert dst["dst_tax"] == 20_000_000.0

    gaming = ToolRegistry.call(
        "calculate_gaming_tax",
        {"total_stakes_ugx": 500_000_000, "total_payouts_ugx": 350_000_000, "fiscal_year": "FY2026-27"},
    )
    assert gaming["ok"] is True
    assert gaming["operator_gaming_tax"] == 45_000_000.0

    adv_psv = ToolRegistry.call(
        "calculate_advance_vehicle_tax",
        {"vehicle_type": "passenger", "seats": 14, "fiscal_year": "FY2026-27"},
    )
    assert adv_psv["ok"] is True
    assert adv_psv["annual_advance_tax"] == 280_000.0

    adv_goods = ToolRegistry.call(
        "calculate_advance_vehicle_tax",
        {"vehicle_type": "goods", "tonnes": 10, "fiscal_year": "FY2026-27"},
    )
    assert adv_goods["ok"] is True
    assert adv_goods["annual_advance_tax"] == 500_000.0
