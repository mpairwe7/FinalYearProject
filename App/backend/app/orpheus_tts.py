"""Client for the Orpheus-3B Sunbird TTS sidecar — the receptionist's local voice.

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
| ``ORPHEUS_TTS_SPEAKER_EN`` | ``salt_eng_0001``| English (Ugandan) speaker id             |
| ``ORPHEUS_TTS_TIMEOUT_S``  | ``20``           | per-request ceiling                      |
| ``ORPHEUS_TTS_MAX_CHARS``  | ``120``          | longest text sent in one request         |

A connection failure opens a short cooldown (:data:`COOLDOWN_S`) so a dead
sidecar costs one timeout, not one per sentence; callers fall through to the
next voice on :class:`OrpheusUnavailable`.
"""

from __future__ import annotations

import concurrent.futures
import logging
import os
import re
import threading
import time
from collections.abc import AsyncIterator

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000
COOLDOWN_S = 30.0


class OrpheusUnavailable(RuntimeError):
    """The sidecar is not configured, not reachable, or refused the request."""


class OrpheusIncomplete(RuntimeError):
    """A stream stopped after its first audio: the caller heard only part of it.

    Deliberately not an :class:`OrpheusUnavailable`: that one sends a sentence
    to the next voice, which would repeat the part already heard. The caller
    keeps what it has but must not cache it as the whole sentence.
    """


def _url() -> str:
    return os.getenv("ORPHEUS_TTS_URL", "").strip().rstrip("/")


def _timeout_s() -> float:
    try:
        return float(os.getenv("ORPHEUS_TTS_TIMEOUT_S", "20"))
    except ValueError:
        return 20.0


def _max_chars() -> int:
    try:
        return max(40, int(os.getenv("ORPHEUS_TTS_MAX_CHARS", "120")))
    except ValueError:
        return 120


_DEFAULT_SPEAKERS = {
    # ISO 639-1 UI codes
    "lg": "salt_lug_0001",
    "sw": "waxal_swa_0006",
    "en": "salt_eng_0001",
    # ISO 639-3 Sunbird/tts config codes (per Sunbird/orpheus-3b-tts-multilingual model card)
    "lug": "salt_lug_0001",
    "swa": "waxal_swa_0006",
    "eng": "salt_eng_0001",
    "nyn": "salt_nyn_0001",
    "ach": "salt_ach_0001",
    "teo": "salt_teo_0001",
    "luo": "waxal_luo_0001",
    "kin": "bateesa_kin_0001",
    "rw": "bateesa_kin_0001",
    "kik": "waxal_kik_0003",
    "ki": "waxal_kik_0003",
    "yor": "waxal_yor_0002",
    "yo": "waxal_yor_0002",
    "hau": "waxal_hau_0004",
    "ha": "waxal_hau_0004",
    "ibo": "waxal_ibo_0003",
    "ig": "waxal_ibo_0003",
    "afr": "slr32_afr_0009",
    "af": "slr32_afr_0009",
    "xho": "slr32_xho_0012",
    "xh": "slr32_xho_0012",
    "ewe": "slr129_ewe_0001",
    "ee": "slr129_ewe_0001",
    "ful": "waxal_ful_0003",
    "ff": "waxal_ful_0003",
    "lin": "slr129_lin_0001",
    "ln": "slr129_lin_0001",
}
_ISO_EQUIVALENTS: dict[str, tuple[str, ...]] = {
    "lg": ("lg", "lug"),
    "lug": ("lg", "lug"),
    "sw": ("sw", "swa"),
    "swa": ("sw", "swa"),
    "en": ("en", "eng"),
    "eng": ("en", "eng"),
    "nyn": ("nyn",),
    "ach": ("ach",),
    "teo": ("teo",),
}
_SENTENCE_BREAK_RE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_BREAK_RE = re.compile(r"(?<=[,;:\u2014\u2013])\s+")

_lock = threading.Lock()
_down_until = 0.0
_probe_lock = threading.Lock()
_probe_until = 0.0
_probe_state = "unknown"


def is_configured() -> bool:
    return bool(_url())


def in_cooldown() -> bool:
    """True when a recent connection failure said not to call the sidecar."""
    with _lock:
        return time.monotonic() < _down_until


def speaker_for(language: str) -> str | None:
    """The Orpheus speaker for *language* (ISO 639-1 or ISO 639-3), or ``None`` if unvoiced."""
    if not is_configured():
        return None
    norm = (language or "").strip().lower()
    if not norm:
        return None
    enabled = {p.strip().lower() for p in os.getenv("ORPHEUS_TTS_LANGUAGES", "lg,sw,en,nyn,ach").split(",") if p.strip()}
    equivalents = _ISO_EQUIVALENTS.get(norm, (norm,))
    if not any(code in enabled for code in equivalents):
        return None
    canonical = equivalents[0]
    return (
        os.getenv(f"ORPHEUS_TTS_SPEAKER_{canonical.upper()}", "").strip()
        or os.getenv(f"ORPHEUS_TTS_SPEAKER_{norm.upper()}", "").strip()
        or _DEFAULT_SPEAKERS.get(norm)
    )


