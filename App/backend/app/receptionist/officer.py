"""Officer leg handling, bidirectional audio bridge, and officer speech transcription."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..speech_service import pcm16_to_wav
from ..voice_stream import EnergyVAD
from .hub import hub
from .store import create_turn

logger = logging.getLogger(__name__)


class OfficerLeg:
    """Manages the officer's WebSocket connection and audio bridge to the caller."""

    def __init__(
        self,
        ws: Any,
        officer_id: str,
        officer_name: str,
        room: Any,
        speech_model: Any,
    ) -> None:
        self.ws = ws
        self.officer_id = officer_id
        self.officer_name = officer_name
        self.room = room
        self.speech_model = speech_model
        self.vad = EnergyVAD()
        self._caller_audio_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=50)
        self._sender_task: asyncio.Task | None = None
        self._running = True
        self.on_hold = False

    def start(self) -> None:
        """Start background loop forwarding caller audio to officer WebSocket."""
        self._sender_task = asyncio.create_task(self._send_loop())

    def send_caller_audio(self, pcm: bytes) -> None:
        """Forward caller audio chunk to officer without blocking."""
        if not self._running:
            return
        if self._caller_audio_queue.full():
            try:
                self._caller_audio_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._caller_audio_queue.put_nowait(pcm)
        except asyncio.QueueFull:
            pass

    async def _send_loop(self) -> None:
        while self._running:
            try:
                pcm = await self._caller_audio_queue.get()
                await self.ws.send_bytes(pcm)
            except asyncio.CancelledError:
                break
            except Exception:
                break

    async def handle_officer_message(self, data: bytes | str) -> None:
        """Process incoming message/audio from the officer."""
        if isinstance(data, bytes):
            if self.on_hold:
                return
            # 1. Forward directly to caller WebSocket, bypassing Pipecat pipeline
            if self.room.caller_ws is not None:
                try:
                    await self.room.caller_ws.send_bytes(data)
                except Exception:
                    pass

            # 2. Feed officer PCM to EnergyVAD for transcription
            is_speech, complete = self.vad.detect(data)
            if complete and len(self.vad.audio_buffer) > 0:
                audio_to_transcribe = bytes(self.vad.audio_buffer)
                self.vad.reset()
                asyncio.create_task(self._transcribe_officer_turn(audio_to_transcribe))

        elif isinstance(data, str):
            try:
                msg = json.loads(data)
                if msg.get("type") == "hangup":
                    await self.room.end("officer_hangup")
            except Exception:
                pass

    async def _transcribe_officer_turn(self, pcm_bytes: bytes) -> None:
        try:
            res = await asyncio.to_thread(
                self.speech_model.transcribe,
                pcm16_to_wav(pcm_bytes, 16000),
                16000,
                "en",
                with_words=False,
            )
            if res and res.text:
                self.room.state.turn_seq += 1
                turn = create_turn(
                    call_id=self.room.call_id,
                    seq=self.room.state.turn_seq,
                    speaker="officer",
                    kind="utterance",
                    text=res.text,
                    latencies={"stt_ms": round((res.latency_s or 0) * 1000, 1)},
                )
                hub.publish_call(self.room.call_id, "turn", turn)

                # Send caption to caller browser
                caption_msg = {
                    "type": "caption",
                    "speaker": "officer",
                    "text": res.text,
                    "final": True,
                    "turn_id": self.room.state.turn_seq,
                }
                if self.room.caller_ws:
                    await self.room.caller_ws.send_text(json.dumps(caption_msg))
        except Exception:
            logger.exception("Failed transcribing officer utterance")

    async def close(self, reason: str = "normal") -> None:
        """Close officer WebSocket connection."""
        self._running = False
        if self._sender_task:
            self._sender_task.cancel()
        try:
            await self.ws.close()
        except Exception:
            pass
