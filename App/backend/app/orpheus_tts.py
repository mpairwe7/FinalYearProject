"""Client for the Orpheus-3B Sunbird TTS sidecar — the receptionist's Luganda voice.

Sibling to :mod:`app.spark_tts_salt` (a local batch voice) and
:mod:`app.sunbird` (a cloud one), but a *streaming* voice: the sidecar
(``App/backend/orpheus_sidecar``) returns 24 kHz PCM frame by frame, so the
receptionist hears the first ~85 ms of an answer ~350 ms after asking, instead
of after the whole sentence is rendered (Spark-TTS-SALT ≈ 4.3 s, Sunbird cloud
≈ 7.2 s per sentence — too slow for a live call).

Opt-in by URL: with ``ORPHEUS_TTS_URL`` unset nothing here is ever called and
:func:`speaker_for` answers ``None`` for every language.

| Variable                   | Default          | Meaning                                  |
|----------------------------|------------------|------------------------------------------|
| ``ORPHEUS_TTS_URL``        | unset            | e.g. ``http://orpheus-tts:8100``         |
| ``ORPHEUS_TTS_LANGUAGES``  | ``lg``           | languages Orpheus speaks, comma-separated|
| ``ORPHEUS_TTS_SPEAKER_LG`` | ``salt_lug_0001``| Luganda speaker id (model card table)    |
| ``ORPHEUS_TTS_SPEAKER_SW`` | ``waxal_swa_0006``| Swahili speaker id                      |
| ``ORPHEUS_TTS_TIMEOUT_S``  | ``20``           | per-request ceiling                      |

A connection failure opens a short cooldown (:data:`COOLDOWN_S`) so a dead
sidecar costs one timeout, not one per sentence; callers fall through to the
next voice on :class:`OrpheusUnavailable`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
COOLDOWN_S = 30.0


class OrpheusUnavailable(RuntimeError):
    """The sidecar is not configured, not reachable, or refused the request."""


def _url() -> str:
    return os.getenv("ORPHEUS_TTS_URL", "").strip().rstrip("/")


def _timeout_s() -> float:
    try:
        return float(os.getenv("ORPHEUS_TTS_TIMEOUT_S", "20"))
    except ValueError:
        return 20.0


_DEFAULT_SPEAKERS = {"lg": "salt_lug_0001", "sw": "waxal_swa_0006"}

_lock = threading.Lock()
_down_until = 0.0


def is_configured() -> bool:
    return bool(_url())


def speaker_for(language: str) -> str | None:
    """The Orpheus speaker for *language*, or ``None`` if Orpheus does not voice it here."""
    if not is_configured():
        return None
    enabled = {p.strip() for p in os.getenv("ORPHEUS_TTS_LANGUAGES", "lg").split(",") if p.strip()}
    if language not in enabled:
        return None
    return os.getenv(f"ORPHEUS_TTS_SPEAKER_{language.upper()}", "").strip() or _DEFAULT_SPEAKERS.get(language)


def _check_cooldown() -> None:
    with _lock:
        if time.monotonic() < _down_until:
            raise OrpheusUnavailable("sidecar in cooldown after a connection failure")


def _mark_down(exc: Exception) -> None:
    global _down_until
    with _lock:
        _down_until = time.monotonic() + COOLDOWN_S
    logger.warning("Orpheus TTS unreachable (%s); skipping it for %.0fs", type(exc).__name__, COOLDOWN_S)


def _request(text: str, language: str, response_format: str) -> tuple[str, dict[str, object]]:
    speaker = speaker_for(language)
    if speaker is None:
        raise OrpheusUnavailable(f"no Orpheus voice configured for {language!r}")
    _check_cooldown()
    body = {"input": text, "voice": speaker, "response_format": response_format}
    return f"{_url()}/v1/audio/speech", body


def pcm16_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Orpheus's 24 kHz PCM as a WAV (the phrase cache stores WAVs)."""
    from .speech_service import pcm16_to_wav as wrap

    return wrap(pcm, sample_rate)


def synthesize(text: str, language: str) -> bytes:
    """Whole utterance as 24 kHz PCM16. Raises :class:`OrpheusUnavailable`."""
    import httpx

    url, body = _request(text, language, "pcm")
    try:
        resp = httpx.post(url, json=body, timeout=_timeout_s())
    except httpx.HTTPError as exc:
        _mark_down(exc)
        raise OrpheusUnavailable(str(exc)) from exc
    if resp.status_code != 200 or not resp.content:
        raise OrpheusUnavailable(f"HTTP {resp.status_code}")
    return resp.content


async def stream(text: str, language: str) -> AsyncIterator[bytes]:
    """24 kHz PCM16 chunks as the sidecar decodes them.

    Raises :class:`OrpheusUnavailable` before the first chunk if the sidecar
    cannot be used; a failure after audio has started ends the stream early
    (the caller has already heard part of the sentence — falling back to a
    different voice mid-sentence would be worse).
    """
    import httpx

    url, body = _request(text, language, "pcm")
    started = False
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            async with client.stream("POST", url, json=body) as resp:
                if resp.status_code != 200:
                    raise OrpheusUnavailable(f"HTTP {resp.status_code}")
                carry = b""
                async for chunk in resp.aiter_bytes():
                    data = carry + chunk
                    # Keep whole 16-bit samples: an odd split byte would
                    # shift every later sample by one byte (loud noise).
                    cut = len(data) - (len(data) % 2)
                    carry = data[cut:]
                    if cut:
                        started = True
                        yield data[:cut]
    except httpx.HTTPError as exc:
        if not started:
            _mark_down(exc)
            raise OrpheusUnavailable(str(exc)) from exc
        logger.warning("Orpheus stream broke mid-utterance: %s", exc)
