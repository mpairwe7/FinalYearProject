"""PDF → structured chunks.

Strategy:
    1. Use ``pymupdf4llm.to_markdown`` with ``table_strategy="lines_strict"``
       so tax rate tables survive extraction as markdown tables.
    2. Split on markdown headings hierarchically (H1 > H2 > H3 > H4), keeping
       the heading trail so downstream prompts can cite section.
    3. Within each section, recursively split by paragraph → sentence to
       respect a soft token budget.
    4. Optionally prepend a short "contextual prefix" (Anthropic, late 2024):
       a deterministic header describing where the chunk sits in the
       document. Improves retrieval recall by ~35% on long PDFs.

Only the pure-Python pieces run on import. The heavy ``pymupdf4llm`` import
is lazy so the rest of the pipeline works even when PDFs are disabled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ml.scripts.data_aug.text_utils import clean_text

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@dataclass
class Chunk:
    """A structure-preserving chunk of a document."""

    text: str
    doc_id: str  # filename without extension
    source: str  # full basename
    chunk_id: int
    heading_trail: list[str] = field(default_factory=list)
    token_count: int | None = None
    page_numbers: list[int] = field(default_factory=list)

    @property
    def page_range(self) -> str:
        """Compact 1-based page locator (e.g. ``'4'`` or ``'4-5'``), or ``''`` if unknown."""
        if not self.page_numbers:
            return ""
        pages = sorted({int(p) for p in self.page_numbers if int(p) > 0})
        if not pages:
            return ""
        if len(pages) == 1 or pages[0] == pages[-1]:
            return str(pages[0])
        return f"{pages[0]}-{pages[-1]}"

    @property
    def contextual_prefix(self) -> str:
        """Short, cite-able prefix describing chunk location.

        Prepend this to the raw text during QA generation for the 'late
        chunking' technique. At training time the chunk is stored *without*
        the prefix so the model learns the raw content; the prefix is only
        useful when the chunk is used as retrieval context.
        """
        parts = [p for p in self.heading_trail if p]
        if not parts:
            return f"[Document: {self.doc_id}]"
        crumb = " > ".join(parts[-3:])  # last 3 levels, keep prompt short
        return f"[Document: {self.doc_id} — {crumb}]"


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
# Statutory headings in plain-text Ugandan Bills, Acts, and Regulations when
# extracted by engines that do not emit markdown `#` markers (e.g. pypdfium2).
_STATUTORY_PART_RE = re.compile(
    r"^\s*((?:PART|CHAPTER)\s+[IVXLCDM0-9]+[^\n]{0,90}|MEMORANDUM\b[^\n]{0,60}|"
    r"(?:FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH)?\s*SCHEDULE(?:\s+[0-9IVX]+)?\b[^\n]{0,80}|"
    r"ARRANGEMENT\s+OF\s+(?:SECTIONS|CLAUSES|REGULATIONS|ARTICLES)\b[^\n]{0,60}|"
    r"(?:LUGANDA|RUNYANKORE|RUNYANKOLE|KISWAHILI|SWAHILI|ENGLISH|ACHOLI))\s*$",
    re.IGNORECASE,
)
_STATUTORY_CLAUSE_RE = re.compile(
    r"^\s*((?:SECTION|CLAUSE|ARTICLE|REGULATION|RULE)\s+\d+[A-Za-z]?\b[^\n]{0,90}|"
    r"\d{1,3}\.\s+(?:Amendment|Insertion|Repeal|Substitution|Commencement|Interpretation|"
    r"Application|Imposition|Exemption|Rate|Duty|Tax|Offences?|Penalt\w+|Regulations?)\b[^\n]{0,90})\s*$",
    re.IGNORECASE,
)
# Standalone numbered or unnumbered Q&A headings in URA taxpayer handbooks
# across English, Luganda, Runyankole, and Swahili (e.g. "1. What is EFRIS?",
# "**1. What is EFRIS?**", "EFRIS kye ki?", "1. EFRIS Nenki?", "1.EFRIS ni nini?").
_QA_HEADING_RE = re.compile(
    r"^\s*\*{0,2}((?:\d{1,2}\s*[.)]\s*)?[A-Z][^\n?*]{4,100}\?)\*{0,2}\s*$"
)
# When pymupdf4llm joins a numbered Q&A heading and its answer onto one line
# or mid-paragraph (e.g. "...enkola ya EFRIS. 5. Nsobola okukozesa enkola ya EFRIS? Yee..."),
# split the question onto its own line before heading promotion.
_INLINE_QA_SPLIT_RE = re.compile(
    r"(?:(?<=^)|(?<=[.!?:;\n])\s*)(\*{0,2}(?:\d{1,2}\s*[.)]\s*[A-Z][^\n?*]{4,95}\?|(?:EFRIS|Ani|Nani|Namna|Biashara)\s+[^\n?*]{3,85}\?)\*{0,2})(?=\s+[A-Z\"'*]|\s*$)",
    re.MULTILINE,
)
# A paragraph is a maximal run of non-blank lines. Tables are treated as a
# single paragraph so they survive as one chunk.
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n+")
_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP_LINE_RE = re.compile(r"^\s*\|\s*[:\-]+(?:\s*\|\s*[:\-]+)*\s*\|\s*$")
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")
_PAGE_MARKER_RE = re.compile(r"<!--\s*page:(\d+)\s*-->")
_FURNITURE_LINE_RE = re.compile(
    r"^\s*(?:(?:DOMESTIC\s+TAX\s+LAWS\s+OF\s+UGANDA\s*)?\d+\s*\|\s*P\s*a\s*g\s*e|"
    r"P\s*a\s*g\s*e\s*\|\s*\d+|Page\s+\d+\s+(?:of|/)\s+\d+|"
    r"THE\s+UGANDA\s+GAZETTE)\s*$",
    re.IGNORECASE,
)


def _count_md_cols(line: str) -> int:
    """Count Markdown table columns using unescaped pipes (ignoring ``\\|``, G451)."""
    return max(0, len(_UNESCAPED_PIPE_RE.findall(line or "")) - 1)


def _is_markdown_table_block(para: str) -> bool:
    """Return True when *para* is a multi-line pipe-delimited Markdown table block."""
    lines = [ln.strip() for ln in (para or "").strip().splitlines() if ln.strip()]
    if len(lines) < 2:
        return False
    return all(_TABLE_LINE_RE.match(ln) for ln in lines)


def _split_markdown_table_rows(
    table_para: str,
    *,
    target_chars: int,
    hard_max_chars: int,
) -> list[str]:
    """Split an oversized Markdown table strictly on row boundaries, repeating the header in every chunk (G446)."""
    lines = [ln.strip() for ln in (table_para or "").strip().splitlines() if ln.strip()]
    if not lines:
        return []
    if len(table_para) <= hard_max_chars:
        return ["\n".join(lines)]

    if len(lines) >= 2 and _TABLE_SEP_LINE_RE.match(lines[1]):
        header_lines = lines[:2]
        data_lines = lines[2:]
    else:
        num_cols = max(1, _count_md_cols(lines[0]))
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
            clipped = row[: max(80, hard_budget - header_len)]
            chunks.append(header_block + "\n" + clipped)
            continue
        cur_rows.append(row)
        cur_len += row_len

    if cur_rows:
        chunks.append(header_block + "\n" + "\n".join(cur_rows))
    return chunks


def _stitch_cross_page_tables(markdown: str) -> str:
    """Stitch Markdown tables split across page breaks (`---` or `<!-- page:N -->`) when columns match (G446, G451, G452)."""
    if not markdown or "|" not in markdown:
        return markdown

    lines = markdown.splitlines()
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        if _FURNITURE_LINE_RE.match(line.strip()):
            i += 1
            continue
        out.append(line)
        if _TABLE_LINE_RE.match(line):
            j = i + 1
            saw_break = False
            page_markers: list[str] = []
            while j < n:
                cand = lines[j].strip()
                if not cand or _FURNITURE_LINE_RE.match(cand):
                    j += 1
                    continue
                if cand == "---":
                    saw_break = True
                    j += 1
                    continue
                if _PAGE_MARKER_RE.fullmatch(cand):
                    saw_break = True
                    page_markers.append(cand)
                    j += 1
                    continue
                break

            if saw_break and j < n and _TABLE_LINE_RE.match(lines[j]):
                prev_cols = _count_md_cols(line)
                next_cols = _count_md_cols(lines[j])
                if prev_cols >= 2 and prev_cols == next_cols:
                    k = len(out) - 1
                    while k >= 0 and (_TABLE_LINE_RE.match(out[k]) or _PAGE_MARKER_RE.fullmatch(out[k].strip())):
                        k -= 1
                    table_start_idx = k + 1
                    while table_start_idx < len(out) and _PAGE_MARKER_RE.fullmatch(out[table_start_idx].strip()):
                        table_start_idx += 1
                    first_header_norm = re.sub(r"\s+", " ", out[table_start_idx].strip().lower())
                    next_first_norm = re.sub(r"\s+", " ", lines[j].strip().lower())

                    if first_header_norm == next_first_norm:
                        j += 1
                        if j < n and _TABLE_SEP_LINE_RE.match(lines[j]):
                            j += 1
                        for pm in page_markers:
                            out.append(pm)
                        i = j
                        continue
                    if not (j + 1 < n and _TABLE_SEP_LINE_RE.match(lines[j + 1])):
                        # Continuation rows without a new header/separator
                        for pm in page_markers:
                            out.append(pm)
                        i = j
                        continue
        i += 1

    return "\n".join(out)


def _inject_statutory_headings(text: str) -> str:
    """Promote plain-text statutory Part/Schedule/Section/Clause and Q&A lines to markdown headings."""
    text = _INLINE_QA_SPLIT_RE.sub(r"\n\n\1\n\n", text)
    out_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith("#")
            and len(stripped) <= 115
            and not _TABLE_LINE_RE.match(stripped)
        ):
            unbolded = stripped.strip("*").strip()
            if _STATUTORY_PART_RE.match(unbolded):
                out_lines.append(f"# {unbolded}")
                continue
            if _STATUTORY_CLAUSE_RE.match(unbolded) or _QA_HEADING_RE.match(stripped):
                out_lines.append(f"## {unbolded}")
                continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _split_headings(markdown: str) -> list[tuple[list[str], str]]:
    """Split markdown into ``(heading_trail, body)`` sections.

    The heading trail is the path from the document root to the current
    section, e.g. ``["Taxation Handbook", "Part A", "1.2 Origin of Taxation"]``.
    This is what powers the contextual prefix.
    """
    if markdown:
        markdown = _inject_statutory_headings(markdown)
    sections: list[tuple[list[str], str]] = []
    trail: list[tuple[int, str]] = []
    current_body: list[str] = []

    def _flush():
        if current_body:
            body = "\n".join(current_body).strip()
            if body:
                if trail and trail[-1][1].endswith("?") and not body.startswith(trail[-1][1]):
                    body = f"{trail[-1][1]}\n{body}"
                sections.append(([t for _, t in trail], body))
            current_body.clear()

    pos = 0
    for m in _HEADING_RE.finditer(markdown):
        # Flush accumulated body up to the heading.
        if m.start() > pos:
            current_body.append(markdown[pos : m.start()])
            _flush()

        level = len(m.group(1))
        title = m.group(2).strip()

        # Pop headings at the same or deeper level off the trail.
        while trail and trail[-1][0] >= level:
            trail.pop()
        trail.append((level, title))
        pos = m.end()

    # Tail after last heading.
    if pos < len(markdown):
        current_body.append(markdown[pos:])
    _flush()
    return sections


def _split_paragraphs_keep_tables(body: str) -> list[str]:
    """Split into paragraphs, but keep markdown tables atomic."""
    out: list[str] = []
    current: list[str] = []
    in_table = False

    for line in body.splitlines():
        is_table_line = bool(_TABLE_LINE_RE.match(line))
        if is_table_line:
            if not in_table and current:
                out.append("\n".join(current).strip())
                current = []
            in_table = True
            current.append(line)
        else:
            if in_table:
                out.append("\n".join(current).strip())
                current = []
                in_table = False
            if line.strip():
                current.append(line)
            else:
                if current:
                    out.append("\n".join(current).strip())
                    current = []

    if current:
        out.append("\n".join(current).strip())
    return [p for p in out if p]


def _recursive_chunk(
    paragraphs: list[str],
    *,
    target_chars: int,
    hard_max_chars: int,
) -> list[str]:
    """Greedy chunk accumulator.

    We aim for ``target_chars`` per chunk but allow up to ``hard_max_chars``
    so we never split mid-sentence inside a table. If a single paragraph
    exceeds the hard maximum it is sentence-split; if even a single sentence
    is too long it is character-split (last resort).
    """
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    def _flush():
        nonlocal buf, buf_len
        if buf:
            chunks.append("\n\n".join(buf).strip())
            buf = []
            buf_len = 0

    for para in paragraphs:
        plen = len(para)
        if plen > hard_max_chars:
            _flush()
            if _is_markdown_table_block(para):
                chunks.extend(
                    _split_markdown_table_rows(
                        para,
                        target_chars=target_chars,
                        hard_max_chars=hard_max_chars,
                    )
                )
                continue
            # sentence split
            sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", para)
            inner: list[str] = []
            inner_len = 0
            for s in sentences:
                if inner_len + len(s) + 1 > hard_max_chars and inner:
                    chunks.append(" ".join(inner).strip())
                    inner, inner_len = [], 0
                if len(s) > hard_max_chars:
                    # last-resort char split
                    for i in range(0, len(s), hard_max_chars):
                        chunks.append(s[i : i + hard_max_chars].strip())
                    inner, inner_len = [], 0
                    continue
                inner.append(s)
                inner_len += len(s) + 1
            if inner:
                chunks.append(" ".join(inner).strip())
            continue

        if buf_len + plen + 2 > target_chars and buf:
            _flush()
        buf.append(para)
        buf_len += plen + 2

    _flush()
    return [c for c in chunks if c]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _char_outside_bboxes(char_obj: dict, bboxes: list[tuple[float, float, float, float]]) -> bool:
    """Return True if a pdfplumber char dict's center lies outside all table bounding boxes (G441)."""
    try:
        cx = (float(char_obj.get("x0", 0.0)) + float(char_obj.get("x1", 0.0))) / 2.0
        cy = (float(char_obj.get("top", 0.0)) + float(char_obj.get("bottom", 0.0))) / 2.0
        for x0, top, x1, bottom in bboxes:
            if (x0 - 1.0) <= cx <= (x1 + 1.0) and (top - 1.0) <= cy <= (bottom + 1.0):
                return False
    except Exception:
        return True
    return True


