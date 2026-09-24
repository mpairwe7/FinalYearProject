"""Orpheus-3B Sunbird multilingual TTS as a streaming HTTP service.

A thin FastAPI wrapper around an in-process vLLM ``AsyncLLMEngine`` serving
``Sunbird/orpheus-3b-tts-multilingual`` plus the SNAC 24 kHz decoder. The
receptionist's Luganda voice (``app.orpheus_tts``) calls it; nothing else
does.

    POST /v1/audio/speech  {"input": str, "voice": "salt_lug_0001",
                            "response_format": "pcm" | "wav", "seed": int?}
        -> 24 kHz mono 16-bit PCM, streamed as each ~85 ms frame decodes
           (``wav`` buffers the whole utterance behind a header).
    GET  /health           -> 200 once the model is loaded and warmed, else 503.

Why a separate process: vLLM pins its own torch/transformers, which the api
image's Whisper-SALT and Spark-TTS stack cannot share, and the model wants
~8-14 GB of its own GPU memory. Configuration is environment-only:

| Variable                  | Default                                  |
|---------------------------|------------------------------------------|
| ``ORPHEUS_MODEL``         | ``Sunbird/orpheus-3b-tts-multilingual``  |
| ``ORPHEUS_SNAC_MODEL``    | ``hubertsiuzdak/snac_24khz``             |
| ``ORPHEUS_GPU_MEM_UTIL``  | ``0.35``                                 |
| ``ORPHEUS_MAX_MODEL_LEN`` | ``2048``                                 |
| ``ORPHEUS_SNAC_DEVICE``   | ``cuda`` (``cpu`` adds ~50-150 ms/utterance) |
| ``ORPHEUS_MAX_TOKENS``    | ``1400`` (~12 s of speech)               |
| ``ORPHEUS_QUANTIZATION``  | unset (bf16); ``fp8`` = weight-only FP8  |
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import time
import uuid
import wave
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import numpy as np
from codes import (
    END_OF_SPEECH,
    SAMPLE_RATE,
    FrameAssembler,
    build_prompt_ids,
    next_steps,
    split_layers,
)
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("orpheus_sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

MODEL = os.getenv("ORPHEUS_MODEL", "Sunbird/orpheus-3b-tts-multilingual")
SNAC_MODEL = os.getenv("ORPHEUS_SNAC_MODEL", "hubertsiuzdak/snac_24khz")
GPU_MEM_UTIL = float(os.getenv("ORPHEUS_GPU_MEM_UTIL", "0.35"))
MAX_MODEL_LEN = int(os.getenv("ORPHEUS_MAX_MODEL_LEN", "2048"))
SNAC_DEVICE = os.getenv("ORPHEUS_SNAC_DEVICE", "cuda")
MAX_TOKENS = int(os.getenv("ORPHEUS_MAX_TOKENS", "1400"))
# "fp8" = weight-only FP8 (Marlin kernels on Ampere). Decoding a 3B model is
# memory-bound, and at bf16 an RTX A6000 generates at ~0.96x real time — no
# headroom for a second caller. See evals/reports/orpheus_tts_*.json.
QUANTIZATION = os.getenv("ORPHEUS_QUANTIZATION", "").strip() or None

# The model card's speaker table (Luganda, Swahili, English). A voice outside
# it is refused rather than guessed: the language travels only in the speaker
# tag, and an unknown tag produces confident gibberish.
SPEAKERS: frozenset[str] = frozenset({
    "salt_lug_0001", "waxal_lug_0002", "waxal_lug_0003", "waxal_lug_0004",
    "waxal_lug_0005", "waxal_lug_0006", "waxal_lug_0007", "waxal_lug_0008",
    "waxal_swa_0006", "waxal_swa_0007",
    "salt_eng_0001", "salt_eng_0002", "salt_eng_0003",
})
_VOICE_RE = re.compile(r"^[a-z0-9]+_[a-z]{3}_\d{4}$")


class SpeechRequest(BaseModel):
    input: str = Field(min_length=1, max_length=600)
    voice: str = Field(default="salt_lug_0001", max_length=32)
    response_format: str = Field(default="pcm", pattern="^(pcm|wav)$")
    seed: int | None = None
    temperature: float = Field(default=0.6, ge=0.1, le=1.2)
    top_p: float = Field(default=0.95, ge=0.5, le=1.0)
    repetition_penalty: float = Field(default=1.1, ge=1.0, le=2.0)


class _Engine:
    def __init__(self) -> None:
        self.llm: Any = None
        self.tokenizer: Any = None
        self.snac: Any = None
        self._torch: Any = None
        self.ready = False

    async def load(self) -> None:
        import torch
        from snac import SNAC
        from transformers import AutoTokenizer
        from vllm import AsyncEngineArgs, AsyncLLMEngine

        self.tokenizer = AutoTokenizer.from_pretrained(MODEL)
        self.snac = SNAC.from_pretrained(SNAC_MODEL).eval().to(SNAC_DEVICE)
        self._torch = torch
        self.llm = AsyncLLMEngine.from_engine_args(AsyncEngineArgs(
            model=MODEL,
            dtype="bfloat16",
            max_model_len=MAX_MODEL_LEN,
            gpu_memory_utilization=GPU_MEM_UTIL,
            quantization=QUANTIZATION,
            enforce_eager=os.getenv("ORPHEUS_ENFORCE_EAGER", "false").lower() == "true",
        ))
        # Warm the whole path (CUDA graphs, SNAC kernels) before /health says yes.
        async for _ in self.stream("Oli otya.", "salt_lug_0001", seed=0):
            pass
        self.ready = True
        logger.info("Orpheus sidecar ready: model=%s quantization=%s snac=%s on %s",
                    MODEL, QUANTIZATION or "bf16", SNAC_MODEL, SNAC_DEVICE)

    def _decode(self, frames: list[list[int]]) -> np.ndarray:
        torch = self._torch
        l1, l2, l3 = split_layers(frames)
        codes = [torch.tensor(layer, dtype=torch.int32, device=SNAC_DEVICE).unsqueeze(0) for layer in (l1, l2, l3)]
        with torch.inference_mode():
            audio = self.snac.decode(codes)
        return audio.squeeze().float().cpu().numpy()

    async def stream(
        self,
        text: str,
        voice: str,
        *,
        seed: int | None = None,
        temperature: float = 0.6,
        top_p: float = 0.95,
        repetition_penalty: float = 1.1,
    ) -> AsyncIterator[bytes]:
        """Yield 24 kHz PCM16 chunks as frames decode."""
        from vllm import SamplingParams
        from vllm.inputs import TokensPrompt
        from vllm.sampling_params import RequestOutputKind

        text_ids = self.tokenizer.encode(f"{voice}: {text}", add_special_tokens=True)
        params = SamplingParams(
            temperature=temperature,
            top_p=top_p,
            repetition_penalty=repetition_penalty,
            max_tokens=MAX_TOKENS,
            stop_token_ids=[END_OF_SPEECH],
            skip_special_tokens=False,
            seed=seed,
            output_kind=RequestOutputKind.DELTA,
        )
        assembler = FrameAssembler()
        emitted = 0
        request_id = uuid.uuid4().hex
        # Orpheus is a speech model: the prompt is the text to read aloud and
        # the only output accepted is SNAC audio codes (FrameAssembler drops
        # anything else), so injected "instructions" in `text` can change which
        # audio comes out, never what the receptionist does — as with
        # Spark-TTS-SALT. The text is the receptionist's own reply, not a
        # caller's words, and this sidecar has no tools or data to reach.
        # nosemgrep: ura-llm01-raw-user-input-to-llm
        async for out in self.llm.generate(TokensPrompt(prompt_token_ids=build_prompt_ids(text_ids)), params, request_id):
            new_frame = False
            for tid in out.outputs[0].token_ids:
                new_frame = assembler.feed(tid) or new_frame
            final = out.finished or assembler.finished
            if new_frame or final:
                steps, emitted = next_steps(len(assembler.frames), emitted, final)
                for step in steps:
                    audio = await asyncio.to_thread(self._decode, assembler.frames[step.start:step.end])
                    chunk = audio[step.lo:step.hi]
                    if chunk.size:
                        yield (np.clip(chunk, -1.0, 1.0) * 32767).astype("<i2").tobytes()
            if final:
                break


engine = _Engine()


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    loader = asyncio.create_task(engine.load())
    try:
        yield
    finally:
        loader.cancel()


app = FastAPI(title="Orpheus TTS sidecar", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, Any]:
    if not engine.ready:
        raise HTTPException(status_code=503, detail="loading")
    return {"status": "ok", "model": MODEL, "sample_rate": SAMPLE_RATE}


@app.get("/v1/voices")
async def voices() -> dict[str, list[str]]:
    return {"voices": sorted(SPEAKERS)}


def _wav(pcm: bytes) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm)
    return buf.getvalue()


@app.post("/v1/audio/speech")
async def speech(req: SpeechRequest) -> Response:
    if not engine.ready:
        raise HTTPException(status_code=503, detail="loading")
    if not _VOICE_RE.match(req.voice) or req.voice not in SPEAKERS:
        raise HTTPException(status_code=400, detail="unknown voice")
    text = " ".join(req.input.split())
    kwargs = {"seed": req.seed, "temperature": req.temperature, "top_p": req.top_p,
              "repetition_penalty": req.repetition_penalty}
    t0 = time.perf_counter()
    if req.response_format == "wav":
        pcm = b"".join([chunk async for chunk in engine.stream(text, req.voice, **kwargs)])
        logger.info("synth voice=%s chars=%d audio_s=%.2f total_ms=%.0f", req.voice, len(text),
                    len(pcm) / 2 / SAMPLE_RATE, (time.perf_counter() - t0) * 1000)
        return Response(content=_wav(pcm), media_type="audio/wav")

    async def body() -> AsyncIterator[bytes]:
        first = True
        n = 0
        async for chunk in engine.stream(text, req.voice, **kwargs):
            if first:
                logger.info("ttfa voice=%s ms=%.0f", req.voice, (time.perf_counter() - t0) * 1000)
                first = False
            n += len(chunk)
            yield chunk
        logger.info("synth voice=%s chars=%d audio_s=%.2f total_ms=%.0f", req.voice, len(text),
                    n / 2 / SAMPLE_RATE, (time.perf_counter() - t0) * 1000)

    return StreamingResponse(body(), media_type="audio/pcm", headers={"X-Sample-Rate": str(SAMPLE_RATE)})
