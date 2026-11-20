"""A recording reaches Gemini only through its policy, and an outage stays an outage.

These ran against ``speech_service.py`` as the HF Space's build script rewrote it. The fallback
is repository code now (ADR-0068 part 3), so they run on the module itself.
"""

from __future__ import annotations

import types
from typing import Any

import numpy as np
import pytest

from app.speech import speech_service


class _Answer:
    def __init__(self, text: str = "what is the vat rate", status: int = 200) -> None:
        self.text, self.status = text, status

    def raise_for_status(self) -> None:
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


@pytest.fixture
def gemini_audio(monkeypatch):
    """Everything open, so each test closes the one gate it is about."""
    from app.config.flags import flags
    from app.providers import breakers, budget, gateway
    from app.providers import config as cfg

    sent: list[str] = []

    def post(url: str, **_kwargs: Any) -> _Answer:
        sent.append(url)
        return _Answer()

    monkeypatch.setenv("STT_FALLBACK_BACKEND", "gemini")
    monkeypatch.setattr(flags, "is_enabled", lambda name, **_kw: name == "cloudflare_fallback")
    monkeypatch.setattr(speech_service, "cloud_models_allowed", lambda: True)
    monkeypatch.setattr(gateway, "cloud_models_allowed", lambda: True)
    monkeypatch.setattr(cfg, "is_gemini_configured", lambda: True)
    monkeypatch.setattr(budget, "try_consume_gemini_call", lambda: True)
    monkeypatch.setattr(
        cfg,
        "get_cloud_settings",
        lambda: types.SimpleNamespace(gemini_api_key=types.SimpleNamespace(get_secret_value=lambda: "test-key")),
    )
    monkeypatch.setattr("httpx.post", post)
    breakers.GEMINI_BREAKER.record_success()
    yield sent
    breakers.GEMINI_BREAKER.record_success()


def _transcribe(language: str | None) -> str | None:
    return speech_service.SpeechModel._gemini_audio_transcribe(None, np.full(1600, 0.2), 16000, language)


class TestRecordingsReachGeminiOnlyThroughItsPolicy:
    """A recording is personal data leaving Uganda (G375)."""

    def test_a_stated_english_clip_is_sent_once_opted_in(self, gemini_audio) -> None:
        assert _transcribe("en") == "what is the vat rate"
        assert len(gemini_audio) == 1

    def test_nothing_is_sent_without_opting_in(self, gemini_audio, monkeypatch) -> None:
        monkeypatch.setenv("STT_FALLBACK_BACKEND", "none")
        assert _transcribe("en") is None
        assert gemini_audio == []

    def test_the_cloud_tier_flag_stops_it(self, gemini_audio, monkeypatch) -> None:
        from app.config.flags import flags

        monkeypatch.setattr(flags, "is_enabled", lambda _name, **_kw: False)
        assert _transcribe("en") is None
        assert gemini_audio == []

    def test_a_turn_barred_from_cloud_models_is_not_sent(self, gemini_audio, monkeypatch) -> None:
        # Sovereign mode and call turns, which run on local models only.
        monkeypatch.setattr(speech_service, "cloud_models_allowed", lambda: False)
        assert _transcribe("en") is None
        assert gemini_audio == []

    @pytest.mark.parametrize("language", ["lg", "nyn", "ach"])
    def test_a_sunbird_language_is_never_sent(self, gemini_audio, language) -> None:
        assert _transcribe(language) is None
        assert gemini_audio == []

    @pytest.mark.parametrize("language", [None, ""])
    def test_a_clip_of_unknown_language_is_never_sent(self, gemini_audio, language) -> None:
        # gemini_allowed_for reads no language as English, and this could be Luganda.
        assert _transcribe(language) is None
        assert gemini_audio == []

    def test_a_spent_budget_stops_it(self, gemini_audio, monkeypatch) -> None:
        monkeypatch.setattr("app.providers.budget.try_consume_gemini_call", lambda: False)
        assert _transcribe("en") is None
        assert gemini_audio == []

    def test_an_open_breaker_stops_it(self, gemini_audio) -> None:
        from app.providers import breakers

        for _ in range(breakers.GEMINI_BREAKER.failure_threshold):
            breakers.GEMINI_BREAKER.record_failure()
        assert _transcribe("en") is None
        assert gemini_audio == []

    def test_a_failed_call_counts_against_the_breaker(self, gemini_audio, monkeypatch) -> None:
        from app.providers import breakers

        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Answer(status=500))
        assert _transcribe("en") is None
        assert breakers.GEMINI_BREAKER._failures == 1

    def test_hearing_nothing_is_not_a_failure(self, gemini_audio, monkeypatch) -> None:
        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Answer(text=""))
        assert _transcribe("en") == ""

    def test_multilingual_gemini_opt_in_allows_sunbird_locales_and_autodetect(
        self, gemini_audio, monkeypatch
    ) -> None:
        monkeypatch.setenv("STT_ALLOW_MULTILINGUAL_GEMINI", "true")
        assert _transcribe("lg") == "what is the vat rate"
        assert _transcribe(None) == "what is the vat rate"
        assert len(gemini_audio) == 2

    def test_gemini_35_transcribe_audio_transcription_part_and_prompt_feedback_fallback(
        self, gemini_audio, monkeypatch
    ) -> None:
        calls: list[str] = []

        class _TranscribeResp:
            def __init__(self, payload: dict[str, Any]) -> None:
                self._payload = payload

            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, Any]:
                return self._payload

        def _fake_post(url: str, **_kw: Any) -> _TranscribeResp:
            calls.append(url)
            if len(calls) == 1:
                return _TranscribeResp({"promptFeedback": {"blockReason": "OTHER"}})
            return _TranscribeResp(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"audioTranscription": {"text": "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi."}}
                                ]
                            }
                        }
                    ]
                }
            )

        monkeypatch.setattr("httpx.post", _fake_post)
        assert _transcribe("en") == "Omusolo gwa VAT guli ebitundu 18 ku buli kikumi."
        assert len(calls) == 2
        assert "gemini-3.5-transcribe" in calls[0]
        assert "gemini-2.5-flash" in calls[1]


