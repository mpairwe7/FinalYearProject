"""Docling Document Intelligence Engine & Hybrid Structural Chunker (November 2026).

Implements the November 2026 government-grade RAG Document Intelligence pipeline
aligned with IBM Docling (Livathinos et al. 2025; Heron Layout Model arXiv:2509.11720;
Granite-Docling; TableFormer) and Citation-Enforced Fiscal RAG (arXiv:2603.14170):

1. **Unified Document Intermediate Representation (:class:`DoclingDocumentIR`)**:
   - Segregates ``ContentLayer.BODY`` from ``ContentLayer.FURNITURE`` (running headers,
     footers, page numbers, gazette chrome, watermarks) so page furniture never
     contaminates retrieval chunks or splits multi-page sentences/tables (G443).
   - Preserves per-item provenance (:class:`DoclingProvenance`: ``page_no``, ``bbox``,
     ``charspan``, ``source``) from extraction through chunking (G444, G445).
2. **2D Multi-Column Reading-Order Reconstruction (:func:`sort_spatial_reading_order`)**:
   - Uses recursive XY-Cut / vertical gutter detection to order full-width spanning
     headers first, then left column top-to-bottom, then right column top-to-bottom,
     preventing horizontal cross-gutter line interleaving on two-column Uganda Gazette
     notices, Acts, and URA brochures (G442).
3. **TableFormer & TripletTableSerializer Intelligence**:
   - Serializes structured tables both as self-contained Markdown tables (with
     row-boundary splitting and header-row propagation on every split sub-table, G446)
     and as semantic row-column triplets (``[Table | RowKey] ColHeader: Value``, G448).
   - Stitches multi-page continuation tables across page boundaries when column
     counts or repeated header rows match (G446).
4. **Dual-Backend Execution (:func:`convert_document_bytes`)**:
   - Uses native ``docling`` (``DocumentConverter`` + ``TableFormerMode.ACCURATE``)
     when installed and enabled via ``DOCLING_BACKEND=auto|docling``, and degrades
     seamlessly to the zero-crash ``native_structural`` pipeline on CPU-slim /
     air-gapped deployments (G441).
"""

from __future__ import annotations

import io
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .guardrails import scan_retrieved_text
from .vision.ocr import escape_untrusted_document_markers, strip_hidden_control_and_bidi

logger = logging.getLogger(__name__)

DOCLING_BACKEND = os.getenv("DOCLING_BACKEND", "auto").strip().lower()


class ContentLayer(StrEnum):
    """Docling-compatible content layer classification (BODY vs FURNITURE)."""

    BODY = "body"
    FURNITURE = "furniture"


@dataclass
class DoclingProvenance:
    """Fine-grained spatial and page provenance for a document item (Docling DocItem.prov)."""

    page_no: int
    bbox: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])  # [x0, y0, x1, y1]
    charspan: tuple[int, int] = (0, 0)
    source: str = "native_structural"

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "bbox": [round(float(c), 2) for c in (self.bbox[:4] if len(self.bbox) >= 4 else [0.0, 0.0, 0.0, 0.0])],
            "charspan": list(self.charspan),
            "source": self.source,
        }


@dataclass
class DoclingItem:
    """A single structural element in a DoclingDocumentIR."""

    item_id: str
    label: str  # "section_header" | "text" | "table" | "list_item" | "page_header" | "page_footer"
    text: str
    content_layer: ContentLayer = ContentLayer.BODY
    heading_trail: list[str] = field(default_factory=list)
    prov: list[DoclingProvenance] = field(default_factory=list)
    table_matrix: list[list[str]] = field(default_factory=list)
    table_triplets: list[str] = field(default_factory=list)
    level: int = 1

    @property
    def page_numbers(self) -> list[int]:
        return sorted({p.page_no for p in self.prov if p.page_no > 0})

    @property
    def page_range_str(self) -> str:
        pages = self.page_numbers
        if not pages:
            return ""
        if len(pages) == 1 or pages[0] == pages[-1]:
            return str(pages[0])
        return f"{pages[0]}-{pages[-1]}"


@dataclass
class DoclingDocumentIR:
    """Unified Docling-compatible intermediate document representation."""

    filename: str
    backend_used: str = "native_structural"
    page_count: int = 0
    items: list[DoclingItem] = field(default_factory=list)
    furniture_removed_count: int = 0
    tables_stitched_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def body_items(self) -> list[DoclingItem]:
        """Return only ContentLayer.BODY items in reading order."""
        return [item for item in self.items if item.content_layer == ContentLayer.BODY and item.text.strip()]

    def export_to_markdown(
        self,
        *,
        include_furniture: bool = False,
        include_page_markers: bool = False,
    ) -> str:
        """Serialize the document IR to clean Markdown."""
        lines: list[str] = []
        last_page: int | None = None
        source_items = self.items if include_furniture else self.body_items()

        for item in source_items:
            pages = item.page_numbers
            cur_page = pages[0] if pages else None
            if include_page_markers and cur_page is not None and cur_page != last_page:
                lines.append(f"<!-- page:{cur_page} -->")
                last_page = cur_page

            if item.label == "section_header":
                hashes = "#" * max(1, min(6, item.level))
                clean_heading = item.text.lstrip("#").strip()
                lines.append(f"{hashes} {clean_heading}")
            else:
                lines.append(item.text.strip())

        return "\n\n".join(part for part in lines if part).strip()

    def export_tables_to_triplets(self) -> list[str]:
        """Return all table row-column triplets across the document."""
        triplets: list[str] = []
        for item in self.body_items():
            if item.label == "table" and item.table_triplets:
                triplets.extend(item.table_triplets)
        return triplets

    def hybrid_chunk(
        self,
        *,
        target_chars: int = 2000,
        hard_max_chars: int = 4000,
        min_chars: int = 80,
    ) -> list[dict[str, Any]]:
        """Docling HybridChunker-compatible structure & provenance preserving chunker."""
        md = self.export_to_markdown(include_furniture=False, include_page_markers=True)
        return hybrid_chunk_markdown(
            md,
            doc_id=os.path.splitext(self.filename)[0] or "document",
            source=self.filename,
            target_chars=target_chars,
            hard_max_chars=hard_max_chars,
            min_chars=min_chars,
        )


