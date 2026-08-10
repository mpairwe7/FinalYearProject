"""OCR utilities — local EasyOCR or optional sidecar with URA post-processing.

Provides document text extraction optimised for Ugandan tax documents:
TIN numbers, UGX amounts, dates, and reference codes.

``OCR_BACKEND=service`` calls the local, health-checked sidecar documented in
``docs/local-ocr.md``.  ``auto`` uses it when configured and otherwise falls
back to embedded EasyOCR; unavailable OCR always returns empty results rather
than making document analysis fail.
"""

from __future__ import annotations

import io
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "en").split(",")
OCR_GPU = os.getenv("OCR_GPU", "true").lower() in ("1", "true", "yes")


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive integer without turning a bad local env into an outage."""
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %d", name, default)
        return default


_REMOTE_OCR_MAX_CONCURRENT = _positive_int_env("OCR_SERVICE_MAX_CONCURRENT", 2)
_remote_ocr_slots = threading.BoundedSemaphore(_REMOTE_OCR_MAX_CONCURRENT)
_REMOTE_OCR_MAX_PIXELS = _positive_int_env("OCR_SERVICE_MAX_PIXELS", 20_000_000)


class OCRUnavailableError(RuntimeError):
    """Raised by the strict local sidecar path when no OCR model is ready."""


@dataclass(frozen=True)
class OCRResult:
    """OCR output plus enough status to distinguish empty text from an outage."""

    items: list[dict[str, Any]]
    backend: str
    status: str
    detail: str = ""
    used_fallback: bool = False

    @property
    def text(self) -> str:
        return " ".join(item["text"] for item in self.items)

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
            logger.warning("EasyOCR unavailable", exc_info=True)
    return _ocr_reader


def local_ocr_status(*, warmup: bool = False) -> dict[str, Any]:
    """Return local OCR readiness without conflating liveness with model health.

    ``warmup=True`` deliberately initializes EasyOCR.  The sidecar readiness
    probe uses it so orchestration never routes scanned-document traffic to a
    container that has merely started Python but cannot recognise text.
    """
    reader = _get_reader() if warmup else _ocr_reader
    return {
        "backend": "easyocr",
        "ready": reader is not None,
        "model_loaded": reader is not None,
        "initialization_attempted": _ocr_attempted,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_text_with_boxes_local(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run the in-process EasyOCR reader and retain geometry/confidence.

    This is deliberately separate from :func:`extract_text_with_boxes` so
    ``app.ocr_service`` can force local inference even when the API process is
    configured to call that service remotely.  It avoids a sidecar-to-itself
    request loop.
    """
    reader = _get_reader()
    if reader is None:
        raise OCRUnavailableError("EasyOCR model is unavailable")
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
        logger.warning("OCR extraction failed", exc_info=True)
        raise OCRUnavailableError("EasyOCR inference failed") from None


def extract_text_with_boxes_local(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run only the embedded OCR backend.

    This is the sidecar entry point.  Application callers should normally use
    :func:`extract_text_with_boxes`, which honours ``OCR_BACKEND``.
    """
    return _extract_text_with_boxes_local(image_array)


def _configured_backend() -> str:
    """Return the current OCR backend, tolerating an invalid local setting."""
    backend = os.getenv("OCR_BACKEND", "auto").strip().lower()
    if backend in {"auto", "service", "easyocr", "disabled"}:
        return backend
    logger.warning("Unknown OCR_BACKEND=%r; using auto", backend)
    return "auto"


def _service_url() -> str:
    return os.getenv("OCR_SERVICE_URL", "").strip().rstrip("/")


def _normalise_remote_items(payload: Any) -> list[dict[str, Any]]:
    """Validate the narrow, versioned sidecar response contract."""
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("OCR service returned an invalid response")

    items: list[dict[str, Any]] = []
    for item in payload["items"]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        box = item.get("box")
        if not text or not isinstance(box, list):
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        items.append({"text": text, "box": box, "confidence": confidence})
    return items


def _ocr_via_service(image_array: np.ndarray) -> list[dict[str, Any]] | None:
    """Call the optional local OCR sidecar.

    ``None`` represents an unavailable service; an empty list is a successful
    OCR response containing no readable text.  Keeping those cases distinct
    lets ``auto`` fall back to embedded EasyOCR without double-running a valid
    empty result.
    """
    url = _service_url()
    if not url:
        logger.warning("OCR_BACKEND=service but OCR_SERVICE_URL is not configured")
        return None
    try:
        import httpx
        from PIL import Image  # type: ignore[import-untyped]

        if image_array.ndim < 2 or image_array.shape[0] * image_array.shape[1] > _REMOTE_OCR_MAX_PIXELS:
            logger.warning("OCR image exceeds configured remote pixel limit")
            return None
        image = Image.fromarray(image_array).convert("RGB")
        encoded = io.BytesIO()
        image.save(encoded, format="PNG", optimize=False)
        timeout = max(0.1, float(os.getenv("OCR_SERVICE_TIMEOUT_SECONDS", "6")))
        with _remote_ocr_slots:
            response = httpx.post(
                f"{url}/v1/ocr",
                content=encoded.getvalue(),
                headers={"Content-Type": "image/png"},
                timeout=timeout,
            )
        response.raise_for_status()
        return _normalise_remote_items(response.json())
    except Exception:
        logger.warning("OCR sidecar request failed", exc_info=True)
        return None


def _extract_items(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Compatibility wrapper returning only text regions for legacy callers."""
    return extract_ocr_result(image_array).items


def extract_ocr_result(image_array: np.ndarray) -> OCRResult:
    """Select OCR backend while preserving availability and fallback metadata."""
    backend = _configured_backend()
    if backend == "disabled":
        return OCRResult([], backend="disabled", status="disabled")

    should_try_service = backend == "service" or (backend == "auto" and bool(_service_url()))
    if should_try_service:
        remote_items = _ocr_via_service(image_array)
        if remote_items is not None:
            return OCRResult(remote_items, backend="service", status="ready")
        if backend == "service":
            return OCRResult(
                [],
                backend="service",
                status="unavailable",
                detail="OCR sidecar did not return a successful response",
            )

    try:
        items = _extract_text_with_boxes_local(image_array)
    except OCRUnavailableError as err:
        return OCRResult(
            [],
            backend="easyocr",
            status="unavailable",
            detail=str(err),
            used_fallback=should_try_service,
        )
    return OCRResult(
        items,
        backend="easyocr",
        status="ready",
        used_fallback=should_try_service,
    )


def extract_text(image_array: np.ndarray) -> str:
    """Run the configured OCR backend and return concatenated text."""
    return extract_ocr_result(image_array).text


def extract_text_with_boxes(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run OCR and return text with bounding boxes.

    Returns:
        List of ``{"text": str, "box": [[x1,y1], ...], "confidence": float}``
    """
    return _extract_items(image_array)


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
