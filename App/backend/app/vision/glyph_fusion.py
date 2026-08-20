"""Vector Glyph & Spatial OCR Fusion Engine (2026).

Combines vector glyphs (crisp, zero OCR error) with OCR outputs for embedded
images/diagrams on the same page. Aligned with enterprise BP workflow-engine
contracts (issue #480 / PR #235 / #476):

1. Vector Glyphs (Exact Digital Text): Extracted directly from PDF vector text
   streams with exact Unicode strings, exact bounding geometry, and 1.0 confidence.
2. Inset OCR (Embedded Images/Diagrams): Extracted from raster images, diagrams,
   and visual stamps using Triton PP-OCRv6 with line-level bounding polygons.
3. Spatial Fusion: Correlates vector glyphs and OCR regions spatially per page.
   - Non-overlapping OCR items (embedded receipts, diagram callouts, stamps) are
     promoted as ``ocr_inset`` tokens.
   - Overlapping regions are validated for consistency without corrupting crisp vector text.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VectorGlyph:
    """A vector text glyph or word extracted directly from the PDF text stream."""

    text: str
    bbox: list[float]  # [x1, y1, x2, y2] in raster pixel space
    page: int
    confidence: float = 1.0
    source: str = "vector_glyph"


@dataclass
class FusedTextToken:
    """Canonical fused text token representing either vector glyphs or visual insets."""

    text: str
    bbox: list[float]  # [x1, y1, x2, y2]
    page: int
    confidence: float = 1.0
    source: str = "vector_glyph"  # "vector_glyph", "ocr_inset", "ocr"
    polygon: list[list[float]] | None = None
    alt_text: str | None = None
    consistent: bool | None = None


def _bbox_area(bbox: list[float]) -> float:
    if len(bbox) < 4:
        return 0.0
    return max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])


def _bbox_intersection(b1: list[float], b2: list[float]) -> float:
    if len(b1) < 4 or len(b2) < 4:
        return 0.0
    ix1 = max(b1[0], b2[0])
    iy1 = max(b1[1], b2[1])
    ix2 = min(b1[2], b2[2])
    iy2 = min(b1[3], b2[3])
    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0
    return (ix2 - ix1) * (iy2 - iy1)


def extract_page_vector_glyphs(
    pdf_data: bytes,
    page_index: int,
    *,
    dpi: float = 150.0,
) -> list[VectorGlyph]:
    """Extract crisp vector words/glyphs from a PDF page scaled to raster pixel space."""
    glyphs: list[VectorGlyph] = []
    scale = dpi / 72.0

    # 1. Try pdfplumber (MIT) for word-level bounding boxes
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
            if 0 <= page_index < len(pdf.pages):
                page = pdf.pages[page_index]
                words = page.extract_words() or []
                for w in words:
                    text = str(w.get("text", "")).strip()
                    if not text:
                        continue
                    x0 = float(w.get("x0", 0.0)) * scale
                    top = float(w.get("top", 0.0)) * scale
                    x1 = float(w.get("x1", 0.0)) * scale
                    bottom = float(w.get("bottom", 0.0)) * scale
                    glyphs.append(
                        VectorGlyph(
                            text=text,
                            bbox=[x0, top, x1, bottom],
                            page=page_index + 1,
                            confidence=1.0,
                            source="vector_glyph",
                        )
                    )
                if glyphs:
                    return glyphs
    except Exception as plumb_err:
        logger.debug("pdfplumber word extraction failed on page %d: %s", page_index, plumb_err)

    # 2. Fallback to pypdfium2 (Apache-2.0)
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(pdf_data)
        if 0 <= page_index < len(pdf):
            page = pdf[page_index]
            textpage = page.get_textpage()
            raw_text = textpage.get_text_range() or ""
            width_pt, height_pt = page.get_size()
            width_px = width_pt * scale
            height_px = height_pt * scale

            for line in raw_text.splitlines():
                line = line.strip()
                if line:
                    glyphs.append(
                        VectorGlyph(
                            text=line,
                            bbox=[0.0, 0.0, width_px, height_px],
                            page=page_index + 1,
                            confidence=1.0,
                            source="vector_glyph",
                        )
                    )
    except Exception as pdfium_err:
        logger.debug("pypdfium2 vector extraction failed on page %d: %s", page_index, pdfium_err)

    return glyphs


def fuse_page_glyphs_and_ocr(
    vector_glyphs: list[VectorGlyph],
    ocr_items: list[dict[str, Any]],
    page_number: int = 1,
    *,
    min_overlap_ratio: float = 0.3,
) -> list[FusedTextToken]:
    """Fuse vector glyphs with OCR outputs for embedded images/diagrams on the same page.

    - Vector glyphs are preserved with 1.0 confidence (zero OCR error).
    - OCR items matching vector glyphs are checked for consistency.
    - OCR items from embedded images/diagrams (no vector glyph overlap) are merged as ``ocr_inset``.
    """
    fused_tokens: list[FusedTextToken] = []

    # 1. Add all crisp vector glyphs as primary tokens
    for glyph in vector_glyphs:
        fused_tokens.append(
            FusedTextToken(
                text=glyph.text,
                bbox=glyph.bbox,
                page=page_number,
                confidence=1.0,
                source="vector_glyph",
            )
        )

    # 2. Correlate OCR items with vector glyphs
    for ocr_item in ocr_items:
        ocr_text = str(ocr_item.get("text", "")).strip()
        if not ocr_text:
            continue

        raw_box = ocr_item.get("box", [])
        polygon = ocr_item.get("polygon") or raw_box
        confidence = float(ocr_item.get("confidence", 0.0))

        # Normalize 4-point polygon or box to [x1, y1, x2, y2]
        if isinstance(raw_box, list) and len(raw_box) >= 4 and isinstance(raw_box[0], list):
            xs = [float(p[0]) for p in raw_box if len(p) >= 2]
            ys = [float(p[1]) for p in raw_box if len(p) >= 2]
            ocr_bbox = [min(xs), min(ys), max(xs), max(ys)] if xs and ys else [0.0, 0.0, 0.0, 0.0]
        elif isinstance(raw_box, list) and len(raw_box) == 4 and isinstance(raw_box[0], (int, float)):
            ocr_bbox = [float(v) for v in raw_box]
        else:
            ocr_bbox = [0.0, 0.0, 0.0, 0.0]

        ocr_area = _bbox_area(ocr_bbox)
        matched_glyph: VectorGlyph | None = None
        max_overlap = 0.0

        if ocr_area > 0.0:
            for glyph in vector_glyphs:
                glyph_area = _bbox_area(glyph.bbox)
                if glyph_area <= 0.0:
                    continue
                intersection = _bbox_intersection(ocr_bbox, glyph.bbox)
                overlap = intersection / min(ocr_area, glyph_area)
                if overlap > max_overlap and overlap >= min_overlap_ratio:
                    max_overlap = overlap
                    matched_glyph = glyph

        if matched_glyph is not None:
            # Overlapping region: Vector glyph is already present. Record consistency note.
            norm_ocr = re.sub(r"[^a-zA-Z0-9]+", "", ocr_text).lower()
            norm_vec = re.sub(r"[^a-zA-Z0-9]+", "", matched_glyph.text).lower()
            consistent = norm_ocr in norm_vec or norm_vec in norm_ocr

            # Find matching fused token and annotate
            for token in fused_tokens:
                if token.source == "vector_glyph" and token.text == matched_glyph.text and token.bbox == matched_glyph.bbox:
                    token.alt_text = ocr_text
                    token.consistent = consistent
                    token.polygon = polygon
                    break
        else:
            # Non-overlapping: Embedded image, diagram callout, receipt or visual stamp
            fused_tokens.append(
                FusedTextToken(
                    text=ocr_text,
                    bbox=ocr_bbox,
                    page=page_number,
                    confidence=confidence,
                    source="ocr_inset",
                    polygon=polygon,
                    consistent=True,
                )
            )

    return fused_tokens
