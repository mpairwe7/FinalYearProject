"""Search backends: their wire contracts, failover and failure semantics.

Request and response shapes follow each vendor's published reference, read on
2026-10-31. SearXNG publishes no JSON response schema, so that adapter is the one
to confirm against a deployed instance.
"""

from __future__ import annotations

import json
from time import monotonic

import httpx
import pytest

from app import web_search_providers as wsp
from app.web_search_providers import (
    BraveProvider,
    ChainResult,
    DuckDuckGoHtmlProvider,
    ProviderError,
    RawResult,
    SearchRequest,
    SearxngProvider,
    TavilyProvider,
    search_with_failover,
)

REQUEST = SearchRequest(
    query="site:ura.go.ug penalty waiver 2026",
    keywords="uganda penalty waiver 2026",
    domains=("ura.go.ug",),
    freshness_days=30,
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    wsp.reset_breakers()
    for name in ("TAVILY_API_KEY", "BRAVE_SEARCH_API_KEY", "SEARXNG_URL", "WEB_SEARCH_PROVIDERS",
                 "WEB_SEARCH_ALLOW_UNSANCTIONED", "APP_ENV"):
        monkeypatch.delenv(name, raising=False)
    yield
    wsp.reset_breakers()


def _serve(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    """Route the module's HTTP clients to *handler*, recording each request."""
    seen: list[httpx.Request] = []
    real_client = httpx.Client

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(
        wsp.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return seen


class TestWireContracts:
    def test_tavily_is_domain_restricted_and_never_asked_to_answer(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        seen = _serve(
            monkeypatch,
            lambda _r: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Notice",
                            "url": "https://ura.go.ug/en/notice/",
                            "content": "Penalty waiver.",
                            "score": 0.9,
                            "published_date": "Tue, 11 Mar 2025 17:00:00 GMT",
                        }
                    ]
                },
            ),
        )

        rows = TavilyProvider().search(REQUEST, 2.0)

        body = json.loads(seen[0].content)
        assert str(seen[0].url) == "https://api.tavily.com/search"
        assert seen[0].headers["authorization"] == "Bearer tvly-test"
        assert body["include_domains"] == ["ura.go.ug"]
        assert body["time_range"] == "month"
        assert body["query"] == "uganda penalty waiver 2026"
        # A vendor's generated answer is prose with no page behind it.
        assert body["include_answer"] is False
        assert rows == [
            RawResult("Notice", "https://ura.go.ug/en/notice/", "Penalty waiver.", "Tue, 11 Mar 2025 17:00:00 GMT")
        ]

    def test_the_default_recency_horizon_reaches_every_provider_as_a_filter(self):
        from app.web_research import WEB_RESEARCH_RECENCY_HORIZON_DAYS as horizon

        assert TavilyProvider._time_range(horizon) == "year"
        assert BraveProvider._freshness(horizon) == "py"
        assert SearxngProvider._time_range(horizon) == "year"

    def test_brave_uses_its_header_and_strips_highlight_markup(self, monkeypatch):
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test")
        seen = _serve(
            monkeypatch,
            lambda _r: httpx.Response(
                200,
                json={
                    "web": {
                        "results": [
                            {
                                "title": "URA <strong>notice</strong>",
                                "url": "https://ura.go.ug/en/notice/",
                                "description": "A <strong>penalty</strong> waiver &amp; more",
                            }
                        ]
                    }
                },
            ),
        )

        rows = BraveProvider().search(REQUEST, 2.0)

        assert seen[0].url.path == "/res/v1/web/search"
        assert seen[0].headers["x-subscription-token"] == "brave-test"
        assert seen[0].url.params["q"] == REQUEST.query
        assert seen[0].url.params["freshness"] == "pm"
        assert rows[0].title == "URA notice"
        assert rows[0].snippet == "A penalty waiver & more"

    def test_brave_with_no_web_section_is_an_empty_answer_not_an_error(self, monkeypatch):
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test")
        _serve(monkeypatch, lambda _r: httpx.Response(200, json={"query": {"original": "x"}}))

        assert BraveProvider().search(REQUEST, 2.0) == []

    def test_searxng_asks_for_json_from_the_operators_instance(self, monkeypatch):
        monkeypatch.setenv("SEARXNG_URL", "https://search.internal.example/")
        seen = _serve(
            monkeypatch,
            lambda _r: httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Notice",
                            "url": "https://ura.go.ug/en/notice/",
                            "content": "Penalty waiver.",
                            "publishedDate": "2026-10-01T00:00:00",
                        }
                    ]
                },
            ),
        )

        rows = SearxngProvider().search(REQUEST, 2.0)

        assert str(seen[0].url).startswith("https://search.internal.example/search?")
        assert seen[0].url.params["format"] == "json"
        assert seen[0].url.params["time_range"] == "month"
        assert rows[0].published_at == "2026-10-01T00:00:00"

    @pytest.mark.parametrize(
        ("status", "kind"),
        [(401, "auth"), (403, "auth"), (429, "rate_limited"), (432, "rate_limited"), (500, "upstream")],
    )
    def test_http_failures_name_their_cause(self, monkeypatch, status, kind):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        _serve(monkeypatch, lambda _r: httpx.Response(status, json={}))

        with pytest.raises(ProviderError) as caught:
            TavilyProvider().search(REQUEST, 2.0)

        assert caught.value.kind == kind

    def test_an_unknown_body_is_malformed_rather_than_empty(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        _serve(monkeypatch, lambda _r: httpx.Response(200, json={"unexpected": True}))

        with pytest.raises(ProviderError) as caught:
            TavilyProvider().search(REQUEST, 2.0)

        assert caught.value.kind == "malformed"


class TestDuckDuckGoFallback:
    def test_a_bot_challenge_is_a_failure_not_an_empty_result(self, monkeypatch):
        # Parsed as HTML this was [], which read as "URA has published nothing".
        _serve(
            monkeypatch,
            lambda _r: httpx.Response(200, text="<div class='anomaly-modal'>Unfortunately, bots use DuckDuckGo too.</div>"),
        )

        with pytest.raises(ProviderError) as caught:
            DuckDuckGoHtmlProvider().search(REQUEST, 2.0)

        assert caught.value.kind == "blocked"

    @pytest.mark.parametrize("status", [202, 302])
    def test_an_unexpected_status_is_a_failure(self, monkeypatch, status):
        _serve(monkeypatch, lambda _r: httpx.Response(status, text=""))

        with pytest.raises(ProviderError):
            DuckDuckGoHtmlProvider().search(REQUEST, 2.0)

    def test_it_presents_as_software_not_as_a_browser(self, monkeypatch):
        seen = _serve(monkeypatch, lambda _r: httpx.Response(200, text=""))

        DuckDuckGoHtmlProvider().search(REQUEST, 2.0)

        assert "Mozilla" not in seen[0].headers["user-agent"]

    def test_it_never_runs_in_production(self, monkeypatch):
        monkeypatch.setenv("APP_ENV", "production")
        monkeypatch.setenv("WEB_SEARCH_ALLOW_UNSANCTIONED", "true")

        assert wsp.unsanctioned_allowed() is False
        assert wsp.active_providers() == []

    def test_it_is_the_development_default_when_nothing_is_configured(self):
        assert [p.name for p in wsp.active_providers()] == ["duckduckgo_html"]
        assert wsp.sanctioned_provider_configured() is False

    def test_a_configured_provider_is_tried_first(self, monkeypatch):
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "k")

        assert [p.name for p in wsp.active_providers()] == ["brave", "duckduckgo_html"]
        assert wsp.sanctioned_provider_configured() is True


