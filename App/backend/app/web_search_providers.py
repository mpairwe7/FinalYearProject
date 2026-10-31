"""Web-search backends behind one narrow contract, with failover.

A taxpayer's question about a waiver announced this week cannot wait for the
nightly crawl, so the research step needs a search index. What it must not have
is a single unsupported dependency: the original integration parsed
DuckDuckGo's HTML while presenting a browser User-Agent. That has no API
contract and no availability commitment, and a bot-challenge page parsed into
an empty result list indistinguishable from "URA has published nothing".

Three things follow from that, and this module is where they are enforced:

* A provider's job ends at :class:`RawResult`. Authority, injection screening
  and recency are decided once, in :mod:`app.web_research`, so a new backend
  cannot weaken them. A provider's own generated answer is never requested: it
  is prose with no page behind it.
* Failure is a distinct outcome. :class:`ProviderError` names why a backend
  failed, and :func:`search_with_failover` reports ``unavailable`` rather than
  an empty list, because the caller owes the taxpayer a different sentence.
* The unsupported HTML path is not available in production at all
  (:func:`unsanctioned_allowed`), whatever the environment says.

Only the closed-vocabulary query from :func:`app.web_research.build_search_queries`
reaches this module. Every hosted provider here is an external processor, so
:func:`hosted_provider_names` feeds the cross-border gate in
:mod:`app.production_readiness` (G35).
"""

from __future__ import annotations

import html as _html
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .resilience import CircuitBreaker

logger = logging.getLogger(__name__)

WEB_SEARCH_TIMEOUT_SECONDS = float(os.getenv("WEB_SEARCH_TIMEOUT_SECONDS", "3.5"))
_USER_AGENT = "ura-chatbot-research/1.0"

ALLOWED_SEARCH_ENGINE_HOSTS: frozenset[str] = frozenset(
    {
        "html.duckduckgo.com",
        "lite.duckduckgo.com",
        "duckduckgo.com",
    }
)

#: Provider order when ``WEB_SEARCH_PROVIDERS`` is unset. Each entry is used
#: only once it is configured, so an unconfigured deployment has no provider.
DEFAULT_PROVIDER_ORDER: tuple[str, ...] = ("tavily", "brave", "searxng")

#: The recency horizon runs a little past a year, and no provider has a coarser
#: window than "past year". Without a filter old notices crowd out recent ones;
#: anything older that still comes back is labelled at admission.
_LONGEST_YEAR_WINDOW_DAYS = 400

