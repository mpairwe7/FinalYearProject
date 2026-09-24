"""End-to-end multilingual receptionist check: real audio through WS /v1/calls/stream.

Plays each scenario's caller turns into a running backend the way the browser
does (16 kHz PCM16 in 20 ms frames, in real time, silence between turns) and
records what comes back: language events, the assistant's captions, status
changes, and the time from the end of each caller turn to the first byte of
reply audio. Checks each scenario against what the plan (§10) expects.

The caller's voice is rendered by the Orpheus sidecar with speakers the
receptionist itself never uses (a Ugandan-English SALT speaker, a WAXAL
Luganda and a WAXAL Swahili speaker), so no recording is needed to rerun it.
Synthetic speech is cleaner than a phone call — this checks the wiring and
the latency budget, not recognition accuracy under noise.

    python scripts/replay_call_audio.py --render-url http://127.0.0.1:18100 \\
        --ws ws://127.0.0.1:8083/v1/calls/stream

Writes ``evals/reports/call_replay_<date>.json``. Needs ``websockets`` and
``numpy``; run inside app-api:gpu with ``--network host``.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "evals" / "call_replay" / "audio"
FRAME_BYTES = 640  # 20 ms at 16 kHz mono PCM16

try:  # the receptionist's own filler lines, to tell a filler from an answer
    import sys

    sys.path.insert(0, str(ROOT / "App" / "backend"))
    from app.receptionist.phrases import fillers as _fillers

    FILLERS = {f for lang in ("en", "lg", "sw") for f in _fillers(lang)}
except Exception:  # noqa: BLE001 — timing still works, just without the split
    FILLERS = set()

SPEAKERS = {"en": "salt_eng_0002", "lg": "waxal_lug_0005", "sw": "waxal_swa_0007"}

UTTERANCES: dict[str, tuple[str, str]] = {
    "en_tin": ("en", "Hello. How do I register for a TIN number with URA?"),
    "en_vat": ("en", "What is the standard VAT rate in Uganda?"),
    "en_ask_sw": ("en", "Can we speak Swahili, please?"),
    "lg_tin": ("lg", "Nnyinza ntya okwewandiisa okufuna TIN yange okuva mu URA?"),
    "lg_vat": ("lg", "VAT yange ntya okugisasula, era ebitundu bimeka?"),
    "lg_person": ("lg", "Njagala okwogera n'omuntu, omukozi wa URA."),
    "sw_tin": ("sw", "Habari. Ninawezaje kujisajili kupata namba ya TIN kutoka URA?"),
}


@dataclass
class Scenario:
    name: str
    turns: list[str]
    expect_languages: list[str]  # language events in order, as the caller should see them
    override: str | None = None
    expect_status: str | None = None
    expect_status_on: str | None = None  # the utterance that must trigger it
    note: str = ""


SCENARIOS = [
    Scenario("1_english_stays_english", ["en_tin", "en_vat"], ["en"],
             note="English caller: locks English, never leaves it."),
    Scenario("2_selected_english_speaks_luganda", ["lg_tin"], ["lg"],
             note="First answer in Luganda, same question, not repeated."),
    Scenario("3_swahili_on_gemini", ["sw_tin"], ["sw"], note="Answered in Swahili by Gemini Live."),
    Scenario("4_luganda_then_english", ["lg_vat", "en_tin"], ["lg", "en"],
             note="Mid-call switch followed within one turn."),
    Scenario("5_code_switched_luganda_stays", ["lg_tin", "lg_vat"], ["lg"],
             note="Luganda with TIN/VAT: no flip-flop."),
    Scenario("6_explicit_request", ["en_tin", "en_ask_sw"], ["en", "sw"], note="Immediate switch."),
    Scenario("7_override_luganda_holds", ["en_vat"], ["lg"], override="lg",
             note="Pinned to Luganda on screen; English speech does not move it."),
    # Opens on lg_vat: the synthetic lg_tin clip is heard as "ttiimu", which
    # the knowledge base cannot answer and escalates on its own — the officer
    # request would then land on a call that is already transferring.
    Scenario("8_transfer_from_luganda", ["lg_vat", "lg_person"], ["lg"], expect_status="transferring",
             expect_status_on="lg_person",
             note="Officer request in Luganda transfers; staff see a Luganda caller."),
]


def render(render_url: str) -> None:
    """Synthesise any missing caller utterance (16 kHz WAV) with the sidecar."""
    import httpx

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    for key, (lang, text) in UTTERANCES.items():
        path = AUDIO_DIR / f"{key}.wav"
        if path.exists():
            continue
        resp = httpx.post(f"{render_url}/v1/audio/speech",
                          json={"input": text, "voice": SPEAKERS[lang], "response_format": "pcm", "seed": 3},
                          timeout=60)
        resp.raise_for_status()
        pcm24 = np.frombuffer(resp.content, dtype="<i2").astype(np.float32)
        idx = np.arange(0, len(pcm24), 1.5)  # 24 kHz → 16 kHz, linear
        pcm16 = np.interp(idx, np.arange(len(pcm24)), pcm24).astype("<i2")
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm16.tobytes())
        print(f"rendered {key} ({len(pcm16) / 16000:.1f}s)")


def pcm_of(key: str) -> bytes:
    with wave.open(str(AUDIO_DIR / f"{key}.wav"), "rb") as w:
        return w.readframes(w.getnframes())


@dataclass
class Listener:
    messages: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    audio_times: list[float] = field(default_factory=list)

    def last_audio(self) -> float:
        return self.audio_times[-1] if self.audio_times else 0.0


async def pump(ws: Any, listener: Listener) -> None:
    async for msg in ws:
        now = time.monotonic()
        if isinstance(msg, bytes):
            listener.audio_times.append(now)
        else:
            try:
                listener.messages.append((now, json.loads(msg)))
            except ValueError:
                pass


async def wait_quiet(listener: Listener, since: float, quiet_s: float, timeout_s: float) -> None:
    """Until reply audio that began after *since* has been silent for *quiet_s*.

    Replies pause mid-way — a filler, then the answer once it is generated, or
    Gemini going quiet while its tool runs — so *quiet_s* has to outlast those.
    """
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        last = max(listener.last_audio(), since)
        if time.monotonic() - last > quiet_s:
            return
        await asyncio.sleep(0.1)


async def first_audio_after(listener: Listener, since: float, timeout_s: float) -> float | None:
    deadline = since + timeout_s
    while time.monotonic() < deadline:
        after = [t for t in listener.audio_times if t > since]
        if after:
            return after[0]
        await asyncio.sleep(0.05)
    return None


async def speak(ws: Any, pcm: bytes, trailing_silence_s: float = 1.5) -> float:
    """Send speech then silence in real time; return when the speech ended."""
    t = time.monotonic()
    for i in range(0, len(pcm), FRAME_BYTES):
        await ws.send(pcm[i : i + FRAME_BYTES].ljust(FRAME_BYTES, b"\x00"))
        t += 0.02
        await asyncio.sleep(max(0.0, t - time.monotonic()))
    ended = time.monotonic()
    silence = b"\x00" * FRAME_BYTES
    for _ in range(int(trailing_silence_s / 0.02)):
        await ws.send(silence)
        t += 0.02
        await asyncio.sleep(max(0.0, t - time.monotonic()))
    return ended


async def keep_silence(ws: Any, stop: asyncio.Event) -> None:
    """A live mic never goes quiet on the wire; neither does this caller."""
    silence = b"\x00" * FRAME_BYTES
    while not stop.is_set():
        await ws.send(silence)
        await asyncio.sleep(0.02)


async def run_scenario(url: str, sc: Scenario, reply_wait_s: float) -> dict[str, Any]:
    import websockets

    listener = Listener()
    result: dict[str, Any] = {"name": sc.name, "note": sc.note, "turns": []}
    async with websockets.connect(url, max_size=None) as ws:
        await ws.send(json.dumps({"type": "call_start", "locale": "en", "preferred_locale": "en",
                                  "voice_consent_accepted": True, "sample_rate": 16000}))
        reader = asyncio.create_task(pump(ws, listener))
        stop = asyncio.Event()
        filler = asyncio.create_task(keep_silence(ws, stop))
        # Let the greeting play out.
        opened = time.monotonic()
        if await first_audio_after(listener, opened, 20.0):
            await wait_quiet(listener, opened, quiet_s=2.0, timeout_s=30)
        ready = next((m for _, m in listener.messages if m.get("type") == "call_ready"), {})
        result["call_id"] = ready.get("call_id")
        result["language_detection"] = ready.get("language_detection")
        if sc.override:
            sent = time.monotonic()
            await ws.send(json.dumps({"type": "set_language", "language": sc.override}))
            if await first_audio_after(listener, sent, 15.0):
                await wait_quiet(listener, sent, quiet_s=2.5, timeout_s=20)

        for key in sc.turns:
            stop.set()
            await filler
            mark = len(listener.messages)
            ended = await speak(ws, pcm_of(key))
            stop = asyncio.Event()
            filler = asyncio.create_task(keep_silence(ws, stop))
            first_audio = await first_audio_after(listener, ended, reply_wait_s)
            if first_audio is not None:
                # Past the filler: until an answer (or a status change) arrives.
                deadline = ended + reply_wait_s
                while time.monotonic() < deadline:
                    later = [m for _, m in listener.messages[mark:]]
                    if any(m.get("type") == "status" for m in later) or any(
                        m.get("type") == "caption" and m.get("speaker") == "assistant" and m.get("final")
                        and m.get("text", "").strip() not in FILLERS for m in later
                    ):
                        break
                    await asyncio.sleep(0.1)
                await wait_quiet(listener, ended, quiet_s=3.0, timeout_s=reply_wait_s)
            new = [m for _, m in listener.messages[mark:]]
            timed = [(t, m) for t, m in listener.messages[mark:]]
            said = [(t, m["text"]) for t, m in timed if m.get("type") == "caption"
                    and m.get("speaker") == "assistant" and m.get("final")]
            answer_at = next((t for t, text in said if text.strip() not in FILLERS), None)
            filler_at = next((t for t, text in said if text.strip() in FILLERS), None)
            result["turns"].append({
                "utterance": key,
                "said": UTTERANCES[key][1],
                "first_audio_ms": round((first_audio - ended) * 1000) if first_audio else None,
                "filler_caption_ms": round((filler_at - ended) * 1000) if filler_at else None,
                "answer_caption_ms": round((answer_at - ended) * 1000) if answer_at else None,
                "languages": [m for m in new if m.get("type") == "language"],
                "assistant": [m["text"] for m in new if m.get("type") == "caption"
                              and m.get("speaker") == "assistant" and m.get("final")],
                "caller": [m["text"] for m in new if m.get("type") == "caption"
                           and m.get("speaker") == "caller" and m.get("final")],
                "status": [m.get("status") for m in new if m.get("type") == "status"],
            })
        stop.set()
        await filler
        await ws.send(json.dumps({"type": "hangup"}))
        await asyncio.sleep(0.5)
        reader.cancel()

    seen: list[str] = []
    for _, message in listener.messages:
        if message.get("type") == "language" and (not seen or seen[-1] != message.get("language")):
            seen.append(message["language"])
    statuses = [s for t in result["turns"] for s in t["status"]]
    status_ok = sc.expect_status is None or sc.expect_status in statuses
    if sc.expect_status and sc.expect_status_on:
        on = [t for t in result["turns"] if t["utterance"] == sc.expect_status_on]
        before = result["turns"][: result["turns"].index(on[0])] if on else []
        status_ok = bool(on) and sc.expect_status in on[0]["status"] and not any(
            sc.expect_status in t["status"] for t in before
        )
    result["language_sequence"] = seen
    result["passed"] = bool(result["language_detection"]) and seen == sc.expect_languages and status_ok
    return result


async def main_async(args: argparse.Namespace) -> None:
    chosen = [s for s in SCENARIOS if not args.only or any(o in s.name for o in args.only.split(","))]
    chosen = [s for s in chosen for _ in range(max(1, args.repeat))]
    results = []
    for sc in chosen:
        print(f"== {sc.name}", flush=True)
        try:
            res = await run_scenario(args.ws, sc, args.reply_wait)
        except Exception as exc:  # a crashed scenario is a result, not an abort
            res = {"name": sc.name, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
        results.append(res)
        print(json.dumps({k: res.get(k) for k in ("passed", "language_sequence", "error")}), flush=True)
        for turn in res.get("turns", []):
            print(f"   {turn['utterance']}: audio {turn['first_audio_ms']} ms, filler {turn['filler_caption_ms']} ms,"
                  f" answer {turn['answer_caption_ms']} ms  langs={[e['language'] for e in turn['languages']]}"
                  f"  status={turn['status']}  reply={[a[:90] for a in turn['assistant'][-1:]]}", flush=True)
    lat = [t["first_audio_ms"] for r in results for t in r.get("turns", []) if t.get("first_audio_ms") is not None]
    report = {
        "date": dt.date.today().isoformat(),
        "ws": args.ws,
        "passed": sum(bool(r.get("passed")) for r in results),
        "scenarios": len(results),
        "first_audio_ms_p50": float(np.percentile(lat, 50)) if lat else None,
        "results": results,
    }
    out = ROOT / "evals" / "reports" / f"call_replay_{report['date']}{args.label}.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False))
    print(f"{report['passed']}/{report['scenarios']} scenarios passed — {out}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--ws", default="ws://127.0.0.1:8083/v1/calls/stream")
    ap.add_argument("--render-url", default="", help="Orpheus sidecar, to render missing caller audio")
    ap.add_argument("--only", default="", help="comma-separated substrings of scenario names")
    ap.add_argument("--reply-wait", type=float, default=30.0)
    ap.add_argument("--repeat", type=int, default=1, help="run each chosen scenario this many times")
    ap.add_argument("--label", default="", help="appended to the report name, e.g. _flag_off")
    args = ap.parse_args()
    if args.render_url:
        render(args.render_url)
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
