"""Sunbird first, Workers AI behind it, Gemini out of generation (ADR-0036, G537).

On 2026-11-09 every live answer was a passage with the "I could not write a
tailored answer" notice: Gemini refused its key from the Space's IP
(API_KEY_IP_ADDRESS_BLOCKED), and the Crane Cloud relay answered 502 for every
Workers AI call while Cloudflare itself served the account. Generation now leads
with Sunflower through Sunbird's API, and Workers AI is reached directly when the
relay fails.
"""

from __future__ import annotations

import logging
from unittest import mock

import httpx
import pytest
from app import service, sunbird
from app.providers import breakers, gateway, relay_client
from app.providers import config as cloud_config


@pytest.fixture
def chain(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_FALLBACK_BACKEND", "sunbird")
    monkeypatch.setattr(service.flags, "is_enabled", lambda name, **_kw: name == "cloudflare_fallback")
    monkeypatch.setattr("app.production_readiness.is_sovereign_mode", lambda: False)
    monkeypatch.setattr("app.providers.config.is_cloudflare_configured", lambda: True)
    monkeypatch.setattr("app.providers.budget.try_consume_neurons", lambda *_a, **_k: True)
    monkeypatch.setattr(sunbird, "is_available", lambda: True)
    breakers.SUNBIRD_LLM_BREAKER.record_success()
    breakers.CF_LLM_BREAKER.record_success()
    yield
    breakers.SUNBIRD_LLM_BREAKER.record_success()
    breakers.CF_LLM_BREAKER.record_success()


def _suspended(*_a, **_k):
    request = httpx.Request("POST", "https://api.sunbird.ai/tasks/chat/completions")
    response = httpx.Response(403, json={"error_code": "ACCOUNT_SUSPENDED"}, request=request)
    raise httpx.HTTPStatusError("403 Forbidden", request=request, response=response)


# --------------------------------------------------------------------------- the chain


def test_a_suspended_sunbird_account_opens_its_breaker_and_workers_ai_keeps_serving(chain) -> None:
    calls = []
    with mock.patch.object(sunbird, "chat_completion", side_effect=_suspended) as sb, \
         mock.patch.object(gateway, "workers_ai_chat", side_effect=lambda *a, **k: calls.append(1) or "Served. [1]"):
        replies = [service._llm_cloud_fallback("q", [{"text": "p"}], None, "en") for _ in range(5)]
    assert replies == ["Served. [1]"] * 5
    threshold = breakers.SUNBIRD_LLM_BREAKER.failure_threshold
    assert sb.call_count == threshold, "an open breaker stops paying the 403 round trip on every turn"
    assert len(calls) == 5


def test_a_call_turn_never_reaches_sunbird(chain) -> None:
    from app.providers.scope import local_models_only

    with mock.patch.object(sunbird, "chat_completion") as sb, \
         mock.patch.object(gateway, "workers_ai_chat") as cf, local_models_only():
        assert service._llm_cloud_fallback("q", [{"text": "p"}], None, "en") == ""
    sb.assert_not_called()
    cf.assert_not_called()


def test_sunbird_gets_the_qwen_directive_and_workers_ai_does_not(chain, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, str] = {}
    monkeypatch.setattr(service.llm_module, "_build_messages",
                        lambda **_k: [{"role": "system", "content": "/no_think\nRules."}, {"role": "user", "content": "q"}])

    def sunbird_reply(messages, **_k):
        seen["sunbird"] = messages[0]["content"]
        return "x"  # truncated: the chain moves on

    def cf_reply(messages, **_k):
        seen["cf"] = messages[0]["content"]
        return "A complete answer."

    with mock.patch.object(sunbird, "chat_completion", side_effect=sunbird_reply), \
         mock.patch.object(gateway, "workers_ai_chat", side_effect=cf_reply):
        assert service._llm_cloud_fallback("q", [{"text": "p"}], None, "en") == "A complete answer."
    assert seen == {"sunbird": "/no_think\nRules.", "cf": "Rules."}


def test_thinking_text_never_reaches_the_answer(chain) -> None:
    with mock.patch.object(sunbird, "chat_completion", return_value="<think>scratch</think>VAT is 18%. [1]"):
        assert service._llm_cloud_fallback("q", [{"text": "p"}], None, "en") == "VAT is 18%. [1]"


def test_the_retired_gemini_setting_warns_once(chain, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setenv("LLM_FALLBACK_BACKEND", "gemini")
    monkeypatch.setattr(service, "_RETIRED_BACKEND_WARNED", set())
    with caplog.at_level(logging.WARNING, logger="app.service"):
        assert service._cloud_llm_backend() == "sunbird"
        assert service._cloud_llm_backend() == "sunbird"
    assert sum("retired" in r.getMessage() for r in caplog.records) == 1


# --------------------------------------------------------------------------- the relay


@pytest.fixture
def relay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CF_RELAY_BASE_URL", "https://relay.example")
    monkeypatch.setenv("CF_RELAY_SECRET", "s")  # pragma: allowlist secret
    monkeypatch.setattr(gateway, "is_sovereign_mode", lambda: False, raising=False)
    monkeypatch.setattr("app.production_readiness.is_sovereign_mode", lambda: False)
    cloud_config.get_cloud_settings.cache_clear()
    yield
    cloud_config.get_cloud_settings.cache_clear()


def _relay_status(status: int):
    def fail(*_a, **_k):
        request = httpx.Request("POST", "https://relay.example/internal/cf-relay/workers-ai-chat")
        raise httpx.HTTPStatusError(f"{status}", request=request, response=httpx.Response(status, request=request))
    return fail


@pytest.mark.parametrize("failure", [_relay_status(502), _relay_status(503),
                                     lambda *_a, **_k: (_ for _ in ()).throw(httpx.ConnectError("down"))])
def test_a_failing_relay_falls_back_to_cloudflare_directly(relay, failure) -> None:
    direct = {"result": {"response": "Direct answer.", "usage": {}}}
    with mock.patch.object(relay_client, "relay_workers_ai_chat", side_effect=failure), \
         mock.patch.object(gateway, "_workers_ai_call", return_value=direct) as call:
        assert gateway.workers_ai_chat([{"role": "user", "content": "q"}]) == "Direct answer."
    call.assert_called_once()


def test_a_relay_4xx_is_not_retried_directly(relay) -> None:
    with mock.patch.object(relay_client, "relay_workers_ai_chat", side_effect=_relay_status(401)), \
         mock.patch.object(gateway, "_workers_ai_call") as call, pytest.raises(httpx.HTTPStatusError):
        gateway.workers_ai_chat([{"role": "user", "content": "q"}])
    call.assert_not_called()


# --------------------------------------------------------------------------- diagnosis


def test_a_sunbird_refusal_logs_its_error_code(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(403, json={"error_code": "ACCOUNT_SUSPENDED"}))
    client = httpx.Client(base_url="https://api.sunbird.ai", transport=transport)
    monkeypatch.setattr(sunbird, "_account_tokens", lambda: ["t"])
    monkeypatch.setattr(sunbird, "_client_for", lambda _t: client)
    with caplog.at_level(logging.WARNING), pytest.raises(httpx.HTTPStatusError):
        sunbird.chat_completion([{"role": "user", "content": "q"}])
    assert "ACCOUNT_SUSPENDED" in caplog.text
