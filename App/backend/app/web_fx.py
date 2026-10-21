"""Live global exchange-rate lookup engine for Ugandan Shillings (UGX).

Fetches real-time mid-market exchange rates from:
1. XE.com Universal Currency Converter
2. Google Currency / Google Finance
3. Open Exchange Rates JSON mirror (fallback when HTML endpoints rate-limit)

All fetched rates are validated against plausible 2026 UGX bounds from
``BOU_mid_rates_2026.json`` and cached for 30 minutes in L1 memory + Redis.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from .tax.tables import DATA_DIR

logger = logging.getLogger(__name__)

FX_CACHE_TTL_SECONDS = int(os.getenv("FX_CACHE_TTL_SECONDS", "1800"))
FX_HTTP_TIMEOUT_SECONDS = float(os.getenv("FX_HTTP_TIMEOUT_SECONDS", "4.0"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
FX_REDIS_PREFIX = "ura:fx:live:"

SUPPORTED_FX_CURRENCIES: frozenset[str] = frozenset(
    {
        "USD",
        "EUR",
        "GBP",
        "KES",
        "TZS",
        "RWF",
        "AED",
        "CNY",
        "ZAR",
        "JPY",
        "INR",
        "CAD",
        "CHF",
        "AUD",
        "UGX",
    }
)

_ALLOWED_FX_HOSTS: frozenset[str] = frozenset(
    {
        "www.xe.com",
        "xe.com",
        "www.google.com",
        "google.com",
        "open.er-api.com",
    }
)

_DEFAULT_REFERENCE_RATES_UGX: dict[str, float] = {
    "USD": 3900.0,
    "EUR": 4350.0,
    "GBP": 5150.0,
    "KES": 30.0,
    "TZS": 1.5,
    "RWF": 2.75,
    "AED": 1060.0,
    "CNY": 570.0,
    "ZAR": 230.0,
    "JPY": 25.5,
    "INR": 44.0,
    "CAD": 2800.0,
    "CHF": 4650.0,
    "AUD": 2700.0,
    "UGX": 1.0,
}

_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class LiveFXQuote:
    """Validated exchange-rate quote for converting a foreign currency into UGX."""

    currency: str
    rate_ugx: float
    source: str
    fetched_at_utc: str
    is_live: bool


_CACHE_LOCK = threading.Lock()
_L1_FX_CACHE: dict[str, tuple[float, LiveFXQuote]] = {}
_redis_client: Any = None
_redis_init_attempted = False


def _utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_redis() -> Any:
    """Lazy-initialize optional Redis client for cross-worker FX caching."""
    global _redis_client, _redis_init_attempted
    if _redis_init_attempted:
        return _redis_client
    with _CACHE_LOCK:
        if _redis_init_attempted:
            return _redis_client
        _redis_init_attempted = True
        try:
            import redis  # type: ignore[import-untyped]

            client = redis.from_url(
                REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            client.ping()
            _redis_client = client
        except Exception:
            _redis_client = None
        return _redis_client


def clear_fx_cache() -> None:
    """Clear the L1 in-memory FX cache (used by tests and admin refresh)."""
    with _CACHE_LOCK:
        _L1_FX_CACHE.clear()


def _load_bou_fx_file() -> dict[str, Any]:
    """Load the local BOU_mid_rates_2026.json file."""
    fx_path = DATA_DIR / "fx" / "BOU_mid_rates_2026.json"
    if not fx_path.exists():
        return {}
    try:
        return json.loads(fx_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _reference_rate_for(currency: str) -> float | None:
    """Return the 2026 reference UGX mid-rate for bounds checking."""
    cur = (currency or "").strip().upper()
    if cur == "UGX":
        return 1.0
    data = _load_bou_fx_file()
    ref_map = data.get("reference_mid_rates_2026") or {}
    val = ref_map.get(cur)
    if isinstance(val, int | float) and float(val) > 0:
        return float(val)
    return _DEFAULT_REFERENCE_RATES_UGX.get(cur)


def _is_plausible_ugx_rate(currency: str, rate_ugx: float, *, tolerance: float = 0.35) -> bool:
    """Verify that *rate_ugx* is finite, positive, and within ±35% of the 2026 reference band."""
    cur = (currency or "").strip().upper()
    if not isinstance(rate_ugx, int | float) or math.isnan(rate_ugx) or math.isinf(rate_ugx):
        return False
    if rate_ugx <= 0:
        return False
    if cur == "UGX":
        return abs(rate_ugx - 1.0) < 1e-6
    ref = _reference_rate_for(cur)
    if ref is None or ref <= 0:
        return False
    low = ref * (1.0 - tolerance)
    high = ref * (1.0 + tolerance)
    return low <= float(rate_ugx) <= high


def _parse_xe_rate(html: str, currency: str, target: str = "UGX") -> float | None:
    """Extract the mid-market rate from XE.com Converter HTML or embedded JSON state."""
    if not html:
        return None
    cur = currency.strip().upper()
    tgt = target.strip().upper()

    # 1. Direct JSON pair manifest: "rates":{"USD":1,"UGX":4192.1550465981}
    pair_pat = re.compile(
        rf'"rates"\s*:\s*\{{\s*"{re.escape(cur)}"\s*:\s*1(?:\.0+)?\s*,\s*"{re.escape(tgt)}"\s*:\s*([0-9]+(?:\.[0-9]+)?)',
        re.IGNORECASE,
    )
    m = pair_pat.search(html)
    if m:
        try:
            val = float(m.group(1))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            pass

    # 2. Direct midMarketRate JSON field: "midMarketRate":"3,688.42"
    mid_pat = re.compile(r'"midMarketRate"\s*:\s*"([0-9,]+(?:\.[0-9]+)?)"')
    m = mid_pat.search(html)
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            pass

    # 3. Embedded initialRatesData JSON table (USD-based rates map)
    init_pat = re.compile(r'"initialRatesData"\s*:\s*\{\s*"timestamp"\s*:\s*\d+\s*,\s*"rates"\s*:\s*(\{[^}]+\})')
    m = init_pat.search(html)
    if m:
        try:
            rates_obj = json.loads(m.group(1))
            ugx_per_usd = float(rates_obj.get(tgt, 0))
            cur_per_usd = 1.0 if cur == "USD" else float(rates_obj.get(cur, 0))
            if ugx_per_usd > 0 and cur_per_usd > 0:
                val = ugx_per_usd / cur_per_usd
                if _is_plausible_ugx_rate(cur, val):
                    return round(val, 4)
        except Exception:
            pass

    # 4. Human-readable converter text: "1 USD = 3,688.42 UGX" or "1.00 USD = 3,688.42 UGX" or "1 USD equals 3,688.42 UGX"
    txt_pat = re.compile(
        rf"\b1(?:\.00)?\s*(?:{re.escape(cur)}|US\s*Dollars?)\s*(?:=|equals)\s*([0-9,]+(?:\.[0-9]+)?)\s*(?:<[^>]+>\s*)*{re.escape(tgt)}\b",
        re.IGNORECASE,
    )
    m = txt_pat.search(html)
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            pass

    return None


def _parse_google_finance_rate(html: str, currency: str, target: str = "UGX") -> float | None:
    """Extract the quote rate from Google Finance HTML (`data-last-price` or `AF_initDataCallback`)."""
    if not html:
        return None
    cur = currency.strip().upper()
    tgt = target.strip().upper()

    # 1. Explicit data-last-price attribute on quote container
    for m in re.finditer(r'data-last-price="([0-9,]+(?:\.[0-9]+)?)"', html):
        try:
            val = float(m.group(1).replace(",", ""))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            continue

    # 2. Google Finance AF_initDataCallback array: ["USD / UGX",3,null,[4203.89036,...]]
    af_pat = re.compile(
        rf'["\']{re.escape(cur)}\s*/\s*{re.escape(tgt)}["\']\s*,\s*\d+\s*,\s*null\s*,\s*\[\s*([0-9]+(?:\.[0-9]+)?)',
        re.IGNORECASE,
    )
    m = af_pat.search(html)
    if m:
        try:
            val = float(m.group(1))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            pass

    # 3. Primary quote display div class="YMlKec fxKbKc"
    div_pat = re.compile(r'class="[^"]*\bfxKbKc\b[^"]*"[^>]*>\s*([0-9,]+(?:\.[0-9]+)?)')
    m = div_pat.search(html)
    if m:
        try:
            val = float(m.group(1).replace(",", ""))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            pass

    return None


def _parse_open_er_api_rate(payload: str | dict[str, Any], currency: str, target: str = "UGX") -> float | None:
    """Extract the UGX rate from Open Exchange Rate API JSON response."""
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(data, dict):
            return None
        rates = data.get("rates") or {}
        raw = rates.get(target.strip().upper())
        if isinstance(raw, int | float):
            val = float(raw)
            if _is_plausible_ugx_rate(currency, val):
                return round(val, 4)
    except Exception:
        return None
    return None


def _default_http_fetcher(url: str) -> str:
    """Fetch an allowlisted HTTPS FX endpoint with strict timeout and hostname checks."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_FX_HOSTS:
        raise ValueError(f"Disallowed FX URL: {url}")
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cookie": "CONSENT=YES+cb.20230531-04-p0.en+FX+999",
    }
    with httpx.Client(timeout=FX_HTTP_TIMEOUT_SECONDS, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


def _get_cached_quote(cache_key: str) -> LiveFXQuote | None:
    """Read a cached LiveFXQuote from L1 memory or L2 Redis."""
    now = time.time()
    with _CACHE_LOCK:
        entry = _L1_FX_CACHE.get(cache_key)
        if entry is not None:
            expires_at, quote = entry
            if expires_at > now:
                return quote
            _L1_FX_CACHE.pop(cache_key, None)

    r_client = _get_redis()
    if r_client is not None:
        try:
            raw = r_client.get(f"{FX_REDIS_PREFIX}{cache_key}")
            if raw:
                obj = json.loads(raw)
                quote = LiveFXQuote(
                    currency=str(obj["currency"]),
                    rate_ugx=float(obj["rate_ugx"]),
                    source=str(obj["source"]),
                    fetched_at_utc=str(obj["fetched_at_utc"]),
                    is_live=bool(obj.get("is_live", True)),
                )
                with _CACHE_LOCK:
                    _L1_FX_CACHE[cache_key] = (now + FX_CACHE_TTL_SECONDS, quote)
                return quote
        except Exception:
            logger.debug("Redis FX cache read failed", exc_info=True)
    return None


def _set_cached_quote(cache_key: str, quote: LiveFXQuote) -> None:
    """Store a validated LiveFXQuote in L1 memory and L2 Redis."""
    now = time.time()
    with _CACHE_LOCK:
        _L1_FX_CACHE[cache_key] = (now + FX_CACHE_TTL_SECONDS, quote)
    r_client = _get_redis()
    if r_client is not None:
        try:
            r_client.setex(
                f"{FX_REDIS_PREFIX}{cache_key}",
                FX_CACHE_TTL_SECONDS,
                json.dumps(asdict(quote)),
            )
        except Exception:
            logger.debug("Redis FX cache write failed", exc_info=True)


def lookup_live_exchange_rate(
    currency: str,
    target: str = "UGX",
    *,
    date: str | None = None,
    http_fetcher: Callable[[str], str] | None = None,
    allow_offline_fallback: bool = True,
) -> LiveFXQuote | None:
    """Fetch and cross-verify a live exchange rate to UGX from XE.com and Google Currency."""
    cur = (currency or "").strip().upper()
    tgt = (target or "UGX").strip().upper()
    if not cur or cur not in SUPPORTED_FX_CURRENCIES or tgt != "UGX":
        return None
    if cur == "UGX":
        return LiveFXQuote(
            currency="UGX",
            rate_ugx=1.0,
            source="identity",
            fetched_at_utc=_utc_now_iso(),
            is_live=True,
        )

    cache_key = f"{cur}:{tgt}"
    if http_fetcher is None:
        cached = _get_cached_quote(cache_key)
        if cached is not None:
            return cached

    fetch = http_fetcher or _default_http_fetcher
    xe_url = f"https://www.xe.com/currencyconverter/convert/?Amount=1&From={cur}&To={tgt}"
    google_url = f"https://www.google.com/finance/quote/{cur}-{tgt}"
    er_url = f"https://open.er-api.com/v6/latest/{cur}"

    xe_rate: float | None = None
    google_rate: float | None = None

    try:
        xe_html = fetch(xe_url)
        xe_rate = _parse_xe_rate(xe_html, cur, tgt)
    except Exception:
        logger.debug("XE.com rate fetch failed for %s", cur, exc_info=True)

    try:
        google_html = fetch(google_url)
        google_rate = _parse_google_finance_rate(google_html, cur, tgt)
    except Exception:
        logger.debug("Google Finance rate fetch failed for %s", cur, exc_info=True)

    now_iso = _utc_now_iso()
    quote: LiveFXQuote | None = None

    if xe_rate is not None and google_rate is not None:
        rel_diff = abs(xe_rate - google_rate) / max(xe_rate, 1e-9)
        if rel_diff <= 0.02:
            quote = LiveFXQuote(
                currency=cur,
                rate_ugx=xe_rate,
                source=f"XE.com / Google Currency ({xe_url})",
                fetched_at_utc=now_iso,
                is_live=True,
            )
        else:
            quote = LiveFXQuote(
                currency=cur,
                rate_ugx=xe_rate,
                source=f"XE.com ({xe_url})",
                fetched_at_utc=now_iso,
                is_live=True,
            )
    elif xe_rate is not None:
        quote = LiveFXQuote(
            currency=cur,
            rate_ugx=xe_rate,
            source=f"XE.com ({xe_url})",
            fetched_at_utc=now_iso,
            is_live=True,
        )
    elif google_rate is not None:
        quote = LiveFXQuote(
            currency=cur,
            rate_ugx=google_rate,
            source=f"Google Currency ({google_url})",
            fetched_at_utc=now_iso,
            is_live=True,
        )
    else:
        try:
            er_body = fetch(er_url)
            er_rate = _parse_open_er_api_rate(er_body, cur, tgt)
            if er_rate is not None:
                quote = LiveFXQuote(
                    currency=cur,
                    rate_ugx=er_rate,
                    source=f"Open Exchange Rates ({er_url})",
                    fetched_at_utc=now_iso,
                    is_live=True,
                )
        except Exception:
            logger.debug("Open ER fallback fetch failed for %s", cur, exc_info=True)

    if quote is not None:
        if http_fetcher is None:
            _set_cached_quote(cache_key, quote)
        return quote

    if not allow_offline_fallback:
        return None

    # Offline fallback to BOU_mid_rates_2026.json
    fx_data = _load_bou_fx_file()
    rates_map = (fx_data.get("rates") or {}).get(cur)
    snap_val: float | None = None
    if isinstance(rates_map, int | float) and float(rates_map) > 0:
        snap_val = float(rates_map)
    elif isinstance(rates_map, dict) and rates_map:
        month_key = (date or "")[:7]
        v = rates_map.get(month_key) or rates_map.get("default")
        if isinstance(v, int | float) and float(v) > 0:
            snap_val = float(v)
    if snap_val is None:
        snap_val = _reference_rate_for(cur)

    if snap_val is not None and snap_val > 0:
        return LiveFXQuote(
            currency=cur,
            rate_ugx=float(snap_val),
            source="BOU_mid_rates_2026.json (offline reference snapshot)",
            fetched_at_utc=now_iso,
            is_live=False,
        )
    return None
