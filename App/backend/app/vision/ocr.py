"""OCR utilities — EasyOCR wrapper with URA-specific post-processing (2026).

Provides document text extraction optimised for Ugandan tax documents:
TIN numbers, UGX amounts, dates, and reference codes.

Falls back gracefully when EasyOCR is unavailable (returns empty results).
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "en").split(",")
OCR_GPU = os.getenv("OCR_GPU", "true").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# OCR engine
# ---------------------------------------------------------------------------

_ocr_lock = threading.Lock()
_ocr_reader = None
_ocr_attempted = False


def _get_reader():
    """Lazy-load EasyOCR reader (thread-safe singleton)."""
    global _ocr_reader, _ocr_attempted
    if _ocr_attempted:
        return _ocr_reader
    with _ocr_lock:
        if _ocr_attempted:
            return _ocr_reader
        _ocr_attempted = True
        try:
            import easyocr  # type: ignore[import-untyped]

            _ocr_reader = easyocr.Reader(OCR_LANGUAGES, gpu=OCR_GPU, verbose=False)
            logger.info("EasyOCR loaded (langs=%s, gpu=%s)", OCR_LANGUAGES, OCR_GPU)
        except Exception:
            logger.info("EasyOCR unavailable — OCR functions will return empty results")
    return _ocr_reader


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_text(image_array: np.ndarray) -> str:
    """Run OCR on an image array and return concatenated text."""
    reader = _get_reader()
    if reader is None:
        return ""
    try:
        results = reader.readtext(image_array)
        return " ".join(r[1] for r in results if r[1].strip())
    except Exception:
        logger.warning("OCR extraction failed", exc_info=True)
        return ""


def extract_text_with_boxes(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run OCR and return text with bounding boxes.

    Returns:
        List of ``{"text": str, "box": [[x1,y1], ...], "confidence": float}``
    """
    reader = _get_reader()
    if reader is None:
        return []
    try:
        results = reader.readtext(image_array)
        return [
            {
                "text": r[1],
                "box": r[0],
                "confidence": float(r[2]) if len(r) > 2 else 0.0,
            }
            for r in results
            if r[1].strip()
        ]
    except Exception:
        logger.warning("OCR with boxes failed", exc_info=True)
        return []


# ---------------------------------------------------------------------------
# URA-specific post-processing
# ---------------------------------------------------------------------------


def extract_tin_numbers(text: str) -> list[str]:
    """Extract Uganda TIN numbers (10-digit, starts with 1)."""
    return list(set(re.findall(r"\b1\d{9}\b", text)))


def extract_ugx_amounts(text: str) -> list[str]:
    """Extract UGX currency amounts."""
    amounts = re.findall(r"UGX?\s*[\d,]+(?:\.\d{1,2})?", text, re.I)
    if not amounts:
        amounts = re.findall(r"\b[\d,]{4,}(?:\.\d{1,2})?\b", text)
    return amounts


def extract_dates(text: str) -> list[str]:
    """Extract date strings in common formats."""
    return re.findall(r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}", text)


def extract_reference_numbers(text: str) -> list[str]:
    """Extract URA reference/assessment numbers."""
    return re.findall(r"\b[A-Z]{2,4}[-/]?\d{6,12}\b", text)


def clean_ocr_text(raw_text: str) -> str:
    """Clean OCR output: fix common misreads, normalise whitespace."""
    text = raw_text
    # Common OCR substitutions in financial documents
    text = re.sub(r"\bO(\d)", r"0\1", text)  # O → 0 before digit
    text = re.sub(r"(\d)O\b", r"\g<1>0", text)  # 0 → O after digit
    text = re.sub(r"\bl\b", "1", text)  # lone l → 1
    text = re.sub(r"  +", " ", text)  # collapse whitespace
    return text.strip()
