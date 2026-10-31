"""The Space's CPU layer lives in one file, and no CI job builds that file.

The roll pushes ``App/deploy/hf-space/Dockerfile`` to the Space whole. On 2026-10-31 the layer
existed only on the Space, so a roll deleted it and left its flags on, and the API crashed on
every start for about 8 hours while Hugging Face reported RUNNING (G371).

The layer also edits app source at build time with ``assert old in text``. An ordinary change to
one of those lines would pass every test here and then fail the Space's build in production, so
these tests read the patch and check what it depends on. They parse it and never run it, and
load the speech source it produces, because that is where a recording can leave the country.
"""

from __future__ import annotations

import ast
import base64
import re
import sys
import types
from pathlib import Path
from typing import Any

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "App" / "deploy" / "hf-space" / "Dockerfile"
RECEPTIONIST_REQUIREMENTS = ROOT / "App" / "backend" / "requirements-receptionist.txt"


def _patch_script() -> ast.Module:
    encoded = re.search(r'HF_CPU_PATCH_B64="([A-Za-z0-9+/=]+)"', DOCKERFILE.read_text(encoding="utf-8"))
    assert encoded, "the CPU layer is missing from the Dockerfile the roll pushes"
    return ast.parse(base64.b64decode(encoded.group(1)).decode("utf-8"))


def _patch_strings() -> dict[str, str]:
    return {
        node.targets[0].id: node.value.value
        for node in _patch_script().body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _in_repo(container_path: str) -> Path:
    return ROOT / "App" / "backend" / container_path.removeprefix("/app/backend/")


def _patched_lines() -> list[tuple[Path, str, str]]:
    """(source file, patch variable, text) for every line the layer expects to find.

    The script reads a file, then declares what it replaces in it, so each ``old_*`` belongs
    to the last ``Path(".../*.py")`` above it.
    """
    source: Path | None = None
    found: list[tuple[Path, str, str]] = []
    for node in _patch_script().body:
        if not (isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)):
            continue
        name, value = node.targets[0].id, node.value
        if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "Path":
            path = value.args[0].value
            if path.endswith(".py"):
                source = _in_repo(path)
        elif name.startswith("old") and isinstance(value, ast.Constant) and source is not None:
            found.append((source, name, value.value))
    return found


PATCHED_LINES = _patched_lines()


def test_the_patch_was_read() -> None:
    # Guards the parametrized test below, which would pass by running nothing.
    assert len(PATCHED_LINES) >= 13


@pytest.mark.parametrize(
    ("source", "name", "text"),
    PATCHED_LINES,
    ids=[f"{source.name}:{name}" for source, name, _text in PATCHED_LINES],
)
def test_every_line_the_layer_edits_still_exists(source: Path, name: str, text: str) -> None:
    assert text in source.read_text(encoding="utf-8"), (
        f"{source.name} no longer contains {name}. The Space's build asserts on it and would fail "
        "in production. Update HF_CPU_PATCH_B64 in App/deploy/hf-space/Dockerfile with this change."
    )


def test_the_flag_that_needs_pipecat_ships_with_pipecat() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "FLAG_VOICE_RECEPTIONIST=true" in dockerfile
    installed = re.search(r'"(pipecat-ai\[[^"]+)"', dockerfile)
    assert installed, "the receptionist is switched on here and Pipecat is not installed, which stops the API starting"
    assert installed.group(1) in RECEPTIONIST_REQUIREMENTS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def built_speech():
    """``speech_service.py`` as the Space builds it, loaded beside the real one."""
    sys.path.insert(0, str(ROOT / "App" / "backend"))
    strings = _patch_strings()
    source = (ROOT / "App" / "backend" / "app" / "speech_service.py").read_text(encoding="utf-8")
    for old, new in (("old_sp", "new_sp"), ("old_sp_2", "new_sp_2")):
        source = source.replace(strings[old], strings[new], 1)
    module = types.ModuleType("app._speech_service_as_built")
    module.__package__ = "app"
    sys.modules[module.__name__] = module
    exec(compile(source, "speech_service.py as built", "exec"), module.__dict__)  # noqa: S102 - our own source
    yield module
    del sys.modules[module.__name__]


class _Answer:
    def __init__(self, text: str = "what is the vat rate", status: int = 200) -> None:
        self.text, self.status = text, status

    def raise_for_status(self) -> None:
        if self.status != 200:
            raise RuntimeError(f"HTTP {self.status}")

    def json(self) -> dict[str, Any]:
        return {"candidates": [{"content": {"parts": [{"text": self.text}]}}]}


@pytest.fixture
def gemini_audio(built_speech, monkeypatch):
    """Everything open, so each test closes the one gate it is about."""
    from app.flags import flags
    from app.providers import breakers, budget, gateway
    from app.providers import config as cfg

    sent: list[str] = []

    def post(url: str, **_kwargs: Any) -> _Answer:
        sent.append(url)
        return _Answer()

    monkeypatch.setenv("STT_FALLBACK_BACKEND", "gemini")
    monkeypatch.setattr(flags, "is_enabled", lambda name, **_kw: name == "cloudflare_fallback")
    monkeypatch.setattr(built_speech, "cloud_models_allowed", lambda: True)
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


