"""The FAQ judge and the translation fallback run on Sunbird, not Gemini (ADR-0037).

Gemini's key is IP-restricted and fails from the HF Space, so the judge and the
"fast" translation tier failed there on every call. Both now ask Sunflower through
Sunbird's chat endpoint, beside Sunbird's dedicated /tasks/translate.
"""

from __future__ import annotations

from unittest import mock

import pytest
from app import service, sunbird
from app.providers import breakers, gateway
from app.speech_service import SpeechModel


@pytest.fixture(autouse=True)
def healthy_breaker():
    breakers.SUNBIRD_LLM_BREAKER.record_success()
    yield
    breakers.SUNBIRD_LLM_BREAKER.record_success()


@pytest.fixture
def translation_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRANSLATE_FALLBACK_BACKEND", "sunbird")
    monkeypatch.setattr("app.flags.flags.is_enabled", lambda name, **_k: name == "cloudflare_fallback")
    monkeypatch.setattr(sunbird, "is_available", lambda: True)


# --------------------------------------------------------------------------- translation


def test_the_translation_prompt_names_both_languages_and_keeps_figures() -> None:
    with mock.patch.object(sunbird, "chat_completion", return_value=sunbird.ChatReply("Omusolo gwa 18%.")) as chat:
        assert sunbird.translate_with_sunflower("VAT is 18%.", "en", "lg") == "Omusolo gwa 18%."
    system = chat.call_args.args[0][0]["content"]
    assert "from English to Luganda" in system
    assert "Keep every number" in system
    assert chat.call_args.kwargs["temperature"] == pytest.approx(0.1)


def test_thinking_text_is_stripped_and_an_empty_translation_is_a_failure() -> None:
    with mock.patch.object(sunbird, "chat_completion", return_value="<think>hmm</think>Omusolo."):
        assert sunbird.translate_with_sunflower("Tax.", "en", "lg") == "Omusolo."
    with mock.patch.object(sunbird, "chat_completion", return_value="<think>only thinking</think>"), \
         pytest.raises(RuntimeError):
        sunbird.translate_with_sunflower("Tax.", "en", "lg")


@pytest.mark.parametrize("backend", ["sunbird", "gemini"])
def test_the_translation_tier_asks_sunflower_and_never_gemini(translation_on, monkeypatch, backend) -> None:
    monkeypatch.setenv("TRANSLATE_FALLBACK_BACKEND", backend)  # the retired gemini reads as sunbird
    with mock.patch.object(sunbird, "translate_with_sunflower", return_value="Omusolo") as tr, \
         mock.patch.object(gateway, "gemini_generate") as gem:
        assert SpeechModel._sunbird_llm_translate("Tax", "en", "lg") == "Omusolo"
    tr.assert_called_once()
    assert tr.call_args.args == ("Tax", "en", "lg")  # the deadline is checked separately
    gem.assert_not_called()


@pytest.mark.parametrize("backend", ["", "workers_ai"])
def test_the_tier_is_off_unless_selected(translation_on, monkeypatch, backend) -> None:
    monkeypatch.setenv("TRANSLATE_FALLBACK_BACKEND", backend)
    with mock.patch.object(sunbird, "translate_with_sunflower") as tr:
        assert SpeechModel._sunbird_llm_translate("Tax", "en", "lg") == ""
    tr.assert_not_called()


def test_a_failing_translation_opens_the_shared_breaker(translation_on) -> None:
    with mock.patch.object(sunbird, "translate_with_sunflower", side_effect=RuntimeError("down")) as tr:
        results = [SpeechModel._sunbird_llm_translate("Tax", "en", "lg") for _ in range(5)]
    assert results == [""] * 5
    assert tr.call_count == breakers.SUNBIRD_LLM_BREAKER.failure_threshold


def test_a_call_turn_never_reaches_sunflower(translation_on) -> None:
    from app.providers.scope import local_models_only

    with mock.patch.object(sunbird, "translate_with_sunflower") as tr, local_models_only():
        assert SpeechModel._sunbird_llm_translate("Tax", "en", "lg") == ""
    tr.assert_not_called()


