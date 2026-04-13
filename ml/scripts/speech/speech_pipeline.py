#!/usr/bin/env python3
"""End-to-end speech pipeline driver for dev testing.

Runs the full ``audio -> ASR -> [MT] -> LLM -> [MT] -> TTS -> audio``
round-trip on the host machine. Uses the Python wrappers from ``scripts/asr``,
``scripts/tts``, ``scripts/mt``, and ``lang_id`` so the pipeline can run with
or without real model assets (each stage has a mock fallback).

Used by:

    * manual smoke testing before deploying the mobile bundle
    * ``ml/pipelines/benchmark_mobile.py`` for wall-clock round-trip timing
    * ``ml/pipelines/redteam_voice.py`` for voice-mode adversarial runs

Usage::

    python -m ml.scripts.speech.speech_pipeline \\
        --audio sample.wav \\
        --source-lang en --target-lang en

    python -m ml.scripts.speech.speech_pipeline \\
        --audio sample.wav \\
        --source-lang lg --target-lang lg   # Luganda in, Luganda out
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("speech.pipeline")


@dataclass
class PipelineResult:
    audio_in: str
    source_lang: str
    target_lang: str
    asr_text: str
    asr_backend: str
    translated_in: str
    llm_reply: str
    translated_out: str
    mt_backend: str
    tts_backend: str
    total_s: float
    stages: dict

    def to_dict(self):
        return asdict(self)


def _run_llm_stage(query: str) -> tuple[str, str]:
    """Call the existing backend LLM to produce a reply.

    Returns (reply, backend_name). If the backend is unavailable we fall
    back to an echo so the pipeline still produces a result.
    """
    try:
        from App.backend.app import llm as llm_module  # type: ignore

        reply = llm_module.generate(
            query=query, passages=[], conversation_history=None, locale="en"
        )
        return (reply or "").strip(), "backend.llm"
    except Exception as exc:
        log.debug("LLM backend unavailable: %s", exc)
        return f"[mock-llm] {query}", "mock"


def run(
    *,
    audio_path: Path,
    source_lang: str,
    target_lang: str,
    tts_voice: str | None,
) -> PipelineResult:
    from ml.scripts.asr.infer_asr import AsrTranscriber  # type: ignore
    from ml.scripts.mt.infer_mt import MtTranslator  # type: ignore
    from ml.scripts.tts.infer_tts import TtsSynthesizer  # type: ignore

    stages: dict = {}
    total_t0 = time.perf_counter()

    # 1. ASR
    t0 = time.perf_counter()
    transcriber = AsrTranscriber(backend="auto")
    asr_result = transcriber.transcribe_file(audio_path)
    stages["asr_s"] = round(time.perf_counter() - t0, 3)

    # 2. MT (optional — only if source != English and we need English for LLM)
    translator = MtTranslator(backend="auto")
    translated_in = asr_result.text
    mt_backend = "noop"
    if source_lang != "en":
        t0 = time.perf_counter()
        mt = translator.translate(asr_result.text, source_lang=source_lang, target_lang="en")
        translated_in = mt.text
        mt_backend = mt.backend
        stages["mt_in_s"] = round(time.perf_counter() - t0, 3)

    # 3. LLM
    t0 = time.perf_counter()
    llm_reply, llm_backend = _run_llm_stage(translated_in)
    stages["llm_s"] = round(time.perf_counter() - t0, 3)

    # 4. MT back (optional)
    translated_out = llm_reply
    if target_lang != "en":
        t0 = time.perf_counter()
        mt = translator.translate(llm_reply, source_lang="en", target_lang=target_lang)
        translated_out = mt.text
        mt_backend = mt.backend
        stages["mt_out_s"] = round(time.perf_counter() - t0, 3)

    # 5. TTS
    voice = tts_voice or ("luganda-vits-v1" if target_lang == "lg" else "en_US-lessac-medium")
    t0 = time.perf_counter()
    synth = TtsSynthesizer(voice_id=voice, backend="auto")
    tts_result = synth.synthesize(translated_out)
    stages["tts_s"] = round(time.perf_counter() - t0, 3)

    return PipelineResult(
        audio_in=str(audio_path),
        source_lang=source_lang,
        target_lang=target_lang,
        asr_text=asr_result.text,
        asr_backend=asr_result.backend,
        translated_in=translated_in,
        llm_reply=llm_reply,
        translated_out=translated_out,
        mt_backend=mt_backend,
        tts_backend=tts_result.backend,
        total_s=round(time.perf_counter() - total_t0, 3),
        stages=stages,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="en")
    parser.add_argument("--tts-voice", default=None)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    if not args.audio.exists():
        log.error("audio file missing: %s", args.audio)
        return 2

    result = run(
        audio_path=args.audio,
        source_lang=args.source_lang,
        target_lang=args.target_lang,
        tts_voice=args.tts_voice,
    )
    json.dump(result.to_dict(), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