def _format_table_matrix_md(table: list[list[object]]) -> list[str]:
    """Render a 2D table matrix as Markdown, forward-filling horizontal merged header cells (G448)."""
    if not table or len(table) < 1:
        return []
    raw_header = table[0]
    header: list[str] = []
    last_h = ""
    span_idx = 1
    for c in raw_header:
        if c is None and last_h:
            span_idx += 1
            header.append(f"{last_h} ({span_idx})")
        else:
            val = str(c or "").replace("\n", " ").replace("|", "\\|").strip()
            last_h = val
            span_idx = 1
            header.append(val)
    if not any(header):
        return []
    md_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in table[1:]:
        cols = [str(c or "").replace("\n", " ").replace("|", "\\|").strip() for c in row]
        if any(cols):
            if len(cols) < len(header):
                cols.extend([""] * (len(header) - len(cols)))
            md_lines.append("| " + " | ".join(cols[: len(header)]) + " |")
    return md_lines if len(md_lines) > 2 else []


def extract_markdown(pdf_path: Path) -> str | None:
    """Extract PDF as markdown with table preservation using permissive MIT/Apache-2.0 engines.

    Returns None if extraction fails. Never raises — callers must be able
    to process thousands of PDFs without a single bad file killing the run.
    """
    import os

    # 0. Optional: IBM Docling converter when installed and enabled
    if os.getenv("DOCLING_CORPUS_ENABLED", "true").lower() not in ("0", "false", "no", "off"):
        try:
            from docling.document_converter import (
                DocumentConverter,  # type: ignore[import-not-found]
            )

            converter = DocumentConverter()
            res = converter.convert(str(pdf_path))
            md = res.document.export_to_markdown()
            if md and str(md).strip():
                return str(md)
        except Exception:
            pass

    # 1. Primary: pdfplumber (MIT) with table bbox deduplication (G441) and page markers (G444)
    try:
        import pdfplumber

        with pdfplumber.open(str(pdf_path)) as pdf:
            pages_md: list[str] = []
            for p_idx, page in enumerate(pdf.pages, start=1):
                table_bboxes: list[tuple[float, float, float, float]] = []
                try:
                    found_tables = page.find_tables() or []
                    for ft in found_tables:
                        if getattr(ft, "bbox", None) and len(ft.bbox) == 4:
                            table_bboxes.append(
                                (float(ft.bbox[0]), float(ft.bbox[1]), float(ft.bbox[2]), float(ft.bbox[3]))
                            )
                except Exception:
                    table_bboxes = []

                if table_bboxes and hasattr(page, "filter"):
                    try:
                        non_table_page = page.filter(
                            lambda obj, _b=table_bboxes: (
                                _char_outside_bboxes(obj, _b) if obj.get("object_type") == "char" else True
                            )
                        )
                        text = non_table_page.extract_text() or ""
                    except Exception:
                        text = page.extract_text() or ""
                else:
                    text = page.extract_text() or ""

                tables = page.extract_tables() or []
                table_md_parts: list[str] = []
                for table in tables:
                    md_rows = _format_table_matrix_md(table)
                    if md_rows:
                        table_md_parts.append("\n".join(md_rows))

                content_parts = [text.strip()] if text.strip() else []
                if table_md_parts:
                    content_parts.append("\n\n".join(table_md_parts))
                if content_parts:
                    pages_md.append(f"<!-- page:{p_idx} -->\n" + "\n\n".join(content_parts))

            if pages_md:
                return "\n\n---\n\n".join(pages_md)
    except Exception:
        pass

    # 2. Secondary: pypdfium2 (Apache-2.0) — always close native handles in finally
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            pages_md = []
            for i in range(len(pdf)):
                page = pdf[i]
                try:
                    textpage = page.get_textpage()
                    try:
                        page_text = textpage.get_text_range() or ""
                    finally:
                        textpage.close()
                finally:
                    page.close()
                if page_text.strip():
                    pages_md.append(f"<!-- page:{i + 1} -->\n{page_text.strip()}")
            if pages_md:
                return "\n\n---\n\n".join(pages_md)
        finally:
            pdf.close()
    except Exception:
        pass

    # 3. Fallback: pymupdf4llm if installed
    try:
        import pymupdf4llm  # type: ignore[import-untyped]

        md = pymupdf4llm.to_markdown(
            str(pdf_path),
            pages=None,
            show_progress=False,
            table_strategy="lines_strict",
        )
        if md and str(md).strip():
            return str(md)
    except Exception:
        pass

    # 4. Fallback: pymupdf (fitz) direct page text extraction
    try:
        import pymupdf  # type: ignore[import-untyped]

        doc = pymupdf.open(str(pdf_path))
        try:
            pages_md = [
                f"<!-- page:{idx + 1} -->\n{page.get_text('text').strip()}"
                for idx, page in enumerate(doc)
                if page.get_text("text").strip()
            ]
            if pages_md:
                return "\n\n---\n\n".join(pages_md)
        finally:
            doc.close()
    except Exception:
        return None
    return None


