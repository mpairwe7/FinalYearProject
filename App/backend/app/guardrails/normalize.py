"""Unicode canonicalisation the input and output checks share, and the package logger."""

from __future__ import annotations

import logging
import re
import unicodedata


logger = logging.getLogger("app.guardrails")


# ---------------------------------------------------------------------------
# Unicode canonicalization (CVE-mitigation: homoglyph & zero-width evasion)
# ---------------------------------------------------------------------------
# Zero-width, soft-hyphen, Bidi-override, and BMP variation-selector characters used in prompt-injection obfuscation
_ZERO_WIDTH_CHARS = re.compile(
    "[\u00ad\u061c\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060-\u2069\ufeff\ufe00-\ufe0f]"
)


def _strip_invisible_chars(text: str) -> str:
    """Strip BMP zero-width/Bidi/variation characters and Plane-14 Tag/Variation-Selector codepoints."""
    cleaned = _ZERO_WIDTH_CHARS.sub("", text) if _ZERO_WIDTH_CHARS.search(text) else text
    if any(0xE0000 <= ord(ch) <= 0xE01EF for ch in cleaned):
        cleaned = "".join(ch for ch in cleaned if not (0xE0000 <= ord(ch) <= 0xE01EF))
    return cleaned


def _decode_unicode_tags(text: str) -> str:
    """Decode Unicode Plane-14 Tag characters (U+E0020..U+E007E) into visible ASCII.

    Adversaries smuggle invisible instructions using Unicode Tag mirrors of
    ASCII characters (MITRE ATLAS AML.T0051 / OWASP LLM01). Decoding them
    before canonicalization exposes the hidden payload to regex inspection.
    """
    if not any(0xE0000 <= ord(ch) <= 0xE007F for ch in text):
        return text
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xE0020 <= code <= 0xE007E:
            out.append(chr(code - 0xE0000))
        elif 0xE0000 <= code <= 0xE007F:
            continue
        else:
            out.append(ch)
    return "".join(out)


# Cyrillic / Greek / mathematical lookalikes → Latin equivalents.
# Only the characters most commonly abused in injection obfuscation.
_CONFUSABLE_MAP = str.maketrans({
    # Cyrillic lowercase
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p",
    "\u0441": "c", "\u0443": "y", "\u0456": "i", "\u0445": "x",
    "\u044a": "b", "\u0455": "s", "\u0458": "j", "\u0491": "r",
    # Cyrillic uppercase
    "\u0410": "A", "\u0412": "B", "\u0415": "E", "\u041a": "K",
    "\u041c": "M", "\u041d": "H", "\u041e": "O", "\u0420": "P",
    "\u0421": "C", "\u0422": "T", "\u0425": "X",
    # Greek lowercase
    "\u03b1": "a", "\u03b5": "e", "\u03bf": "o", "\u03c1": "p",
    "\u03b9": "i", "\u03ba": "k",
})


_BIDI_RLO_SEGMENT_RE = re.compile("\u202e([^\u202c\n\r]*)[\u202c]?")


def _unwrap_bidi_overrides(text: str) -> str:
    """Reverse segments governed by Right-to-Left Override (U+202E) before stripping Bidi marks.

    Adversaries wrap reversed injection strings in U+202E..U+202C (e.g.
    ``\\u202esnoitcurtsni suoiverp lla erongi\\u202c``) so Bidi-aware parsers
    read the forward instruction while naive regexes see reversed text
    (arXiv:2504.11168 / OWASP Prompt Injection Prevention Cheat Sheet).
    """
    if "\u202e" not in text:
        return text
    unwrapped = _BIDI_RLO_SEGMENT_RE.sub(lambda m: m.group(1)[::-1], text)
    return f"{text} {unwrapped}"


def _canonicalize_text(text: str) -> str:
    """Normalize unicode to defeat homoglyph, Bidi-override, and zero-width evasion.

    NFKD decomposition converts visually similar characters (fullwidth
    Ａ → A) to their ASCII-compatible base forms.  Zero-width joiners,
    non-joiners, Bidi overrides, Unicode tags, and BOM markers are decoded/stripped
    so that injected invisible characters cannot break regex token boundaries.

    A confusable-character table maps the most common Cyrillic, Greek, and
    mathematical lookalikes to their Latin equivalents.  A final ASCII
    encode-with-ignore step drops anything that still isn't ASCII; the
    regex then matches on the remaining skeleton.  This is intentionally
    lossy — the canonical form is only used for pattern matching, never
    displayed.
    """
    text = _decode_unicode_tags(text)
    text = _unwrap_bidi_overrides(text)
    text = _strip_invisible_chars(text)
    text = unicodedata.normalize("NFKD", text)
    # Drop combining marks so accented variants collapse (é → e)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Map common confusable homoglyphs to Latin equivalents
    text = text.translate(_CONFUSABLE_MAP)
    # Drop remaining non-ASCII after mapping
    try:
        text = text.encode("ascii", "ignore").decode("ascii")
    except (UnicodeDecodeError, UnicodeEncodeError):
        pass
    return text
