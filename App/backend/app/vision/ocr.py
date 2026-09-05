"""OCR utilities — Triton-served PP-OCRv6 / PaddleOCR & local fallback with URA post-processing.

Provides state-of-the-art recognition for dense tables, complex alphanumeric codes
(TINs, PRNs, EFRIS invoice numbers, assessment references), and fine print receipts
with low GPU latency and line-level bounding polygons.

Supported Architectures:
- ``triton`` / ``ppocrv6``: Triton Inference Server PP-OCRv6 (primary SOTA pipeline)
  providing line-level bounding polygons and high-throughput dynamic batching.
- ``paddleocr`` / ``ppocr``: In-process PP-OCRv6 / PP-OCRv5 engine.
- ``service``: Calls the health-checked local sidecar / proxy (:8100).
- ``easyocr``: Lightweight local fallback reader.
- ``auto``: Triton/Service when configured, falling back gracefully to embedded engines.
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

OCR_LANGUAGES = os.getenv("OCR_LANGUAGES", "en").split(",")
OCR_GPU = os.getenv("OCR_GPU", "true").lower() in ("1", "true", "yes")
OCR_MODEL_VARIANT = os.getenv("OCR_MODEL_VARIANT", "v6").strip().lower()
TRITON_OCR_URL = os.getenv("TRITON_OCR_URL", "").strip().rstrip("/")


def _positive_int_env(name: str, default: int) -> int:
    """Read a positive integer without turning a bad local env into an outage."""
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        logger.warning("Invalid %s; using %d", name, default)
        return default


_REMOTE_OCR_MAX_CONCURRENT = _positive_int_env("OCR_SERVICE_MAX_CONCURRENT", 4)
_remote_ocr_slots = threading.BoundedSemaphore(_REMOTE_OCR_MAX_CONCURRENT)
_REMOTE_OCR_MAX_PIXELS = _positive_int_env("OCR_SERVICE_MAX_PIXELS", 24_000_000)


class OCRUnavailableError(RuntimeError):
    """Raised by the strict local sidecar path when no OCR model is ready."""


@dataclass(frozen=True)
class OCRResult:
    """OCR output with line-level polygon geometry, bounding boxes, and provenance."""

    items: list[dict[str, Any]]
    backend: str
    status: str
    detail: str = ""
    used_fallback: bool = False

    @property
    def text(self) -> str:
        return " ".join(item["text"] for item in self.items)

    @property
    def polygons(self) -> list[list[list[float]]]:
        return [item.get("polygon") or item.get("box", []) for item in self.items]


# ---------------------------------------------------------------------------
# SOTA Engine Management (PP-OCRv6 / Triton / PaddleOCR)
# ---------------------------------------------------------------------------

_ocr_lock = threading.Lock()
_ocr_reader: Any = None
_ocr_engine_type: str = "ppocrv6"
_ocr_attempted: bool = False


def _get_reader() -> tuple[Any, str]:
    """Lazy-load OCR reader (PP-OCRv6 preferred if installed/configured)."""
    global _ocr_reader, _ocr_engine_type, _ocr_attempted
    if _ocr_attempted:
        return _ocr_reader, _ocr_engine_type
    with _ocr_lock:
        if _ocr_attempted:
            return _ocr_reader, _ocr_engine_type
        _ocr_attempted = True

        preferred_engine = os.getenv("OCR_ENGINE", "auto").strip().lower()

        # 1. Primary: PaddleOCR (PP-OCRv6 SOTA pipeline)
        if preferred_engine in {"paddleocr", "ppocr", "ppocrv6", "paddlex", "triton", "auto"}:
            try:
                from paddleocr import PaddleOCR  # type: ignore[import-untyped]

                lang = OCR_LANGUAGES[0] if OCR_LANGUAGES else "en"
                _ocr_reader = PaddleOCR(
                    use_angle_cls=True,
                    lang=lang,
                    use_gpu=OCR_GPU,
                    show_log=False,
                )
                _ocr_engine_type = "ppocrv6"
                logger.info(
                    "PP-OCRv6 engine loaded (variant=%s, lang=%s, gpu=%s)",
                    OCR_MODEL_VARIANT,
                    lang,
                    OCR_GPU,
                )
                return _ocr_reader, _ocr_engine_type
            except Exception:
                if preferred_engine in {"paddleocr", "ppocr", "ppocrv6", "paddlex"}:
                    logger.warning("PP-OCRv6 explicitly requested but unavailable", exc_info=True)

        # 2. Fall back to EasyOCR
        try:
            import easyocr  # type: ignore[import-untyped]

            _ocr_reader = easyocr.Reader(OCR_LANGUAGES, gpu=OCR_GPU, verbose=False)
            _ocr_engine_type = "easyocr"
            logger.info("EasyOCR fallback loaded (langs=%s, gpu=%s)", OCR_LANGUAGES, OCR_GPU)
        except Exception:
            logger.warning("Embedded OCR unavailable", exc_info=True)
            _ocr_reader = None
            _ocr_engine_type = "ppocrv6"

    return _ocr_reader, _ocr_engine_type


def local_ocr_status(*, warmup: bool = False) -> dict[str, Any]:
    """Return local OCR readiness and model variant without conflating liveness."""
    reader, engine_type = _get_reader() if warmup else (_ocr_reader, _ocr_engine_type)
    return {
        "backend": engine_type or "ppocrv6",
        "variant": OCR_MODEL_VARIANT,
        "ready": reader is not None,
        "model_loaded": reader is not None,
        "initialization_attempted": _ocr_attempted,
        "gpu_enabled": OCR_GPU,
    }


# ---------------------------------------------------------------------------
# In-Process Extraction
# ---------------------------------------------------------------------------


def _extract_text_with_boxes_local(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run in-process PP-OCRv6/PaddleOCR with line-level polygons and confidence."""
    reader, engine_type = _get_reader()
    if reader is None:
        raise OCRUnavailableError(f"{engine_type} model is unavailable")

    try:
        if engine_type in {"ppocrv6", "paddleocr", "ppocr"}:
            # PP-OCR returns [[[polygon_4pts], (text, confidence)], ...]
            raw_results = reader.ocr(image_array, cls=True)
            items: list[dict[str, Any]] = []
            if raw_results and raw_results[0]:
                for line in raw_results[0]:
                    if not line or len(line) < 2:
                        continue
                    poly = line[0]
                    text, score = line[1]
                    if text and text.strip():
                        # Compute bounding box from polygon
                        try:
                            xs = [float(p[0]) for p in poly]
                            ys = [float(p[1]) for p in poly]
                            box = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
                        except Exception:
                            box = poly

                        items.append(
                            {
                                "text": text.strip(),
                                "box": box,
                                "polygon": poly,
                                "confidence": float(score) if score is not None else 0.0,
                            }
                        )
            return items

        # EasyOCR path
        results = reader.readtext(image_array)
        return [
            {
                "text": r[1],
                "box": r[0],
                "polygon": r[0],
                "confidence": float(r[2]) if len(r) > 2 else 0.0,
            }
            for r in results
            if r[1].strip()
        ]
    except Exception:
        logger.warning("OCR extraction failed", exc_info=True)
        raise OCRUnavailableError(f"{engine_type} inference failed") from None


