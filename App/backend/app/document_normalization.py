"""Document input normalization foundation (2026).

Standardizes document intake across statutory corpora, user attachments,
and multimodal RAG workflows using exclusively permissive licenses:
1. Primary: ``pypdfium2`` (Apache-2.0) for C++ PDFium rasterization & text.
2. Secondary: ``pdfplumber`` (MIT) for structured text & table extraction.
3. Raster Images: ``Pillow`` (MIT/HPND) for JPEG, PNG, TIFF, and WEBP.

Completely eliminates AGPL/commercial copyleft friction from the runtime path.
"""

from __future__ import annotations

import hashlib
import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RasterizeSpec:
    """Rasterization parameters and bounding limits."""

    dpi_x: float = 150.0
    dpi_y: float = 150.0
    page_indices: list[int] | None = None
    max_pixels: int = 12_000_000


@dataclass
class NormalizedPage:
    """Canonical representation of one normalized document page."""

    page_index: int
    source_page_number: int
    original_size: tuple[int, int]  # (width, height)
    text: str = ""
    image_bytes: bytes | None = None
    source_kind: str = "pdf_page"
    pdf_source_id: str | None = None
    tables: list[list[list[str | None]]] = field(default_factory=list)
    glyphs: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizationBundle:
    """Unified multi-page normalization bundle with provenance tracking."""

    source_fingerprint: str
    page_count: int
    pages: list[NormalizedPage] = field(default_factory=list)
    adapter: str = "unknown"
    source_kind: str = "pdf"
    warnings: list[str] = field(default_factory=list)


@dataclass
class NormalizationManifest:
    """Immutable cryptographic provenance manifest for an entire document intake."""

    document_id: str
    source_fingerprint: str
    page_count: int
    adapter: str
    source_kind: str
    page_manifests: list[dict[str, Any]]
    total_tables: int
    total_glyphs: int
    created_at_iso: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_fingerprint": self.source_fingerprint,
            "page_count": self.page_count,
            "adapter": self.adapter,
            "source_kind": self.source_kind,
            "page_manifests": self.page_manifests,
            "total_tables": self.total_tables,
            "total_glyphs": self.total_glyphs,
            "created_at_iso": self.created_at_iso,
        }


def build_normalization_manifest(
    bundle: NormalizationBundle,
    document_id: str = "",
) -> NormalizationManifest:
    """Construct an auditable cryptographic normalization manifest from a bundle."""
    from datetime import datetime, timezone

    doc_id = document_id or bundle.source_fingerprint[:16]
    page_manifests: list[dict[str, Any]] = []
    total_tables = 0
    total_glyphs = 0

    for p in bundle.pages:
        num_tables = len(p.tables)
        num_glyphs = len(p.glyphs)
        total_tables += num_tables
        total_glyphs += num_glyphs
        page_manifests.append(
            {
                "page_index": p.page_index,
                "source_page_number": p.source_page_number,
                "pdf_source_id": p.pdf_source_id,
                "original_size": p.original_size,
                "text_length": len(p.text),
                "table_count": num_tables,
                "glyph_count": num_glyphs,
                "has_rendered_image": p.image_bytes is not None,
                "warnings": list(p.warnings),
            }
        )

    return NormalizationManifest(
        document_id=doc_id,
        source_fingerprint=bundle.source_fingerprint,
        page_count=bundle.page_count,
        adapter=bundle.adapter,
        source_kind=bundle.source_kind,
        page_manifests=page_manifests,
        total_tables=total_tables,
        total_glyphs=total_glyphs,
        created_at_iso=datetime.now(timezone.utc).isoformat(),
    )


def validate_normalization_manifest(
    manifest: NormalizationManifest,
    raw_data: bytes,
) -> bool:
    """Verify that a document's bytes match its cryptographic normalization manifest."""
    expected_hash = hashlib.sha256(raw_data).hexdigest()
    return manifest.source_fingerprint == expected_hash


