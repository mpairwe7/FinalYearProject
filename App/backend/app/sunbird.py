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

# Sunbird TTS voices — orpheus-3b-tts catalog tags, per the API's own guide.
#
# These are NOT the numeric speaker ids this table used to hold. Numeric ids
# address spark-tts; /tasks/audio/speech serves orpheus-3b-tts, which takes a
# catalog tag and rejects an id with 400. That mismatch is why every
# non-English voice failed even after the endpoint itself was corrected.
#
# `salt_*_0001` is each language's first catalog voice and is used as the
# default. Other tags exist per language (waxal_lug_0002..0008,
# waxal_ach_0001/0005/0006/0008, waxal_nyn_0003/0004/0007/0008,
# waxal_swa_0007) if a different speaker is ever wanted.
TTS_VOICES: dict[str, str] = {
    "lg": "salt_lug_0001",    # Luganda
    "nyn": "salt_nyn_0001",   # Runyankole
    "ach": "salt_ach_0001",   # Acholi
    "sw": "waxal_swa_0006",   # Swahili — the catalog exposes no salt_swa_* tag
    "en": "salt_eng_0001",    # last-resort only; edge-tts serves English first
}
# Ateso (teo) has salt_teo_0001 available but is not in the UI's locale picker.
# Lugbara (lgg) is in the model's training mix but exposes NO voice id, so it
# would have no narration even if it were offered.

# Languages supported for TRANSLATION (Sunbird NLLB-based): English plus the
# five Ugandan languages their production translate endpoint is trained on —
# Acholi, Ateso, Luganda, Lugbara, Runyankole.
#
# "swa" is deliberately absent. Swahili appears in Sunbird's SALT *dataset* and
# has a TTS voice (see TTS_VOICES), which makes it look like an oversight —
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
        files = {"audio": (filename, io.BytesIO(audio_bytes), "audio/wav")}
        # /tasks/modal/stt was retired; /tasks/audio/transcriptions replaces it
        # (along with /stt, /stt_from_gcs and /org/stt). `language` is REQUIRED
        # here — it used to be sent only when truthy, which would have meant
        # omitting a mandatory field rather than falling back to English.
        data: dict[str, Any] = {"language": language or "eng"}
        resp = _post("/tasks/audio/transcriptions", files=files, data=data)
        result = resp.json()
        transcription = (
            result.get("output", {}).get("audio_transcription")
            or result.get("audio_transcription", "")
        )
        logger.info("Sunbird STT (%s): '%s'", language, transcription[:80])
        return {"text": transcription, "language": result.get("language", language)}
    except httpx.HTTPStatusError as e:
        body = ""
        try:
            body = e.response.text[:400]
        except Exception:  # noqa: BLE001 — body may be unreadable; status still useful
            pass
        logger.warning("Sunbird STT failed (%s): %s | body=%s", language, e, body)
        return None
    except Exception as e:
        logger.warning("Sunbird STT failed (%s): %s", language, e)
        return None


# ── Text-to-Speech ────────────────────────────────────────────────────────

def text_to_speech(
    text: str,
    locale: str = "en",
) -> dict[str, Any] | None:
    """Convert text to speech via Sunbird native voices."""
    voice = TTS_VOICES.get(locale)
    if not is_available() or not voice:
        return None
    lang_code = LOCALE_TO_SUNBIRD.get(locale)
    try:
        # orpheus-3b-tts is the documented model for this endpoint and the only
        # one that answers here. It takes a catalog tag as `voice` and an ISO
        # 639-3 `language`; numeric speaker ids belong to spark-tts, and sending
        # one is what the API was rejecting with 400.
        payload: dict[str, Any] = {
            "text": text[:10000],
            "model": "orpheus-3b-tts",
            "voice": voice,
            "response_mode": "url",
        }
        if lang_code:
            payload["language"] = lang_code  # orpheus-only field
        resp = _post("/tasks/audio/speech", json=payload)
        data = resp.json()
        audio_url = data.get("output", {}).get("audio_url") or data.get("audio_url")
        logger.info("Sunbird TTS (%s, voice %s): url=%s", locale, voice,
                     (audio_url or "")[:60])
        return {
            "audio_url": audio_url,
            "file_name": data.get("gcs_object") or data.get("file_name"),
            # The response names this `audio_url_expires_at`; the old key is
            # still read so rolling the Space back does not lose the field.
            # Signed URLs expire in ~30 minutes, so it must be fetched promptly.
            "expires_at": data.get("audio_url_expires_at") or data.get("expires_at"),
        }
    except httpx.HTTPStatusError as e:
        # The status alone cost a full deploy cycle to diagnose last time: a
        # bare "400 Bad Request" says nothing about *which* field was wrong.
        # The body carries the validation detail, so log it (truncated).
        body = ""
        try:
            body = e.response.text[:400]
        except Exception:  # noqa: BLE001 — body may be unreadable; status still useful
            pass
        logger.warning("Sunbird TTS failed (%s, voice %s): %s | body=%s",
                       locale, voice, e, body)
        return None
    except Exception as e:
        logger.warning("Sunbird TTS failed (%s, voice %s): %s", locale, voice, e)
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
