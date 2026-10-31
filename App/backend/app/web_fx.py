"""Indicative exchange rates for Ugandan Shillings (UGX).

The rate that counts for a return is the Bank of Uganda mid-exchange rate for
the transaction date (``fx_conversion_rule`` in the statutory tables). This
module's market quotes are a convenience for estimating before that rate is to
hand, and everything that shows one says so. :func:`official_bou_rate` is the
only source this module treats as official, and it takes precedence.

Quotes come from:
1. XE.com Universal Currency Converter
2. Google Currency / Google Finance
3. Open Exchange Rates JSON mirror (when the HTML endpoints give nothing)

A quote must clear three checks before anyone sees it: inside the plausibility
band, not older than the provider's own timestamp allows, and, when two sources
answered, in agreement. Two sources that disagree are a reason to ask the
taxpayer for the rate, not to pick one. Accepted quotes are cached for 30 minutes
in L1 memory + Redis.
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
FX_NEGATIVE_CACHE_TTL_SECONDS = int(os.getenv("FX_NEGATIVE_CACHE_TTL_SECONDS", "90"))
FX_HTTP_TIMEOUT_SECONDS = float(os.getenv("FX_HTTP_TIMEOUT_SECONDS", "3.0"))
#: Wall clock for one lookup across every source.
FX_LOOKUP_BUDGET_SECONDS = float(os.getenv("FX_LOOKUP_BUDGET_SECONDS", "6.0"))
#: Beyond this relative gap two sources are not describing the same market.
FX_MAX_SOURCE_DIVERGENCE = float(os.getenv("FX_MAX_SOURCE_DIVERGENCE", "0.02"))
#: A daily-updated mirror can be a long weekend old, and no older.
FX_MAX_PROVIDER_AGE_SECONDS = int(os.getenv("FX_MAX_PROVIDER_AGE_SECONDS", str(96 * 3600)))
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

_USER_AGENT = "ura-chatbot-fx/1.0"


@dataclass(frozen=True)
class LiveFXQuote:
    """Validated exchange-rate quote for converting a foreign currency into UGX."""

    currency: str
    rate_ugx: float
    source: str
    fetched_at_utc: str
    is_live: bool
    #: Two independent sources agreed. False means one source answered alone.
    corroborated: bool = False


_CACHE_LOCK = threading.Lock()
_L1_FX_CACHE: dict[str, tuple[float, LiveFXQuote | None]] = {}
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


def _count_quote(outcome: str) -> None:
    try:
        from .analytics import metrics

        metrics.inc("fx_quotes_total", labels={"outcome": outcome})
    except Exception:  # noqa: BLE001 - telemetry must never fail a conversion
        logger.debug("fx_quotes_total not recorded", exc_info=True)


def official_bou_rate(currency: str, date: str | None = None) -> float | None:
    """The Bank of Uganda mid-rate for *currency*, when an operator has loaded one.

    Only a snapshot whose ``status`` is ``verified`` qualifies: the file ships
    empty and ``unverified``, and a number nobody confirmed is not official
    because of the file it sits in. A dated transaction needs its own month's
    entry: an undated or default rate is no evidence of what applied on that day.
    """
    data = _load_bou_fx_file()
    if str(data.get("status") or "").lower() != "verified":
        return None
    rates = data.get("rates")
    entry = rates.get((currency or "").strip().upper()) if isinstance(rates, dict) else None
    if isinstance(entry, dict):
        month = (date or _utc_now_iso())[:7]
        entry = entry[month] if month in entry else (None if date else entry.get("default"))
    elif date:
        entry = None
    if isinstance(entry, int | float) and not isinstance(entry, bool) and float(entry) > 0:
        return float(entry)
    return None


def _reference_rate_for(currency: str) -> float | None:
    """The plausibility anchor for *currency*. A sanity bound, never a rate to quote."""
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

    # 1. Direct JSON pair manifest: "rates":{"USD":1,"UGX":3750.25}
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

    # 2. Direct midMarketRate JSON field bound to requested pair context
    mid_pat = re.compile(r'"midMarketRate"\s*:\s*"([0-9,]+(?:\.[0-9]+)?)"')
    for m in mid_pat.finditer(html):
        window = html[max(0, m.start() - 250) : min(len(html), m.end() + 250)]
        from_m = re.search(r'"fromCurrency"\s*:\s*"([A-Z]{3})"', window)
        to_m = re.search(r'"toCurrency"\s*:\s*"([A-Z]{3})"', window)
        if from_m and from_m.group(1).upper() != cur:
            continue
        if to_m and to_m.group(1).upper() != tgt:
            continue
        try:
            val = float(m.group(1).replace(",", ""))
            if _is_plausible_ugx_rate(cur, val):
                return round(val, 4)
        except ValueError:
            continue

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
    cur_alt = rf"{re.escape(cur)}|US\s*Dollars?" if cur == "USD" else re.escape(cur)
    txt_pat = re.compile(
        rf"\b1(?:\.00)?\s*(?:{cur_alt})\s*(?:=|equals)\s*([0-9,]+(?:\.[0-9]+)?)\s*(?:<[^>]+>\s*)*{re.escape(tgt)}\b",
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

    # 1. Explicit data-last-price attribute on quote container (verifying source/target if present)
    for m in re.finditer(r"<[^>]*\bdata-last-price=\"([0-9,]+(?:\.[0-9]+)?)\"[^>]*>", html):
        tag = m.group(0)
        src_m = re.search(r'\bdata-source="([A-Z]{3})"', tag, re.IGNORECASE)
        tgt_m = re.search(r'\bdata-(?:target|currency)="([A-Z]{3})"', tag, re.IGNORECASE)
        if src_m and src_m.group(1).upper() != cur:
            continue
        if tgt_m and tgt_m.group(1).upper() != tgt:
            continue
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
        if not isinstance(data, dict) or data.get("result", "success") != "success":
            return None
        # "Live" describes when it was fetched, not how old the figure is, and a
        # figure that cannot say how old it is cannot pass this check.
        updated = data.get("time_last_update_unix")
        if (
            isinstance(updated, bool)
            or not isinstance(updated, int | float)
            or time.time() - float(updated) > FX_MAX_PROVIDER_AGE_SECONDS
        ):
            logger.warning("Rejected a stale or undated FX mirror payload for %s", currency)
            _count_quote("stale_provider")
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
    """Fetch an allowlisted HTTPS FX endpoint, re-checking every redirect hop.

    Sent as what it is. No browser User-Agent and no pre-accepted consent
    cookie: a government service does not forge a person's consent to a third
    party's terms, and a source that refuses this request simply does not count.
    """
    headers = {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    current = url
    with httpx.Client(timeout=FX_HTTP_TIMEOUT_SECONDS, follow_redirects=False) as client:
        for _hop in range(3):
            parsed = urlparse(current)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_FX_HOSTS:
                raise ValueError(f"Disallowed FX URL: {current}")
            resp = client.get(current, headers=headers)
            if not resp.is_redirect:
                resp.raise_for_status()
                return resp.text
            current = str(resp.url.join(resp.headers.get("Location", "")))
    raise ValueError("Too many FX redirects")


def _check_cached_quote(cache_key: str) -> tuple[bool, LiveFXQuote | None]:
    """Read a cached LiveFXQuote (or negative-cache sentinel) from L1 memory or L2 Redis."""
    now = time.time()
    with _CACHE_LOCK:
        entry = _L1_FX_CACHE.get(cache_key)
        if entry is not None:
            expires_at, quote = entry
            if expires_at > now:
                return True, quote
            _L1_FX_CACHE.pop(cache_key, None)

    r_client = _get_redis()
    if r_client is not None:
        try:
            raw = r_client.get(f"{FX_REDIS_PREFIX}{cache_key}")
            if raw:
                obj = json.loads(raw)
                if obj.get("negative"):
                    with _CACHE_LOCK:
                        _L1_FX_CACHE[cache_key] = (now + FX_NEGATIVE_CACHE_TTL_SECONDS, None)
                    return True, None
                quote = LiveFXQuote(
                    currency=str(obj["currency"]),
                    rate_ugx=float(obj["rate_ugx"]),
                    source=str(obj["source"]),
                    fetched_at_utc=str(obj["fetched_at_utc"]),
                    is_live=bool(obj.get("is_live", True)),
                    corroborated=bool(obj.get("corroborated", False)),
                )
                with _CACHE_LOCK:
                    _L1_FX_CACHE[cache_key] = (now + FX_CACHE_TTL_SECONDS, quote)
                return True, quote
        except Exception:
            logger.debug("Redis FX cache read failed", exc_info=True)
    return False, None


def _get_cached_quote(cache_key: str) -> LiveFXQuote | None:
    """Read a cached LiveFXQuote from L1 memory or L2 Redis."""
    _, quote = _check_cached_quote(cache_key)
    return quote


def _set_cached_quote(cache_key: str, quote: LiveFXQuote | None) -> None:
    """Store a validated LiveFXQuote (or short-lived negative cache entry) in L1 memory and L2 Redis."""
    now = time.time()
    ttl = FX_CACHE_TTL_SECONDS if quote is not None else FX_NEGATIVE_CACHE_TTL_SECONDS
    with _CACHE_LOCK:
        _L1_FX_CACHE[cache_key] = (now + ttl, quote)
    r_client = _get_redis()
    if r_client is not None:
        try:
            payload = asdict(quote) if quote is not None else {"negative": True}
            r_client.setex(
                f"{FX_REDIS_PREFIX}{cache_key}",
                ttl,
                json.dumps(payload),
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
    """An indicative rate to UGX, cross-checked when more than one source answers.

    ``None`` means no quote cleared the checks, and the caller asks the taxpayer.
    A *date* outside the current month gets no market quote at all: today's rate
    is not evidence of an earlier one.
    """
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
    skip_network = bool(date) and str(date).strip()[:7] != _utc_now_iso()[:7]
    if http_fetcher is None and not skip_network:
        hit, cached = _check_cached_quote(cache_key)
        if hit:
            if cached is not None:
                return cached
            skip_network = True

    fetch = http_fetcher or _default_http_fetcher
    xe_url = f"https://www.xe.com/currencyconverter/convert/?Amount=1&From={cur}&To={tgt}"
    google_url = f"https://www.google.com/finance/quote/{cur}-{tgt}"
    er_url = f"https://open.er-api.com/v6/latest/{cur}"

    xe_rate: float | None = None
    google_rate: float | None = None
    deadline = time.monotonic() + FX_LOOKUP_BUDGET_SECONDS
    diverged = False

    if not skip_network:
        try:
            xe_html = fetch(xe_url)
            xe_rate = _parse_xe_rate(xe_html, cur, tgt)
        except Exception as exc:  # noqa: BLE001 - one source down is an expected state
            logger.info("XE.com rate unavailable for %s: %s", cur, type(exc).__name__)

        if time.monotonic() < deadline:
            try:
                google_html = fetch(google_url)
                google_rate = _parse_google_finance_rate(google_html, cur, tgt)
            except Exception as exc:  # noqa: BLE001 - one source down is an expected state
                logger.info("Google rate unavailable for %s: %s", cur, type(exc).__name__)

    now_iso = _utc_now_iso()
    quote: LiveFXQuote | None = None

    if xe_rate is not None and google_rate is not None:
        rel_diff = abs(xe_rate - google_rate) / max(xe_rate, 1e-9)
        if rel_diff <= FX_MAX_SOURCE_DIVERGENCE:
            quote = LiveFXQuote(
                currency=cur,
                rate_ugx=xe_rate,
                source=f"XE.com / Google Currency ({xe_url})",
                fetched_at_utc=now_iso,
                is_live=True,
                corroborated=True,
            )
        else:
            # Picking a side would present a guess as a measurement.
            diverged = True
            logger.warning("FX sources disagree on %s by %.1f%%; no quote served", cur, rel_diff * 100)
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
    elif not skip_network and time.monotonic() < deadline:
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
        except Exception as exc:  # noqa: BLE001 - one source down is an expected state
            logger.info("Open ER rate unavailable for %s: %s", cur, type(exc).__name__)

    if quote is not None:
        _count_quote("corroborated" if quote.corroborated else "single_source")
        if http_fetcher is None:
            _set_cached_quote(cache_key, quote)
        return quote

    _count_quote("diverged" if diverged else "unavailable")

    if http_fetcher is None and not skip_network:
        _set_cached_quote(cache_key, None)

    if not allow_offline_fallback:
        return None

    # The plausibility anchor is deliberately not a fallback here: it is a sanity
    # bound, and serving it would put an invented figure under the BoU's name.
    official = official_bou_rate(cur, date)
    if official is None:
        return None
    return LiveFXQuote(
        currency=cur,
        rate_ugx=official,
        source="BOU_mid_rates_2026.json (verified Bank of Uganda snapshot)",
        fetched_at_utc=now_iso,
        is_live=False,
    )