def _check_cooldown() -> None:
    with _lock:
        if time.monotonic() < _down_until:
            raise OrpheusUnavailable("sidecar in cooldown after a connection failure")


def _mark_down(exc: Exception) -> None:
    global _down_until
    with _lock:
        _down_until = time.monotonic() + COOLDOWN_S
    logger.warning("Orpheus TTS unreachable (%s); skipping it for %.0fs", type(exc).__name__, COOLDOWN_S)


def _tcp_probe() -> str:
    """``up`` when the sidecar port accepts a connection, else ``down``.

    A name that does not resolve (the container is stopped) fails here in
    well under a second. The speech request path must not rediscover that
    with ``ORPHEUS_TTS_TIMEOUT_S``.
    """
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(_url())
    host = parsed.hostname
    if not host:
        return "down"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=0.4):
            return "up"
    except OSError:
        return "down"


def reachability() -> str:
    """``unconfigured``, ``cooldown``, ``up``, or ``down``.

    A down result opens the same cooldown as a failed synthesis, so the next
    Luganda sentence goes straight to Spark-TTS-SALT on the local GPU instead
    of waiting on a sidecar that is not there. Cached for 15 seconds.
    """
    global _probe_until, _probe_state
    if not is_configured():
        return "unconfigured"
    if in_cooldown():
        return "cooldown"
    now = time.monotonic()
    with _probe_lock:
        if now < _probe_until and _probe_state in ("up", "down"):
            return _probe_state
    state = _tcp_probe()
    with _probe_lock:
        _probe_state = state
        _probe_until = time.monotonic() + 15.0
    if state == "down":
        _mark_down(OSError("sidecar probe failed"))
        return "cooldown"
    return "up"


def _request(text: str, language: str, response_format: str) -> tuple[str, dict[str, object]]:
    speaker = speaker_for(language)
    if speaker is None:
        raise OrpheusUnavailable(f"no Orpheus voice configured for {language!r}")
    _check_cooldown()
    body = {"input": text, "voice": speaker, "response_format": response_format}
    return f"{_url()}/v1/audio/speech", body


def _first_clause_max_chars() -> int | None:
    raw = os.getenv("ORPHEUS_TTS_FIRST_CLAUSE_MAX_CHARS", "").strip()
    if not raw:
        return None
    try:
        return max(30, int(raw))
    except ValueError:
        return None


def split_for_voice(
    text: str,
    limit: int | None = None,
    *,
    first_clause_limit: int | None = None,
    low_bandwidth: bool = False,
) -> list[str]:
    """*text* in pieces of at most *limit* characters, cut at sentence, clause, then word breaks.

    The sidecar stops at ``ORPHEUS_MAX_TOKENS`` (1400 tokens, 16.98 s of audio)
    whatever it was asked to say. Sent whole, a 335-character Luganda sentence
    was cut off mid-word on a call, and every /v1/voice/chat reply ended at
    exactly 16.98 s. Speech runs up to ~0.12 s a character when digits are read
    one by one, so the default 120 characters stays inside the cap.

    When *first_clause_limit* (or ``ORPHEUS_TTS_FIRST_CLAUSE_MAX_CHARS``, or
    *low_bandwidth=True*) is set, the very first clause is emitted with a tighter
    cap (~55 chars standard, ~38 chars on low-bandwidth 2G/3G) so the caller hears
    the opening audio ~140-200 ms sooner.
    """
    limit = limit or _max_chars()
    first_cap = (
        first_clause_limit
        if first_clause_limit is not None
        else (38 if low_bandwidth else _first_clause_max_chars())
    )
    if len(text) <= limit and (first_cap is None or len(text) <= first_cap):
        return [text]
    units: list[str] = []
    for sentence in _SENTENCE_BREAK_RE.split(" ".join(text.split())):
        if len(sentence) <= limit and (units or first_cap is None or len(sentence) <= first_cap):
            units.append(sentence)
            continue
        for clause in _CLAUSE_BREAK_RE.split(sentence):
            units.extend([clause] if len(clause) <= limit else _pack(clause.split(" "), limit))
    packed = _pack(units, limit)
    if first_cap is not None and packed and len(packed[0]) > first_cap:
        clauses = _CLAUSE_BREAK_RE.split(packed[0])
        if len(clauses) > 1 and len(clauses[0]) <= first_cap:
            head = clauses[0]
            tail = " ".join(clauses[1:])
            return [head, *_pack([tail, *packed[1:]], limit)]
        if low_bandwidth:
            word_pieces = _pack(packed[0].split(" "), first_cap)
            if len(word_pieces) > 1 and len(word_pieces[0]) <= first_cap:
                head = word_pieces[0]
                tail = packed[0][len(head) :].lstrip()
                return [head, *_pack([tail, *packed[1:]], limit)]
    return packed


