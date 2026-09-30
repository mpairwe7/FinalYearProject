"""The chat's spoken reply: how soon it is heard, whether it pauses, and its size.

For each language, sends the replay harness's TIN question
(``evals/call_replay/audio/<lang>_tin.wav``) to ``/v1/voice/chat`` for a text
answer, as the chat's voice mode does, then voices that answer two ways:

* **streamed** — ``/v1/tts/stream`` with ``format=opus``, what the chat plays
  now: time to the first piece, and each silence between pieces as a listener
  hears it (a piece that arrives after the one before has finished playing
  leaves a gap);
* **whole** — ``/v1/tts``, one WAV for the whole answer, which is what the chat
  played before the stream existed. Nothing is heard until all of it arrives.

Each voicing opens with its own fresh sentence, so its first piece never comes
from the api's phrase cache. The later pieces of an answer voiced before do,
and are counted in ``cached_pieces``: for the gaps between pieces, run once per
language on a freshly started api, where that count is 0. Render the clips
first with ``scripts/replay_call_audio.py --render-url``.

    python3 scripts/bench_chat_speech.py --api http://127.0.0.1:8083

Writes ``evals/reports/chat_speech_<date>.json``. Stdlib only.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import http.client
import json
import time
import uuid
import wave
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "evals" / "call_replay" / "audio"


def _connect(api: str) -> http.client.HTTPConnection:
    url = urlparse(api)
    cls = http.client.HTTPSConnection if url.scheme == "https" else http.client.HTTPConnection
    return cls(url.hostname or "127.0.0.1", url.port, timeout=180)


def _answer(api: str, lang: str) -> tuple[str, float]:
    """The voice chat's text answer to the TIN question, and how long it took."""
    path = AUDIO_DIR / f"{lang}_tin.wav"
    body = path.read_bytes()
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
    query = urlencode({"language": lang, "sample_rate": rate, "tts_enabled": "false",
                       "conversation_id": f"bench-{uuid.uuid4().hex[:8]}"})
    conn = _connect(api)
    started = time.perf_counter()
    conn.request("POST", f"/v1/voice/chat?{query}", body=body,
                 headers={"Content-Type": "audio/wav", "X-Voice-Consent": "true"})
    resp = conn.getresponse()
    data = json.loads(resp.read())
    elapsed = time.perf_counter() - started
    conn.close()
    if resp.status != 200:
        raise SystemExit(f"/v1/voice/chat {lang}: HTTP {resp.status}: {data}")
    reply = " ".join((data.get("reply") or "").replace("**", "").replace("#", "").split())
    return reply, elapsed


def _fresh(text: str) -> str:
    return f"Reference {uuid.uuid4().hex[:4]}. {text}"


def _streamed(api: str, lang: str, text: str) -> dict[str, Any]:
    conn = _connect(api)
    started = time.perf_counter()
    conn.request("POST", "/v1/tts/stream", body=json.dumps({"text": text, "language": lang, "format": "opus"}),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    first: float | None = None
    play_end = 0.0
    gaps: list[float] = []
    size = pieces = cached = failed = 0
    formats: set[str] = set()
    for raw in resp:
        if not raw.strip():
            continue
        line = json.loads(raw)
        if line.get("error"):
            failed += 1
            continue
        if not line.get("audio_base64"):
            continue
        arrived = time.perf_counter() - started
        size += len(base64.b64decode(line["audio_base64"]))
        pieces += 1
        cached += str(line.get("backend", "")).endswith("+cache")
        formats.add(line.get("format", ""))
        if first is None:
            first, play_end = arrived, arrived + line["duration_s"]
        else:
            gaps.append(round(max(0.0, arrived - play_end), 2))
            play_end = max(play_end, arrived) + line["duration_s"]
    conn.close()
    if first is None:
        raise SystemExit(f"/v1/tts/stream {lang}: no audio ({failed} pieces failed)")
    return {"first_piece_s": round(first, 2), "pieces": pieces, "cached_pieces": cached, "failed": failed,
            "formats": sorted(formats), "gaps_s": gaps, "kb": round(size / 1024, 1),
            "heard_s": round(play_end - first, 1)}


def _whole(api: str, lang: str, text: str) -> dict[str, Any]:
    """One WAV for the whole answer; ``{"error": ...}`` and no timings when none came back."""
    conn = _connect(api)
    started = time.perf_counter()
    conn.request("POST", "/v1/tts", body=json.dumps({"text": text, "language": lang}),
                 headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    raw = resp.read()
    elapsed = time.perf_counter() - started
    conn.close()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    audio = base64.b64decode(data.get("audio_base64") or "")
    if resp.status != 200 or data.get("error") or not audio:
        return {"error": data.get("error") or f"HTTP {resp.status}, {len(audio)} bytes of audio"}
    return {"total_s": round(elapsed, 2), "kb": round(len(audio) / 1024, 1),
            "duration_s": data.get("duration_s"), "backend": data.get("backend")}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", default="http://127.0.0.1:8083")
    ap.add_argument("--languages", default="en,lg,sw")
    ap.add_argument("--runs", type=int, default=1, help="questions per language")
    ap.add_argument("--no-whole", action="store_true", help="skip the whole-WAV comparison")
    args = ap.parse_args()

    runs: dict[str, list[dict[str, Any]]] = {}
    for lang in [x.strip() for x in args.languages.split(",") if x.strip()]:
        for _ in range(args.runs):
            reply, text_s = _answer(args.api, lang)
            run: dict[str, Any] = {"text_s": round(text_s, 2), "reply_chars": len(reply)}
            run["streamed"] = _streamed(args.api, lang, _fresh(reply))
            run["streamed"]["first_audio_s"] = round(text_s + run["streamed"]["first_piece_s"], 2)
            if not args.no_whole:
                run["whole"] = _whole(args.api, lang, _fresh(reply))
                if "total_s" in run["whole"]:
                    run["whole"]["first_audio_s"] = round(text_s + run["whole"]["total_s"], 2)
            runs.setdefault(lang, []).append(run)
            print(lang, json.dumps(run))

    report = {"date": dt.date.today().isoformat(), "api": args.api, "question": "<lang>_tin.wav", "runs": runs}
    out = ROOT / "evals" / "reports" / f"chat_speech_{report['date']}.json"
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
