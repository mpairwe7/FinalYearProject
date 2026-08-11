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
import time
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
# Attempts per account before failing over (1 = no retry). Retries apply to
# timeouts, transport errors, 429 and 5xx — never to auth failures.
SUNBIRD_RETRIES = max(1, int(os.getenv("SUNBIRD_RETRIES", "2")))

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_BACKOFF_BASE_S = 0.5
_RETRY_AFTER_CAP_S = 5.0

# URA locale → Sunbird language code
LOCALE_TO_SUNBIRD: dict[str, str] = {
    "en": "eng",
    "lg": "lug",      # Luganda
    "nyn": "nyn",     # Runyankole
    "ach": "ach",     # Acholi
    "sw": "swa",      # Swahili
}

# Sunbird TTS speaker IDs (native speakers).
#
# Numbering comes from the Sunbird/spark-tts-salt voice catalogue:
#   241 Acholi ♀ · 242 Ateso ♀ · 243 Runyankore ♀ · 245 Lugbara ♀
#   246 Swahili ♂ · 248 Luganda ♀
# The three IDs already here matched that catalogue exactly, gender included,
# which is what confirms this table uses the same numbering rather than a
# coincidental overlap.
TTS_SPEAKERS: dict[str, int] = {
    "lg": 248,    # Luganda female
    "nyn": 243,   # Runyankole female
    "sw": 246,    # Swahili male
    "ach": 241,   # Acholi female
    "en": 246,    # fallback English — the catalogue has no dedicated English
                  # voice, and edge-tts serves English first anyway.
}
# Also available in the catalogue but not wired up, because neither is offered
# in the UI's locale picker: 242 Ateso (teo) and 245 Lugbara (lgg). Both are
# already in TRANSLATION_LANGUAGES, so adding them is a picker change plus one
# line each here rather than new infrastructure.

# Languages supported for TRANSLATION (Sunbird NLLB-based): English plus the
# five Ugandan languages their production translate endpoint is trained on —
# Acholi, Ateso, Luganda, Lugbara, Runyankole.
#
# "swa" is deliberately absent. Swahili appears in Sunbird's SALT *dataset* and
# has a TTS speaker (see TTS_SPEAKERS), which makes it look like an oversight —
# it is not. The translate endpoint does not serve Swahili, so adding it here
# would turn today's graceful "unsupported, fall through to the next backend"
# into a live API error. Swahili translation is handled by the Gemini / Workers
# AI / prompted tiers in speech_service.translate instead.
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


def _rewind_uploads(kwargs: dict[str, Any]) -> None:
    """Seek file-like upload bodies back to 0 so a retry re-sends the bytes.

    Without this, a retried multipart request would silently upload an
    already-consumed (empty) stream.
    """
    for value in (kwargs.get("files") or {}).values():
        stream = value[1] if isinstance(value, tuple) and len(value) > 1 else value
        if hasattr(stream, "seek"):
            try:
                stream.seek(0)
            except Exception:  # noqa: BLE001 — non-seekable; retry sends as-is
                pass


def _retry_delay(attempt: int, retry_after: str = "") -> float:
    """Exponential backoff, honouring a small server Retry-After when given."""
    if retry_after:
        try:
            return min(float(retry_after), _RETRY_AFTER_CAP_S)
        except ValueError:
            pass
    return _BACKOFF_BASE_S * (2 ** (attempt - 1))


def _post(path: str, **kwargs: Any) -> httpx.Response:
    """POST to Sunbird with bounded retry, then account failover.

    Per account: up to :data:`SUNBIRD_RETRIES` attempts with short
    exponential backoff on timeouts, transport errors, and retryable
    statuses (429/5xx — honouring small ``Retry-After`` values). Auth
    failures (401/403) skip straight to the fallback account. Raises the
    last error when every configured account is exhausted.
    """
    tokens = _account_tokens()
    if not tokens:
        raise RuntimeError("no Sunbird token configured")
    last_exc: Exception | None = None
    for idx, token in enumerate(tokens):
        for attempt in range(1, SUNBIRD_RETRIES + 1):
            _rewind_uploads(kwargs)
            try:
                resp = _client_for(token).post(path, **kwargs)
                resp.raise_for_status()
                if idx > 0:
                    logger.info("Sunbird fallback account served %s", path)
                return resp
            except httpx.HTTPStatusError as e:
                last_exc = e
                status = e.response.status_code
                if status in (401, 403):
                    logger.warning(
                        "Sunbird auth failed (%d) on account #%d for %s", status, idx + 1, path
                    )
                    break  # retrying the same credentials cannot help
                if status in _RETRYABLE_STATUSES and attempt < SUNBIRD_RETRIES:
                    delay = _retry_delay(attempt, e.response.headers.get("Retry-After", ""))
                    logger.warning(
                        "Sunbird %d on %s (account #%d, attempt %d); retrying in %.1fs",
                        status, path, idx + 1, attempt, delay,
                    )
                    time.sleep(delay)
                    continue
                break
            except httpx.HTTPError as e:  # timeouts + transport errors
                last_exc = e
                if attempt < SUNBIRD_RETRIES:
                    delay = _retry_delay(attempt)
                    logger.warning(
                        "Sunbird transport error on %s (account #%d, attempt %d): %s; "
                        "retrying in %.1fs", path, idx + 1, attempt, e, delay,
                    )
                    time.sleep(delay)
                    continue
                break
            except Exception as e:  # noqa: BLE001 — unexpected; fail over to next account
                last_exc = e
                break
        if idx + 1 < len(tokens):
            logger.warning(
                "Sunbird account #%d exhausted for %s (%s); trying fallback",
                idx + 1, path, last_exc,
            )
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
        # /tasks/modal/stt was retired; /tasks/audio/transcriptions replaces
        # it (along with /stt, /stt_from_gcs and /org/stt). Same multipart
        # shape — an `audio` part plus a `language` field.
        resp = _post("/tasks/audio/transcriptions", files=files, data=data)
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
        # /tasks/modal/tts was retired and answered 405 for every request —
        # which is why non-English narration had been failing across the
        # board, not just for the locales missing a speaker id.
        # /tasks/audio/speech replaces it (along with /tasks/runpod/tts and
        # /tasks/modal/orpheus/tts) and routes by model. The numeric ids in
        # TTS_SPEAKERS are spark-tts speakers, so the model must be named
        # explicitly; orpheus uses catalog tags like "salt_lug_0001" instead
        # and would reject them. `voice` is a string here, not the old
        # integer `speaker_id`.
        resp = _post("/tasks/audio/speech", json={
            "text": text[:10000],
            "model": "spark-tts",
            "voice": str(speaker_id),
            "response_mode": "url",
        })
        data = resp.json()
        audio_url = data.get("output", {}).get("audio_url") or data.get("audio_url")
        logger.info("Sunbird TTS (%s, speaker %d): url=%s", locale, speaker_id,
                     (audio_url or "")[:60])
        return {
            "audio_url": audio_url,
            "file_name": data.get("file_name"),
            # The new response names this `audio_url_expires_at`; keep reading
            # the old key too so a rollback to an older Space image still
            # populates it.
            "expires_at": data.get("audio_url_expires_at") or data.get("expires_at"),
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