def extract_text_with_boxes_local(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run in-process OCR engine."""
    return _extract_text_with_boxes_local(image_array)


def _configured_backend() -> str:
    """Return the current OCR backend."""
    backend = os.getenv("OCR_BACKEND", "auto").strip().lower()
    if backend in {"auto", "service", "triton", "ppocrv6", "paddleocr", "ppocr", "easyocr", "disabled"}:
        return backend
    logger.warning("Unknown OCR_BACKEND=%r; using auto", backend)
    return "auto"


def _service_url() -> str:
    return (TRITON_OCR_URL or os.getenv("OCR_SERVICE_URL", "")).strip().rstrip("/")


def _normalise_remote_items(payload: Any) -> list[dict[str, Any]]:
    """Validate remote response contract supporting Triton & sidecar formats."""
    if not isinstance(payload, dict):
        raise ValueError("OCR service returned an invalid response type")

    # 1. Standard /v1/ocr sidecar or proxy format
    if "items" in payload and isinstance(payload["items"], list):
        items: list[dict[str, Any]] = []
        for item in payload["items"]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            box = item.get("box") or item.get("polygon")
            polygon = item.get("polygon") or item.get("box")
            if not text or not isinstance(box, list):
                continue
            try:
                confidence = float(item.get("confidence", 0.0))
            except (TypeError, ValueError):
                confidence = 0.0
            items.append({
                "text": text,
                "box": box,
                "polygon": polygon,
                "confidence": confidence,
            })
        return items

    # 2. Triton KServe v2 raw inference response format
    if "outputs" in payload and isinstance(payload["outputs"], list):
        outputs = {out.get("name"): out.get("data", []) for out in payload["outputs"] if isinstance(out, dict)}
        texts = outputs.get("rec_texts", [])
        scores = outputs.get("rec_scores", [])
        polys = outputs.get("rec_polys", outputs.get("dt_polys", []))
        items = []
        for idx, text in enumerate(texts):
            if not text or not str(text).strip():
                continue
            score = float(scores[idx]) if idx < len(scores) else 0.0
            poly = polys[idx] if idx < len(polys) and isinstance(polys[idx], list) else []
            items.append({
                "text": str(text).strip(),
                "box": poly,
                "polygon": poly,
                "confidence": score,
            })
        return items

    return []


def _ocr_via_service(image_array: np.ndarray) -> list[dict[str, Any]] | None:
    """Call Triton-served PP-OCRv6 sidecar or proxy."""
    url = _service_url()
    if not url:
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
            endpoint = f"{url}/v1/ocr"
            response = httpx.post(
                endpoint,
                content=encoded.getvalue(),
                headers={"Content-Type": "image/png"},
                timeout=timeout,
            )
        response.raise_for_status()
        return _normalise_remote_items(response.json())
    except Exception:
        logger.warning("Triton OCR request failed", exc_info=True)
        return None


def extract_ocr_result(image_array: np.ndarray) -> OCRResult:
    """Select OCR backend (Triton PP-OCRv6 prioritized) with fallback metadata."""
    backend = _configured_backend()
    if backend == "disabled":
        return OCRResult([], backend="disabled", status="disabled")

    should_try_service = backend in {"service", "triton"} or (backend == "auto" and bool(_service_url()))
    if should_try_service:
        remote_items = _ocr_via_service(image_array)
        if remote_items is not None:
            return OCRResult(remote_items, backend="triton_ppocrv6", status="ready")
        if backend in {"service", "triton"}:
            return OCRResult(
                [],
                backend="triton_ppocrv6",
                status="unavailable",
                detail="Triton PP-OCRv6 sidecar did not return a successful response",
            )

    active_engine = _ocr_engine_type or "ppocrv6"
    try:
        items = _extract_text_with_boxes_local(image_array)
    except OCRUnavailableError as err:
        return OCRResult(
            [],
            backend=active_engine,
            status="unavailable",
            detail=str(err),
            used_fallback=should_try_service,
        )
    return OCRResult(
        items,
        backend=active_engine,
        status="ready",
        used_fallback=should_try_service,
    )


def extract_text(image_array: np.ndarray) -> str:
    """Run configured OCR backend and return concatenated text."""
    return extract_ocr_result(image_array).text


def extract_text_with_boxes(image_array: np.ndarray) -> list[dict[str, Any]]:
    """Run OCR and return text with line-level polygon geometry and bounding boxes."""
    return extract_ocr_result(image_array).items


# ---------------------------------------------------------------------------
# SOTA URA Financial Document & Alphanumeric Extractors
# ---------------------------------------------------------------------------


def extract_tin_numbers(text: str) -> list[str]:
    """Extract Uganda TIN numbers (10-digit, starting with 1)."""
    return list(set(re.findall(r"\b1\d{9}\b", text)))


def extract_prn_numbers(text: str) -> list[str]:
    """Extract Uganda Payment Registration Numbers (PRNs: 12-15 digits, typically starts with 2)."""
    matches = re.findall(r"(?:PRN[:\s#]*)?\b(2\d{11,14})\b", text, re.I)
    return list(set(matches))


def extract_efris_invoice_numbers(text: str) -> list[str]:
    """Extract EFRIS fiscal receipt and invoice reference numbers."""
    matches = re.findall(r"\b(?:FD|INV|EF)[A-Z0-9]{8,18}\b", text, re.I)
    return list(set(matches))


def extract_ugx_amounts(text: str) -> list[str]:
    """Extract UGX and currency amounts from dense financial tables."""
    amounts = re.findall(r"(?:UGX|Shs?\.?|USD|UShs?\.?)\s*[\d,]+(?:\.\d{1,2})?", text, re.I)
    if not amounts:
        candidates = re.findall(r"\b\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?\b", text)
        amounts = [c for c in candidates if not re.fullmatch(r"(?:19|20)\d{2}", c)]
    return amounts


def extract_dates(text: str) -> list[str]:
    """Extract date strings from receipts, notices, and assessment tables."""
    return re.findall(r"\b\d{1,2}[/\-\.](?:\d{1,2}|[A-Za-z]{3})[/\-\.]\d{2,4}\b", text)


def extract_reference_numbers(text: str) -> list[str]:
    """Extract URA assessment, case, and transaction reference numbers containing digits."""
    raw_matches = re.findall(r"\b[A-Z]{2,6}(?:[-/][0-9A-Z]+)+\b|\b[A-Z]{2,6}[-/]?[0-9]{4,14}\b", text)
    # Filter out plain English hyphenated words (e.g. ANTI-AVOIDANCE, NON-RESIDENTS) - require digits
    return list(set(m for m in raw_matches if re.search(r"\d", m)))


def clean_ocr_text(raw_text: str) -> str:
    """Clean OCR output for dense financial tables and alphanumeric codes."""
    text = raw_text
    # Common OCR substitutions in financial documents
    text = re.sub(r"\bO(\d)", r"0\1", text)  # O → 0 before digit
    text = re.sub(r"(\d)O\b", r"\g<1>0", text)  # O → 0 after digit
    text = re.sub(r"\bl\b", "1", text)  # lone l → 1
    text = re.sub(r"  +", " ", text)  # collapse whitespace
    return text.strip()
