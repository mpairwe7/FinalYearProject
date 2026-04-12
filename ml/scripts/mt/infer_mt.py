#!/usr/bin/env python3
"""MT inference wrapper with language-ID routing.

Tries backends in order:

    1. ``onnxruntime`` + exported student (production mobile path)
    2. ``transformers`` + fine-tuned teacher (server path)
    3. ``transformers`` + base MADLAD-400 (no fine-tune, commercial-safe)
    4. **prompted** — use the existing Gemma-2-2B LLM with a translation prompt
    5. mock passthrough — always succeeds for CI

Used by:
    * ``App/backend/app/speech_service.py``
    * ``ml/pipelines/evaluate_mt.py``
    * ``ml/scripts/mt/backtranslate.py``
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger("mt.infer")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts" / "mt"
DEFAULT_STUDENT_ONNX = ARTIFACTS_DIR / "onnx" / "student_int8"
DEFAULT_TEACHER = ARTIFACTS_DIR / "madlad_ura_lgen" / "final"
DEFAULT_BASE_REPO = "google/madlad400-3b-mt"


@dataclass
class TranslateResult:
    text: str
    source_lang: str
    target_lang: str
    latency_s: float
    backend: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Backend: onnxruntime + student
# ---------------------------------------------------------------------------


def _translate_onnx(onnx_dir: Path, text: str, src: str, tgt: str):
    try:
        from optimum.onnxruntime import ORTModelForSeq2SeqLM  # type: ignore
        from transformers import AutoTokenizer  # type: ignore
    except ImportError:
        return None
    if not onnx_dir.exists():
        return None
    try:
        tok = AutoTokenizer.from_pretrained(str(onnx_dir))
        model = ORTModelForSeq2SeqLM.from_pretrained(str(onnx_dir))
    except Exception as exc:
        log.debug("onnx MT load failed: %s", exc)
        return None
    prompt = f"<2{tgt}> {text}"
    t0 = time.perf_counter()
    inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=512)
    outputs = model.generate(**inputs, max_new_tokens=512, num_beams=4)
    latency = time.perf_counter() - t0
    return tok.decode(outputs[0], skip_special_tokens=True), latency


# ---------------------------------------------------------------------------
# Backend: transformers (teacher or base)
# ---------------------------------------------------------------------------


def _translate_transformers(model_src: str, text: str, src: str, tgt: str):
    try:
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer  # type: ignore
    except ImportError:
        return None
    try:
        tok = AutoTokenizer.from_pretrained(model_src)
        model = AutoModelForSeq2SeqLM.from_pretrained(model_src)
    except Exception as exc:
        log.debug("transformers MT load failed: %s", exc)
        return None
    prompt = f"<2{tgt}> {text}"
    t0 = time.perf_counter()
    inputs = tok(prompt, return_tensors="pt", truncation=True, max_length=512)
    outputs = model.generate(**inputs, max_new_tokens=512, num_beams=4)
    latency = time.perf_counter() - t0
    return tok.decode(outputs[0], skip_special_tokens=True), latency


# ---------------------------------------------------------------------------
# Backend: prompted (use existing LLM with a translation prompt)
# ---------------------------------------------------------------------------


def _translate_prompted(text: str, src: str, tgt: str):
    """Last-resort fallback using the existing Gemma LLM via service.llm.

    This reuses the translation prompt already baked into the Luganda data
    loaders: ``Translate to Luganda: <src>`` / ``Translate to English: <src>``.
    """
    try:
        from App.backend.app import llm as llm_module  # type: ignore
    except ImportError:
        return None
    lang_name = {"lg": "Luganda", "en": "English"}.get(tgt, tgt)
    prompt = f"Translate to {lang_name}: {text}"
    t0 = time.perf_counter()
    try:
        reply = llm_module.generate(query=prompt, passages=[], conversation_history=None, locale=tgt)
    except Exception as exc:
        log.debug("prompted MT failed: %s", exc)
        return None
    return (reply or ""), time.perf_counter() - t0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class MtTranslator:
    def __init__(
        self,
        *,
        onnx_dir: Optional[Path] = None,
        teacher_dir: Optional[Path] = None,
        base_repo: str = DEFAULT_BASE_REPO,
        backend: str = "auto",
    ) -> None:
        self.onnx_dir = onnx_dir or DEFAULT_STUDENT_ONNX
        self.teacher_dir = teacher_dir or DEFAULT_TEACHER
        self.base_repo = base_repo
        self.backend = backend

    def translate(
        self, text: str, *, source_lang: str = "en", target_lang: str = "lg"
    ) -> TranslateResult:
        if not text.strip():
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend="noop",
            )

        attempts = (
            ("onnx", lambda: _translate_onnx(self.onnx_dir, text, source_lang, target_lang)),
            ("teacher", lambda: _translate_transformers(str(self.teacher_dir), text, source_lang, target_lang) if self.teacher_dir.exists() else None),
            ("base", lambda: _translate_transformers(self.base_repo, text, source_lang, target_lang)),
            ("prompted", lambda: _translate_prompted(text, source_lang, target_lang)),
        )
        if self.backend != "auto":
            attempts = tuple(a for a in attempts if a[0] == self.backend)

        last_error: Optional[str] = None
        for name, fn in attempts:
            try:
                r = fn()
            except Exception as exc:
                last_error = str(exc)
                log.debug("%s backend raised: %s", name, exc)
                continue
            if r is None:
                continue
            out_text, latency = r
            return TranslateResult(
                text=out_text.strip() if out_text else "",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=round(latency, 3),
                backend=name,
            )

        # Final fallback — passthrough so the request chain never hard-fails.
        return TranslateResult(
            text=text,
            source_lang=source_lang,
            target_lang=target_lang,
            latency_s=0.0,
            backend="passthrough",
            error=f"all backends unavailable: {last_error}",
        )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MT inference wrapper")
    parser.add_argument("--text", required=True)
    parser.add_argument("--source-lang", default="en")
    parser.add_argument("--target-lang", default="lg")
    parser.add_argument("--backend", choices=("auto", "onnx", "teacher", "base", "prompted"), default="auto")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    translator = MtTranslator(backend=args.backend)
    result = translator.translate(args.text, source_lang=args.source_lang, target_lang=args.target_lang)
    json.dump(asdict(result), sys.stdout, indent=2, ensure_ascii=False)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