def test_a_pair_the_translate_endpoint_does_not_serve_leads_with_sunflower(monkeypatch) -> None:
    model = SpeechModel.__new__(SpeechModel)
    model._mt = None
    model._chat_model = None
    monkeypatch.setattr(sunbird, "is_available", lambda: True)
    with mock.patch.object(SpeechModel, "_sunbird_llm_translate", return_value="Bonjour"), \
         mock.patch.object(SpeechModel, "_cf_llama_translate", return_value="Bonjour (Workers AI)"):  # would win if first
        result = model._do_translate("Hello", "en", "fr")
    assert (result.text, result.backend) == ("Bonjour", "sunbird_llm")


# --------------------------------------------------------------------------- the FAQ judge


def _index() -> dict:
    return {"vat": [
        {"question": "What is the VAT rate in Uganda?", "answer": "The standard VAT rate is 18%."},
        {"question": "How do I register for VAT?", "answer": "Apply on the URA portal."},
    ]}


@pytest.fixture
def judge_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(service, "FAQ_JUDGE_ENABLED", True)
    monkeypatch.setattr(sunbird, "is_available", lambda: True)


def test_the_judge_asks_sunflower_and_reads_past_its_thinking(judge_on) -> None:
    # A decoy object inside the thinking: parsed without stripping, it would win.
    verdict = '<think>Maybe {"pick": 2} fits.</think>{"pick": 1, "confident": true}'
    with mock.patch.object(sunbird, "chat_completion", return_value=verdict) as chat, \
         mock.patch.object(gateway, "gemini_generate") as gem:
        hits = service._judge_rescue("What is the VAT rate?", _index(), 4)
    assert hits
    assert "VAT" in hits[0]["question"]
    assert chat.call_args.kwargs["timeout"] == service.FAQ_JUDGE_TIMEOUT
    gem.assert_not_called()


def test_the_judge_is_skipped_when_sunbird_is_unavailable(judge_on, monkeypatch) -> None:
    monkeypatch.setattr(sunbird, "is_available", lambda: False)
    with mock.patch.object(sunbird, "chat_completion") as chat:
        assert service._judge_rescue("What is the VAT rate?", _index(), 4) == []
    chat.assert_not_called()


def test_a_failing_judge_opens_the_shared_breaker(judge_on) -> None:
    with mock.patch.object(sunbird, "chat_completion", side_effect=RuntimeError("down")) as chat:
        results = [service._judge_rescue("What is the VAT rate?", _index(), 4) for _ in range(5)]
    assert results == [[]] * 5
    assert chat.call_count == breakers.SUNBIRD_LLM_BREAKER.failure_threshold


# --------------------------------------------------------------------------- review fixes (PR 712)


def test_the_translation_tier_bounds_failover_by_the_speech_deadline(translation_on) -> None:
    from app import speech_service

    with mock.patch.object(sunbird, "translate_with_sunflower", return_value="Omusolo") as tr:
        before = speech_service.time.monotonic()
        SpeechModel._sunbird_llm_translate("Tax", "en", "lg")
        after = speech_service.time.monotonic()
    deadline = tr.call_args.kwargs["deadline"]
    assert before + speech_service.SPEECH_CLOUD_DEADLINE_S <= deadline <= after + speech_service.SPEECH_CLOUD_DEADLINE_S


def test_translate_with_sunflower_hands_the_deadline_to_the_chat_call() -> None:
    with mock.patch.object(sunbird, "chat_completion", return_value=sunbird.ChatReply("Omusolo.")) as chat:
        sunbird.translate_with_sunflower("Tax.", "en", "lg", deadline=1234.5)
    assert chat.call_args.kwargs["deadline"] == 1234.5


def test_the_final_translation_error_names_the_current_backends(monkeypatch) -> None:
    model = SpeechModel.__new__(SpeechModel)
    model._mt = None
    model._chat_model = None
    monkeypatch.setattr(sunbird, "is_available", lambda: False)
    with mock.patch.object(SpeechModel, "_sunbird_llm_translate", return_value=""), \
         mock.patch.object(SpeechModel, "_cf_llama_translate", return_value=""):
        result = model._do_translate("Hello", "en", "lg")
    assert "Gemini" not in (result.error or "")
    assert "Sunflower" in (result.error or "")