def _extract_page_spans_and_strip_markers(markdown: str) -> tuple[str, list[tuple[int, int, int]]]:
    """Strip ``<!-- page:N -->`` markers from *markdown* and return ``(clean_md, [(start, end, page_no)])`` (G444)."""
    if not _PAGE_MARKER_RE.search(markdown):
        return markdown, []

    out_parts: list[str] = []
    spans: list[tuple[int, int, int]] = []
    cur_pos = 0
    last_idx = 0
    cur_page: int | None = None
    seg_start = 0

    for m in _PAGE_MARKER_RE.finditer(markdown):
        piece = markdown[last_idx : m.start()]
        out_parts.append(piece)
        cur_pos += len(piece)
        if cur_page is not None and cur_pos > seg_start:
            spans.append((seg_start, cur_pos, cur_page))
        cur_page = int(m.group(1))
        seg_start = cur_pos
        last_idx = m.end()
        if last_idx < len(markdown) and markdown[last_idx] == "\n":
            last_idx += 1

    tail = markdown[last_idx:]
    out_parts.append(tail)
    cur_pos += len(tail)
    if cur_page is not None and cur_pos > seg_start:
        spans.append((seg_start, cur_pos, cur_page))

    return "".join(out_parts), spans


def _resolve_chunk_pages(
    chunk_text: str,
    clean_md: str,
    page_spans: list[tuple[int, int, int]],
    search_cursor: int,
) -> tuple[list[int], int]:
    """Resolve 1-based page numbers for *chunk_text* from *page_spans*."""
    if not page_spans:
        return [], search_cursor

    probe_lines = [
        ln.strip()
        for ln in chunk_text.splitlines()
        if ln.strip() and not _TABLE_SEP_LINE_RE.match(ln.strip())
    ]
    if not probe_lines:
        return [], search_cursor

    first_probe = probe_lines[0][:60]
    last_probe = probe_lines[-1][:60]

    start_pos = clean_md.find(first_probe, search_cursor)
    if start_pos < 0:
        start_pos = clean_md.find(first_probe)
    if start_pos < 0:
        # Fallback: match any span overlapping search_cursor
        matched = [pg for s, e, pg in page_spans if s <= search_cursor <= e]
        return sorted(set(matched)), search_cursor

    end_pos = clean_md.find(last_probe, start_pos)
    if end_pos < 0:
        end_pos = start_pos + len(first_probe)
    else:
        end_pos += len(last_probe)

    matched_pages = sorted({pg for s, e, pg in page_spans if max(s, start_pos) <= min(e, end_pos)})
    return matched_pages, max(search_cursor, start_pos)


