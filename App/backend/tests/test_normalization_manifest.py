"""Unit tests for the canonical NormalizationManifest and bundle provenance."""

from __future__ import annotations

import io
from PIL import Image

from app.document_normalization import (
    NormalizationBundle,
    NormalizedPage,
    RasterizeSpec,
    build_normalization_manifest,
    normalize_document,
    validate_normalization_manifest,
)


def _make_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color="white").save(buf, format="PNG")
    return buf.getvalue()


def test_normalization_manifest_build_and_validation() -> None:
    img_data = _make_png_bytes()
    bundle = normalize_document(img_data, RasterizeSpec(), render_images=True)
    manifest = build_normalization_manifest(bundle, document_id="doc-test-001")

    assert manifest.document_id == "doc-test-001"
    assert manifest.page_count == 1
    assert manifest.source_kind == "image"
    assert len(manifest.page_manifests) == 1
    assert manifest.page_manifests[0]["has_rendered_image"] is True
    assert validate_normalization_manifest(manifest, img_data) is True

    # Mutated data must fail validation
    assert validate_normalization_manifest(manifest, img_data + b"tampered") is False


def test_normalization_manifest_serialization() -> None:
    bundle = NormalizationBundle(
        source_fingerprint="abc123sha256",
        page_count=2,
        pages=[
            NormalizedPage(
                page_index=0,
                source_page_number=1,
                original_size=(800, 600),
                text="Page 1 text",
                tables=[[["H1", "H2"], ["V1", "V2"]]],
                glyphs=[{"text": "H1", "bbox": [0, 0, 10, 10]}],
            ),
            NormalizedPage(
                page_index=1,
                source_page_number=2,
                original_size=(800, 600),
                text="Page 2 text",
            ),
        ],
        adapter="pypdfium2",
        source_kind="pdf",
    )
    manifest = build_normalization_manifest(bundle, document_id="doc-manifest-002")
    manifest_dict = manifest.to_dict()

    assert manifest_dict["document_id"] == "doc-manifest-002"
    assert manifest_dict["total_tables"] == 1
    assert manifest_dict["total_glyphs"] == 1
    assert manifest_dict["page_count"] == 2
    assert len(manifest_dict["page_manifests"]) == 2
