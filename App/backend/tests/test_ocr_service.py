"""Unit tests for the opt-in Triton-served PP-OCRv6 sidecar and its API client contract."""

from __future__ import annotations

import asyncio
import io
import os
import unittest.mock as mock
from typing import Any

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
    assert "backend" in response
    assert "ready" in response
    assert "model_variant" in response


def test_sidecar_ready_warms_a_available_model() -> None:
    with mock.patch.object(
        ocr_service,
        "local_ocr_status",
        return_value={"backend": "ppocrv6", "ready": True, "model_loaded": True, "variant": "v6"},
    ) as status:
        response = ocr_service.ready()

    status.assert_called_once_with(warmup=True)
    assert response["status"] == "ready"


def test_sidecar_ready_rejects_unavailable_model() -> None:
    with mock.patch.object(
        ocr_service,
        "local_ocr_status",
        return_value={"backend": "ppocrv6", "ready": False, "model_loaded": False},
    ):
        with pytest.raises(HTTPException) as error:
            ocr_service.ready()

    assert error.value.status_code == 503


def test_sidecar_returns_normalised_ocr_items() -> None:
    local_items = [
        {
            "text": "TIN 1001234567",
            "box": [[0.0, 0.0], [5.0, 0.0], [5.0, 3.0], [0.0, 3.0]],
            "polygon": [[0.0, 0.0], [5.0, 0.0], [5.0, 3.0], [0.0, 3.0]],
            "confidence": 0.92,
        }
    ]
    with mock.patch.object(ocr_service, "extract_text_with_boxes_local", return_value=local_items), \
         mock.patch.object(ocr_service, "local_ocr_status", return_value={"backend": "ppocrv6", "variant": "v6"}):
        response = asyncio.run(ocr_service.ocr_image(_request_with_body(_png_bytes())))

    dumped = response.model_dump()
    assert dumped["items"] == local_items
    assert dumped["backend"] == "ppocrv6"
    assert dumped["model_variant"] == "v6"


def test_sidecar_rejects_invalid_image() -> None:
    with pytest.raises(HTTPException, match="not a valid image") as error:
        asyncio.run(ocr_service.ocr_image(_request_with_body(b"not-an-image")))

    assert error.value.status_code == 422


def test_service_backend_returns_sidecar_text_and_boxes() -> None:
    remote_items = [
        {
            "text": "UGX 50,000",
            "box": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "polygon": [[0, 0], [1, 0], [1, 1], [0, 1]],
            "confidence": 0.88,
        }
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
    local_items = [{"text": "fallback", "box": [], "polygon": [], "confidence": 0.5}]
    with mock.patch.dict(
        os.environ,
        {"OCR_BACKEND": "auto", "OCR_SERVICE_URL": "http://ocr.test"},
        clear=False,
    ), mock.patch.object(ocr, "_ocr_via_service", return_value=None), mock.patch.object(
        ocr, "_extract_text_with_boxes_local", return_value=local_items
    ):
        assert ocr.extract_text_with_boxes(np.zeros((2, 2, 3), dtype=np.uint8)) == local_items


def test_paddleocr_backend_parsing() -> None:
    class MockPaddleOCR:
        def ocr(self, image: np.ndarray, cls: bool = True) -> list[Any]:
            return [
                [
                    [[[0.0, 0.0], [10.0, 0.0], [10.0, 5.0], [0.0, 5.0]], ("TIN 1001234567", 0.98)],
                    [[[0.0, 10.0], [20.0, 10.0], [20.0, 15.0], [0.0, 15.0]], ("UGX 1,500,000", 0.95)],
                ]
            ]

    with mock.patch.object(ocr, "_get_reader", return_value=(MockPaddleOCR(), "ppocrv6")):
        items = ocr.extract_text_with_boxes_local(np.zeros((10, 10, 3), dtype=np.uint8))

    assert len(items) == 2
    assert items[0]["text"] == "TIN 1001234567"
    assert items[0]["confidence"] == 0.98
    assert "polygon" in items[0]
    assert items[1]["text"] == "UGX 1,500,000"


def test_triton_kserve_v2_payload_normalization() -> None:
    kserve_payload = {
        "model_name": "ocr",
        "outputs": [
            {"name": "rec_texts", "data": ["PRN: 220019283746", "INVOICE: FDEFRIS10029384"]},
            {"name": "rec_scores", "data": [0.99, 0.97]},
            {
                "name": "rec_polys",
                "data": [
                    [[10.0, 20.0], [100.0, 20.0], [100.0, 35.0], [10.0, 35.0]],
                    [[10.0, 40.0], [120.0, 40.0], [120.0, 55.0], [10.0, 55.0]],
                ],
            },
        ],
    }
    normalized = ocr._normalise_remote_items(kserve_payload)
    assert len(normalized) == 2
    assert normalized[0]["text"] == "PRN: 220019283746"
    assert normalized[0]["confidence"] == 0.99
    assert normalized[0]["polygon"] == [[10.0, 20.0], [100.0, 20.0], [100.0, 35.0], [10.0, 35.0]]


def test_domain_extractors_prn_efris_tins() -> None:
    receipt_text = (
        "UGANDA REVENUE AUTHORITY\n"
        "RECEIPT NUMBER: FDEFRIS9928371\n"
        "PAYMENT PRN: 22009988776655\n"
        "TAXPAYER TIN: 1001234567\n"
        "ASSESSMENT REF: ASMT-2026-8819\n"
        "TOTAL PAID: UGX 4,500,000\n"
        "DATE: 21/08/2026\n"
    )
    assert ocr.extract_tin_numbers(receipt_text) == ["1001234567"]
    assert "22009988776655" in ocr.extract_prn_numbers(receipt_text)
    assert "FDEFRIS9928371" in ocr.extract_efris_invoice_numbers(receipt_text)
    assert "ASMT-2026-8819" in ocr.extract_reference_numbers(receipt_text)
    assert len(ocr.extract_ugx_amounts(receipt_text)) >= 1
    assert "21/08/2026" in ocr.extract_dates(receipt_text)


def test_local_ocr_status_reports_variant() -> None:
    with mock.patch.dict(os.environ, {"OCR_MODEL_VARIANT": "v6"}, clear=False):
        status = ocr.local_ocr_status(warmup=False)
        assert "variant" in status
        assert status["variant"] == "v6"


def test_document_normalization_adapter_on_image() -> None:
    from app.document_normalization import normalize_document, RasterizeSpec

    img_data = _png_bytes()
    bundle = normalize_document(img_data, RasterizeSpec(), render_images=True)
    assert bundle.page_count == 1
    assert bundle.source_kind == "image"
    assert bundle.pages[0].original_size == (8, 6)
    assert bundle.pages[0].image_bytes is not None
