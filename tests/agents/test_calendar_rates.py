"""Tests for the calendar and rates tools."""

from __future__ import annotations

import datetime as _dt


class TestCurrentDate:
    def test_returns_today(self, fresh_registry):
        r = fresh_registry.call("get_current_date", {})
        assert r["ok"] is True
        today = _dt.date.today()
        assert r["today"] == today.isoformat()
        assert r["day_of_week"] == today.strftime("%A")

    def test_fiscal_year_shape(self, fresh_registry):
        r = fresh_registry.call("get_current_date", {})
        fy = r["fiscal_year"]
        assert fy.startswith("FY")
        assert len(fy) == 9                    # FY2025-26
        assert fy[2:6].isdigit()
        assert fy[7:9].isdigit()

    def test_fiscal_year_boundary_july(self):
        """FY rolls over on July 1."""
        from app.tools.calendar import _fiscal_year_for
        assert _fiscal_year_for(_dt.date(2026, 6, 30)) == "FY2025-26"
        assert _fiscal_year_for(_dt.date(2026, 7, 1)) == "FY2026-27"
        # Mid-year
        assert _fiscal_year_for(_dt.date(2025, 10, 15)) == "FY2025-26"
        assert _fiscal_year_for(_dt.date(2026, 3, 31)) == "FY2025-26"

    def test_days_remaining_non_negative(self, fresh_registry):
        r = fresh_registry.call("get_current_date", {})
        assert r["days_into_fy"] >= 0
        assert r["days_remaining_in_fy"] >= 0
        # Total should be ~365 (± 1 for leap year)
        total = r["days_into_fy"] + r["days_remaining_in_fy"]
        assert 364 <= total <= 366


class TestNextDeadlines:
    def test_returns_up_to_requested_count(self, fresh_registry):
        """The tool returns min(limit, deadlines_in_horizon).

        The default 90-day horizon catches ~3 monthly deadlines; a
        wider horizon catches more.  So the count is bounded above
        by the requested limit, not strictly equal to it.
        """
        r = fresh_registry.call("get_next_deadlines",
                                {"limit": 5, "within_days": 180})
        assert r["ok"] is True
        assert 1 <= r["count"] <= 5
        assert len(r["deadlines"]) == r["count"]

    def test_default_limit_three(self, fresh_registry):
        # The default `limit` is 3, so the count is capped at 3 — but it may be
        # fewer when the default 90-day horizon happens to contain fewer
        # upcoming deadlines (e.g. when only two monthly due-dates fall inside
        # the window). Asserting `== 3` made this date-brittle; the contract is
        # an upper bound, matching test_returns_up_to_requested_count.
        r = fresh_registry.call("get_next_deadlines", {})
        assert r["ok"] is True
        assert 1 <= r["count"] <= 3
        assert len(r["deadlines"]) == r["count"]

    def test_deadlines_are_sorted_ascending(self, fresh_registry):
        r = fresh_registry.call("get_next_deadlines", {"limit": 5})
        dates = [d["date"] for d in r["deadlines"]]
        assert dates == sorted(dates)

    def test_deadlines_are_in_future(self, fresh_registry):
        r = fresh_registry.call("get_next_deadlines", {"limit": 5})
        today = _dt.date.today().isoformat()
        for d in r["deadlines"]:
            assert d["date"] >= today
            assert d["days_away"] >= 0

    def test_within_days_horizon_respected(self, fresh_registry):
        r = fresh_registry.call("get_next_deadlines",
                                {"limit": 10, "within_days": 10})
        for d in r["deadlines"]:
            assert d["days_away"] <= 10

    def test_limit_clamped_to_range(self, fresh_registry):
        r1 = fresh_registry.call("get_next_deadlines", {"limit": 999})
        assert r1["count"] <= 10   # hard cap in the tool
        r2 = fresh_registry.call("get_next_deadlines", {"limit": 0})
        assert r2["count"] >= 1    # minimum 1


class TestLookupRate:
    def test_vat_standard(self, fresh_registry):
        r = fresh_registry.call(
            "lookup_rate", {"tax_type": "vat_standard", "fiscal_year": "FY2025-26"}
        )
        assert r["ok"] is True
        assert r["rate"] == 0.18
        assert r["rate_pct"] == 18.0
        assert r["display_name"] == "VAT (standard rate)"
        assert r["fiscal_year"] == "FY2025-26"

    def test_corporation_tax(self, fresh_registry):
        r = fresh_registry.call("lookup_rate", {"tax_type": "corporation_tax"})
        assert r["rate"] == 0.30
        assert r["rate_pct"] == 30.0

    def test_unknown_tax_type_rejected(self, fresh_registry):
        r = fresh_registry.call("lookup_rate", {"tax_type": "fictional_tax"})
        assert r["ok"] is False
        assert "Unknown tax_type" in r["error"]
        assert "available" in r

    def test_non_scalar_rate_rejected(self, fresh_registry):
        """PAYE bands are a list, not a scalar — the tool should reject."""
        r = fresh_registry.call("lookup_rate", {"tax_type": "paye_bands_resident"})
        assert r["ok"] is False


class TestListAvailableRates:
    def test_returns_non_empty(self, fresh_registry):
        r = fresh_registry.call("list_available_rates", {})
        assert r["ok"] is True
        assert r["count"] >= 8

    def test_every_row_has_required_fields(self, fresh_registry):
        r = fresh_registry.call("list_available_rates", {})
        for row in r["rates"]:
            assert "tax_type" in row
            assert "display_name" in row
            assert "formatted" in row
            # Rows are either a rate (0-1) or a money threshold; a threshold
            # reported as a percentage would read as 30,000,000,000%.
            assert row["kind"] in ("rate", "amount")
            if row["kind"] == "rate":
                assert 0.0 <= row["rate"] <= 1.0
                assert "rate_pct" in row
            else:
                assert row["value"] > 1.0

    def test_vat_and_cit_present(self, fresh_registry):
        r = fresh_registry.call("list_available_rates", {})
        types = {row["tax_type"] for row in r["rates"]}
        assert "vat_standard" in types
        assert "corporation_tax" in types
        assert "withholding_services" in types

    def test_provisional_vs_confirmed_explanation(self, fresh_registry, monkeypatch):
        r = fresh_registry.call("list_available_rates", {"fiscal_year": "FY2025-26"})
        assert r["ok"] is True
        assert "All rates are verified under current statutory instruments." in r["explanation"]

        import dataclasses
        from app.tax import tables
        real_table = tables.get_table("FY2025-26")
        provisional_table = dataclasses.replace(real_table, status="provisional")
        monkeypatch.setattr("app.tools.rates._resolve", lambda *args, **kwargs: provisional_table)
        r_prov = fresh_registry.call("list_available_rates", {"fiscal_year": "FY2025-26"})
        assert r_prov["ok"] is True
        assert "These rates are provisional; verify them with URA before use." in r_prov["explanation"]