def _transcribe(built_speech: Any, language: str | None) -> str | None:
    return built_speech.SpeechModel._gemini_audio_transcribe(None, np.full(1600, 0.2), 16000, language)


class TestRecordingsReachGeminiOnlyThroughItsPolicy:
    """A recording is personal data leaving Uganda (G375)."""

    def test_a_stated_english_clip_is_sent_once_opted_in(self, built_speech, gemini_audio) -> None:
        assert _transcribe(built_speech, "en") == "what is the vat rate"
        assert len(gemini_audio) == 1

    def test_nothing_is_sent_without_opting_in(self, built_speech, gemini_audio, monkeypatch) -> None:
        monkeypatch.setenv("STT_FALLBACK_BACKEND", "none")
        assert _transcribe(built_speech, "en") is None
        assert gemini_audio == []

    def test_the_cloud_tier_flag_stops_it(self, built_speech, gemini_audio, monkeypatch) -> None:
        from app.flags import flags

        monkeypatch.setattr(flags, "is_enabled", lambda _name, **_kw: False)
        assert _transcribe(built_speech, "en") is None
        assert gemini_audio == []

    def test_a_turn_barred_from_cloud_models_is_not_sent(self, built_speech, gemini_audio, monkeypatch) -> None:
        # Sovereign mode and call turns, which run on local models only.
        monkeypatch.setattr(built_speech, "cloud_models_allowed", lambda: False)
        assert _transcribe(built_speech, "en") is None
        assert gemini_audio == []

    @pytest.mark.parametrize("language", ["lg", "nyn", "ach"])
    def test_a_sunbird_language_is_never_sent(self, built_speech, gemini_audio, language) -> None:
        assert _transcribe(built_speech, language) is None
        assert gemini_audio == []

    @pytest.mark.parametrize("language", [None, ""])
    def test_a_clip_of_unknown_language_is_never_sent(self, built_speech, gemini_audio, language) -> None:
        # gemini_allowed_for reads no language as English, and this could be Luganda.
        assert _transcribe(built_speech, language) is None
        assert gemini_audio == []

    def test_a_spent_budget_stops_it(self, built_speech, gemini_audio, monkeypatch) -> None:
        monkeypatch.setattr("app.providers.budget.try_consume_gemini_call", lambda: False)
        assert _transcribe(built_speech, "en") is None
        assert gemini_audio == []

    def test_an_open_breaker_stops_it(self, built_speech, gemini_audio) -> None:
        from app.providers import breakers

        for _ in range(breakers.GEMINI_BREAKER.failure_threshold):
            breakers.GEMINI_BREAKER.record_failure()
        assert _transcribe(built_speech, "en") is None
        assert gemini_audio == []

    def test_a_failed_call_counts_against_the_breaker(self, built_speech, gemini_audio, monkeypatch) -> None:
        from app.providers import breakers

        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Answer(status=500))
        assert _transcribe(built_speech, "en") is None
        assert breakers.GEMINI_BREAKER._failures == 1

    def test_hearing_nothing_is_not_a_failure(self, built_speech, gemini_audio, monkeypatch) -> None:
        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Answer(text=""))
        assert _transcribe(built_speech, "en") == ""


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

    def __init__(self, built_speech: Any, samples: Any) -> None:
        self._samples = samples
        self._gemini_audio_transcribe = types.MethodType(built_speech.SpeechModel._gemini_audio_transcribe, self)

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

    def _chain(self, built_speech: Any, samples: Any) -> Any:
        model = _EveryTierDown(built_speech, samples)
        return built_speech.SpeechModel._transcribe_chain(model, b"\x00\x00" * 1600, 16000, "en")

    def test_a_silent_clip_is_not_sent_and_does_not_hide_it(self, built_speech, gemini_audio) -> None:
        result = self._chain(built_speech, np.zeros(1600))
        assert result.backend == "unavailable"
        assert "All ASR backends failed" in (result.error or "")
        assert gemini_audio == []

    def test_gemini_hearing_nothing_is_not_a_failure(self, built_speech, gemini_audio, monkeypatch) -> None:
        monkeypatch.setattr("httpx.post", lambda *_a, **_k: _Answer(text=""))
        result = self._chain(built_speech, np.full(1600, 0.2))
        assert result.backend == "gemini_audio"
        assert result.error is None

    def test_speech_reaches_gemini_once_every_other_tier_has_failed(self, built_speech, gemini_audio) -> None:
        result = self._chain(built_speech, np.full(1600, 0.2))
        assert result.text == "what is the vat rate"
        assert result.backend == "gemini_audio"