def chunk_markdown(
    markdown: str,
    *,
    doc_id: str,
    source: str,
    target_chars: int = 2000,
    hard_max_chars: int = 4000,
    min_chars: int = 200,
) -> Iterator[Chunk]:
    """Yield hierarchical, structure-preserving chunks from *markdown*.

    Shared by :func:`chunk_pdf` and by the crawl corpus, whose pages are
    already markdown — the heading hierarchy, atomic tables and contextual
    prefixes are worth exactly as much to a crawled page as to a PDF.

    Args:
        markdown: document body
        doc_id: short identifier used in the contextual prefix
        source: full source name recorded on each chunk
        target_chars: soft size target (~500 tokens for Gemma/Llama)
        hard_max_chars: maximum before we force-split (~1000 tokens)
        min_chars: drop chunks smaller than this (usually orphan headings)
    """
    if not markdown:
        return

    stitched_md = _stitch_cross_page_tables(markdown)
    clean_md, page_spans = _extract_page_spans_and_strip_markers(stitched_md)

    sections = _split_headings(clean_md)
    if not sections:
        sections = [([], clean_md)]

    chunk_idx = 0
    search_cursor = 0
    for trail, body in sections:
        # Light cleaning inside the section (preserve newlines for tables)
        body = clean_text(body, preserve_newlines=True)
        eff_min = min(min_chars, 80) if (trail and str(trail[-1]).endswith("?")) else min_chars
        if len(body) < eff_min:
            continue

        paragraphs = _split_paragraphs_keep_tables(body)
        chunk_texts = _recursive_chunk(
            paragraphs,
            target_chars=target_chars,
            hard_max_chars=hard_max_chars,
        )
        for text in chunk_texts:
            if len(text) < eff_min:
                continue
            pages, search_cursor = _resolve_chunk_pages(text, clean_md, page_spans, search_cursor)
            yield Chunk(
                text=text,
                doc_id=doc_id,
                source=source,
                chunk_id=chunk_idx,
                heading_trail=list(trail),
                page_numbers=pages,
            )
            chunk_idx += 1


def chunk_pdf(
    pdf_path: Path,
    *,
    target_chars: int = 2000,
    hard_max_chars: int = 4000,
    min_chars: int = 200,
) -> Iterator[Chunk]:
    """Yield hierarchical, structure-preserving chunks from a PDF.

    Extraction only; the chunking itself is :func:`chunk_markdown`.
    """
    markdown = extract_markdown(pdf_path)
    if not markdown:
        return
    yield from chunk_markdown(
        markdown,
        doc_id=pdf_path.stem,
        source=pdf_path.name,
        target_chars=target_chars,
        hard_max_chars=hard_max_chars,
        min_chars=min_chars,
    )
