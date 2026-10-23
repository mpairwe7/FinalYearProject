"""Vision encoder — Qwen2-VL-2B document understanding (2026).

Encodes document images (receipts, TIN cards, tax forms, assessment
notices) into structured context for the LLM.  Runs in parallel with
ASR so it adds zero latency to the critical path.

Pipeline:
    1. Resize image to ``VISION_MAX_PIXELS`` (default 1280x720)
    2. Run Qwen2-VL-2B-Instruct for document description + field extraction
    3. Run OCR (EasyOCR) in parallel for raw text extraction
    4. Classify document type via pattern matching on VLM output
    5. Return fused context dict

Feature flag: ``voice_vision_v2``
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
from typing import Any

from ..guardrails import scan_retrieved_text
from .document_classifier import classify_with_context
from .ocr import (
    clean_ocr_text,
    extract_dates,
    extract_efris_invoice_numbers,
    extract_nin_numbers,
    extract_ocr_result,
    extract_prn_numbers,
    extract_reference_numbers,
    extract_tax_heads,
    extract_tin_numbers,
    extract_ugx_amounts,
    strip_hidden_control_and_bidi,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

VISION_MODEL = os.getenv("VISION_MODEL", "Qwen/Qwen2-VL-2B-Instruct")
VISION_DEVICE = os.getenv("VISION_DEVICE", "cuda:0")
VISION_MAX_PIXELS = int(os.getenv("VISION_MAX_PIXELS", str(1280 * 720)))
VISION_MAX_TOKENS = int(os.getenv("VISION_MAX_TOKENS", "256"))
_VISION_MAX_IMAGE_BYTES = int(os.getenv("DOCUMENT_MAX_BYTES", str(40 * 1024 * 1024)))
_VISION_MAX_DECODE_PIXELS = int(os.getenv("DOCUMENT_MAX_IMAGE_PIXELS", "20_000_000"))

# URA document analysis prompt — tuned for Ugandan tax documents
_VLM_PROMPT = (
    "Analyze this Uganda Revenue Authority document carefully.\n"
    "Identify:\n"
    "1) Document type: receipt, TIN certificate, tax assessment notice, "
    "customs declaration, filing form, EFRIS invoice, or other.\n"
    "2) Key fields: amounts (in UGX), dates, TIN numbers, taxpayer names, "
    "tax types, reference numbers.\n"
    "3) Brief summary of what this document shows.\n\n"
    "Format your response as:\n"
    "TYPE: <document type>\n"
    "FIELDS: <key-value pairs>\n"
    "SUMMARY: <1-2 sentence summary>"
)


# ---------------------------------------------------------------------------
# VisionEncoder
# ---------------------------------------------------------------------------


class VisionEncoder:
    """Encodes document images into structured context for the LLM.

    Thread-safety: model is loaded once behind a lock.  Inference calls
    are dispatched via the asyncio executor so they don't block the
    event loop.

    Graceful degradation: if the vision model is unavailable, falls back
    to OCR-only mode.  If OCR is also unavailable, returns an empty
    context dict (never raises).
    """

    def __init__(self) -> None:
        self._model = None
        self._processor = None
        self._ocr = None
        self._lock = threading.Lock()
        self._vlm_loaded = False
        self._ocr_loaded = False

    # ------------------------------------------------------------------
    # Lazy loading
    # ------------------------------------------------------------------

    def _ensure_vlm(self) -> bool:
        """Lazy-load Qwen2-VL model.  Returns True if usable."""
        if self._vlm_loaded:
            return self._model is not None
        with self._lock:
            if self._vlm_loaded:
                return self._model is not None
            try:
                from transformers import AutoProcessor, Qwen2VLForConditionalGeneration
                import torch

                self._processor = AutoProcessor.from_pretrained(VISION_MODEL)
                self._model = Qwen2VLForConditionalGeneration.from_pretrained(
                    VISION_MODEL,
                    torch_dtype=torch.float16,
                    device_map=VISION_DEVICE,
                )
                self._model.eval()
                logger.info("VisionEncoder loaded: %s on %s", VISION_MODEL, VISION_DEVICE)
            except Exception:
                logger.info("Vision model unavailable — using OCR-only mode")
            self._vlm_loaded = True
            return self._model is not None

    def _ensure_ocr(self) -> bool:
        """Lazy-load OCR engine.  Returns True if usable."""
        if self._ocr_loaded:
            return self._ocr is not None
        with self._lock:
            if self._ocr_loaded:
                return self._ocr is not None
            try:
                import easyocr  # type: ignore[import-untyped]

                self._ocr = easyocr.Reader(
                    ["en"],
                    gpu=VISION_DEVICE.startswith("cuda"),
                    verbose=False,
                )
                logger.info("OCR engine loaded (EasyOCR, lang=en)")
            except Exception:
                logger.info("EasyOCR unavailable — OCR disabled")
            self._ocr_loaded = True
            return self._ocr is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(self, image_bytes: bytes) -> dict[str, Any]:
        """Encode an image into structured context.

        Returns:
            ``{"ocr_text": str, "doc_type": str, "summary": str,
            "fields": dict, "confidence": float}``

        Never raises — returns empty/partial results on failure.
        """
        result: dict[str, Any] = {
            "ocr_text": "",
            "doc_type": "generic",
            "summary": "",
            "fields": {},
            "confidence": 0.0,
        }

        if not image_bytes or len(image_bytes) > _VISION_MAX_IMAGE_BYTES:
            logger.warning("Rejected empty or oversized vision image (%d bytes)", len(image_bytes or b""))
            return result

        # Load image with EXIF orientation normalization and pixel bomb guard
        try:
            from PIL import Image, ImageOps

            with Image.open(io.BytesIO(image_bytes)) as raw_img:
                if raw_img.width * raw_img.height > _VISION_MAX_DECODE_PIXELS:
                    logger.warning(
                        "Vision image exceeds decode pixel cap (%d > %d)",
                        raw_img.width * raw_img.height,
                        _VISION_MAX_DECODE_PIXELS,
                    )
                    return result
                transposed = ImageOps.exif_transpose(raw_img)
                image = (transposed if transposed is not None else raw_img).convert("RGB")
        except Exception:
            logger.warning("Failed to decode image", exc_info=True)
            return result

        # Resize if too large
        if image.width * image.height > VISION_MAX_PIXELS:
            ratio = (VISION_MAX_PIXELS / (image.width * image.height)) ** 0.5
            new_w = max(1, int(image.width * ratio))
            new_h = max(1, int(image.height * ratio))
            image = image.resize((new_w, new_h))

        # OCR pass + OWASP LLM01/ASI01 visual indirect prompt injection scrub
        raw_ocr = clean_ocr_text(self._run_ocr(image))
        scrubbed_ocr, ocr_scrubbed = scan_retrieved_text(raw_ocr)
        result["ocr_text"] = scrubbed_ocr

        # VLM pass + visual indirect prompt injection scrub
        raw_vlm = strip_hidden_control_and_bidi(self._run_vlm(image))
        vlm_output, vlm_scrubbed = scan_retrieved_text(raw_vlm)
        if ocr_scrubbed or vlm_scrubbed:
            result["indirect_injection_scrubbed"] = True

        if vlm_output:
            result["summary"] = vlm_output
            result["doc_type"] = _classify_doc_type(vlm_output, result["ocr_text"])
            result["fields"] = _extract_fields(vlm_output, result["ocr_text"])
            result["confidence"] = 0.85
        elif result["ocr_text"]:
            # VLM unavailable but OCR succeeded — classify from OCR text
            result["doc_type"] = _classify_doc_type("", result["ocr_text"])
            result["summary"] = f"OCR text from scanned document: {result['ocr_text'][:200]}"
            result["fields"] = _extract_fields("", result["ocr_text"])
            result["confidence"] = 0.5

        return result

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_ocr(self, image) -> str:
        """Run OCR on a PIL Image.  Returns extracted text or empty string."""
        try:
            import numpy as np

            arr = np.array(image)
            unified = extract_ocr_result(arr)
            if unified.status == "ready" and unified.text.strip():
                return unified.text.strip()
        except Exception:
            logger.debug("Unified OCR pass skipped in VisionEncoder", exc_info=True)

        if not self._ensure_ocr():
            return ""
        try:
            import numpy as np

            results = self._ocr.readtext(np.array(image))
            return " ".join(r[1] for r in results if r[1].strip())
        except Exception:
            logger.warning("OCR failed", exc_info=True)
            return ""

    def _run_vlm(self, image) -> str:
        """Run Qwen2-VL on a PIL Image.  Returns analysis text or empty string."""
        if not self._ensure_vlm():
            return ""
        try:
            import torch

            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": image},
                        {"type": "text", "text": _VLM_PROMPT},
                    ],
                }
            ]

            text_input = self._processor.apply_chat_template(
                messages, add_generation_prompt=True,
            )
            inputs = self._processor(
                text=[text_input],
                images=[image],
                return_tensors="pt",
            ).to(self._model.device)

            with torch.no_grad():
                output_ids = self._model.generate(  # nosemgrep: ura-llm01-raw-user-input-to-llm -- PyTorch vision model with fixed _VLM_PROMPT; output scrubbed via scan_retrieved_text.
                    **inputs,
                    max_new_tokens=VISION_MAX_TOKENS,
                    do_sample=False,
                )

            # Decode only the generated tokens
            generated = output_ids[:, inputs.input_ids.shape[1]:]
            output_text = self._processor.batch_decode(
                generated, skip_special_tokens=True,
            )[0]

            return output_text.strip()
        except Exception:
            logger.warning("VLM inference failed", exc_info=True)
            return ""


# ---------------------------------------------------------------------------
# Document classification
# ---------------------------------------------------------------------------

# Pattern → document type mapping
_DOC_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"efris|e-?receipt|electronic\s+fiscal", re.I), "receipt"),
    (re.compile(r"receipt|payment\s+slip|pay\s+slip", re.I), "receipt"),
    (re.compile(r"tin\s*(certificate|registration|card)|taxpayer\s+identification", re.I), "tin_card"),
    (re.compile(r"assessment\s+notice|demand\s+note|tax\s+assessment", re.I), "assessment"),
    (re.compile(r"customs?\s+declaration|bill\s+of\s+entry|import\s+permit", re.I), "customs_declaration"),
    (re.compile(r"(tax\s+)?return|filing\s+form|annual\s+return|vat\s+return", re.I), "filing_form"),
    (re.compile(r"invoice|proforma|commercial\s+invoice", re.I), "invoice"),
]


def _classify_doc_type(vlm_output: str, ocr_text: str) -> str:
    """Classify document type from VLM output and/or OCR text."""
    combined = f"{vlm_output} {ocr_text}"
    for pattern, doc_type in _DOC_PATTERNS:
        if pattern.search(combined):
            return doc_type
    cls_res = classify_with_context(ocr_text, vlm_output)
    return cls_res.doc_type.value


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------


def _extract_fields(vlm_output: str, ocr_text: str) -> dict[str, list[str]]:
    """Extract structured fields from text via URA OCR extractors and regex."""
    combined = strip_hidden_control_and_bidi(f"{vlm_output} {ocr_text}")
    fields: dict[str, list[str]] = {}

    amounts = extract_ugx_amounts(combined)
    if not amounts:
        amounts = re.findall(r"\b[\d,]{4,}(?:\.\d{1,2})?\b", combined)
    if amounts:
        fields["amounts"] = amounts[:10]

    tins = extract_tin_numbers(combined)
    if tins:
        fields["tin_numbers"] = tins[:10]
        fields["tins"] = tins[:10]

    nins = extract_nin_numbers(combined)
    if nins:
        fields["nins"] = nins[:10]

    prns = extract_prn_numbers(combined)
    if prns:
        fields["prns"] = prns[:10]

    efris = extract_efris_invoice_numbers(combined)
    if efris:
        fields["efris_invoices"] = efris[:10]

    dates = extract_dates(combined)
    if dates:
        fields["dates"] = dates[:10]

    refs = extract_reference_numbers(combined)
    if refs:
        fields["reference_numbers"] = refs[:5]
        fields["references"] = refs[:5]

    tax_heads = extract_tax_heads(combined)
    if tax_heads:
        fields["tax_heads"] = tax_heads[:10]

    return fields
