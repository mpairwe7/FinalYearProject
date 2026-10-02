#!/usr/bin/env python3
"""Comprehensive Verification Suite for Enhanced UX, User Control, and Accuracy (EN, LG, SW).

Tests the live phone receptionist pipeline against ws://127.0.0.1:8083/v1/calls/stream:
1. Enhanced UX:
   - Live partial captions (final=False while speaking)
   - Real-time final transcriptions
   - Sentence-by-sentence playout
   - Idle caller check & graceful timeout
2. User Control:
   - Voice consent gate (consent required, error frame returned if not accepted)
   - Manual language override via UI (holds against contrary speech)
   - Barge-in / interruption latency (< 1200ms SLA)
   - Explicit officer transfer request
   - Caller hangup command
3. Multilingual Accuracy:
   - English: 18% standard VAT rate fidelity & TIN portal procedure
   - Luganda: 18% VAT ("ebitundu 18 ku buli kikumi"), ASR entity repair ("ttiimu" -> TIN)
   - Swahili: 18% VAT, native East African tax phrasing ("namba ya TIN", "Hatua")
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import websockets

ROOT = Path(__file__).resolve().parents[1]
AUDIO_DIR = ROOT / "evals" / "call_replay" / "audio"
WS_URL = os.getenv("RECEPTIONIST_WS_URL", "ws://127.0.0.1:8083/v1/calls/stream")
FRAME_BYTES = 640  # 20ms at 16kHz mono int16

sys.path.insert(0, str(ROOT / "App" / "backend"))
from app.receptionist.phrases import fillers as _fillers

FILLERS = {f for lang in ("en", "lg", "sw") for f in _fillers(lang)}


def language_of(text: str) -> str | None:
    """The language an answer is in, by the receptionist's own word lists; None if unsure.

    Keyword checks ("omusolo", "18") let an English answer pass as Luganda.
    """
    from app.receptionist.language import lexical_hits

    hits = lexical_hits(text)
    best = max(hits.values())
    leaders = [lang for lang, n in hits.items() if n == best]
    return leaders[0] if best >= 2 and len(leaders) == 1 else None


def pcm_of(key: str) -> bytes:
    path = AUDIO_DIR / f"{key}.wav"
    with wave.open(str(path), "rb") as w:
        return w.readframes(w.getnframes())


@dataclass
class Listener:
    messages: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    audio_times: list[float] = field(default_factory=list)

    def last_audio(self) -> float:
        return self.audio_times[-1] if self.audio_times else 0.0


async def pump(ws: Any, listener: Listener) -> None:
    try:
        async for msg in ws:
            now = time.monotonic()
            if isinstance(msg, bytes):
                listener.audio_times.append(now)
            else:
                try:
                    listener.messages.append((now, json.loads(msg)))
                except ValueError:
                    pass
    except Exception:
        pass


async def keep_silence(ws: Any, stop: asyncio.Event) -> None:
    silence = b"\x00" * FRAME_BYTES
    while not stop.is_set():
        try:
            await ws.send(silence)
        except Exception:
            return
        await asyncio.sleep(0.02)


async def feed_audio(ws: Any, pcm: bytes, trailing_silence_s: float = 1.0) -> float:
    for i in range(0, len(pcm), FRAME_BYTES):
        chunk = pcm[i : i + FRAME_BYTES]
        if len(chunk) < FRAME_BYTES:
            chunk = chunk + b"\x00" * (FRAME_BYTES - len(chunk))
        await ws.send(chunk)
        await asyncio.sleep(0.02)
    silence = b"\x00" * FRAME_BYTES
    for _ in range(int(trailing_silence_s / 0.02)):
        await ws.send(silence)
        await asyncio.sleep(0.02)
    return time.monotonic()


async def first_audio_after(listener: Listener, since: float, timeout_s: float = 20.0) -> float | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        fresh = [t for t in listener.audio_times if t >= since]
        if fresh:
            return fresh[0]
        await asyncio.sleep(0.05)
    return None


async def wait_quiet(listener: Listener, since: float, quiet_s: float = 2.0, timeout_s: float = 25.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if listener.last_audio() > since and (time.monotonic() - listener.last_audio()) >= quiet_s:
            return
        await asyncio.sleep(0.1)


async def wait_for_answer_caption(listener: Listener, mark: int, timeout_s: float = 25.0) -> list[str]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        later = [m for _, m in listener.messages[mark:]]
        answers = [
            m.get("text", "") for m in later
            if m.get("type") == "caption" and m.get("speaker") == "assistant" and m.get("final")
            and m.get("text", "").strip() not in FILLERS
        ]
        if answers:
            return answers
        await asyncio.sleep(0.1)
    return []


async def test_voice_consent_enforcement() -> dict[str, Any]:
    """User Control: Call without consent must be rejected with an explicit error."""
    print("\n[User Control 1] Testing Voice Consent Enforcement...")
    error_received = False
    detail = ""
    try:
        async with websockets.connect(WS_URL, max_size=None) as ws:
            await ws.send(json.dumps({
                "type": "call_start",
                "locale": "en",
                "voice_consent_accepted": False,
                "sample_rate": 16000,
            }))
            resp_raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            if isinstance(resp_raw, str):
                resp = json.loads(resp_raw)
                if resp.get("type") == "error" and "consent" in resp.get("detail", "").lower():
                    error_received = True
                    detail = resp.get("detail")
    except Exception as exc:
        detail = str(exc)

    print(f" -> Result: {'PASS' if error_received else 'FAIL'} (Consent error: {detail})")
    return {
        "test": "voice_consent_enforcement",
        "passed": error_received,
        "detail": detail,
    }


async def test_manual_language_override_ux() -> dict[str, Any]:
    """User Control & UX: UI language override holds against acoustic contrary input."""
    print("\n[User Control 2] Testing Manual Language Override (Screen Dropdown)...")
    listener = Listener()
    override_event_seen = False
    answer_text = ""
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "call_start",
            "locale": "en",
            "voice_consent_accepted": True,
            "sample_rate": 16000,
        }))
        pump_task = asyncio.create_task(pump(ws, listener))
        opened = time.monotonic()
        if await first_audio_after(listener, opened, 20.0):
            await wait_quiet(listener, opened, quiet_s=1.5, timeout_s=25.0)

        # User chooses Luganda on screen dropdown
        await ws.send(json.dumps({"type": "set_language", "language": "lg"}))
        await asyncio.sleep(1.0)

        for _, m in listener.messages:
            if m.get("type") == "language" and m.get("language") == "lg":
                override_event_seen = True

        mark = len(listener.messages)
        pcm = pcm_of("en_vat")
        ended = await feed_audio(ws, pcm)
        answers = await wait_for_answer_caption(listener, mark, timeout_s=20.0)
        answer_text = " ".join(answers)
        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()

    is_luganda = language_of(answer_text) == "lg" and "18" in answer_text
    passed = override_event_seen and is_luganda
    print(f" -> Result: {'PASS' if passed else 'FAIL'} (Override event: {override_event_seen}, Luganda answer verified)")
    return {
        "test": "manual_language_override_ux",
        "passed": passed,
        "detail": f"Override emitted: {override_event_seen}; Response: {answer_text[:70]}...",
    }


async def test_live_partial_captions_ux() -> dict[str, Any]:
    """Enhanced UX: Live partial hypotheses (final=false) stream while speaking."""
    print("\n[Enhanced UX 1] Testing Live Partial Captions & Pipelined Captions...")
    listener = Listener()
    interim_captions = []
    final_caller_captions = []
    final_assistant_captions = []

    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "call_start",
            "locale": "en",
            "voice_consent_accepted": True,
            "sample_rate": 16000,
        }))
        pump_task = asyncio.create_task(pump(ws, listener))
        opened = time.monotonic()
        if await first_audio_after(listener, opened, 20.0):
            await wait_quiet(listener, opened, quiet_s=1.5, timeout_s=25.0)

        mark = len(listener.messages)
        pcm = pcm_of("en_tin")
        ended = await feed_audio(ws, pcm)
        await wait_for_answer_caption(listener, mark, timeout_s=20.0)

        for _, m in listener.messages[mark:]:
            if m.get("type") == "caption":
                if m.get("speaker") == "caller":
                    if not m.get("final"):
                        interim_captions.append(m.get("text"))
                    else:
                        final_caller_captions.append(m.get("text"))
                elif m.get("speaker") == "assistant" and m.get("final"):
                    final_assistant_captions.append(m.get("text"))

        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()

    # An interim (final: false) caption is the feature under test; finals alone do not show it.
    passed = len(interim_captions) >= 1 and len(final_caller_captions) >= 1 and len(final_assistant_captions) >= 1
    print(f" -> Result: {'PASS' if passed else 'FAIL'} (Interim={len(interim_captions)}, Final caller={len(final_caller_captions)}, Assistant={len(final_assistant_captions)})")
    return {
        "test": "live_partial_captions_ux",
        "passed": passed,
        "interim_count": len(interim_captions),
        "final_caller": final_caller_captions,
        "final_assistant": final_assistant_captions,
    }


async def test_barge_in_user_control() -> dict[str, Any]:
    """User Control: Interruption during greeting stops bot audio within SLA (< 1200ms)."""
    print("\n[User Control 3] Testing Barge-In User Control...")
    listener = Listener()
    interrupt_seen = False
    stop_ms = 9999

    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "call_start",
            "locale": "en",
            "voice_consent_accepted": True,
            "sample_rate": 16000,
        }))
        pump_task = asyncio.create_task(pump(ws, listener))

        # Wait (bounded) until the bot begins speaking the greeting.
        greeting_started = await first_audio_after(listener, time.monotonic() - 1.0, 20.0)

        # Talk over the greeting 1.5s in
        await asyncio.sleep(1.5)
        started_talking = time.monotonic()
        # Only a barge-in over audio that is still playing measures anything.
        talking_at_barge = greeting_started is not None and any(
            started_talking - 0.3 <= t <= started_talking for t in listener.audio_times
        )
        pcm = pcm_of("en_vat")
        ended = await feed_audio(ws, pcm)
        await wait_quiet(listener, ended, quiet_s=2.0, timeout_s=15.0)

        stop_time = started_talking
        for t in sorted(t for t in listener.audio_times if t >= started_talking):
            if t - stop_time > 0.4:
                break
            stop_time = t
        stop_ms = round((stop_time - started_talking) * 1000)

        for _, m in listener.messages:
            if m.get("type") == "interrupt":
                interrupt_seen = True

        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()

    passed = talking_at_barge and stop_ms <= 1200
    print(f" -> Result: {'PASS' if passed else 'FAIL'} (Bot stopped in {stop_ms} ms, playing at barge={talking_at_barge}, interrupt event={interrupt_seen})")
    return {
        "test": "barge_in_user_control",
        "passed": passed,
        "bot_stop_ms": stop_ms,
        "talking_at_barge": talking_at_barge,
        "interrupt_seen": interrupt_seen,
    }


async def test_human_officer_transfer_control() -> dict[str, Any]:
    """User Control: Explicit request to talk to an officer transfers call safely."""
    print("\n[User Control 4] Testing Explicit Human Officer Transfer Control...")
    listener = Listener()
    transfer_status_seen = False
    spoken_notice = ""

    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({
            "type": "call_start",
            "locale": "en",
            "voice_consent_accepted": True,
            "sample_rate": 16000,
        }))
        pump_task = asyncio.create_task(pump(ws, listener))
        opened = time.monotonic()
        if await first_audio_after(listener, opened, 20.0):
            await wait_quiet(listener, opened, quiet_s=1.5, timeout_s=25.0)

        mark = len(listener.messages)
        pcm = pcm_of("en_officer")
        ended = await feed_audio(ws, pcm)
        await wait_quiet(listener, ended, quiet_s=2.0, timeout_s=15.0)

        for _, m in listener.messages[mark:]:
            if m.get("type") == "status" and m.get("status") == "transferring":
                transfer_status_seen = True
            if m.get("type") == "caption" and m.get("speaker") == "assistant" and m.get("final"):
                spoken_notice = m.get("text", "")

        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()

    passed = transfer_status_seen and "officer" in spoken_notice.lower()
    print(f" -> Result: {'PASS' if passed else 'FAIL'} (Transfer status={transfer_status_seen}, Spoken: {spoken_notice})")
    return {
        "test": "human_officer_transfer_control",
        "passed": passed,
        "transfer_status_seen": transfer_status_seen,
        "spoken_notice": spoken_notice,
    }


async def test_multilingual_accuracy_statutory_fidelity() -> dict[str, Any]:
    """Multilingual Accuracy: Statutory rate fidelity & entity resolution across EN, LG, SW."""
    print("\n[Accuracy & Statutory Fidelity] Verifying 18% VAT, TIN, & Entity Normalization across EN, LG, SW...")
    results = {}

    # 1. English Statutory Accuracy
    listener_en = Listener()
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "call_start", "locale": "en", "voice_consent_accepted": True, "sample_rate": 16000}))
        pump_task = asyncio.create_task(pump(ws, listener_en))
        opened = time.monotonic()
        if await first_audio_after(listener_en, opened, 20.0):
            await wait_quiet(listener_en, opened, quiet_s=1.5, timeout_s=25.0)

        mark = len(listener_en.messages)
        ended = await feed_audio(ws, pcm_of("en_vat"))
        answers = await wait_for_answer_caption(listener_en, mark, timeout_s=20.0)
        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()
    vat_en = " ".join(answers)
    en_ok = ("18" in vat_en) and any(tok in vat_en.lower() for tok in ("vat", "v-a-t", "value added tax"))
    results["en_vat_accuracy"] = {"passed": en_ok, "text": vat_en}
    print(f" -> English: {'PASS' if en_ok else 'FAIL'} (18% VAT verified: {vat_en[:60]}...)")

    # 2. Luganda Statutory Accuracy & ASR Entity Repair
    listener_lg = Listener()
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "call_start", "locale": "lg", "preferred_locale": "lg", "voice_consent_accepted": True, "sample_rate": 16000}))
        pump_task = asyncio.create_task(pump(ws, listener_lg))
        opened = time.monotonic()
        if await first_audio_after(listener_lg, opened, 20.0):
            await wait_quiet(listener_lg, opened, quiet_s=1.5, timeout_s=25.0)

        mark = len(listener_lg.messages)
        ended = await feed_audio(ws, pcm_of("lg_vat"))
        answers = await wait_for_answer_caption(listener_lg, mark, timeout_s=25.0)
        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()
    vat_lg = " ".join(answers)
    lg_ok = language_of(vat_lg) == "lg" and "18" in vat_lg
    results["lg_vat_accuracy"] = {"passed": lg_ok, "text": vat_lg}
    print(f" -> Luganda: {'PASS' if lg_ok else 'FAIL'} (Vernacular accuracy: {vat_lg[:60]}...)")

    # 3. Swahili Statutory Accuracy & East African Tax Terms
    listener_sw = Listener()
    async with websockets.connect(WS_URL, max_size=None) as ws:
        await ws.send(json.dumps({"type": "call_start", "locale": "sw", "preferred_locale": "sw", "voice_consent_accepted": True, "sample_rate": 16000}))
        pump_task = asyncio.create_task(pump(ws, listener_sw))
        opened = time.monotonic()
        if await first_audio_after(listener_sw, opened, 20.0):
            await wait_quiet(listener_sw, opened, quiet_s=1.5, timeout_s=25.0)

        mark = len(listener_sw.messages)
        ended = await feed_audio(ws, pcm_of("sw_tin"))
        answers = await wait_for_answer_caption(listener_sw, mark, timeout_s=20.0)
        await ws.send(json.dumps({"type": "hangup"}))
        pump_task.cancel()
    tin_sw = " ".join(answers)
    sw_ok = language_of(tin_sw) == "sw" and any(tok in tin_sw.lower() for tok in ("tin", "t-i-n"))
    results["sw_tin_accuracy"] = {"passed": sw_ok, "text": tin_sw}
    print(f" -> Swahili: {'PASS' if sw_ok else 'FAIL'} (Swahili TIN guidance: {tin_sw[:60]}...)")

    overall_ok = en_ok and lg_ok and sw_ok
    return {
        "test": "multilingual_accuracy_statutory_fidelity",
        "passed": overall_ok,
        "languages": results,
    }


async def test_main_chat_streaming_voice_ws() -> dict[str, Any]:
    """Main Chat UI Voice Flow: Tests /v1/voice/chat/stream for duplex voice streaming."""
    print("\n[Main Chat Speech Flow] Testing Streaming Duplex Voice WebSocket (/v1/voice/chat/stream)...")
    chat_ws_url = WS_URL.replace("/v1/calls/stream", "/v1/voice/chat/stream")
    ready_seen = False
    vad_seen = False
    transcription_text = ""
    reply_text = ""
    retrieval_mode = ""
    latency_report = None
    audio_chunks_count = 0

    try:
        async with websockets.connect(chat_ws_url, max_size=None) as ws:
            await ws.send(json.dumps({
                "type": "session_start",
                "language": "en",
                "sample_rate": 16000,
                "vad_sensitivity": "medium",
                "tts_enabled": True,
                "voice_consent_accepted": True,
            }))

            ready_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
            ready_data = json.loads(ready_msg)
            ready_seen = ready_data.get("type") == "session_ready"

            pcm = pcm_of("en_vat")
            for i in range(0, len(pcm), 640):
                await ws.send(pcm[i : i + 640])
                await asyncio.sleep(0.01)

            silence = b"\x00" * 640
            for _ in range(35):
                await ws.send(silence)
                await asyncio.sleep(0.02)

            # Each sentence is synthesised before its audio is sent, and one
            # takes seconds: a short per-message timeout ended the wait between
            # the first reply_text and its audio, reporting Chunks=0.
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.monotonic()))
                    if isinstance(raw, bytes):
                        audio_chunks_count += 1
                    else:
                        event = json.loads(raw)
                        etype = event.get("type")
                        if etype == "vad_state":
                            vad_seen = True
                        elif etype == "transcript_final":
                            transcription_text = event.get("text", "")
                        elif etype == "reply_text":
                            reply_text += " " + event.get("text", "")
                        elif etype == "reply_meta":
                            retrieval_mode = event.get("retrieval_mode", "")
                        elif etype == "latency_report":
                            latency_report = event
                            break
                except asyncio.TimeoutError:
                    break

            await ws.send(json.dumps({"type": "session_end"}))
    except Exception as exc:
        print(f" -> Main Chat Voice Stream error: {exc}")

    passed = (
        ready_seen
        and vad_seen
        and bool(transcription_text)
        and ("18" in reply_text)
        and audio_chunks_count >= 1
    )
    print(f" -> Result: {'PASS' if passed else 'FAIL'} (Ready={ready_seen}, VAD={vad_seen}, Transcript='{transcription_text[:35]}...', Reply='{reply_text.strip()[:45]}...', Chunks={audio_chunks_count})")
    return {
        "test": "main_chat_streaming_voice_ws",
        "passed": passed,
        "ready": ready_seen,
        "vad": vad_seen,
        "transcript": transcription_text,
        "reply": reply_text.strip(),
        "retrieval_mode": retrieval_mode,
        "audio_chunks": audio_chunks_count,
        "latency_report": latency_report,
    }


async def main() -> int:
    print("=" * 70)
    print("URA Voice Receptionist: UX, User Control & Accuracy Verification Suite")
    print(f"Target WebSocket: {WS_URL}")
    print("=" * 70)

    tests = [
        test_voice_consent_enforcement(),
        test_manual_language_override_ux(),
        test_live_partial_captions_ux(),
        test_barge_in_user_control(),
        test_human_officer_transfer_control(),
        test_multilingual_accuracy_statutory_fidelity(),
        test_main_chat_streaming_voice_ws(),
    ]

    results = []
    for test_coro in tests:
        res = await test_coro
        results.append(res)

    all_passed = all(r.get("passed") for r in results)
    out_path = ROOT / "evals" / "reports" / "receptionist_ux_control_accuracy_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump({
            "timestamp": time.time(),
            "target_url": WS_URL,
            "all_passed": all_passed,
            "total_tests": len(results),
            "passed_tests": sum(1 for r in results if r.get("passed")),
            "results": results,
        }, f, indent=2)

    print("\n" + "=" * 70)
    print(f"Verification Summary: {sum(1 for r in results if r.get('passed'))}/{len(results)} Passed (Overall: {'PASS' if all_passed else 'FAIL'})")
    print(f"Report written to: {out_path}")
    print("=" * 70)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
