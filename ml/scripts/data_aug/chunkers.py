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
# A paragraph is a maximal run of non-blank lines. Tables are treated as a
# single paragraph so they survive as one chunk.
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n+")
_TABLE_LINE_RE = re.compile(r"^\s*\|.+\|\s*$")


def _split_headings(markdown: str) -> list[tuple[list[str], str]]:
    """Split markdown into ``(heading_trail, body)`` sections.

    The heading trail is the path from the document root to the current
    section, e.g. ``["Taxation Handbook", "Part A", "1.2 Origin of Taxation"]``.
    This is what powers the contextual prefix.
    """
    sections: list[tuple[list[str], str]] = []
    trail: list[str] = []
    current_body: list[str] = []

    def _flush():
        if current_body:
            body = "\n".join(current_body).strip()
            if body:
                sections.append((trail.copy(), body))
            current_body.clear()

    pos = 0
    for m in _HEADING_RE.finditer(markdown):
        # Flush accumulated body up to the heading.
        if m.start() > pos:
            current_body.append(markdown[pos : m.start()])
            _flush()

        level = len(m.group(1))
        title = m.group(2).strip()

        # Pop deeper levels off the trail.
        while len(trail) >= level:
            trail.pop()
        trail.append(title)
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


def extract_markdown(pdf_path: Path) -> str | None:
    """Extract PDF as markdown with table preservation.

    Returns None if extraction fails. Never raises — callers must be able
    to process thousands of PDFs without a single bad file killing the run.
    """
    try:
        import pymupdf4llm  # lazy
    except ImportError:
        return None

    try:
        md = pymupdf4llm.to_markdown(
            str(pdf_path),
            pages=None,
            show_progress=False,
            # lines_strict keeps table structure; fallback to simple if unsupported.
            table_strategy="lines_strict",
        )
        return str(md) if md else None
    except TypeError:
        # Older pymupdf4llm may not accept table_strategy
        try:
            return str(pymupdf4llm.to_markdown(str(pdf_path), pages=None, show_progress=False))
        except Exception:
            return None
    except Exception:
        return None


def chunk_pdf(
    pdf_path: Path,
    *,
    target_chars: int = 2000,
    hard_max_chars: int = 4000,
    min_chars: int = 200,
) -> Iterator[Chunk]:
    """Yield hierarchical, structure-preserving chunks from a PDF.

    Args:
        pdf_path: PDF file
        target_chars: soft size target (~500 tokens for Gemma/Llama)
        hard_max_chars: maximum before we force-split (~1000 tokens)
        min_chars: drop chunks smaller than this (usually orphan headings)
    """
    markdown = extract_markdown(pdf_path)
    if not markdown:
        return

    doc_id = pdf_path.stem
    sections = _split_headings(markdown)
    if not sections:
        sections = [([], markdown)]

    chunk_idx = 0
    for trail, body in sections:
        # Light cleaning inside the section (preserve newlines for tables)
        body = clean_text(body, preserve_newlines=True)
        if len(body) < min_chars:
            continue

        paragraphs = _split_paragraphs_keep_tables(body)
        chunk_texts = _recursive_chunk(
            paragraphs,
            target_chars=target_chars,
            hard_max_chars=hard_max_chars,
        )
        for text in chunk_texts:
            if len(text) < min_chars:
                continue
            yield Chunk(
                text=text,
                doc_id=doc_id,
                source=pdf_path.name,
                chunk_id=chunk_idx,
                heading_trail=list(trail),
            )
            chunk_idx += 1
