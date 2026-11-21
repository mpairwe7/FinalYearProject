"""A hung primary Sunbird account must leave the fallback time to answer (G714).

On the Space (2026-11-21) a Luganda transcription returned ``backend: unavailable``:
the primary account hung for its whole 30 s ``SUNBIRD_TIMEOUT``, the fallback started
with 10 s of the 40 s ``SPEECH_CLOUD_DEADLINE_S`` left, the deadline expired, and the
fallback's transcript arrived after the request had failed.

These tests run the speech chain against a local stand-in for ``api.sunbird.ai`` whose
accounts answer, or hang, on cue. Every time is the live one scaled by 0.1: a 4 s
deadline and a 3 s per-account timeout. The fallback answers in 1.2 s, as one that
needs 12 s of a 40 s deadline would.
"""

from __future__ import annotations

import json
import select
import socket
import struct
import sys
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import sunbird  # noqa: E402
from app.speech import speech_service as ss  # noqa: E402

DEADLINE_S = 4.0
ACCOUNT_TIMEOUT_S = 3
FALLBACK_S = 1.2
HANG = None


def _wav(num_samples: int = 2400, rate: int = 24000) -> bytes:
    pcm = b"\x00\x00" * num_samples
    return (
        b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
        + b"data" + struct.pack("<I", len(pcm)) + pcm
    )


class FakeSunbird:
    """Answers each account token after its own delay, or holds the request until the client leaves."""

    def __init__(self, delays: dict[str, float | None]) -> None:
        self.delays = delays
        self.requests: list[tuple[str, str]] = []
        self.ended: list[float] = []
        self.stop = threading.Event()
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                body = _wav()
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self._read_body()
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                fake.requests.append((token, self.path))
                delay = fake.delays[token]
                if delay is HANG:
                    self._wait_for_client_to_leave()
                else:
                    time.sleep(delay)
                    self._answer(token)
                fake.ended.append(time.monotonic())

            def _read_body(self):
                if "chunked" in self.headers.get("Transfer-Encoding", ""):
                    while (size := int(self.rfile.readline().strip() or b"0", 16)) > 0:
                        self.rfile.read(size + 2)
                    self.rfile.readline()
                else:
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))

            def _wait_for_client_to_leave(self):
                conn = self.connection
                while not fake.stop.is_set():
                    readable, _, _ = select.select([conn], [], [], 0.02)
                    if readable and not conn.recv(1, socket.MSG_PEEK):
                        return

            def _answer(self, token):
                port = self.server.server_address[1]
                payload = {
                    "/tasks/audio/transcriptions": {
                        "audio_transcription": f"{token} transcript", "language": "lug",
                    },
                    "/tasks/audio/speech": {"audio_url": f"http://127.0.0.1:{port}/audio.wav"},
                    "/tasks/translate": {"output": {"translated_text": f"{token} translation"}},
                }[self.path]
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,), daemon=True).start()

    def wait_for_requests_to_end(self, count: int, within_s: float) -> None:
        limit = time.monotonic() + within_s
        while len(self.ended) < count and time.monotonic() < limit:
            time.sleep(0.02)

    def close(self) -> None:
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def sunbird_accounts(monkeypatch: pytest.MonkeyPatch):
    """Point the real client at a FakeSunbird with the given account delays."""
    started: list[FakeSunbird] = []
    clients: dict = {}

    def start(**delays: float | None) -> FakeSunbird:
        fake = FakeSunbird(delays)
        started.append(fake)
        tokens = list(delays)
        monkeypatch.setattr(sunbird, "SUNBIRD_API_URL", fake.url)
        monkeypatch.setattr(sunbird, "SUNBIRD_API_TOKEN", tokens[0])
        monkeypatch.setattr(sunbird, "SUNBIRD_FALLBACK_API_TOKEN", tokens[1] if len(tokens) > 1 else "")
        return fake

    monkeypatch.setattr(sunbird, "_clients", clients)
    monkeypatch.setattr(sunbird, "_quota_cooldown_until", {})
    monkeypatch.setattr(sunbird, "SUNBIRD_TIMEOUT", ACCOUNT_TIMEOUT_S)
    monkeypatch.setattr(ss, "SPEECH_CLOUD_DEADLINE_S", DEADLINE_S)
    yield start
    for fake in started:
        fake.close()
    for client in clients.values():
        client.close()