def _pack(units: list[str], limit: int) -> list[str]:
    """Consecutive *units* joined by spaces into as few pieces of at most *limit* as fit."""
    pieces: list[str] = []
    for unit in units:
        if pieces and len(pieces[-1]) + 1 + len(unit) <= limit:
            pieces[-1] = f"{pieces[-1]} {unit}"
        else:
            pieces.append(unit)
    return pieces


def pcm16_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Orpheus's 24 kHz PCM as a WAV (the phrase cache stores WAVs)."""
    from .speech_service import pcm16_to_wav as wrap

    return wrap(pcm, sample_rate)


def synthesize(text: str, language: str) -> bytes:
    """Whole utterance as 24 kHz PCM16. Raises :class:`OrpheusUnavailable`.

    Longer text is voiced as :func:`split_for_voice` pieces, concurrently (the
    sidecar batches them), and joined in order.
    """
    url, body = _request(text, language, "pcm")
    pieces = split_for_voice(text)
    if len(pieces) == 1:
        return _post(url, body)
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=min(3, len(pieces)), thread_name_prefix="orpheus"
    ) as pool:
        return b"".join(pool.map(lambda piece: _post(url, {**body, "input": piece}), pieces))


def synthesize_batch(
    items: list[dict[str, str]],
    *,
    temperature: float = 0.6,
    top_p: float = 0.95,
    repetition_penalty: float = 1.1,
    seed: int | None = None,
) -> list[bytes]:
    """Multi-speaker batched synthesis via the sidecar's ``/v1/audio/speech/batch`` endpoint.

    *items* is a list of ``{"text": str, "speaker_id": str}`` (or ``{"text": str, "language": str}``)
    dictionaries — different speakers and languages can be mixed in one GPU pass.
    Returns 24 kHz PCM16 byte buffers in the same order.
    """
    import base64
    import httpx

    if not items:
        return []
    if not is_configured():
        raise OrpheusUnavailable("ORPHEUS_TTS_URL is not configured")
    _check_cooldown()

    normalized_items: list[dict[str, str]] = []
    for it in items:
        text = str(it.get("text") or "").strip()
        spk = str(it.get("speaker_id") or "").strip()
        if not spk:
            lang = str(it.get("language") or "lg").strip()
            spk = speaker_for(lang) or "salt_lug_0001"
        normalized_items.append({"text": text, "speaker_id": spk})

    payload: dict[str, object] = {
        "items": normalized_items,
        "temperature": temperature,
        "top_p": top_p,
        "repetition_penalty": repetition_penalty,
    }
    if seed is not None:
        payload["seed"] = seed

    url = f"{_url()}/v1/audio/speech/batch"
    try:
        resp = httpx.post(url, json=payload, timeout=_timeout_s())
    except httpx.HTTPError as exc:
        _mark_down(exc)
        raise OrpheusUnavailable(str(exc)) from exc
    if resp.status_code != 200:
        raise OrpheusUnavailable(f"Orpheus batch returned HTTP {resp.status_code}")
    data = resp.json()
    out_items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(out_items, list) or len(out_items) != len(normalized_items):
        raise OrpheusUnavailable("Orpheus batch returned malformed items")
    return [base64.b64decode(str(entry.get("pcm_b64") or "")) for entry in out_items]


def _post(url: str, body: dict[str, object]) -> bytes:
    import httpx

    try:
        resp = httpx.post(url, json=body, timeout=_timeout_s())
    except httpx.HTTPError as exc:
        _mark_down(exc)
        raise OrpheusUnavailable(str(exc)) from exc
    if resp.status_code != 200 or not resp.content:
        raise OrpheusUnavailable(f"HTTP {resp.status_code}")
    # Whole samples only: an odd byte would shift the next piece's samples.
    return resp.content[: len(resp.content) - len(resp.content) % 2]


async def stream(text: str, language: str) -> AsyncIterator[bytes]:
    """24 kHz PCM16 chunks as the sidecar decodes them.

    Raises :class:`OrpheusUnavailable` before the first chunk if the sidecar
    cannot be used. A failure after audio has started raises
    :class:`OrpheusIncomplete` instead: the caller has already heard part of
    the sentence, and falling back to a different voice mid-sentence would be
    worse. Longer text is voiced as :func:`split_for_voice` pieces, one after
    another: each streams faster than it plays, so the next piece is ready
    before the last one ends.
    """
    import httpx

    url, body = _request(text, language, "pcm")
    started = False
    try:
        async with httpx.AsyncClient(timeout=_timeout_s()) as client:
            for piece in split_for_voice(text):
                async with client.stream("POST", url, json={**body, "input": piece}) as resp:
                    if resp.status_code != 200:
                        if started:
                            logger.warning("Orpheus refused the rest of an utterance: HTTP %d", resp.status_code)
                            raise OrpheusIncomplete(f"HTTP {resp.status_code}")
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
        raise OrpheusIncomplete(str(exc)) from exc
