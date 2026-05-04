"""Machine translation wrapper for the speech pipeline.

The production path is prompted translation through the already-loaded Qwen
backend. This avoids loading a second MT model into GPU memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TranslateResult:
    text: str
    source_lang: str
    target_lang: str
    latency_s: float
    backend: str
    error: str | None = None


class MtTranslator:
    """Translate text with a prompted-LLM backend and explicit failure mode."""

    def __init__(self, backend: str = "prompted", **_: object):
        self.backend = (backend or "prompted").lower()
        logger.info("MT: %s backend configured", self.backend)

    def translate(
        self,
        text: str,
        source_lang: str = "en",
        target_lang: str = "lg",
    ) -> TranslateResult:
        t0 = time.perf_counter()
        source_lang = source_lang or "en"
        target_lang = target_lang or "lg"

        if not text.strip():
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=0.0,
                backend=self.backend,
            )

        if source_lang == target_lang:
            return TranslateResult(
                text=text,
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=round(time.perf_counter() - t0, 3),
                backend="identity",
            )

        if self.backend in {"auto", "prompted", "transformers", "llm"}:
            try:
                from app import llm as llm_module

                translated = llm_module.translate_text(
                    text,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
                if translated and translated.strip():
                    return TranslateResult(
                        text=translated.strip(),
                        source_lang=source_lang,
                        target_lang=target_lang,
                        latency_s=round(time.perf_counter() - t0, 3),
                        backend="prompted_qwen3",
                    )
            except Exception as exc:
                logger.debug("Prompted MT failed", exc_info=True)
                return TranslateResult(
                    text="",
                    source_lang=source_lang,
                    target_lang=target_lang,
                    latency_s=round(time.perf_counter() - t0, 3),
                    backend="prompted_qwen3",
                    error=str(exc),
                )

        if self.backend == "mock":
            return TranslateResult(
                text="",
                source_lang=source_lang,
                target_lang=target_lang,
                latency_s=round(time.perf_counter() - t0, 3),
                backend="mock",
                error="mock MT backend has no translation model",
            )

        return TranslateResult(
            text="",
            source_lang=source_lang,
            target_lang=target_lang,
            latency_s=round(time.perf_counter() - t0, 3),
            backend=self.backend,
            error=f"unsupported MT backend: {self.backend}",
        )
