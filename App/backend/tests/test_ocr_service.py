"""Unit tests for the opt-in local OCR sidecar and its API client contract."""

from __future__ import annotations

import asyncio
import io
import os
import unittest.mock as mock

import numpy as np
import pytest
from fastapi import HTTPException
from PIL import Image
from starlette.requests import Request

from app import ocr_service
from app.vision import ocr


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 6), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


def _request_with_body(body: bytes) -> Request:
    """Build a minimal ASGI request without TestClient's app lifecycle."""
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/ocr",
            "headers": [(b"content-length", str(len(body)).encode())],
        },
        receive,
    )


def test_sidecar_health() -> None:
    response = ocr_service.health()

    assert response["status"] == "alive"
    assert response["backend"] == "easyocr"
    assert "ready" in response


def test_sidecar_ready_warms_a_available_model() -> None:
    with mock.patch.object(
        ocr_service,
        "local_ocr_status",
        return_value={"backend": "easyocr", "ready": True, "model_loaded": True},
    ) as status:
        response = ocr_service.ready()

    status.assert_called_once_with(warmup=True)
    assert response["status"] == "ready"


def test_sidecar_ready_rejects_unavailable_model() -> None:
    with mock.patch.object(
        ocr_service,
        "local_ocr_status",
        return_value={"backend": "easyocr", "ready": False, "model_loaded": False},
    ):
        with pytest.raises(HTTPException) as error:
            ocr_service.ready()

    assert error.value.status_code == 503


def test_sidecar_returns_normalised_ocr_items() -> None:
    local_items = [
        {
            "text": "TIN 1001234567",
            "box": [[0, 0], [5, 0], [5, 3], [0, 3]],
            "confidence": 0.92,
        }
    ]
    with mock.patch.object(ocr_service, "extract_text_with_boxes_local", return_value=local_items):
        response = asyncio.run(ocr_service.ocr_image(_request_with_body(_png_bytes())))

    assert response.model_dump() == {"items": local_items, "backend": "easyocr"}


def test_sidecar_rejects_invalid_image() -> None:
    with pytest.raises(HTTPException, match="not a valid image") as error:
        asyncio.run(ocr_service.ocr_image(_request_with_body(b"not-an-image")))

    assert error.value.status_code == 422


def test_service_backend_returns_sidecar_text_and_boxes() -> None:
    remote_items = [
        {"text": "UGX 50,000", "box": [[0, 0], [1, 0], [1, 1], [0, 1]], "confidence": 0.88}
    ]
    with mock.patch.dict(
        os.environ,
        {"OCR_BACKEND": "service", "OCR_SERVICE_URL": "http://ocr.test"},
        clear=False,
    ), mock.patch.object(ocr, "_ocr_via_service", return_value=remote_items) as remote:
        image = np.zeros((2, 2, 3), dtype=np.uint8)
        assert ocr.extract_text(image) == "UGX 50,000"
        assert ocr.extract_text_with_boxes(image) == remote_items

    assert remote.call_count == 2


def test_auto_backend_falls_back_only_when_sidecar_is_unavailable() -> None:
    local_items = [{"text": "fallback", "box": [], "confidence": 0.5}]
    with mock.patch.dict(
        os.environ,
        {"OCR_BACKEND": "auto", "OCR_SERVICE_URL": "http://ocr.test"},
        clear=False,
    ), mock.patch.object(ocr, "_ocr_via_service", return_value=None), mock.patch.object(
        ocr, "_extract_text_with_boxes_local", return_value=local_items
    ):
        assert ocr.extract_text_with_boxes(np.zeros((2, 2, 3), dtype=np.uint8)) == local_items
