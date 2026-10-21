"""Unit tests for Phase B: live XE.com / Google Currency exchange-rate engine and scenario_planner FX chaining."""

from __future__ import annotations

import json
from typing import Any

import pytest

from app import web_fx
from app.case_facts import extract_case_facts
from app.flags import flags
from app.scenario_planner import build_verified_facts, plan_tools
from app.tools import ToolRegistry


@pytest.fixture(autouse=True)
def _reset_fx_cache():
    """Clear L1 FX cache before and after every test."""
    web_fx.clear_fx_cache()
    yield
    web_fx.clear_fx_cache()


def test_parse_xe_rate_variants() -> None:
    """Verify XE.com HTML parser handles JSON manifest, midMarketRate, initialRatesData, and text fallback."""
    html_manifest = '{"dataManifest":{"k":{"timestamp":1792594800000,"rates":{"USD":1,"UGX":3745.55}}}}'
    assert web_fx._parse_xe_rate(html_manifest, "USD", "UGX") == pytest.approx(3745.55)

    html_mid = '<script>{"toCurrency":"UGX","midMarketRate":"3,688.42"}</script>'
    assert web_fx._parse_xe_rate(html_mid, "USD", "UGX") == pytest.approx(3688.42)

    html_init = (
        '{"initialRatesData":{"timestamp":1792594800000,'
        '"rates":{"USD":1,"EUR":0.90,"UGX":3780.0}}}'
    )
    # 1 EUR = 3780.0 / 0.90 = 4200.0 UGX
    assert web_fx._parse_xe_rate(html_init, "EUR", "UGX") == pytest.approx(4200.0)

    html_text = "<p>1 USD equals 3,712.80 UGX using the current mid-market exchange rate.</p>"
    assert web_fx._parse_xe_rate(html_text, "USD", "UGX") == pytest.approx(3712.80)


def test_parse_google_finance_rate_variants() -> None:
    """Verify Google Finance parser handles data-last-price, AF_initDataCallback, and fxKbKc class."""
    html_attr = '<div data-last-price="3720.15" data-currency="UGX"></div>'
    assert web_fx._parse_google_finance_rate(html_attr, "USD", "UGX") == pytest.approx(3720.15)

    html_af = (
        "AF_initDataCallback({key: 'ds:2', data:[[[['/g/11bvvznfd1',null,"
        "'USD / UGX',3,null,[3755.42,12.1,0.32,4,4,2],null,3743.32]]]]});"
    )
    assert web_fx._parse_google_finance_rate(html_af, "USD", "UGX") == pytest.approx(3755.42)

    html_div = '<div class="YMlKec fxKbKc">3,710.50</div>'
    assert web_fx._parse_google_finance_rate(html_div, "USD", "UGX") == pytest.approx(3710.50)


def test_plausible_rate_bounds_reject_glitches() -> None:
    """Verify _is_plausible_ugx_rate rejects 0, 1.0, negative, NaN, and extreme outliers."""
    assert web_fx._is_plausible_ugx_rate("USD", 3750.0) is True
    assert web_fx._is_plausible_ugx_rate("USD", 4192.15) is True
    assert web_fx._is_plausible_ugx_rate("USD", 0.0) is False
    assert web_fx._is_plausible_ugx_rate("USD", 1.0) is False
    assert web_fx._is_plausible_ugx_rate("USD", -3700.0) is False
    assert web_fx._is_plausible_ugx_rate("USD", 999_999_999.0) is False
    assert web_fx._is_plausible_ugx_rate("UGX", 1.0) is True


def test_lookup_live_exchange_rate_cross_checks_xe_and_google() -> None:
    """When both XE.com and Google Finance respond within 2%, quote cites both."""

    def _fake_fetch(url: str) -> str:
        """Return mocked XE or Google HTML."""
        if "xe.com" in url:
            return '{"midMarketRate":"3,750.00"}'
        if "google.com/finance" in url:
            return '<div data-last-price="3755.00"></div>'
        raise AssertionError(f"Unexpected URL {url}")

    quote = web_fx.lookup_live_exchange_rate("USD", http_fetcher=_fake_fetch)
    assert quote is not None
    assert quote.is_live is True
    assert quote.rate_ugx == pytest.approx(3750.0)
    assert "XE.com / Google Currency" in quote.source


