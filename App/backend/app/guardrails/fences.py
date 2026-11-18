"""Spotlight fences: retrieved, uploaded and header text stays data in every prompt (ADR-0027)."""

from __future__ import annotations

import re
import unicodedata
from .input_checks import (
    _PASSAGE_MARKER_REMOVED,
    _PASSAGE_TAG_RE,
    _PROMPT_SECTION_HEADING_RE,
    _record_guardrail_security_event,
)
from .normalize import _strip_invisible_chars


def fence_spotlight_body(text: str) -> str:
    """Keep untrusted text from closing or forging a ``<passage>`` fence.

    Invisible characters are removed first, so a zero-width break cannot hide
    the tag from this check while the model still reads it. Fullwidth
    brackets are folded only when one is present. A prompt section heading
    the assembler itself emits (``## User question``, ``## Rules``) is
    demoted; a statutory heading such as ``## Rules of origin`` is left as
    written. Returns the fenced text. Records one indirect-injection event
    when a tag or section heading was rewritten.
    """
    if not text:
        return ""
    cleaned = _strip_invisible_chars(text)
    if "\uff1c" in cleaned or "\uff1e" in cleaned:
        cleaned = _strip_invisible_chars(unicodedata.normalize("NFKC", cleaned))
    before = cleaned
    cleaned = _PASSAGE_TAG_RE.sub(_PASSAGE_MARKER_REMOVED, cleaned)
    cleaned = _PROMPT_SECTION_HEADING_RE.sub(r"[section] \1", cleaned)
    if cleaned != before:
        _record_guardrail_security_event(
            "guardrail_indirect_injection",
            reason="spotlight_breakout",
        )
    return cleaned


#: Any spelling of the document fence a model would still read as the tag:
#: spaced, with attributes, a stray slash, or ``-``/space for ``_`` (G463).
_UNTRUSTED_DOC_TAG_RE = re.compile(
    r"<(?:[ \t]*/[ \t]*|[ \t]*)untrusted[ _-]?user[ _-]?document\b[^>\n]{0,120}>?",
    re.IGNORECASE,
)


_DOC_BOUNDARY_REMOVED = "[REDACTED_BOUNDARY_TAG]"


def fence_untrusted_document_body(text: str) -> str:
    """Keep a taxpayer's upload from closing ``<untrusted_user_document>`` or forging a passage.

    The document fence used to match the closing tag only when typed exactly:
    a zero-width character, fullwidth brackets, a space after ``<`` or an
    attribute got it through, and nothing stopped an upload writing
    ``<passage>`` markers or ``## Rules`` headings. It now goes through
    :func:`fence_spotlight_body` first, so both fences share one
    normalization (ADR-0027, ADR-0029).
    """
    if not text:
        return ""
    fenced = fence_spotlight_body(text)
    rewritten = _UNTRUSTED_DOC_TAG_RE.sub(_DOC_BOUNDARY_REMOVED, fenced)
    if rewritten != fenced:
        _record_guardrail_security_event("guardrail_indirect_injection", reason="document_breakout")
    return rewritten


def fence_prompt_header_field(value: object, *, limit: int = 180) -> str:
    """One single-line header field that cannot open a new prompt section.

    Passage headers sit outside the spotlight. A source, title, or tool name
    with a newline, a ``#`` heading, or a passage tag would be read as
    structure rather than as a label.
    """
    text = _strip_invisible_chars(str(value or ""))
    if "\uff1c" in text or "\uff1e" in text:
        text = _strip_invisible_chars(unicodedata.normalize("NFKC", text))
    text = text.replace("\r", " ").replace("\n", " ").replace("#", "")
    text = _PASSAGE_TAG_RE.sub(" ", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit].rstrip()
    return text