class _AsrModel:
    """The state the transcription chain reads, with every tier but Sunbird absent."""

    enabled = True
    _asr = None
    _lang_det = None
    _whisper_peft = None
    _whisper_salt = None
    _accent_detector = None
    _whisper_adapters: dict = {}
    _breakers: dict = {}
    _executor = None

    def _transcribe_faster_whisper(self, *_a, **_kw):
        return None

    def _cf_whisper_transcribe(self, *_a, **_kw) -> str:
        return ""

    def _decode_audio_bytes(self, *_a, **_kw):
        return np.zeros(1600, dtype="float32")


def _transcribe_luganda() -> tuple[ss.TranscribeResult, float]:
    started = time.monotonic()
    result = ss.SpeechModel._transcribe_chain(_AsrModel(), b"\x00\x00" * 1600, 16000, "lg")
    return result, time.monotonic() - started


def _tts_model() -> ss.SpeechModel:
    model = ss.SpeechModel.__new__(ss.SpeechModel)
    model.enabled = True
    model._tts_cache = OrderedDict()
    model._tts_cache_lock = threading.Lock()
    model._spark_tts = None
    breaker = MagicMock()
    breaker.allow_request.return_value = False
    model._breakers = {"tts": breaker}
    model._synthesize_gemini_tts = lambda *_a, **_kw: None
    model._synthesize_edge_tts = MagicMock(side_effect=RuntimeError("edge-tts is not under test"))
    return model


def test_a_hung_primary_leaves_the_fallback_time_to_transcribe(sunbird_accounts) -> None:
    fake = sunbird_accounts(primary=HANG, fallback=FALLBACK_S)
    result, elapsed = _transcribe_luganda()
    assert (result.backend, result.text) == ("sunbird_cloud", "fallback transcript")
    assert elapsed < DEADLINE_S
    assert [token for token, _path in fake.requests] == ["primary", "fallback"]


def test_a_hung_primary_leaves_the_fallback_time_to_speak(sunbird_accounts) -> None:
    fake = sunbird_accounts(primary=HANG, fallback=FALLBACK_S)
    started = time.monotonic()
    result = _tts_model().synthesize("Oli otya", language="lg")
    assert result.backend == "sunbird_cloud"
    assert time.monotonic() - started < DEADLINE_S
    assert [token for token, _path in fake.requests] == ["primary", "fallback"]


def test_a_primary_that_answers_is_the_only_account_asked(sunbird_accounts) -> None:
    fake = sunbird_accounts(primary=0.5, fallback=FALLBACK_S)
    result, _elapsed = _transcribe_luganda()
    assert (result.backend, result.text) == ("sunbird_cloud", "primary transcript")
    assert fake.requests == [("primary", "/tasks/audio/transcriptions")]


def test_a_single_account_keeps_its_whole_timeout(sunbird_accounts) -> None:
    """With nothing to fail over to, the one account is not cut to a share: 2.5 s is past half the deadline."""
    fake = sunbird_accounts(primary=2.5)
    result, elapsed = _transcribe_luganda()
    assert (result.backend, result.text) == ("sunbird_cloud", "primary transcript")
    assert elapsed < DEADLINE_S
    assert len(fake.requests) == 1


def test_two_hung_accounts_stop_at_the_deadline(sunbird_accounts) -> None:
    """The caller moves on at the deadline either way; the Sunbird requests must not outlive it."""
    fake = sunbird_accounts(primary=HANG, fallback=HANG)
    started = time.monotonic()
    result, elapsed = _transcribe_luganda()
    assert result.backend == "unavailable"
    assert elapsed < DEADLINE_S + 1.0
    fake.wait_for_requests_to_end(2, within_s=2 * ACCOUNT_TIMEOUT_S + 2.0)
    assert [token for token, _path in fake.requests] == ["primary", "fallback"]
    assert max(fake.ended) - started < DEADLINE_S + 1.0, "a Sunbird request outlived the speech deadline"


def test_translation_already_left_the_fallback_its_whole_timeout(sunbird_accounts, monkeypatch) -> None:
    """``/tasks/translate`` caps each account at 12 s, so two fit in the 40 s deadline; it had no G714."""
    monkeypatch.setattr(sunbird, "SUNBIRD_TRANSLATE_TIMEOUT", 1.2)
    sunbird_accounts(primary=HANG, fallback=0.5)
    started = time.monotonic()
    assert sunbird.translate("Oli otya", "lug", "eng") == "fallback translation"
    assert time.monotonic() - started < DEADLINE_S
