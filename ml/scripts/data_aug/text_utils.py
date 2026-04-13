"""Text normalisation, PII redaction, and hashing helpers.

Hard imports: ``re``, ``unicodedata``, ``hashlib`` (stdlib).
Optional: ``ftfy`` (mojibake repair). Degrades gracefully if missing.

PII redaction is regex-based by design: Presidio is heavy, introduces a
heavy spaCy dependency, and is not required for the types of identifiers
actually present in Ugandan tax data. Upgrade is straightforward if needed
(swap :func:`redact_pii` for a Presidio-backed version — signature is
stable).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

try:
    import ftfy

    _HAS_FTFY = True
except ImportError:  # pragma: no cover - optional
    _HAS_FTFY = False


# ---------------------------------------------------------------------------
# Unicode normalisation
# ---------------------------------------------------------------------------


def normalise_unicode(text: str) -> str:
    """Apply NFKC normalisation plus ftfy mojibake repair.

    NFKC collapses compatibility characters (ligatures, full-width Latin,
    non-breaking whitespace, etc.). ftfy repairs the classic
    ``â€œâ€"`` double-encoding damage frequently seen in CSV exports from
    Windows systems.

    The ``eng.lug.txt`` corpus in this repo ships with mojibake — this is
    the pass that fixes it.
    """
    if not text:
        return ""
    if _HAS_FTFY:
        text = ftfy.fix_text(text)
    text = unicodedata.normalize("NFKC", text)
    return text


# ---------------------------------------------------------------------------
# Whitespace / structural cleaning
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"[\t\r\f\v]+")
_MULTI_SPACE_RE = re.compile(r"[ ]{2,}")
_MULTI_NL_RE = re.compile(r"\n{3,}")
# Letter-spaced text: "T a x a t i o n" → "Taxation". Common artefact of
# PDF text extraction (title blocks, handbook covers). We only collapse
# runs of 6+ single characters to avoid destroying legitimate 2-letter
# acronyms.
_SPACED_LETTERS_RE = re.compile(r"(?:\b[A-Za-z]\s){5,}[A-Za-z]\b")
_DOT_LEADER_RE = re.compile(r"\s*\.{3,}\s*\d*\s*")  # TOC dot leaders


def _collapse_letter_spaced(text: str) -> str:
    """Undo letter-spaced words like 'T a x a t i o n'."""

    def _fix(m: re.Match[str]) -> str:
        return m.group(0).replace(" ", "")

    return _SPACED_LETTERS_RE.sub(_fix, text)


def clean_text(text: str, *, preserve_newlines: bool = False) -> str:
    """Normalise and lightly clean text without destroying structure.

    Args:
        text: raw input
        preserve_newlines: if True, keep paragraph breaks (for chunkers).
            Default False collapses to single-line for QA pairs.
    """
    if not text:
        return ""
    text = normalise_unicode(text)
    text = _collapse_letter_spaced(text)
    text = _DOT_LEADER_RE.sub(" ", text)  # drop TOC dot leaders
    text = _WS_RE.sub(" ", text)  # tabs/CR/FF → space

    if preserve_newlines:
        text = _MULTI_NL_RE.sub("\n\n", text)
        lines = [_MULTI_SPACE_RE.sub(" ", ln).strip() for ln in text.split("\n")]
        return "\n".join(ln for ln in lines if ln is not None).strip()

    text = text.replace("\n", " ")
    text = _MULTI_SPACE_RE.sub(" ", text)
    return text.strip()


# ---------------------------------------------------------------------------
# PII redaction
# ---------------------------------------------------------------------------
#
# Targets (in rough order of sensitivity):
#   - Ugandan TIN (10-digit numeric)
#   - Ugandan NIN (14-char alphanum)
#   - Phone numbers (Uganda +256, or 07xxxxxxxx)
#   - Email addresses
#   - URLs (not strictly PII but drops low-value boilerplate)
#   - 13+ digit sequences (card / account numbers)
#
# Placeholders are chosen to be distinctive so downstream filters can strip
# examples that became mostly placeholders.


@dataclass(frozen=True)
class RedactionReport:
    """Count of redactions per category. Used by quality filters to drop
    examples that are mostly redacted."""

    tin: int = 0
    nin: int = 0
    phone: int = 0
    email: int = 0
    url: int = 0
    account: int = 0

    @property
    def total(self) -> int:
        return self.tin + self.nin + self.phone + self.email + self.url + self.account


# Order matters: redact the most-specific patterns first (NIN before TIN
# before generic numerics), and redact emails before URLs before phone
# numbers so substrings don't cannibalise each other.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_URL_RE = re.compile(
    r"\bhttps?://[\w\-._~:/?#\[\]@!$&'()*+,;=%]+",
    flags=re.IGNORECASE,
)
_NIN_RE = re.compile(r"\b[A-Z]{2}\d{11}[A-Z0-9]\b")  # 14-char Uganda NIN
_TIN_RE = re.compile(r"(?<!\d)(10\d{8})(?!\d)")  # Uganda TINs start 10 and are 10 digits
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?256\s?7\d{8}|0?7\d{8})(?!\d)",
)
_ACCOUNT_RE = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def redact_pii(text: str) -> tuple[str, RedactionReport]:
    """Redact the PII categories listed above. Returns (redacted, report)."""
    if not text:
        return "", RedactionReport()

    counts = {"email": 0, "url": 0, "nin": 0, "tin": 0, "phone": 0, "account": 0}

    def _mk(placeholder: str, key: str):
        def _sub(_m: re.Match[str]) -> str:
            counts[key] += 1
            return placeholder

        return _sub

    text = _EMAIL_RE.sub(_mk("[EMAIL]", "email"), text)
    text = _URL_RE.sub(_mk("[URL]", "url"), text)
    text = _NIN_RE.sub(_mk("[NIN]", "nin"), text)
    text = _TIN_RE.sub(_mk("[TIN]", "tin"), text)
    text = _PHONE_RE.sub(_mk("[PHONE]", "phone"), text)
    text = _ACCOUNT_RE.sub(_mk("[ACCOUNT]", "account"), text)

    return text, RedactionReport(**counts)


# ---------------------------------------------------------------------------
# Heuristic quality signals
# ---------------------------------------------------------------------------


def repetition_ratio(text: str, n: int = 4) -> float:
    """Fraction of ``n``-grams that are exact duplicates of a previous n-gram.

    High values indicate looping or boilerplate (e.g. repeated dot leaders,
    "Click here Click here"). Used in the quality stage to drop degenerate
    chunks. Always returns a value in [0, 1].
    """
    words = text.split()
    if len(words) < n + 1:
        return 0.0
    grams = [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]
    unique = len(set(grams))
    return 1.0 - (unique / len(grams))


def symbol_ratio(text: str) -> float:
    """Fraction of non-alphanumeric, non-space characters."""
    if not text:
        return 0.0
    alnum_or_space = sum(1 for c in text if c.isalnum() or c.isspace())
    return 1.0 - (alnum_or_space / len(text))


# ---------------------------------------------------------------------------
# Content hashing
# ---------------------------------------------------------------------------


def file_sha256(path: str) -> str:
    """Streaming SHA-256 of a file. Used in the provenance manifest."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