def test_lookup_live_exchange_rate_falls_back_to_open_er_and_offline() -> None:
    """When XE and Google fail, fallback to Open ER JSON mirror, then offline snapshot."""

    def _mirror_fetch(url: str) -> str:
        """Simulate XE/Google failure and Open ER success."""
        if "open.er-api.com" in url:
            return json.dumps({"result": "success", "rates": {"UGX": 3762.25}})
        raise RuntimeError("upstream timeout")

    quote = web_fx.lookup_live_exchange_rate("USD", http_fetcher=_mirror_fetch)
    assert quote is not None
    assert quote.is_live is True
    assert quote.rate_ugx == pytest.approx(3762.25)
    assert "Open Exchange Rates" in quote.source

    def _all_fail_fetch(url: str) -> str:
        """Simulate complete network outage."""
        raise RuntimeError("offline")

    offline_quote = web_fx.lookup_live_exchange_rate(
        "USD", http_fetcher=_all_fail_fetch, allow_offline_fallback=True
    )
    assert offline_quote is not None
    assert offline_quote.is_live is False
    assert "BOU_mid_rates_2026.json" in offline_quote.source

    assert (
        web_fx.lookup_live_exchange_rate(
            "USD", http_fetcher=_all_fail_fetch, allow_offline_fallback=False
        )
        is None
    )


def test_redis_and_l1_caching_and_ssrf_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify L1/L2 Redis cache round-trip and _default_http_fetcher URL allowlist."""
    store: dict[str, str] = {}

    class _FakeRedis:
        """Fake Redis client for testing L2 FX cache."""

        def get(self, key: str) -> str | None:
            """Get cached value."""
            return store.get(key)

        def setex(self, key: str, ttl: int, value: str) -> None:
            """Set cached value with TTL."""
            assert ttl == web_fx.FX_CACHE_TTL_SECONDS
            store[key] = value

    monkeypatch.setattr(web_fx, "_get_redis", lambda: _FakeRedis())
    q = web_fx.LiveFXQuote(
        currency="USD",
        rate_ugx=3750.0,
        source="XE.com / Google Currency",
        fetched_at_utc="2026-10-22T10:00:00Z",
        is_live=True,
    )
    web_fx._set_cached_quote("USD:UGX", q)
    assert "ura:fx:live:USD:UGX" in store

    # Clear L1 so _get_cached_quote reads from L2 Redis
    web_fx.clear_fx_cache()
    loaded = web_fx._get_cached_quote("USD:UGX")
    assert loaded is not None
    assert loaded.rate_ugx == 3750.0
    assert loaded.source == "XE.com / Google Currency"

    # SSRF / scheme guard on _default_http_fetcher
    with pytest.raises(ValueError, match="Disallowed FX URL"):
        web_fx._default_http_fetcher("http://www.xe.com/currencyconverter/")
    with pytest.raises(ValueError, match="Disallowed FX URL"):
        web_fx._default_http_fetcher("https://evil.example.com/fx")


def test_scenario1_expat_salary_computes_full_paye_on_turn1_with_live_fx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When live_web_research is enabled, $6,000/month salary converts via live FX and computes Resident & Non-Resident PAYE on Turn 1."""
    fake_quote = web_fx.LiveFXQuote(
        currency="USD",
        rate_ugx=3750.0,
        source="XE.com / Google Currency (https://www.xe.com/currencyconverter/convert/?Amount=1&From=USD&To=UGX)",
        fetched_at_utc="2026-10-22T09:30:00Z",
        is_live=True,
    )
    monkeypatch.setattr(
        web_fx,
        "lookup_live_exchange_rate",
        lambda currency, target="UGX", **kw: fake_quote if currency == "USD" else None,
    )
    flags.set("live_web_research", True)
    try:
        msg = (
            "We are hiring a foreign technical specialist on an 8-month contract in Entebbe. "
            "They will receive a gross monthly salary of $6,000 plus employer-paid housing and "
            "a company vehicle. How is PAYE calculated for a non-resident individual, and does "
            "the non-resident tax table differ from resident individuals?"
        )
        facts = extract_case_facts(msg)
        specs = plan_tools(msg, facts)
        fx_spec = next(s for s in specs if s.tool == "convert_currency")
        paye_spec = next(s for s in specs if s.tool == "calculate_paye")
        assert fx_spec.missing_inputs == []
        assert paye_spec.missing_inputs == []

        called_tools: list[str] = []

        def _caller(name: str, params: dict[str, Any]) -> dict[str, Any]:
            """Execute registered tool."""
            called_tools.append(name)
            return ToolRegistry.call(name, params)

        block = build_verified_facts(msg, facts, tool_caller=_caller)
        assert "convert_currency" in called_tools
        assert "calculate_paye" in called_tools
        # $6,000 * 3,750 = UGX 22,500,000
        assert "22,500,000" in block
        assert "Live FX: 1 USD = UGX 3,750.00 via XE.com / Google Currency" in block
        # Resident PAYE on 22,500,000 = 7,888,250; Non-Resident PAYE = 7,925,500; diff = 37,250
        assert "7,888,250" in block
        assert "7,925,500" in block
        assert "37,250" in block
        assert "exchange rate" not in block.split("Still needed for an exact figure:")[-1]
    finally:
        flags.clear("live_web_research")