class _EveryTierDown:
    """A SpeechModel with nothing to reach, shaped as in ``test_asr_no_speech.py``."""

    enabled = True
    _asr = None
    _lang_det = None
    _whisper_peft = None
    _whisper_salt = None
    _accent_detector = None
    _whisper_adapters: dict[str, Any] = {}  # noqa: RUF012 - read only, as in the real model
    _breakers: dict[str, Any] = {}  # noqa: RUF012 - read only, as in the real model
    _executor = None

    def __init__(self, samples: Any) -> None:
        self._samples = samples
        self._gemini_audio_transcribe = types.MethodType(speech_service.SpeechModel._gemini_audio_transcribe, self)

    def _transcribe_whisper_peft(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def _transcribe_faster_whisper(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def _cf_whisper_transcribe(self, *_args: Any, **_kwargs: Any) -> str:
        return ""

    def _decode_audio_bytes(self, *_args: Any, **_kwargs: Any) -> Any:
        return self._samples


class TestAnOutageStaysAnOutage:
    @pytest.fixture(autouse=True)
    def _sunbird_down(self, monkeypatch) -> None:
        monkeypatch.setattr("app.sunbird.is_available", lambda: False)

    def _chain(self, samples: Any) -> Any:
        model = _EveryTierDown(samples)
        return speech_service.SpeechModel._transcribe_chain(model, b"\x00\x00" * 1600, 16000, "en")

    def test_a_silent_clip_is_not_sent_and_does_not_hide_it(self, gemini_audio) -> None:
        result = self._chain(np.zeros(1600))
        assert result.backend == "unavailable"
        assert "All ASR backends failed" in (result.error or "")
        assert gemini_audio == []

    def test_gemini_hearing_nothing_is_not_a_failure(self, gemini_audio, monkeypatch) -> None:
        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Answer(text=""))
        result = self._chain(np.full(1600, 0.2))
        assert result.backend == "gemini_audio"
        assert result.error is None

    def test_speech_reaches_gemini_once_every_other_tier_has_failed(self, gemini_audio) -> None:
        result = self._chain(np.full(1600, 0.2))
        assert result.text == "what is the vat rate"
        assert result.backend == "gemini_audio"

    def test_a_blocked_response_is_not_silence(self, gemini_audio, monkeypatch) -> None:
        """Both models refused the prompt. "No speech detected" would hide that every tier is down."""

        class _Blocked(_Answer):
            def json(self) -> dict[str, Any]:
                return {"promptFeedback": {"blockReason": "OTHER"}}

        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Blocked())
        result = self._chain(np.full(1600, 0.2))
        assert result.backend == "unavailable"
        assert "All ASR backends failed" in (result.error or "")

    def test_a_candidate_with_no_part_is_not_silence(self, gemini_audio, monkeypatch) -> None:
        class _NoParts(_Answer):
            def json(self) -> dict[str, Any]:
                return {"candidates": [{"content": {"parts": []}}]}

        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _NoParts())
        assert _transcribe("en") is None
        assert self._chain(np.full(1600, 0.2)).backend == "unavailable"

    def test_a_call_turn_sends_nothing_through_the_pool(self, gemini_audio, monkeypatch) -> None:
        """The cloud pool does not carry a turn's context. Run in it bare, the helper would read
        "cloud allowed" on a call turn and send the recording (providers.scope)."""
        from app.providers import scope

        monkeypatch.setattr(speech_service, "cloud_models_allowed", scope.cloud_models_allowed)
        with scope.local_models_only():
            result = self._chain(np.full(1600, 0.2))
        assert gemini_audio == []
        assert result.backend == "unavailable"

    def test_a_slow_gemini_is_left_at_the_cloud_deadline(self, gemini_audio, monkeypatch) -> None:
        import time

        def slow(*_a: Any, **_k: Any) -> _Answer:
            time.sleep(0.6)
            return _Answer()

        monkeypatch.setattr("httpx.post", slow)
        monkeypatch.setattr(speech_service, "SPEECH_CLOUD_DEADLINE_S", 0.05)
        started = time.monotonic()
        result = self._chain(np.full(1600, 0.2))
        assert time.monotonic() - started < 0.5
        assert result.backend == "unavailable"


def test_each_gemini_transcription_is_traced_with_the_providers_usage(gemini_audio, monkeypatch) -> None:
    from contextlib import contextmanager

    from app.observability import tracing

    seen: list[tuple[str, str, str, tuple[Any, Any]]] = []

    @contextmanager
    def llm_call(operation: str, model: str, provider: str, **_kw: Any):
        call = types.SimpleNamespace(usage=lambda prompt, output: seen.append((operation, model, provider, (prompt, output))), fail=lambda _reason: None)
        yield call

    class _Metered(_Answer):
        def json(self) -> dict[str, Any]:
            return {**super().json(), "usageMetadata": {"promptTokenCount": 41, "candidatesTokenCount": 7}}

    monkeypatch.setattr(tracing, "llm_call", llm_call)
    monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Metered())
    assert _transcribe("en") == "what is the vat rate"
    assert seen == [("generate_content", "gemini-3.5-transcribe", "gcp.gemini", (41, 7))]
