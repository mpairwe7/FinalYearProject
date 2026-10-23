"""Deterministic Vector & Line Grid Table Structuring Pipeline (2026).

Implements the enterprise BP workflow-engine table structuring orchestration contract:
Uses a deterministic vector/line grid detector, falling back to VLM / heuristic cell
structuring only when table borders are missing (never silent VLM adoption).

Producers & Selection Hierarchy:
1. ``pdf_vector``: Deterministic vector line and ruling intersection extraction from PDF streams (confidence >= 0.90).
2. ``raster_line_geometry``: Deterministic raster border/line intersection geometry.
3. ``vlm`` / ``raster_fallback``: Used strictly when table borders are missing, stamped with
   ``downstream_action="use_with_warning"`` and ``warnings=["missing_line_candidate", "vlm_fallback"]``.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TableCell:
    """Canonical representation of a single structured table cell."""

    row_idx: int
    col_idx: int
    text: str
    bbox: list[float] = field(default_factory=list)  # [x1, y1, x2, y2]
    row_span: int = 1
    col_span: int = 1
    confidence: float = 1.0
    bbox_source: str = "pdf_vector"  # "pdf_vector", "raster_line_geometry", "vlm", "geometry"


@dataclass
class StructuredTable:
    """Structured table result with deterministic provenance and review flags."""

    table_id: str
    page_number: int
    rows: int
    cols: int
    cells: list[TableCell] = field(default_factory=list)
    matrix: list[list[str]] = field(default_factory=list)
    source: str = "pdf_vector"  # "pdf_vector", "raster_line_geometry", "raster_fallback", "vlm"
    status: str = "ok"  # "ok", "needs_review", "rejected"
    downstream_action: str = "auto_use"  # "auto_use", "use_with_warning", "review_required"
    grid_confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        """Render table as GitHub-flavored Markdown table."""
        if not self.matrix:
            return ""
        lines: list[str] = []
        header = self.matrix[0]
        cleaned_header = [re.sub(r"\s+", " ", str(c or "")).strip() for c in header]
        lines.append("| " + " | ".join(cleaned_header) + " |")
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")
        for row in self.matrix[1:]:
            cleaned_row = [re.sub(r"\s+", " ", str(c or "")).strip() for c in row]
            # Ensure row length matches header
            if len(cleaned_row) < len(header):
                cleaned_row.extend([""] * (len(header) - len(cleaned_row)))
            lines.append("| " + " | ".join(cleaned_row[:len(header)]) + " |")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Producer 1: Deterministic PDF Vector Grid
# ---------------------------------------------------------------------------


def detect_pdf_vector_table_grid(
    pdf_data: bytes,
    page_index: int,
    *,
    min_confidence: float = 0.90,
) -> list[StructuredTable]:
    """Extract table structures deterministically from PDF vector ruling lines."""
    results: list[StructuredTable] = []
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(pdf_data)) as pdf:
            if page_index < 0 or page_index >= len(pdf.pages):
                return []
            page = pdf.pages[page_index]

            # Detect explicit vector tables
            raw_tables = page.extract_tables() or []
            if not raw_tables:
                return []

            # Check for explicit vector line elements
            has_lines = bool(page.lines or page.rects or page.curves)
            confidence = 0.95 if has_lines else 0.85

            for idx, raw_t in enumerate(raw_tables):
                if not raw_t or len(raw_t) < 1:
                    continue
                num_rows = len(raw_t)
                num_cols = max(len(r) for r in raw_t)
                matrix: list[list[str]] = []
                cells: list[TableCell] = []

                for r_i, row in enumerate(raw_t):
                    row_vals: list[str] = []
                    for c_i, cell_val in enumerate(row):
                        text_val = str(cell_val or "").strip()
                        row_vals.append(text_val)
                        cells.append(
                            TableCell(
                                row_idx=r_i,
                                col_idx=c_i,
                                text=text_val,
                                bbox=[0.0, 0.0, 0.0, 0.0],
                                confidence=confidence,
                                bbox_source="pdf_vector",
                            )
                        )
                    matrix.append(row_vals)

                status = "ok" if confidence >= min_confidence else "needs_review"
                action = "auto_use" if status == "ok" else "use_with_warning"
                results.append(
                    StructuredTable(
                        table_id=f"table_p{page_index + 1}_{idx + 1}",
                        page_number=page_index + 1,
                        rows=num_rows,
                        cols=num_cols,
                        cells=cells,
                        matrix=matrix,
                        source="pdf_vector",
                        status=status,
                        downstream_action=action,
                        grid_confidence=confidence,
                    )
                )
    except Exception as err:
        logger.debug("PDF vector table grid extraction failed on page %d: %s", page_index, err)

    return results


# ---------------------------------------------------------------------------
# Producer 2: Deterministic Raster Line Geometry
# ---------------------------------------------------------------------------


def _cluster_1d_indices(indices: np.ndarray, max_gap: int = 4) -> list[int]:
    """Cluster adjacent 1D line indices into single canonical line centers."""
    if indices.size == 0:
        return []
    clusters: list[int] = []
    start = int(indices[0])
    prev = start
    for idx in indices[1:]:
        val = int(idx)
        if val - prev <= max_gap:
            prev = val
        else:
            clusters.append((start + prev) // 2)
            start = val
            prev = val
    clusters.append((start + prev) // 2)
    return clusters


def detect_raster_line_table_grid(
    image: np.ndarray,
    page_number: int = 1,
    *,
    ocr_items: list[dict[str, Any]] | None = None,
) -> list[StructuredTable]:
    """Detect table cell grids from raster image horizontal and vertical line borders."""
    if image is None or not isinstance(image, np.ndarray) or image.ndim < 2:
        return []
    h, w = image.shape[:2]
    if h < 24 or w < 24:
        return []

    gray = image.mean(axis=2) if image.ndim >= 3 else image.astype(np.float32)
    # Dark pixels relative to page background
    threshold = min(180.0, float(np.percentile(gray, 50)) * 0.78)
    dark = gray < threshold

    # Horizontal lines: rows where >= 35% of width consists of dark pixels
    row_density = dark.mean(axis=1)
    h_indices = np.where(row_density >= 0.35)[0]
    h_lines = _cluster_1d_indices(h_indices, max_gap=max(2, h // 150))

    # Vertical lines: cols where >= 25% of height consists of dark pixels
    col_density = dark.mean(axis=0)
    v_indices = np.where(col_density >= 0.25)[0]
    v_lines = _cluster_1d_indices(v_indices, max_gap=max(2, w // 150))

    # Filter out lines that are too close to form real table cells (< 8px apart)
    filtered_h: list[int] = []
    for y in h_lines:
        if not filtered_h or (y - filtered_h[-1]) >= 8:
            filtered_h.append(y)
    filtered_v: list[int] = []
    for x in v_lines:
        if not filtered_v or (x - filtered_v[-1]) >= 8:
            filtered_v.append(x)

    if len(filtered_h) < 3 or len(filtered_v) < 3:
        return []

    num_rows = len(filtered_h) - 1
    num_cols = len(filtered_v) - 1
    matrix: list[list[str]] = [["" for _ in range(num_cols)] for _ in range(num_rows)]

    # Populate cell text from OCR bounding boxes when provided
    for item in ocr_items or []:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        raw_box = item.get("box") or item.get("polygon") or []
        cx, cy = 0.0, 0.0
        try:
            if len(raw_box) >= 4 and isinstance(raw_box[0], list | tuple):
                xs = [float(p[0]) for p in raw_box]
                ys = [float(p[1]) for p in raw_box]
                cx = (min(xs) + max(xs)) / 2.0
                cy = (min(ys) + max(ys)) / 2.0
            elif len(raw_box) == 4:
                cx = (float(raw_box[0]) + float(raw_box[2])) / 2.0
                cy = (float(raw_box[1]) + float(raw_box[3])) / 2.0
        except (TypeError, ValueError, IndexError):
            logger.debug("Skipping malformed OCR box in raster grid mapping")
            continue

        r_match = next(
            (r for r in range(num_rows) if filtered_h[r] <= cy <= filtered_h[r + 1]),
            None,
        )
        c_match = next(
            (c for c in range(num_cols) if filtered_v[c] <= cx <= filtered_v[c + 1]),
            None,
        )
        if r_match is not None and c_match is not None:
            existing = matrix[r_match][c_match]
            matrix[r_match][c_match] = f"{existing} {text}".strip() if existing else text

    cells: list[TableCell] = []
    for r_i in range(num_rows):
        for c_i in range(num_cols):
            cells.append(
                TableCell(
                    row_idx=r_i,
                    col_idx=c_i,
                    text=matrix[r_i][c_i],
                    bbox=[
                        float(filtered_v[c_i]),
                        float(filtered_h[r_i]),
                        float(filtered_v[c_i + 1]),
                        float(filtered_h[r_i + 1]),
                    ],
                    confidence=0.88,
                    bbox_source="raster_line_geometry",
                )
            )

    return [
        StructuredTable(
            table_id=f"raster_table_p{page_number}_1",
            page_number=page_number,
            rows=num_rows,
            cols=num_cols,
            cells=cells,
            matrix=matrix,
            source="raster_line_geometry",
            status="ok",
            downstream_action="auto_use",
            grid_confidence=0.88,
        )
    ]


# ---------------------------------------------------------------------------
# Producer 3: Fallback / VLM Cell Structuring (Borderless Tables)
# ---------------------------------------------------------------------------


def detect_borderless_or_vlm_table(
    raw_text: str,
    page_number: int = 1,
    *,
    table_id: str = "table_fallback",
    enable_vlm_fallback: bool = True,
) -> StructuredTable | None:
    """Structure borderless or plain-text tables when ruling lines are missing.

    Strict contract: Never silently promotes to auto_use. Stamped with
    ``downstream_action="use_with_warning"`` and ``warnings=["missing_line_candidate", "vlm_fallback"]``.
    """
    lines = [line.strip() for line in raw_text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return None

    # Detect tab or multi-space columnar structure
    rows: list[list[str]] = []
    for line in lines:
        if "\t" in line:
            cols = [c.strip() for c in line.split("\t")]
        elif "  " in line:
            cols = [c.strip() for c in re.split(r"\s{2,}", line)]
        else:
            continue
        if len(cols) >= 2:
            rows.append(cols)

    if len(rows) < 2:
        return None

    num_rows = len(rows)
    num_cols = max(len(r) for r in rows)
    cells: list[TableCell] = []

    for r_i, row in enumerate(rows):
        for c_i, val in enumerate(row):
            cells.append(
                TableCell(
                    row_idx=r_i,
                    col_idx=c_i,
                    text=val,
                    bbox=[0.0, 0.0, 0.0, 0.0],
                    confidence=0.75,
                    bbox_source="vlm" if enable_vlm_fallback else "raster_fallback",
                )
            )

    source_label = "vlm" if enable_vlm_fallback else "raster_fallback"
    return StructuredTable(
        table_id=table_id,
        page_number=page_number,
        rows=num_rows,
        cols=num_cols,
        cells=cells,
        matrix=rows,
        source=source_label,
        status="ok",
        downstream_action="use_with_warning",
        grid_confidence=0.75,
        warnings=["missing_line_candidate", "vlm_fallback"],
    )


# ---------------------------------------------------------------------------
# GT-Free Selector / Router
# ---------------------------------------------------------------------------


def select_table_structuring_result(
    pdf_vector_candidates: list[StructuredTable],
    raster_line_candidates: list[StructuredTable],
    fallback_candidates: list[StructuredTable],
    *,
    pdf_min_confidence: float = 0.90,
    enable_vlm_fallback: bool = True,
) -> list[StructuredTable]:
    """GT-free selector consolidating candidate table structures.

    Hierarchy:
    1. ``pdf_vector`` wins outright if ``status=='ok'`` and ``grid_confidence >= pdf_min_confidence``.
    2. ``raster_line_geometry`` adopted according to its downstream action.
    3. Missing table borders / no line candidate -> adopts fallback with ``use_with_warning`` and explicit warning stamps.
    """
    selected: list[StructuredTable] = []

    # 1. Adopt high-confidence PDF vector tables
    for vec_t in pdf_vector_candidates:
        if vec_t.status == "ok" and vec_t.grid_confidence >= pdf_min_confidence:
            selected.append(vec_t)

    if selected:
        return selected

    # 2. Check raster line geometry candidates
    for line_t in raster_line_candidates:
        if line_t.downstream_action in {"auto_use", "use_with_warning"}:
            selected.append(line_t)

    if selected:
        return selected

    # 3. Fallback when table borders are missing
    for fb_t in fallback_candidates:
        # Re-stamp to ensure never silent auto_use
        fb_t.downstream_action = "use_with_warning"
        if "missing_line_candidate" not in fb_t.warnings:
            fb_t.warnings.append("missing_line_candidate")
        if "vlm_fallback" not in fb_t.warnings and fb_t.source == "vlm":
            fb_t.warnings.append("vlm_fallback")
        selected.append(fb_t)

    return selected


def structure_document_tables(
    pdf_bytes: bytes | None,
    raw_text: str = "",
    page_number: int = 1,
    *,
    raster_image: np.ndarray | None = None,
    ocr_items: list[dict[str, Any]] | None = None,
    enable_vlm_fallback: bool = True,
) -> list[StructuredTable]:
    """Execute end-to-end table structuring pipeline on a document page."""
    pdf_vector_candidates: list[StructuredTable] = []
    if pdf_bytes:
        pdf_vector_candidates = detect_pdf_vector_table_grid(pdf_bytes, page_number - 1)

    raster_line_candidates: list[StructuredTable] = []
    if not pdf_vector_candidates and raster_image is not None:
        raster_line_candidates = detect_raster_line_table_grid(
            raster_image, page_number=page_number, ocr_items=ocr_items
        )

    fallback_candidates: list[StructuredTable] = []

    # If vector and raster-line candidates are missing, generate fallback candidate
    if not pdf_vector_candidates and not raster_line_candidates and raw_text:
        fb = detect_borderless_or_vlm_table(
            raw_text,
            page_number=page_number,
            enable_vlm_fallback=enable_vlm_fallback,
        )
        if fb:
            fallback_candidates.append(fb)

    return select_table_structuring_result(
        pdf_vector_candidates=pdf_vector_candidates,
        raster_line_candidates=raster_line_candidates,
        fallback_candidates=fallback_candidates,
        enable_vlm_fallback=enable_vlm_fallback,
    )