# ---------------------------------------------------------------------------
# ContentLayer.FURNITURE Detection & Stripping (G443)
# ---------------------------------------------------------------------------

_FURNITURE_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*(?:DOMESTIC\s+TAX\s+LAWS\s+OF\s+UGANDA\s*)?\d+\s*\|\s*P\s*a\s*g\s*e\s*$", re.I),
    re.compile(r"^\s*P\s*a\s*g\s*e\s*\|\s*\d+\s*$", re.I),
    re.compile(r"^\s*Page\s+\d+\s+(?:of|/)\s+\d+\s*$", re.I),
    re.compile(r"^\s*-\s*\d{1,4}\s*-\s*$"),
    re.compile(r"^\s*\[\s*Rev\.\s*\d{4}\s*\]\s*$", re.I),
    re.compile(
        r"^\s*(?:THE\s+UGANDA\s+GAZETTE|UGANDA\s+REVENUE\s+AUTHORITY\s+-\s+DEVELOPING\s+UGANDA\s+TOGETHER)\s*$",
        re.I,
    ),
)
_STANDALONE_PAGE_NUM_RE = re.compile(r"^\s*\d{1,4}\s*$")
_DIGIT_NORM_RE = re.compile(r"\d+")


def is_furniture_line(line: str, *, is_margin_line: bool = False) -> bool:
    """Return True if *line* is a running header, footer, watermark, or page number (ContentLayer.FURNITURE)."""
    stripped = (line or "").strip()
    if not stripped:
        return False
    for pat in _FURNITURE_LINE_PATTERNS:
        if pat.match(stripped):
            return True
    return bool(is_margin_line and _STANDALONE_PAGE_NUM_RE.match(stripped))


def _normalize_margin_signature(line: str) -> str:
    """Normalize digits and whitespace in a top/bottom margin line to detect running headers/footers."""
    cleaned = _DIGIT_NORM_RE.sub("#", (line or "").strip().lower())
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def strip_document_furniture(pages: list[str]) -> tuple[list[str], int]:
    """Segregate and remove ContentLayer.FURNITURE (running headers/footers) across pages.

    Detects both explicit URA/Gazette/ULII page-number patterns and recurring
    top/bottom margin lines that repeat across >= 3 pages (without stripping
    Markdown table borders or statutory section headings).
    """
    if not pages:
        return [], 0

    # Identify top/bottom margin signatures that repeat across >= 3 multi-line pages
    top_sigs: Counter[str] = Counter()
    bottom_sigs: Counter[str] = Counter()
    if len(pages) >= 3:
        for page_text in pages:
            non_empty = [ln.strip() for ln in (page_text or "").splitlines() if ln.strip()]
            if len(non_empty) < 2:
                continue
            top_cand = non_empty[0]
            if not top_cand.startswith(("#", "|")) and len(top_cand) <= 100:
                top_sigs[_normalize_margin_signature(top_cand)] += 1
            if len(non_empty) >= 3:
                bot_cand = non_empty[-1]
                if not bot_cand.startswith(("#", "|")) and len(bot_cand) <= 100:
                    bottom_sigs[_normalize_margin_signature(bot_cand)] += 1

    recurring_top = {sig for sig, cnt in top_sigs.items() if cnt >= 3 and len(sig) >= 4}
    recurring_bot = {sig for sig, cnt in bottom_sigs.items() if cnt >= 3 and len(sig) >= 4}

    cleaned_pages: list[str] = []
    removed_count = 0

    for page_text in pages:
        raw_lines = (page_text or "").splitlines()
        non_empty_indices = [idx for idx, ln in enumerate(raw_lines) if ln.strip()]
        margin_indices: set[int] = set()
        if non_empty_indices:
            margin_indices.update(non_empty_indices[:2])
            margin_indices.update(non_empty_indices[-2:])

        first_idx = non_empty_indices[0] if len(non_empty_indices) >= 2 else -1
        last_idx = non_empty_indices[-1] if len(non_empty_indices) >= 3 else -1

        kept_lines: list[str] = []
        for idx, ln in enumerate(raw_lines):
            stripped = ln.strip()
            if not stripped:
                kept_lines.append(ln)
                continue
            is_margin = idx in margin_indices
            if is_furniture_line(stripped, is_margin_line=is_margin):
                removed_count += 1
                continue
            if idx == first_idx and _normalize_margin_signature(stripped) in recurring_top:
                removed_count += 1
                continue
            if idx == last_idx and _normalize_margin_signature(stripped) in recurring_bot:
                removed_count += 1
                continue
            kept_lines.append(ln)

        cleaned_pages.append("\n".join(kept_lines).strip())

    return cleaned_pages, removed_count


# ---------------------------------------------------------------------------
# 2D Spatial Multi-Column Reading Order Reconstruction (XY-Cut, G442)
# ---------------------------------------------------------------------------


def _token_bbox(token: Any) -> tuple[float, float, float, float]:
    """Extract (x0, y0, x1, y1) from a token dataclass or dict."""
    raw = getattr(token, "bbox", None) if not isinstance(token, dict) else (token.get("bbox") or token.get("box"))
    if isinstance(raw, list) and len(raw) >= 4:
        if isinstance(raw[0], (list, tuple)):
            xs = [float(p[0]) for p in raw if len(p) >= 2]
            ys = [float(p[1]) for p in raw if len(p) >= 2]
            if xs and ys:
                return min(xs), min(ys), max(xs), max(ys)
        elif isinstance(raw[0], (int, float)):
            return float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3])
    return 0.0, 0.0, 0.0, 0.0