class _Scripted:
    """A provider that replays a script of outcomes."""

    sanctioned = True

    def __init__(self, name: str, *script):
        self.name = name
        self.script = list(script)
        self.calls = 0
        self.timeouts: list[float] = []

    def configured(self) -> bool:
        return True

    def search(self, _request: SearchRequest, timeout: float) -> list[RawResult]:
        self.calls += 1
        self.timeouts.append(timeout)
        step = self.script.pop(0) if self.script else []
        if isinstance(step, Exception):
            raise step
        return step


ROW = RawResult("Notice", "https://ura.go.ug/en/notice/", "Penalty waiver.")


class TestFailover:
    def _run(self, *providers, deadline: float | None = None) -> ChainResult:
        return search_with_failover(
            REQUEST,
            deadline=monotonic() + 3 if deadline is None else deadline,
            providers=providers,
        )

    def test_a_failed_provider_hands_over_to_the_next(self):
        primary = _Scripted("primary", ProviderError("rate_limited"))
        secondary = _Scripted("secondary", [ROW])

        result = self._run(primary, secondary)

        assert (result.status, result.provider) == ("ok", "secondary")

    def test_an_empty_answer_ends_the_chain(self):
        # "Nothing published" is an answer; asking every vendor would only cost.
        primary = _Scripted("primary", [])
        secondary = _Scripted("secondary", [ROW])

        result = self._run(primary, secondary)

        assert (result.status, result.provider) == ("empty", "primary")
        assert secondary.calls == 0

    def test_exhausting_the_chain_reports_unavailable_not_empty(self):
        result = self._run(
            _Scripted("primary", httpx.ConnectTimeout("slow")),
            _Scripted("secondary", httpx.ConnectError("refused")),
        )

        assert result == ChainResult("unavailable")

    def test_no_provider_at_all_is_unavailable(self):
        assert self._run().status == "unavailable"

    def test_three_failures_open_the_breaker_and_stop_the_calls(self):
        failing = _Scripted("failing", *[ProviderError("upstream") for _ in range(6)])

        for _ in range(5):
            self._run(failing)

        assert failing.calls == 3

    def test_success_keeps_the_breaker_closed(self):
        flaky = _Scripted(
            "flaky",
            ProviderError("upstream"),
            ProviderError("upstream"),
            [ROW],
            ProviderError("upstream"),
            ProviderError("upstream"),
            [ROW],
        )

        statuses = [self._run(flaky).status for _ in range(6)]

        assert statuses == ["unavailable", "unavailable", "ok", "unavailable", "unavailable", "ok"]

    def test_a_spent_deadline_makes_no_call(self):
        provider = _Scripted("primary", [ROW])

        result = self._run(provider, deadline=monotonic() - 1)

        assert result.status == "unavailable"
        assert provider.calls == 0

    def test_the_call_timeout_never_exceeds_what_remains(self):
        provider = _Scripted("primary", [ROW])

        self._run(provider, deadline=monotonic() + 0.5)

        assert provider.timeouts[0] <= 0.5


