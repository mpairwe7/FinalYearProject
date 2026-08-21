"""Triton-served PP-OCRv6 sidecar & proxy for high-precision document extraction.

The main API sets ``OCR_BACKEND=triton`` or ``OCR_BACKEND=service`` and calls this
service over the private network. Serving PP-OCRv6 with line-level bounding
polygons provides state-of-the-art recognition for dense tables, complex alphanumeric
codes (TINs, PRNs, EFRIS invoice numbers), and fine-print receipts with low GPU latency.

Architecture:
- PP-OCRv6 (PaddleOCR / Triton Inference Server KServe v2)
- Line-level bounding polygons (4-point quadrilaterals / multi-point contours)
- Independent health/readiness probes with model pre-warming

Internal only: binds to loopback for diagnostics and private container network for API.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from PIL import Image, UnidentifiedImageError  # type: ignore[import-untyped]
from pydantic import BaseModel, Field

from .vision.ocr import OCRUnavailableError, extract_text_with_boxes_local, local_ocr_status

logger = logging.getLogger(__name__)

MAX_INPUT_BYTES = int(os.getenv("OCR_SERVICE_MAX_BYTES", str(16 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.getenv("OCR_SERVICE_MAX_PIXELS", str(24_000_000)))
OCR_MAX_CONCURRENT = max(1, int(os.getenv("OCR_INFERENCE_MAX_CONCURRENT", "2")))
_ocr_slots = threading.BoundedSemaphore(OCR_MAX_CONCURRENT)
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def _looks_like_image(data: bytes) -> bool:
    """Cheap magic-byte check before Pillow decodes an untrusted body."""
    if data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"BM", b"II*\x00", b"MM\x00*")):
        return True
    return data.startswith(b"RIFF") and data[8:12] == b"WEBP"


app = FastAPI(title="URA Triton PP-OCRv6 Service", version="2.0.0", docs_url=None, redoc_url=None)


class OCRItemResponse(BaseModel):
    text: str
    box: list[list[float]] = Field(default_factory=list)
    polygon: list[list[float]] = Field(default_factory=list)
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class OCRResponse(BaseModel):
    items: list[OCRItemResponse] = Field(default_factory=list)
    backend: str = "triton_ppocrv6"
    model_variant: str = "v6"


def _run_local_ocr(image: np.ndarray) -> list[dict[str, Any]]:
    """Execute SOTA PP-OCRv6 inference guarded by concurrency semaphore."""
    with _ocr_slots:
        return extract_text_with_boxes_local(image)


async def _read_body_bounded(request: Request) -> bytes:
    """Consume a raw-image request without buffering unbounded bodies."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_INPUT_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"OCR input exceeds the {MAX_INPUT_BYTES // (1024 * 1024)} MiB limit",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _decode_image(data: bytes) -> np.ndarray:
    """Decode image in-memory without touching disk."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            width, height = opened.size
            if width * height > MAX_IMAGE_PIXELS:
                raise HTTPException(
                    status_code=413,
                    detail=f"Image exceeds the {MAX_IMAGE_PIXELS:,}-pixel OCR limit",
                )
            image = opened.convert("RGB")
            image.load()
            return np.asarray(image)
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as err:
        raise HTTPException(status_code=422, detail="OCR input is not a valid image") from err


def _normalise_item(item: dict[str, Any]) -> OCRItemResponse | None:
    text = str(item.get("text", "")).strip()
    raw_box = item.get("box") or item.get("polygon")
    raw_poly = item.get("polygon") or item.get("box")
    if not text or not isinstance(raw_box, list):
        return None
    try:
        box = [[float(point[0]), float(point[1])] for point in raw_box]
        polygon = [[float(point[0]), float(point[1])] for point in raw_poly] if raw_poly else box
        confidence = min(1.0, max(0.0, float(item.get("confidence", 0.0))))
    except (IndexError, TypeError, ValueError):
        return None
    return OCRItemResponse(text=text, box=box, polygon=polygon, confidence=confidence)


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness endpoint. Use ``/ready`` before sending OCR traffic."""
    status = local_ocr_status(warmup=False)
    return {
        "status": "alive",
        "backend": status.get("backend", "ppocrv6"),
        "model_variant": status.get("variant", "v6"),
        "max_input_bytes": MAX_INPUT_BYTES,
        "max_image_pixels": MAX_IMAGE_PIXELS,
        "ready": status["ready"],
        "gpu_enabled": status.get("gpu_enabled", False),
    }


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Readiness probe that warms and verifies the PP-OCRv6 model."""
    status = local_ocr_status(warmup=True)
    if not status["ready"]:
        backend_label = status.get("backend", "PP-OCRv6")
        raise HTTPException(status_code=503, detail=f"{backend_label} model is unavailable")
    return {"status": "ready", **status}


@app.post("/v1/ocr", response_model=OCRResponse)
async def ocr_image(request: Request) -> OCRResponse:
    """Recognise text from one image and return text, line-level polygons, confidence."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_INPUT_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"OCR input exceeds the {MAX_INPUT_BYTES // (1024 * 1024)} MiB limit",
                )
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None

    data = await _read_body_bounded(request)
    if not data:
        raise HTTPException(status_code=422, detail="OCR input is empty")
    if not _looks_like_image(data):
        raise HTTPException(status_code=422, detail="OCR input is not a valid image")
    if len(data) > MAX_INPUT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"OCR input exceeds the {MAX_INPUT_BYTES // (1024 * 1024)} MiB limit",
        )

    image = _decode_image(data)
    try:
        raw_items = await asyncio.to_thread(_run_local_ocr, image)
    except OCRUnavailableError as err:
        logger.warning("OCR request rejected: %s", err)
        raise HTTPException(status_code=503, detail=str(err)) from err
    items = [item for raw in raw_items if (item := _normalise_item(raw))]
    status = local_ocr_status(warmup=False)
    backend_name = status.get("backend", "ppocrv6")
    variant = status.get("variant", "v6")
    logger.info("PP-OCRv6 completed (%s): pixels=%d items=%d", backend_name, image.shape[0] * image.shape[1], len(items))
    return OCRResponse(items=items, backend=backend_name, model_variant=variant)