_DDG_ANOMALY_RE = re.compile(r"anomaly-modal|bots\s{1,4}use\s{1,4}duckduckgo", re.IGNORECASE)
_DDG_ANCHOR_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_DDG_SNIPPET_RE = re.compile(
    r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>'
    r'|<div[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


class ProviderError(RuntimeError):
    """A backend could not answer. ``kind`` is a bounded metric label."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


@dataclass(frozen=True)
class SearchRequest:
    """One search, in both the forms a backend might need."""

    query: str  # with operators: ``site:ura.go.ug penalty waiver``
    keywords: str  # the same terms, without operators
    domains: tuple[str, ...] = ()
    max_results: int = 8
    freshness_days: int | None = None


@dataclass(frozen=True)
class RawResult:
    """A provider's result before any authority or safety decision."""

    title: str
    url: str
    snippet: str
    published_at: str = ""


class SearchProvider(Protocol):
    """A backend. ``sanctioned`` means a documented API exists to call."""

    name: str
    sanctioned: bool

    def configured(self) -> bool: ...

    def search(self, request: SearchRequest, timeout: float) -> list[RawResult]: ...


def strip_markup(text: Any) -> str:
    """Plain text from a result field: entities decoded, tags and runs of space removed."""
    plain = _TAG_RE.sub(" ", _html.unescape(str(text or "")))
    return " ".join(plain.split())


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    if code in (401, 403):
        raise ProviderError("auth")
    # Tavily answers plan and capacity limits with 432-434 (documented).
    if code in (429, 432, 433, 434):
        raise ProviderError("rate_limited")
    if code >= 400:
        raise ProviderError("upstream")


def _json_body(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as exc:
        raise ProviderError("malformed") from exc
    if not isinstance(body, dict):
        raise ProviderError("malformed")
    return body


class TavilyProvider:
    """``POST https://api.tavily.com/search``, domain-restricted server-side."""

    name = "tavily"
    sanctioned = True
    endpoint = "https://api.tavily.com/search"

    def configured(self) -> bool:
        return bool((os.getenv("TAVILY_API_KEY") or "").strip())

    @staticmethod
    def _time_range(days: int | None) -> str:
        if days is None:
            return ""
        if days <= 1:
            return "day"
        if days <= 7:
            return "week"
        if days <= 31:
            return "month"
        return "year" if days <= _LONGEST_YEAR_WINDOW_DAYS else ""

    def search(self, request: SearchRequest, timeout: float) -> list[RawResult]:
        body: dict[str, Any] = {
            "query": request.keywords or request.query,
            "search_depth": "basic",
            "max_results": min(request.max_results, 20),
            "include_answer": False,
            "include_raw_content": False,
            "include_published_date": True,
        }
        if request.domains:
            body["include_domains"] = list(request.domains)
        time_range = self._time_range(request.freshness_days)
        if time_range:
            body["time_range"] = time_range
        headers = {
            "Authorization": f"Bearer {(os.getenv('TAVILY_API_KEY') or '').strip()}",
            "User-Agent": _USER_AGENT,
        }
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(self.endpoint, json=body, headers=headers)
        _raise_for_status(response)
        rows = _json_body(response).get("results")
        if not isinstance(rows, list):
            raise ProviderError("malformed")
        return [
            RawResult(
                title=strip_markup(row.get("title")),
                url=str(row.get("url") or "").strip(),
                snippet=strip_markup(row.get("content")),
                published_at=str(row.get("published_date") or "").strip(),
            )
            for row in rows
            if isinstance(row, dict)
        ]


class BraveProvider:
    """``GET https://api.search.brave.com/res/v1/web/search``; operators stay in ``q``."""

    name = "brave"
    sanctioned = True
    endpoint = "https://api.search.brave.com/res/v1/web/search"

    def configured(self) -> bool:
        return bool((os.getenv("BRAVE_SEARCH_API_KEY") or "").strip())

    @staticmethod
    def _freshness(days: int | None) -> str:
        if days is None:
            return ""
        if days <= 1:
            return "pd"
        if days <= 7:
            return "pw"
        if days <= 31:
            return "pm"
        return "py" if days <= _LONGEST_YEAR_WINDOW_DAYS else ""

    def search(self, request: SearchRequest, timeout: float) -> list[RawResult]:
        params: dict[str, Any] = {
            "q": request.query,
            "count": min(request.max_results, 20),
            "search_lang": "en",
        }
        freshness = self._freshness(request.freshness_days)
        if freshness:
            params["freshness"] = freshness
        headers = {
            "X-Subscription-Token": (os.getenv("BRAVE_SEARCH_API_KEY") or "").strip(),
            "Accept": "application/json",
            "User-Agent": _USER_AGENT,
        }
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(self.endpoint, params=params, headers=headers)
        _raise_for_status(response)
        web = _json_body(response).get("web")
        # Brave omits ``web`` when a query has no web results.
        rows = web.get("results") if isinstance(web, dict) else []
        if not isinstance(rows, list):
            raise ProviderError("malformed")
        return [
            RawResult(
                title=strip_markup(row.get("title")),
                url=str(row.get("url") or "").strip(),
                snippet=strip_markup(row.get("description")),
                published_at=str(row.get("page_age") or "").strip(),
            )
            for row in rows
            if isinstance(row, dict)
        ]


class SearxngProvider:
    """A SearXNG instance the operator runs (``GET {SEARXNG_URL}/search?format=json``).

    The instance must have the JSON format enabled. Its documentation does not
    specify the JSON response, so unknown shapes fail as ``malformed`` rather
    than being read loosely; confirm against the deployed instance.
    """

    name = "searxng"
    sanctioned = True

    @staticmethod
    def _base() -> str:
        return (os.getenv("SEARXNG_URL") or "").strip().rstrip("/")

    def configured(self) -> bool:
        return bool(self._base())

    @staticmethod
    def _time_range(days: int | None) -> str:
        if days is None:
            return ""
        if days <= 1:
            return "day"
        if days <= 31:
            return "month"
        return "year" if days <= _LONGEST_YEAR_WINDOW_DAYS else ""

    def search(self, request: SearchRequest, timeout: float) -> list[RawResult]:
        params: dict[str, Any] = {"q": request.query, "format": "json", "language": "en"}
        time_range = self._time_range(request.freshness_days)
        if time_range:
            params["time_range"] = time_range
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(
                f"{self._base()}/search",
                params=params,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
        _raise_for_status(response)
        rows = _json_body(response).get("results")
        if not isinstance(rows, list):
            raise ProviderError("malformed")
        return [
            RawResult(
                title=strip_markup(row.get("title")),
                url=str(row.get("url") or "").strip(),
                snippet=strip_markup(row.get("content")),
                published_at=str(row.get("publishedDate") or "").strip(),
            )
            for row in rows
            if isinstance(row, dict)
        ]


def unwrap_ddg_url(raw_href: str) -> str:
    """Extract the target URL from a DuckDuckGo result link."""
    href = _html.unescape(str(raw_href or "").strip())
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if host in ALLOWED_SEARCH_ENGINE_HOSTS:
        target = parse_qs(parsed.query).get("uddg")
        if target and target[0]:
            return unquote(target[0])
    return href


def parse_ddg_html(html_body: str) -> list[RawResult]:
    """Results from DuckDuckGo's HTML page, each paired with *its own* snippet.

    A snippet is only ever looked for between its anchor and the next one. The
    two were once collected as separate lists and zipped by position, so one
    result without a snippet shifted the rest and text from a blocked foreign
    authority was shown against a ura.go.ug link (G313).
    """
    if not html_body:
        return []
    anchors = list(_DDG_ANCHOR_RE.finditer(html_body))
    results: list[RawResult] = []
    for index, anchor in enumerate(anchors):
        end = anchors[index + 1].start() if index + 1 < len(anchors) else len(html_body)
        snippet_match = _DDG_SNIPPET_RE.search(html_body, anchor.end(), end)
        snippet = (snippet_match.group(1) or snippet_match.group(2) or "") if snippet_match else ""
        results.append(
            RawResult(
                title=strip_markup(anchor.group(2)),
                url=unwrap_ddg_url(anchor.group(1)),
                snippet=strip_markup(snippet),
            )
        )
    return results


class DuckDuckGoHtmlProvider:
    """Development fallback: an HTML page with no API contract. Never in production."""

    name = "duckduckgo_html"
    sanctioned = False
    endpoint = "https://html.duckduckgo.com/html/"

    def configured(self) -> bool:
        return unsanctioned_allowed()

    def search(self, request: SearchRequest, timeout: float) -> list[RawResult]:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(
                self.endpoint,
                params={"q": request.query},
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
        _raise_for_status(response)
        if response.status_code != 200:
            raise ProviderError("blocked")
        body = response.text
        if _DDG_ANOMALY_RE.search(body):
            raise ProviderError("blocked")
        return parse_ddg_html(body)


_PROVIDERS: dict[str, SearchProvider] = {
    provider.name: provider
    for provider in (
        TavilyProvider(),
        BraveProvider(),
        SearxngProvider(),
        DuckDuckGoHtmlProvider(),
    )
}
_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKER_LOCK = threading.Lock()


def unsanctioned_allowed() -> bool:
    """Whether the HTML fallback may run: never in production, opt-out elsewhere."""
    from .production_readiness import is_production

    if is_production():
        return False
    return (os.getenv("WEB_SEARCH_ALLOW_UNSANCTIONED") or "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _provider_order() -> list[str]:
    raw = (os.getenv("WEB_SEARCH_PROVIDERS") or "").strip()
    if raw:
        return [name.strip().lower() for name in raw.split(",") if name.strip()]
    return [*DEFAULT_PROVIDER_ORDER, DuckDuckGoHtmlProvider.name]


def active_providers() -> list[SearchProvider]:
    """Configured providers in failover order."""
    return [
        _PROVIDERS[name]
        for name in _provider_order()
        if name in _PROVIDERS and _PROVIDERS[name].configured()
    ]


def hosted_provider_names() -> list[str]:
    """Hosted APIs a query would leave the deployment for (G35 cross-border gate)."""
    return [
        provider.name
        for provider in active_providers()
        if provider.name in (TavilyProvider.name, BraveProvider.name)
    ]


def sanctioned_provider_configured() -> bool:
    """True when at least one documented search API is ready to serve."""
    return any(provider.sanctioned for provider in active_providers())


def _breaker(name: str) -> CircuitBreaker:
    with _BREAKER_LOCK:
        breaker = _BREAKERS.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name=f"web_search_{name}",
                failure_threshold=3,
                reset_timeout=30.0,
                max_timeout=600.0,
            )
            _BREAKERS[name] = breaker
        return breaker


def reset_breakers() -> None:
    """Forget provider health (tests and operator recovery)."""
    with _BREAKER_LOCK:
        _BREAKERS.clear()


def _count(provider: str, outcome: str) -> None:
    try:
        from .analytics import metrics

        metrics.inc(
            "web_search_requests_total",
            labels={"provider": provider, "outcome": outcome},
        )
    except Exception:  # noqa: BLE001 - telemetry must never fail a turn
        logger.debug("web_search_requests_total not recorded", exc_info=True)


@dataclass(frozen=True)
class ChainResult:
    """What the failover chain produced. ``status`` decides the taxpayer's sentence."""

    status: str  # "ok" | "empty" | "unavailable"
    results: tuple[RawResult, ...] = ()
    provider: str = ""


def search_with_failover(
    request: SearchRequest,
    *,
    deadline: float,
    providers: Iterable[SearchProvider] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ChainResult:
    """First answer from the chain, within *deadline* (a ``clock`` instant).

    A healthy provider returning nothing is an answer, so it ends the chain.
    Only failure moves on: breaking that rule would turn "nothing published"
    into a query spent at every vendor.
    """
    for provider in list(active_providers() if providers is None else providers):
        remaining = deadline - clock()
        if remaining <= 0.05:
            _count(provider.name, "deadline")
            break
        breaker = _breaker(provider.name)
        if not breaker.allow_request():
            _count(provider.name, "circuit_open")
            continue
        try:
            rows = provider.search(request, min(remaining, WEB_SEARCH_TIMEOUT_SECONDS))
        except ProviderError as exc:
            breaker.record_failure()
            _count(provider.name, exc.kind)
            logger.warning("web search provider %s failed: %s", provider.name, exc.kind)
            continue
        except httpx.TimeoutException:
            breaker.record_failure()
            _count(provider.name, "timeout")
            logger.warning("web search provider %s timed out", provider.name)
            continue
        except (httpx.HTTPError, ValueError) as exc:
            breaker.record_failure()
            _count(provider.name, "transport")
            logger.warning("web search provider %s failed: %s", provider.name, type(exc).__name__)
            continue
        breaker.record_success()
        _count(provider.name, "ok" if rows else "empty")
        return ChainResult("ok" if rows else "empty", tuple(rows), provider.name)
    return ChainResult("unavailable")
