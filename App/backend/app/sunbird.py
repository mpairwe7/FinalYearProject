"""URA Chatbot — Sunbird AI API integration (cloud fallback).

Provides multilingual Ugandan language support via Sunbird AI:
- Translation (English ↔ Luganda / Runyankole / Acholi)
- Speech-to-Text (STT) with Ugandan language models
- Text-to-Speech (TTS) with native speaker voices
- Language auto-detection

Used as a cloud fallback when local ASR/TTS/MT models are unavailable
or too slow (e.g. first-run cold start, low-resource environments).

API docs: https://docs.sunbird.ai
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("ura.sunbird")

# ── Config ────────────────────────────────────────────────────────────────

SUNBIRD_API_URL = os.getenv("SUNBIRD_API_URL", "https://api.sunbird.ai")
SUNBIRD_API_TOKEN = os.getenv("SUNBIRD_API_TOKEN", "")
# Fallback account — used for resilience when the primary token fails (auth,
# rate-limit, transient errors). Set via "# Sunbird fall back account" in .env
# (SUNBIRD_FALLBACK_API_TOKEN). A request is retried on this account if the
# primary one fails.
SUNBIRD_FALLBACK_API_TOKEN = os.getenv("SUNBIRD_FALLBACK_API_TOKEN", "")
SUNBIRD_TIMEOUT = int(os.getenv("SUNBIRD_TIMEOUT", "30"))

# URA locale → Sunbird language code
LOCALE_TO_SUNBIRD: dict[str, str] = {
    "en": "eng",
    "lg": "lug",      # Luganda
    "nyn": "nyn",     # Runyankole
    "ach": "ach",     # Acholi
    "sw": "swa",      # Swahili
}

# Sunbird TTS speaker IDs (native speakers)
TTS_SPEAKERS: dict[str, int] = {
    "lg": 248,    # Luganda female
    "nyn": 243,   # Runyankole female
    "sw": 246,    # Swahili male
    "en": 246,    # fallback English
}

# Languages supported for translation (Sunbird NLLB-based)
TRANSLATION_LANGUAGES = {"eng", "lug", "nyn", "ach", "teo", "lgg"}

# Sunbird code → URA locale (reverse mapping)
_SUNBIRD_TO_LOCALE: dict[str, str] = {
    "eng": "en", "lug": "lg", "nyn": "nyn", "ach": "ach",
    "swa": "sw", "teo": "en", "lgg": "en",
}

# ── Client ────────────────────────────────────────────────────────────────

# One client per account token (lazily built, reused). A call that fails on the
# primary account is retried on the fallback account for resilience.
_clients: dict[str, httpx.Client] = {}


def _account_tokens() -> list[str]:
    """Configured account tokens in priority order (primary, then fallback)."""
    return [t for t in (SUNBIRD_API_TOKEN, SUNBIRD_FALLBACK_API_TOKEN) if t]


def _client_for(token: str) -> httpx.Client:
    client = _clients.get(token)
    if client is None:
        client = httpx.Client(
            base_url=SUNBIRD_API_URL,
            headers={"Authorization": f"Bearer {token}"},
            timeout=SUNBIRD_TIMEOUT,
        )
        _clients[token] = client
    return client


def is_available() -> bool:
    """True if at least one Sunbird account (primary or fallback) is configured."""
    return bool(_account_tokens())


def _post(path: str, **kwargs: Any) -> httpx.Response:
    """POST to Sunbird, trying the primary account then the fallback account.

    Retries on the next account for ANY failure (auth, rate-limit, transient
    5xx, network). Raises the last error if every configured account fails.
    """
    tokens = _account_tokens()
    if not tokens:
        raise RuntimeError("no Sunbird token configured")
    last_exc: Exception | None = None
    for idx, token in enumerate(tokens):
        try:
            resp = _client_for(token).post(path, **kwargs)
            resp.raise_for_status()
            if idx > 0:
                logger.info("Sunbird fallback account served %s", path)
            return resp
        except Exception as e:  # noqa: BLE001 — try the next account, re-raise if last
            last_exc = e
            if idx + 1 < len(tokens):
                logger.warning("Sunbird account #%d failed for %s (%s); trying fallback", idx + 1, path, e)
    raise last_exc  # type: ignore[misc]


def _get_client() -> httpx.Client:
    """Back-compat shim — the primary account client (prefer ``_post``)."""
    tokens = _account_tokens()
    if not tokens:
        raise RuntimeError("SUNBIRD_API_TOKEN not set")
    return _client_for(tokens[0])


# ── Translation ───────────────────────────────────────────────────────────

def translate(text: str, source_lang: str, target_lang: str) -> str | None:
    """Translate text between supported Ugandan languages and English."""
    if source_lang not in TRANSLATION_LANGUAGES or target_lang not in TRANSLATION_LANGUAGES:
        logger.warning("Translation not supported: %s → %s", source_lang, target_lang)
        return None
    try:
        resp = _post("/tasks/translate", json={
            "source_language": source_lang,
            "target_language": target_lang,
            "text": text,
        })
        data = resp.json()
        result = data.get("output", {}).get("translated_text") or data.get("translated_text")
        if result:
            logger.info("Translated %s→%s: '%s' → '%s'", source_lang, target_lang,
                        text[:50], result[:50])
        return result
    except Exception as e:
        logger.warning("Sunbird translate failed: %s", e)
        return None


def translate_to_english(text: str, locale: str) -> str | None:
    if locale == "en":
        return None
    src = LOCALE_TO_SUNBIRD.get(locale)
    if not src or src not in TRANSLATION_LANGUAGES:
        return None
    return translate(text, src, "eng")


def translate_from_english(text: str, locale: str) -> str | None:
    if locale == "en":
        return None
    tgt = LOCALE_TO_SUNBIRD.get(locale)
    if not tgt or tgt not in TRANSLATION_LANGUAGES:
        return None
    return translate(text, "eng", tgt)


# ── Speech-to-Text ────────────────────────────────────────────────────────

def speech_to_text(
    audio_bytes: bytes,
    language: str = "eng",
    filename: str = "audio.wav",
) -> dict[str, Any] | None:
    """Transcribe audio via Sunbird API."""
    if not is_available():
        return None
    try:
        files = {"audio": (filename, io.BytesIO(audio_bytes))}
        data: dict[str, Any] = {}
        if language:
            data["language"] = language
        resp = _post("/tasks/modal/stt", files=files, data=data)
        result = resp.json()
        transcription = (
            result.get("output", {}).get("audio_transcription")
            or result.get("audio_transcription", "")
        )
        logger.info("Sunbird STT (%s): '%s'", language, transcription[:80])
        return {"text": transcription, "language": result.get("language", language)}
    except Exception as e:
        logger.warning("Sunbird STT failed: %s", e)
        return None


# ── Text-to-Speech ────────────────────────────────────────────────────────

def text_to_speech(
    text: str,
    locale: str = "en",
) -> dict[str, Any] | None:
    """Convert text to speech via Sunbird native voices."""
    speaker_id = TTS_SPEAKERS.get(locale)
    if not is_available() or not speaker_id:
        return None
    try:
        resp = _post("/tasks/modal/tts", json={
            "text": text[:10000],
            "speaker_id": speaker_id,
            "response_mode": "url",
        })
        data = resp.json()
        audio_url = data.get("output", {}).get("audio_url") or data.get("audio_url")
        logger.info("Sunbird TTS (%s, speaker %d): url=%s", locale, speaker_id,
                     (audio_url or "")[:60])
        return {
            "audio_url": audio_url,
            "file_name": data.get("file_name"),
            "expires_at": data.get("expires_at"),
        }
    except Exception as e:
        logger.warning("Sunbird TTS failed: %s", e)
        return None


# ── Language Detection ────────────────────────────────────────────────────

def detect_language(text: str) -> dict[str, Any] | None:
    """Detect the language of input text via Sunbird API."""
    if len(text.strip()) < 3:
        return None
    try:
        resp = _post("/tasks/language_id", json={"text": text[:200]})
        data = resp.json()
        lang_code = (
            data.get("output", {}).get("language")
            or data.get("language", "eng")
        )
        confidence = data.get("confidence", data.get("output", {}).get("confidence"))
        locale = _SUNBIRD_TO_LOCALE.get(lang_code, "en")
        logger.info("Language detected: %s → locale=%s (conf=%s)", lang_code, locale, confidence)
        return {
            "locale": locale,
            "sunbird_code": lang_code,
            "confidence": confidence,
        }
    except Exception as e:
        logger.warning("Sunbird language detection failed: %s", e)
        return None