def _fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def normalize_document(
    source: bytes | str | Path,
    spec: RasterizeSpec | None = None,
    *,
    source_document_id: str = "",
    extract_text_layer: bool = True,
    render_images: bool = False,
    extract_tables: bool = False,
) -> NormalizationBundle:
    """Normalize a PDF or image into canonical pages with provenance."""
    spec = spec or RasterizeSpec()

    data: bytes
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        with source_path.open("rb") as f:
            data = f.read()
    else:
        data = source

    sha256_hash = _fingerprint_bytes(data)
    doc_id = source_document_id or sha256_hash[:16]

    is_pdf = data.lstrip().startswith(b"%PDF-")

    # If not a PDF, handle as image via Pillow (MIT/HPND)
    if not is_pdf:
        try:
            from PIL import Image  # type: ignore[import-untyped]

            with Image.open(io.BytesIO(data)) as img:
                width, height = img.size
                return NormalizationBundle(
                    source_fingerprint=sha256_hash,
                    page_count=1,
                    pages=[
                        NormalizedPage(
                            page_index=0,
                            source_page_number=1,
                            original_size=(width, height),
                            text="",
                            image_bytes=data if render_images else None,
                            source_kind="image",
                            pdf_source_id=None,
                        )
                    ],
                    adapter="pillow",
                    source_kind="image",
                )
        except Exception as img_err:
            logger.debug("Pillow image parse failed: %s", img_err)

    # 1. Try pypdfium2 (Apache-2.0 standard) for PDFs
    try:
        import pypdfium2 as pdfium  # type: ignore[import-untyped]

        pdf = pdfium.PdfDocument(data)
        page_count = len(pdf)
        pages: list[NormalizedPage] = []
        warnings: list[str] = []

        target_indices = spec.page_indices if spec.page_indices is not None else list(range(page_count))

        for idx in target_indices:
            if idx < 0 or idx >= page_count:
                continue
            page = pdf[idx]
            width_pt, height_pt = page.get_size()
            width_px = int(round(width_pt * spec.dpi_x / 72.0))
            height_px = int(round(height_pt * spec.dpi_y / 72.0))

            page_warnings: list[str] = []
            page_text = ""
            page_glyphs: list[dict[str, Any]] = []
            if extract_text_layer:
                try:
                    from app.vision.glyph_fusion import extract_page_vector_glyphs

                    glyphs_list = extract_page_vector_glyphs(data, idx, dpi=spec.dpi_x)
                    page_glyphs = [
                        {"text": g.text, "bbox": g.bbox, "confidence": g.confidence, "source": g.source}
                        for g in glyphs_list
                    ]
                    if glyphs_list:
                        page_text = " ".join(g.text for g in glyphs_list)
                    else:
                        textpage = page.get_textpage()
                        page_text = textpage.get_text_range() or ""
                except Exception:
                    try:
                        textpage = page.get_textpage()
                        page_text = textpage.get_text_range() or ""
                    except Exception:
                        page_warnings.append(f"Could not extract text layer from page {idx + 1}")

            img_bytes: bytes | None = None
            if render_images:
                try:
                    scale = spec.dpi_x / 72.0
                    if width_px * height_px > spec.max_pixels:
                        scale = (spec.max_pixels / max(1.0, width_pt * height_pt * (spec.dpi_x / 72.0) ** 2)) ** 0.5 * (spec.dpi_x / 72.0)
                        page_warnings.append(f"Page {idx + 1} was downscaled to meet pixel limits")
                    bitmap = page.render(scale=scale)
                    pil_image = bitmap.to_pil()
                    buf = io.BytesIO()
                    pil_image.save(buf, format="PNG")
                    img_bytes = buf.getvalue()
                except Exception:
                    page_warnings.append(f"Failed to render image for page {idx + 1}")

            page_tables: list[list[list[str | None]]] = []
            if extract_tables:
                try:
                    from app.vision.table_structuring import structure_document_tables

                    structured = structure_document_tables(data, page_text, page_number=idx + 1)
                    page_tables = [t.matrix for t in structured]
                except Exception:
                    pass

            pages.append(
                NormalizedPage(
                    page_index=idx,
                    source_page_number=idx + 1,
                    original_size=(width_px, height_px),
                    text=page_text,
                    image_bytes=img_bytes,
                    source_kind="pdf_page",
                    pdf_source_id=f"{doc_id}_page_{idx}",
                    tables=page_tables,
                    glyphs=page_glyphs,
                    warnings=page_warnings,
                )
            )

        return NormalizationBundle(
            source_fingerprint=sha256_hash,
            page_count=page_count,
            pages=pages,
            adapter="pypdfium2",
            source_kind="pdf",
            warnings=warnings,
        )
    except (ImportError, Exception) as pdfium_err:
        logger.debug("pypdfium2 normalization unavailable or failed: %s", pdfium_err)

    # 2. Try pdfplumber (MIT standard) for structured PDF extraction
    try:
        import pdfplumber  # type: ignore[import-untyped]

        with pdfplumber.open(io.BytesIO(data)) as pdf:
            page_count = len(pdf.pages)
            pages = []
            warnings = []
            target_indices = spec.page_indices if spec.page_indices is not None else list(range(page_count))

            for idx in target_indices:
                if idx < 0 or idx >= page_count:
                    continue
                page = pdf.pages[idx]
                width_pt, height_pt = float(page.width), float(page.height)
                width_px = int(round(width_pt * spec.dpi_x / 72.0))
                height_px = int(round(height_pt * spec.dpi_y / 72.0))

                page_warnings = []
                page_text = page.extract_text() or "" if extract_text_layer else ""
                tables = page.extract_tables() if extract_tables else []

                img_bytes = None
                if render_images:
                    try:
                        im = page.to_image(resolution=int(spec.dpi_x))
                        buf = io.BytesIO()
                        im.original.save(buf, format="PNG")
                        img_bytes = buf.getvalue()
                    except Exception:
                        page_warnings.append(f"Failed to render image for page {idx + 1}")

                pages.append(
                    NormalizedPage(
                        page_index=idx,
                        source_page_number=idx + 1,
                        original_size=(width_px, height_px),
                        text=page_text,
                        image_bytes=img_bytes,
                        source_kind="pdf_page",
                        pdf_source_id=f"{doc_id}_page_{idx}",
                        tables=tables,
                        warnings=page_warnings,
                    )
                )

            return NormalizationBundle(
                source_fingerprint=sha256_hash,
                page_count=page_count,
                pages=pages,
                adapter="pdfplumber",
                source_kind="pdf",
                warnings=warnings,
            )
    except (ImportError, Exception) as plumber_err:
        logger.debug("pdfplumber normalization failed: %s", plumber_err)

    # 3. Defensive fallback to PyMuPDF (fitz) only if present
    try:
        import fitz  # type: ignore[import-untyped]

        doc = fitz.open(stream=data, filetype="pdf")
        page_count = doc.page_count
        pages = []
        warnings = []
        target_indices = spec.page_indices if spec.page_indices is not None else list(range(page_count))

        for idx in target_indices:
            if idx < 0 or idx >= page_count:
                continue
            page = doc[idx]
            rect = page.rect
            width_px = int(round(rect.width * spec.dpi_x / 72.0))
            height_px = int(round(rect.height * spec.dpi_y / 72.0))

            page_warnings = []
            page_text = page.get_text("text") or "" if extract_text_layer else ""

            img_bytes = None
            if render_images:
                try:
                    scale = spec.dpi_x / 72.0
                    page_area = max(1.0, rect.width * rect.height)
                    if page_area * scale * scale > spec.max_pixels:
                        scale = (spec.max_pixels / page_area) ** 0.5
                        page_warnings.append(f"Page {idx + 1} was downscaled to meet pixel limits")
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    img_bytes = pix.tobytes("png")
                except Exception:
                    page_warnings.append(f"Failed to render image for page {idx + 1}")

            pages.append(
                NormalizedPage(
                    page_index=idx,
                    source_page_number=idx + 1,
                    original_size=(width_px, height_px),
                    text=page_text,
                    image_bytes=img_bytes,
                    source_kind="pdf_page",
                    pdf_source_id=f"{doc_id}_page_{idx}",
                    warnings=page_warnings,
                )
            )

        doc.close()
        return NormalizationBundle(
            source_fingerprint=sha256_hash,
            page_count=page_count,
            pages=pages,
            adapter="fitz",
            source_kind="pdf",
            warnings=warnings,
        )
    except Exception as fitz_err:
        logger.debug("fitz normalization fallback failed: %s", fitz_err)

    return NormalizationBundle(
        source_fingerprint=sha256_hash,
        page_count=0,
        pages=[],
        adapter="failed",
        source_kind="unknown",
        warnings=["Document could not be parsed by any installed normalization adapter."],
    )