def sort_spatial_reading_order(
    tokens: list[Any],
    *,
    page_width: float | None = None,
    line_band_tol: float = 6.0,
) -> list[Any]:
    """Reconstruct 2D layout reading order across single- and multi-column pages (G442).

    Implements a deterministic XY-Cut / vertical column-gutter partition:
    1. Tokens without valid bounding geometry preserve their relative order.
    2. When a vertical gutter cleanly separates left-column and right-column tokens
       (>= 2 tokens in each column and < 20% cross-gutter spanning boxes), tokens are
       ordered:
       - Top full-width spanning titles/headers
       - Left column (top-to-bottom, left-to-right within each line band)
       - Right column (top-to-bottom, left-to-right within each line band)
       - Bottom full-width spanning footers/notes
    3. Within any single column or full-width region, tokens whose vertical centers
       fall within *line_band_tol* pixels are sorted left-to-right by ``x0``.
    """
    if len(tokens) <= 1:
        return list(tokens)

    positioned: list[tuple[int, Any, tuple[float, float, float, float]]] = []
    unpositioned: list[tuple[int, Any]] = []
    for idx, tok in enumerate(tokens):
        b = _token_bbox(tok)
        if b[2] > b[0] and b[3] > b[1]:
            positioned.append((idx, tok, b))
        else:
            unpositioned.append((idx, tok))

    if len(positioned) <= 1:
        return list(tokens)

    min_x = min(b[0] for _, _, b in positioned)
    max_x = max(b[2] for _, _, b in positioned)
    eff_width = max(page_width or 0.0, max_x - min_x)

    def _sort_region(items: list[tuple[int, Any, tuple[float, float, float, float]]]) -> list[Any]:
        if not items:
            return []
        # Sort by top y0 first
        by_y = sorted(items, key=lambda it: ((it[2][1] + it[2][3]) / 2.0, it[2][0], it[0]))
        lines: list[list[tuple[int, Any, tuple[float, float, float, float]]]] = []
        cur_line: list[tuple[int, Any, tuple[float, float, float, float]]] = []
        cur_cy = 0.0

        for it in by_y:
            cy = (it[2][1] + it[2][3]) / 2.0
            h = max(4.0, it[2][3] - it[2][1])
            tol = max(line_band_tol, h * 0.45)
            if not cur_line or abs(cy - cur_cy) <= tol:
                cur_line.append(it)
                cur_cy = sum((x[2][1] + x[2][3]) / 2.0 for x in cur_line) / len(cur_line)
            else:
                lines.append(sorted(cur_line, key=lambda x: (x[2][0], x[0])))
                cur_line = [it]
                cur_cy = cy
        if cur_line:
            lines.append(sorted(cur_line, key=lambda x: (x[2][0], x[0])))
        return [tok for line in lines for _, tok, _ in line]

    # Detect two-column layout via midpoint gutter check
    if eff_width >= 160.0 and len(positioned) >= 4:
        mid_x = min_x + eff_width * 0.50
        gutter_left = min_x + eff_width * 0.44
        gutter_right = min_x + eff_width * 0.56

        left_col: list[tuple[int, Any, tuple[float, float, float, float]]] = []
        right_col: list[tuple[int, Any, tuple[float, float, float, float]]] = []
        spanning: list[tuple[int, Any, tuple[float, float, float, float]]] = []

        for item in positioned:
            _, _, (x0, y0, x1, y1) = item
            if x1 <= gutter_right and (x0 + x1) / 2.0 < mid_x:
                left_col.append(item)
            elif x0 >= gutter_left and (x0 + x1) / 2.0 >= mid_x:
                right_col.append(item)
            else:
                spanning.append(item)

        # Confirm a true empty vertical column gutter separates left_col and right_col
        # (prevents word-level tokens in a single-column sentence from being split at mid-line)
        max_left_x1 = max((b[2] for _, _, b in left_col), default=0.0)
        min_right_x0 = min((b[0] for _, _, b in right_col), default=0.0)
        gutter_gap = min_right_x0 - max_left_x1
        min_required_gutter = max(24.0, eff_width * 0.08)

        if (
            gutter_gap >= min_required_gutter
            and len(left_col) >= 2
            and len(right_col) >= 2
            and len(spanning) <= max(2, len(positioned) // 3)
        ):
            left_min_y = min(b[1] for _, _, b in left_col)
            left_max_y = max(b[3] for _, _, b in left_col)
            right_min_y = min(b[1] for _, _, b in right_col)
            right_max_y = max(b[3] for _, _, b in right_col)
            y_overlap = min(left_max_y, right_max_y) - max(left_min_y, right_min_y)

            if y_overlap > 0:
                col_top_y = min(left_min_y, right_min_y)
                top_spanning = [it for it in spanning if (it[2][1] + it[2][3]) / 2.0 <= col_top_y]
                bottom_spanning = [it for it in spanning if (it[2][1] + it[2][3]) / 2.0 > col_top_y]

                ordered = (
                    _sort_region(top_spanning)
                    + _sort_region(left_col)
                    + _sort_region(right_col)
                    + _sort_region(bottom_spanning)
                )
                if unpositioned:
                    ordered.extend(tok for _, tok in unpositioned)
                return ordered

    ordered_single = _sort_region(positioned)
    if unpositioned:
        ordered_single.extend(tok for _, tok in unpositioned)
    return ordered_single


# ---------------------------------------------------------------------------
# Table Triplet Serialization & Row-Safe Table Chunking (G446, G448)
# ---------------------------------------------------------------------------


def sanitize_table_cell_text(raw: Any) -> str:
    """Scrub bidi/control chars, prompt-injection strings, and unescaped pipes inside a table cell."""
    if raw is None:
        return ""
    cleaned = strip_hidden_control_and_bidi(str(raw))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return ""
    cleaned, _ = scan_retrieved_text(cleaned)
    cleaned = escape_untrusted_document_markers(cleaned)
    return cleaned.replace("|", "\\|")


def propagate_merged_table_headers(matrix: list[list[Any]]) -> tuple[list[list[str]], list[tuple[int, int, int, int]]]:
    """Normalize a raw table matrix, forward-filling horizontal/vertical merged cells and fusing compound headers (G448, G454).

    Returns ``(normalized_matrix, spans)`` where each span is ``(row_idx, col_idx, row_span, col_span)``.
    """
    if not matrix:
        return [], []
    num_rows = len(matrix)
    num_cols = max((len(r) for r in matrix), default=0)
    if num_cols == 0:
        return [], []

    norm: list[list[str]] = [["" for _ in range(num_cols)] for _ in range(num_rows)]
    spans: list[tuple[int, int, int, int]] = []
    row0_spanned_cols: dict[int, str] = {}

    for r_idx, row in enumerate(matrix):
        c_idx = 0
        while c_idx < num_cols:
            raw_val = row[c_idx] if c_idx < len(row) else None
            val = sanitize_table_cell_text(raw_val)
            if val or not norm[r_idx][c_idx]:
                norm[r_idx][c_idx] = val

            # Detect horizontal col_span when subsequent cells in the row are None
            col_span = 1
            if raw_val is not None and val:
                look = c_idx + 1
                while look < len(row) and row[look] is None:
                    col_span += 1
                    if r_idx == 0:
                        norm[r_idx][look] = f"{val} ({look - c_idx + 1})"
                        row0_spanned_cols[c_idx] = val
                        row0_spanned_cols[look] = val
                    look += 1
            if col_span > 1:
                spans.append((r_idx, c_idx, 1, col_span))
            c_idx += col_span

    # G454: Detect 2-row compound header when row 0 has horizontal spans and row 1 is a sub-header row
    # (row 1 col 0 is None/empty while spanned columns in row 1 carry sub-labels like "Resident" / "Non-Resident")
    raw_rows = list(matrix)
    if (
        len(norm) >= 3
        and row0_spanned_cols
        and (matrix[1][0] is None or not sanitize_table_cell_text(matrix[1][0]))
        and any(c_i in row0_spanned_cols and norm[1][c_i] for c_i in range(1, num_cols))
    ):
        fused_header: list[str] = []
        for c_i in range(num_cols):
            sub_lbl = norm[1][c_i]
            parent_lbl = row0_spanned_cols.get(c_i) or norm[0][c_i]
            if c_i in row0_spanned_cols and sub_lbl:
                fused_header.append(f"{parent_lbl} — {sub_lbl}")
            else:
                fused_header.append(norm[0][c_i] or sub_lbl)
        norm = [fused_header] + norm[2:]
        raw_rows = [matrix[0]] + list(matrix[2:])

    # G454: Forward-fill vertical merged category cells (row_span) in column 0 across data rows
    r_i = 1
    while r_i < len(norm):
        if norm[r_i][0]:
            r_span = 1
            look_r = r_i + 1
            while (
                look_r < len(norm)
                and not norm[look_r][0]
                and any(norm[look_r][1:])
                and (look_r >= len(raw_rows) or not raw_rows[look_r] or raw_rows[look_r][0] is None)
            ):
                norm[look_r][0] = norm[r_i][0]
                r_span += 1
                look_r += 1
            if r_span > 1:
                spans.append((r_i, 0, r_span, 1))
            r_i = look_r
        else:
            r_i += 1

    return norm, spans


def serialize_table_to_triplets(
    table_name: str,
    matrix: list[list[Any]],
    *,
    max_triplets: int = 80,
) -> list[str]:
    """Serialize a 2D table matrix into Docling TripletTableSerializer semantic triplets (G448).

    Format: ``[Table: <name> | <row_key>] <col_header>: <cell_value>``
    Preserves row-column-value bindings for both BM25 lexical search and dense embedding recall.
    """
    norm, _ = propagate_merged_table_headers(matrix)
    if len(norm) < 2:
        return []

    safe_table = sanitize_table_cell_text(table_name) or "Table"
    headers = [h or f"Col_{idx + 1}" for idx, h in enumerate(norm[0])]
    triplets: list[str] = []

    for r_idx, row in enumerate(norm[1:], start=1):
        if not any(row):
            continue
        row_key = row[0] or f"Row {r_idx}"
        pairs: list[str] = []
        for c_idx in range(1, min(len(headers), len(row))):
            cell_val = row[c_idx]
            if cell_val:
                pairs.append(f"{headers[c_idx]} = {cell_val}")
        if pairs:
            triplets.append(f"[{safe_table} | {row_key}] " + "; ".join(pairs))
        elif row[0]:
            triplets.append(f"[{safe_table}] {headers[0]} = {row[0]}")
        if len(triplets) >= max_triplets:
            break

    return triplets


_MD_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")
_MD_TABLE_SEP_RE = re.compile(r"^\s*\|\s*[:\-]+(?:\s*\|\s*[:\-]+)*\s*\|\s*$")
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def _count_markdown_table_cols(line: str) -> int:
    """Count table columns by counting unescaped pipe delimiters (ignoring ``\\|``, G451)."""
    return max(0, len(_UNESCAPED_PIPE_RE.findall(line or "")) - 1)


def split_markdown_table_preserving_headers(
    table_md: str,
    *,
    target_chars: int = 2000,
    hard_max_chars: int = 4000,
) -> list[str]:
    """Split an oversized Markdown table strictly on row boundaries, repeating the header in every chunk (G446)."""
    lines = [ln.strip() for ln in (table_md or "").strip().splitlines() if ln.strip()]
    if not lines:
        return []
    if len(table_md) <= hard_max_chars:
        return ["\n".join(lines)]

    # Identify header + separator prefix
    if len(lines) >= 2 and _MD_TABLE_SEP_RE.match(lines[1]):
        header_lines = lines[:2]
        data_lines = lines[2:]
    else:
        num_cols = max(1, _count_markdown_table_cols(lines[0]))
        sep = "| " + " | ".join(["---"] * num_cols) + " |"
        header_lines = [lines[0], sep]
        data_lines = lines[1:]

    if not data_lines:
        return ["\n".join(header_lines)]

    header_block = "\n".join(header_lines)
    header_len = len(header_block) + 1
    eff_budget = max(header_len + 120, min(target_chars, hard_max_chars))
    hard_budget = max(eff_budget, hard_max_chars)

    chunks: list[str] = []
    cur_rows: list[str] = []
    cur_len = header_len

    for row in data_lines:
        row_len = len(row) + 1
        if cur_rows and (cur_len + row_len > eff_budget):
            chunks.append(header_block + "\n" + "\n".join(cur_rows))
            cur_rows = []
            cur_len = header_len
        if header_len + row_len > hard_budget:
            # Extremely wide single row: keep the row intact up to hard_max_chars with header
            clipped = row[: max(80, hard_budget - header_len)]
            chunks.append(header_block + "\n" + clipped)
            continue
        cur_rows.append(row)
        cur_len += row_len

    if cur_rows:
        chunks.append(header_block + "\n" + "\n".join(cur_rows))

    return chunks


def stitch_cross_page_markdown_tables(markdown: str) -> tuple[str, int]:
    """Stitch multi-page Markdown tables split across page boundaries (`---` or `<!-- page:N -->`) (G446).

    When a table at the bottom of page $p$ is immediately followed on page $p+1$
    by a table with the same column count:
    - If page $p+1$ repeats the identical header row, its duplicate header/separator
      is removed and the rows merge into a single contiguous table (preserving any
      `<!-- page:N -->` marker right before the table or tracking page range).
    - If page $p+1$ continues directly with pipe-delimited data rows of the same
      column count without a separator row, they merge directly into page $p$'s table.
    """
    if not markdown or "|" not in markdown:
        return markdown, 0

    lines = markdown.splitlines()
    out: list[str] = []
    stitched_count = 0
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if is_furniture_line(line.strip()):
            i += 1
            continue
        out.append(line)
        if _MD_TABLE_LINE_RE.match(line):
            # Look ahead to see if the next non-empty lines are a page break followed by a continuing table
            j = i + 1
            page_markers: list[str] = []
            saw_break = False
            while j < n:
                cand = lines[j].strip()
                if not cand or is_furniture_line(cand):
                    j += 1
                    continue
                if cand == "---":
                    saw_break = True
                    j += 1
                    continue
                if cand.startswith("<!-- page:") and cand.endswith("-->"):
                    saw_break = True
                    page_markers.append(cand)
                    j += 1
                    continue
                break

            if saw_break and j < n and _MD_TABLE_LINE_RE.match(lines[j]):
                prev_cols = _count_markdown_table_cols(line)
                next_cols = _count_markdown_table_cols(lines[j])
                if prev_cols >= 2 and prev_cols == next_cols:
                    # Find the current table's header row in `out`
                    k = len(out) - 1
                    while k >= 0 and (
                        _MD_TABLE_LINE_RE.match(out[k])
                        or (out[k].strip().startswith("<!-- page:") and out[k].strip().endswith("-->"))
                    ):
                        k -= 1
                    table_start_idx = k + 1
                    while (
                        table_start_idx < len(out)
                        and out[table_start_idx].strip().startswith("<!-- page:")
                    ):
                        table_start_idx += 1
                    first_header_norm = re.sub(r"\s+", " ", out[table_start_idx].strip().lower())
                    next_first_norm = re.sub(r"\s+", " ", lines[j].strip().lower())

                    # Skip duplicate header + separator on the continuation page
                    if first_header_norm == next_first_norm:
                        j += 1
                        if j < n and _MD_TABLE_SEP_RE.match(lines[j]):
                            j += 1
                    elif j + 1 < n and _MD_TABLE_SEP_RE.match(lines[j + 1]):
                        # Distinct table with a different header: do not merge; keep page markers
                        for pm in page_markers:
                            out.append(pm)
                        i += 1
                        continue

                    # Preserve page markers across the stitched continuation boundary (G452)
                    for pm in page_markers:
                        out.append(pm)
                    stitched_count += 1
                    i = j
                    continue
        i += 1

    return "\n".join(out), stitched_count


# ---------------------------------------------------------------------------
# Hybrid Markdown Chunker with Page Provenance (G444, G446)
# ---------------------------------------------------------------------------

_PAGE_MARKER_RE = re.compile(r"<!--\s*page:(\d+)\s*-->")


_HEADING_MD_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def hybrid_chunk_markdown(
    markdown: str,
    *,
    doc_id: str,
    source: str,
    target_chars: int = 2000,
    hard_max_chars: int = 4000,
    min_chars: int = 80,
) -> list[dict[str, Any]]:
    """Chunk Markdown with Docling HybridChunker semantics: heading trails, row-safe tables & page provenance."""
    if not markdown or not markdown.strip():
        return []

    stitched_md, _ = stitch_cross_page_markdown_tables(markdown)

    # Track page markers across lines
    lines_with_pages: list[tuple[int, str]] = []
    cur_page = 1
    saw_explicit_marker = False
    for raw_line in stitched_md.splitlines():
        m = _PAGE_MARKER_RE.search(raw_line)
        if m:
            cur_page = int(m.group(1))
            saw_explicit_marker = True
            cleaned_line = _PAGE_MARKER_RE.sub("", raw_line).strip()
            if cleaned_line:
                lines_with_pages.append((cur_page, cleaned_line))
            continue
        if raw_line.strip() == "---" and not saw_explicit_marker:
            cur_page += 1
            continue
        lines_with_pages.append((cur_page, raw_line))

    # Split into heading sections while keeping (page_no, line)
    sections: list[tuple[list[str], list[tuple[int, str]]]] = []
    trail: list[tuple[int, str]] = []
    cur_lines: list[tuple[int, str]] = []

    def _flush_sec() -> None:
        if cur_lines:
            non_empty = [ln for _, ln in cur_lines if ln.strip()]
            if non_empty:
                sections.append(([t for _, t in trail], list(cur_lines)))
            cur_lines.clear()

    for pg, ln in lines_with_pages:
        hm = _HEADING_MD_RE.match(ln.strip())
        if hm:
            _flush_sec()
            lvl = len(hm.group(1))
            title = hm.group(2).strip()
            while trail and trail[-1][0] >= lvl:
                trail.pop()
            trail.append((lvl, title))
        else:
            cur_lines.append((pg, ln))
    _flush_sec()

    if not sections and lines_with_pages:
        sections = [([], lines_with_pages)]

    chunks: list[dict[str, Any]] = []
    chunk_idx = 0

    for sec_trail, sec_lines in sections:
        # Group into paragraphs / atomic tables with their page sets
        blocks: list[tuple[set[int], str, bool]] = []
        buf_lines: list[str] = []
        buf_pages: set[int] = set()
        in_table = False

        for pg, ln in sec_lines:
            is_tbl = bool(_MD_TABLE_LINE_RE.match(ln))
            if is_tbl:
                if not in_table and buf_lines:
                    blocks.append((set(buf_pages), "\n".join(buf_lines).strip(), False))
                    buf_lines, buf_pages = [], set()
                in_table = True
                buf_lines.append(ln)
                buf_pages.add(pg)
            else:
                if in_table:
                    blocks.append((set(buf_pages), "\n".join(buf_lines).strip(), True))
                    buf_lines, buf_pages = [], set()
                    in_table = False
                if ln.strip():
                    buf_lines.append(ln)
                    buf_pages.add(pg)
                elif buf_lines:
                    blocks.append((set(buf_pages), "\n".join(buf_lines).strip(), False))
                    buf_lines, buf_pages = [], set()
        if buf_lines:
            blocks.append((set(buf_pages), "\n".join(buf_lines).strip(), in_table))

        acc_texts: list[str] = []
        acc_pages: set[int] = set()
        acc_len = 0

        def _emit_acc(trail_snapshot: list[str] = sec_trail) -> None:
            nonlocal acc_texts, acc_pages, acc_len, chunk_idx
            if not acc_texts:
                return
            body_txt = "\n\n".join(acc_texts).strip()
            acc_texts, acc_len = [], 0
            pages_sorted = sorted(acc_pages)
            acc_pages = set()
            if len(body_txt) < min_chars:
                return
            pg_str = (
                ""
                if not pages_sorted
                else (str(pages_sorted[0]) if len(pages_sorted) == 1 else f"{pages_sorted[0]}-{pages_sorted[-1]}")
            )
            crumb = " > ".join(trail_snapshot[-3:]) if trail_snapshot else ""
            prefix = f"[Document: {doc_id} — {crumb}]" if crumb else f"[Document: {doc_id}]"
            chunks.append(
                {
                    "text": body_txt,
                    "doc_id": doc_id,
                    "source": source,
                    "chunk_id": chunk_idx,
                    "heading_trail": list(trail_snapshot),
                    "contextual_prefix": prefix,
                    "page": pg_str,
                    "page_numbers": pages_sorted,
                }
            )
            chunk_idx += 1

        for blk_pages, blk_text, is_tbl in blocks:
            if not blk_text:
                continue
            if is_tbl and len(blk_text) > hard_max_chars:
                _emit_acc()
                for sub_tbl in split_markdown_table_preserving_headers(
                    blk_text, target_chars=target_chars, hard_max_chars=hard_max_chars
                ):
                    acc_texts = [sub_tbl]
                    acc_pages = set(blk_pages)
                    acc_len = len(sub_tbl)
                    _emit_acc()
                continue
            if acc_len + len(blk_text) + 2 > target_chars and acc_texts:
                _emit_acc()
            acc_texts.append(blk_text)
            acc_pages.update(blk_pages)
            acc_len += len(blk_text) + 2

        _emit_acc()

    return chunks


# ---------------------------------------------------------------------------
# Dual-Backend Document Converter (Docling + Native Structural Fallback, G441)
# ---------------------------------------------------------------------------


def docling_intelligence_status() -> dict[str, Any]:
    """Report active Docling document intelligence backend & capabilities."""
    has_docling = False
    try:
        import importlib.util

        has_docling = importlib.util.find_spec("docling") is not None
    except Exception:
        has_docling = False

    active = "docling" if (has_docling and DOCLING_BACKEND in {"auto", "docling"}) else "native_structural"
    return {
        "backend": active,
        "docling_installed": has_docling,
        "configured_backend": DOCLING_BACKEND,
        "capabilities": [
            "furniture_layer_filtering",
            "xy_cut_multicolumn_reading_order",
            "table_row_header_propagation",
            "triplet_table_serialization",
            "cross_page_table_stitching",
            "page_and_bbox_provenance",
        ],
    }


def _parse_markdown_table_matrix(md_table: str) -> list[list[str]]:
    """Parse a Markdown table string back into a 2D matrix of cell values (G453)."""
    matrix: list[list[str]] = []
    for raw_ln in (md_table or "").splitlines():
        ln = raw_ln.strip()
        if not ln or not _MD_TABLE_LINE_RE.match(ln) or _MD_TABLE_SEP_RE.match(ln):
            continue
        inner = ln.strip("|")
        cells = [c.replace("\\|", "|").strip() for c in _UNESCAPED_PIPE_RE.split(inner)]
        if cells:
            matrix.append(cells)
    return matrix


def _convert_via_docling_library(
    data: bytes,
    filename: str,
    *,
    max_pages: int = 40,
) -> DoclingDocumentIR | None:
    """Convert *data* via IBM ``docling`` library if installed and enabled (G441, G453)."""
    if DOCLING_BACKEND not in {"auto", "docling"}:
        return None
    try:
        from docling.datamodel.base_models import DocumentStream  # type: ignore[import-not-found]
        from docling.document_converter import DocumentConverter  # type: ignore[import-not-found]

        converter = DocumentConverter()
        stream = DocumentStream(name=filename or "document.pdf", stream=io.BytesIO(data))
        max_bytes = int(os.getenv("DOCUMENT_MAX_BYTES", str(40 * 1024 * 1024)))
        try:
            conv_res = converter.convert(stream, max_num_pages=max_pages, max_file_size=max_bytes)
        except TypeError:
            # Older Docling versions do not accept both resource caps. Do not
            # retry unbounded; let the bounded native parser handle the file.
            logger.warning("Docling API lacks required page/file caps; using bounded native parser")
            return None
        dl_doc = conv_res.document

        ir = DoclingDocumentIR(
            filename=filename,
            backend_used="docling",
            page_count=len(getattr(dl_doc, "pages", {}) or {}) or 1,
        )
        trail: list[str] = []
        idx = 0
        for item, level in dl_doc.iterate_items():
            idx += 1
            label = str(getattr(item, "label", "text")).lower()
            layer_raw = str(getattr(item, "content_layer", "body")).lower()
            is_furn = "furniture" in layer_raw or label in {"page_header", "page_footer"}
            layer = ContentLayer.FURNITURE if is_furn else ContentLayer.BODY
            if is_furn:
                ir.furniture_removed_count += 1

            provs: list[DoclingProvenance] = []
            for p in getattr(item, "prov", []) or []:
                page_no = int(getattr(p, "page_no", 1) or 1)
                if page_no > max_pages:
                    continue
                bbox_obj = getattr(p, "bbox", None)
                if bbox_obj is not None and hasattr(bbox_obj, "as_tuple"):
                    bbox = [float(x) for x in bbox_obj.as_tuple()[:4]]
                else:
                    bbox = [0.0, 0.0, 0.0, 0.0]
                provs.append(DoclingProvenance(page_no=page_no, bbox=bbox, source="docling"))

            if getattr(item, "prov", None) and not provs:
                continue

            if "table" in label and hasattr(item, "export_to_markdown"):
                try:
                    t_md = item.export_to_markdown(dl_doc)
                except TypeError:
                    t_md = item.export_to_markdown()
                clean_t_md = strip_hidden_control_and_bidi(str(t_md or "").strip())
                clean_t_md, _ = scan_retrieved_text(clean_t_md)
                clean_t_md = escape_untrusted_document_markers(clean_t_md)
                t_matrix = _parse_markdown_table_matrix(clean_t_md)
                pg_num = provs[0].page_no if provs else 1
                t_triplets = serialize_table_to_triplets(f"Page {pg_num} dl_{idx}", t_matrix) if t_matrix else []
                ir.items.append(
                    DoclingItem(
                        item_id=f"dl_{idx}",
                        label="table",
                        text=clean_t_md,
                        content_layer=layer,
                        heading_trail=list(trail),
                        prov=provs,
                        table_matrix=t_matrix,
                        table_triplets=t_triplets,
                        level=max(1, int(level or 1)),
                    )
                )
                continue

            raw_text_val = strip_hidden_control_and_bidi(str(getattr(item, "text", "") or "").strip())
            text_val, _ = scan_retrieved_text(raw_text_val)
            text_val = escape_untrusted_document_markers(text_val).strip()
            if not text_val:
                continue
            if "header" in label and not is_furn:
                lvl = max(1, min(6, int(level or 1)))
                trail = trail[: lvl - 1] + [text_val]
                ir.items.append(
                    DoclingItem(
                        item_id=f"dl_{idx}",
                        label="section_header",
                        text=text_val,
                        content_layer=layer,
                        heading_trail=list(trail),
                        prov=provs,
                        level=lvl,
                    )
                )
            else:
                ir.items.append(
                    DoclingItem(
                        item_id=f"dl_{idx}",
                        label=label,
                        text=text_val,
                        content_layer=layer,
                        heading_trail=list(trail),
                        prov=provs,
                        level=max(1, int(level or 1)),
                    )
                )
        if ir.items:
            return ir
    except Exception:
        if DOCLING_BACKEND == "docling":
            logger.warning("Explicitly requested docling converter failed; falling back to native_structural", exc_info=True)
    return None


def _glyph_in_table_bboxes(glyph_bbox: list[float], table_bboxes: list[list[float]]) -> bool:
    """Return True if a glyph's center lies inside any table bounding box on the page (G456)."""
    if len(glyph_bbox) < 4 or not table_bboxes:
        return False
    cx = (glyph_bbox[0] + glyph_bbox[2]) / 2.0
    cy = (glyph_bbox[1] + glyph_bbox[3]) / 2.0
    for tb in table_bboxes:
        if (
            len(tb) >= 4
            and tb[2] > tb[0]
            and tb[3] > tb[1]
            and (tb[0] - 2.0) <= cx <= (tb[2] + 2.0)
            and (tb[1] - 2.0) <= cy <= (tb[3] + 2.0)
        ):
            return True
    return False


def _vector_glyph_lines_with_paragraphs(glyphs: list[Any]) -> list[str]:
    """Group spatially ordered words into lines and preserve large vertical gaps."""
    ordered = sort_spatial_reading_order(glyphs)
    lines_out: list[str] = []
    cur_words: list[str] = []
    cur_y: float | None = None
    cur_h = 10.0
    for glyph in ordered:
        cy = (glyph.bbox[1] + glyph.bbox[3]) / 2.0 if len(glyph.bbox) >= 4 else 0.0
        height = max(4.0, (glyph.bbox[3] - glyph.bbox[1]) if len(glyph.bbox) >= 4 else 10.0)
        if cur_y is None or abs(cy - cur_y) <= max(6.0, height * 0.5):
            cur_words.append(glyph.text)
            cur_y = cy if cur_y is None else (cur_y + cy) / 2.0
            cur_h = height
            continue
        lines_out.append(" ".join(cur_words))
        if (cy - cur_y) > max(12.0, cur_h * 1.4):
            lines_out.append("")
        cur_words = [glyph.text]
        cur_y = cy
        cur_h = height
    if cur_words:
        lines_out.append(" ".join(cur_words))
    return lines_out


def convert_document_bytes(
    data: bytes,
    filename: str = "document.pdf",
    *,
    max_pages: int = 40,
) -> DoclingDocumentIR:
    """Convert PDF bytes into a structured :class:`DoclingDocumentIR` with layout, table & provenance intelligence."""
    dl_ir = _convert_via_docling_library(data, filename, max_pages=max_pages)
    if dl_ir is not None:
        return dl_ir

    from .vision.glyph_fusion import extract_page_vector_glyphs
    from .vision.table_structuring import structure_document_tables

    ir = DoclingDocumentIR(filename=filename, backend_used="native_structural")
    raw_pages: list[str] = []
    page_bboxes: dict[int, list[float]] = {}
    page_tables_map: dict[int, list[Any]] = {}

    # Extract per-page vector glyphs and sort via 2D XY-Cut reading order
    page_count = 0
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]

        pdf = pdfium.PdfDocument(data)
        try:
            page_count = len(pdf)
        finally:
            pdf.close()
    except Exception:
        try:
            import pymupdf  # type: ignore[import-untyped]

            with pymupdf.open(stream=data, filetype="pdf") as doc:
                page_count = len(doc)
        except Exception:
            page_count = 0

    ir.page_count = page_count
    effective_pages = min(page_count, max_pages)
    dpi_scale = 150.0 / 72.0

    for p_idx in range(effective_pages):
        page_no = p_idx + 1
        # Detect vector tables first so we can exclude their cell bounding boxes from prose paragraphs (G456)
        t_bboxes_px: list[list[float]] = []
        try:
            st_list = structure_document_tables(data, raw_text="", page_number=page_no)
            page_tables_map[page_no] = st_list
            for st in st_list:
                for c in st.cells:
                    if len(c.bbox) >= 4 and c.bbox[2] > c.bbox[0]:
                        t_bboxes_px.append([v * dpi_scale for v in c.bbox[:4]])
        except Exception:
            page_tables_map[page_no] = []

        glyphs = extract_page_vector_glyphs(data, p_idx)
        if glyphs:
            non_table_glyphs = [g for g in glyphs if not _glyph_in_table_bboxes(g.bbox, t_bboxes_px)]
            ordered = sort_spatial_reading_order(non_table_glyphs)
            xs = [g.bbox[0] for g in ordered if len(g.bbox) >= 4]
            ys = [g.bbox[1] for g in ordered if len(g.bbox) >= 4]
            x2s = [g.bbox[2] for g in ordered if len(g.bbox) >= 4]
            y2s = [g.bbox[3] for g in ordered if len(g.bbox) >= 4]
            if xs and ys:
                page_bboxes[page_no] = [min(xs), min(ys), max(x2s), max(y2s)]

            # Group ordered glyphs into lines and paragraphs by vertical spacing (G456)
            lines_out = _vector_glyph_lines_with_paragraphs(ordered)
            raw_pages.append("\n".join(lines_out))
        else:
            raw_pages.append("")

    cleaned_pages, furn_removed = strip_document_furniture(raw_pages)
    ir.furniture_removed_count = furn_removed

    trail: list[str] = []
    item_seq = 0
    for p_idx, p_text in enumerate(cleaned_pages):
        page_no = p_idx + 1
        p_bbox = page_bboxes.get(page_no, [0.0, 0.0, 0.0, 0.0])

        if p_text.strip():
            for para in re.split(r"\n{2,}", p_text.strip()):
                para = para.strip()
                if not para:
                    continue
                item_seq += 1
                if re.match(r"^(?:PART|CHAPTER|SECTION|SCHEDULE|\d+\.\d*)\s+[A-Z0-9]", para, re.I) and len(para) <= 110:
                    trail = [para]
                    ir.items.append(
                        DoclingItem(
                            item_id=f"item_{item_seq}",
                            label="section_header",
                            text=para,
                            content_layer=ContentLayer.BODY,
                            heading_trail=list(trail),
                            prov=[DoclingProvenance(page_no=page_no, bbox=p_bbox)],
                            level=2,
                        )
                    )
                else:
                    ir.items.append(
                        DoclingItem(
                            item_id=f"item_{item_seq}",
                            label="text",
                            text=para,
                            content_layer=ContentLayer.BODY,
                            heading_trail=list(trail),
                            prov=[DoclingProvenance(page_no=page_no, bbox=p_bbox)],
                        )
                    )

        # Attach structured tables on this page
        try:
            tables = page_tables_map.get(page_no)
            if not tables and p_text.strip():
                tables = structure_document_tables(data, raw_text=p_text, page_number=page_no)
            for st in tables or []:
                if st.matrix and len(st.matrix) > 1:
                    item_seq += 1
                    triplets = serialize_table_to_triplets(f"Page {page_no} {st.table_id}", st.matrix)
                    ir.items.append(
                        DoclingItem(
                            item_id=f"table_{item_seq}",
                            label="table",
                            text=st.to_markdown(),
                            content_layer=ContentLayer.BODY,
                            heading_trail=list(trail),
                            prov=[DoclingProvenance(page_no=page_no, bbox=p_bbox, source=st.source)],
                            table_matrix=st.matrix,
                            table_triplets=triplets,
                        )
                    )
        except Exception:
            logger.debug("Table structuring skipped on page %d", page_no, exc_info=True)

    return ir