class TestProcessorGovernance:
    def test_a_hosted_provider_is_an_external_processor(self, monkeypatch):
        from app import production_readiness as pr

        monkeypatch.setenv("FLAG_LIVE_WEB_RESEARCH", "true")
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
        monkeypatch.delenv("CROSS_BORDER_PROCESSING_APPROVED", raising=False)
        monkeypatch.delenv("SUNBIRD_API_TOKEN", raising=False)
        monkeypatch.delenv("SUNBIRD_FALLBACK_API_TOKEN", raising=False)

        assert "web search (tavily)" in pr._external_processor_names()
        assert any("web search (tavily)" in error for error in pr._cross_border_errors())

    def test_a_provider_counts_once_configured_not_once_enabled(self, monkeypatch):
        # The flag can be turned on at runtime by a persisted override, long
        # after this gate ran, so the key is the material fact.
        from app import production_readiness as pr

        monkeypatch.delenv("FLAG_LIVE_WEB_RESEARCH", raising=False)
        monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")

        assert "web search (tavily)" in pr._external_processor_names()

    def test_a_self_hosted_searxng_is_still_an_external_processor(self, monkeypatch):
        # A metasearch engine: its documentation says the query goes to external services.
        from app import production_readiness as pr

        monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

        assert "web search (searxng)" in pr._external_processor_names()

    def test_no_provider_adds_no_processor(self):
        from app import production_readiness as pr

        assert not any("web search" in name for name in pr._external_processor_names())

    def test_sovereign_mode_refuses_hosted_search_and_remote_searxng(self, monkeypatch):
        from app import production_readiness as pr

        monkeypatch.setenv("SOVEREIGN_MODE", "true")
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "brave-test")
        monkeypatch.setenv("SEARXNG_URL", "https://search.example.com")

        errors = " ".join(pr.sovereign_mode_errors())

        assert "BRAVE_SEARCH_API_KEY" in errors
        assert "SEARXNG_URL" in errors

    def test_sovereign_mode_accepts_a_local_searxng(self, monkeypatch):
        from app import production_readiness as pr

        monkeypatch.setenv("SOVEREIGN_MODE", "true")
        monkeypatch.setenv("SEARXNG_URL", "http://searxng:8080")

        assert "SEARXNG_URL" not in " ".join(pr.sovereign_mode_errors())
