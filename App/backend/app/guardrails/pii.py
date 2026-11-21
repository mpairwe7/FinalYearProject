"""Ugandan personal data: patterns, redaction, stated identifiers and the streaming DLP filter (LLM02)."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..linear_text import email_spans

if TYPE_CHECKING:
    from collections.abc import Iterator


STORE_RAW_PROMPTS = os.getenv("STORE_RAW_PROMPTS", "false").lower() == "true"


#: At most one space, no-break space (U+00A0, U+202F) or hyphen between the parts of a grouped mobile number.
_SEP = r"[ \u00a0\u202f-]?"
_MOBILE_START = r"(?<![\w+.,-])"

# Uganda-specific PII patterns (LLM02)
# NOTE: Phone pattern uses (?:^|\s|[^\w]) instead of \b before +256
#       because \b requires a word-char boundary and '+' is not a word char.
_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("ug_phone", re.compile(r"(?:^|(?<=\s))(?:\+256|0)(?:7[0-9]{8}|4[0-9]{8})\b")),
    # A mobile number in groups (G658): one optional _SEP after +256 and between
    # 7XX, XXX and XXX, as the repository writes them; none after a leading 0,
    # so a spaced amount starting "0 772" is not one. It may follow punctuation
    # ("Tel:", "(", "/") but not a word, "+", or the ".", "," or "-" that join
    # figures and references. Grouped landlines are left out because every one
    # in App/Data is a URA office line, and URA's WhatsApp line, which ends
    # every contact footer, is not personal data.
    (
        "ug_phone",
        re.compile(
            rf"{_MOBILE_START}(?!(?:\+256{_SEP}|0)772{_SEP}140{_SEP}000\b)"
            rf"(?:\+256{_SEP}|0)7\d\d{_SEP}\d\d\d{_SEP}\d\d\d\b"
        ),
    ),
    # Standard contiguous TIN, context-bound TIN, or spaced/hyphenated variants
    ("ug_tin", re.compile(r"(?i)(?:\bTIN\s*(?:[:#-]\s*)?|\bTax Identification Number\s*(?:[:#-]\s*)?)(1\d{9})\b|\b1\d{2}[\s-]\d{3}[-\s]\d{4}\b|\b1\d{9}\b")),
    # Ugandan National Identification Number: 14 characters, C + M/F (or R) +
    # 12 letters and digits, the shape vision.ocr.extract_nin_numbers reads.
    # NIRA does not publish the internal layout, so none is assumed. The former
    # pattern assumed one with a fixed 15-character length and so never matched
    # a real NIN (G247). 13-15 characters, so a NIN typed with one character
    # dropped or doubled is still hidden; at least four digits, so a word such
    # as "cryptocurrency" is not taken for one; case-insensitive, as typed.
    # Also covers hyphenated ("CM93-003918-2LIR") and space-grouped
    # ("CM93 0039 182LIR", "CM 93 0039182 LIR") variants via _is_valid_ug_nid_match.
    ("ug_nid", re.compile(r"(?i)\bC[MFR](?:(?=[0-9A-Z]*\d)[0-9A-Z]{12}|(?=(?:[A-Z]*\d){4})[0-9A-Z]{11,13})\b")),
    (
        "ug_nid",
        re.compile(
            r"(?i)\bC[MFR][0-9A-Z]{0,6}(?:-[0-9A-Z]{1,8}){1,4}\b|"
            r"\bC[MFR][0-9A-Z]{0,4}(?:[ \t]+(?:(?=[0-9A-Z]*\d)[0-9A-Z]{1,8}|(?-i:[A-Z]{1,5}))){1,4}\b"
        ),
    ),
    ("credit_card", re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b")),
    ("amex_card", re.compile(r"\b3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}\b")),
    ("ug_passport", re.compile(r"\b[A-Z]{2}\d{7}\b")),
]


def is_official_ura_email(email: str) -> bool:
    """Return True if email is an official public contact address for URA or Uganda gov."""
    addr = email.strip().strip(".,;:\"'").lower()
    _, sep, domain = addr.rpartition("@")
    if not sep or not domain:
        return False
    return domain in {"ura.go.ug", "go.ug"} or domain.endswith((".ura.go.ug", ".go.ug"))


def email_address_spans(text: str) -> Iterator[tuple[int, int]]:
    """The spans ``re.finditer(r"\\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\\b", text)`` gives (G715)."""
    return email_spans(text, r"[A-Za-z0-9._%+-]", r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


_CURRENCY_INDICATORS_BEFORE = re.compile(r"(?i)\b(?:ugx|ush|shs|usd|eur|gbp|\$|€|£)\s*$")


_CURRENCY_INDICATORS_AFTER = re.compile(r"(?i)^\s*(?:ugx|ush|shs|usd|shillings?|/=|per\b|annum\b|month\b|year\b)")


def _is_currency_boundary(match: re.Match[str], text: str) -> bool:
    """Return True if 10-digit number is surrounded by currency markers (for example 1,000,000,000 UGX)."""
    matched = match.group(0)
    if re.search(r"(?i)\btin\b", matched) or "-" in matched or " " in matched:
        return False
    start, end = match.span()
    before = text[max(0, start - 20) : start]
    after = text[end : min(len(text), end + 20)]
    return bool(_CURRENCY_INDICATORS_BEFORE.search(before) or _CURRENCY_INDICATORS_AFTER.search(after))


_HIDDEN_PII_EVASION_RE = re.compile(
    r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f"
    r"\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2060-\u2069\ufeff\ufe00-\ufe0f]"
)


def _strip_hidden_pii_evasion(text: str) -> str:
    """Remove control, zero-width, Bidi, and supplementary-plane Tag/Variation characters before PII scanning."""
    cleaned = _HIDDEN_PII_EVASION_RE.sub("", text) if _HIDDEN_PII_EVASION_RE.search(text) else text
    if any(0xE0000 <= ord(ch) <= 0xE01EF for ch in cleaned):
        cleaned = "".join(ch for ch in cleaned if not (0xE0000 <= ord(ch) <= 0xE01EF))
    return cleaned


_ENGLISH_STOP_TOKENS = frozenset(
    {
        "A", "AN", "AND", "ARE", "AS", "AT", "BE", "BY", "DO", "FOR", "FROM",
        "HAD", "HAS", "HAVE", "HE", "HER", "HIM", "HIS", "IF", "IN", "INTO",
        "IS", "IT", "ITS", "MY", "NO", "NOT", "OF", "ON", "OR", "OUR", "OUT",
        "SHE", "SO", "THE", "TO", "UP", "US", "WAS", "WE", "WHO", "WHY",
        "WITH", "YOU", "YOUR",
    }
)


def _is_valid_ug_nid_match(val: str) -> bool:
    """Validate contiguous, hyphenated, or space-grouped Ugandan NIN candidates."""
    if "-" not in val and " " not in val:
        return True
    alnum = re.sub(r"[- ]", "", val)
    if not (13 <= len(alnum) <= 15):
        return False
    if sum(c.isdigit() for c in alnum) < 4:
        return False
    if " " in val:
        parts = val.split()
        for part in parts[1:]:
            if not any(c.isdigit() for c in part) and part.upper() in _ENGLISH_STOP_TOKENS:
                return False
    return True


def redact_pii_text(
    text: str,
    allowed_values: Iterable[str] | None = None,
    *,
    kinds: frozenset[str] | None = None,
) -> str:
    """Replace detected PII with redaction markers.

    Shared utility used by both OutputGuard (response side) and
    database writes (storage side) to prevent PII persistence.
    Public official URA/government contact addresses (e.g. services@ura.go.ug,
    info@ura.go.ug) are exempt so taxpayers receive valid contact channels.
    Taxpayer-stated identifiers (in *allowed_values*) are exempted from
    output redaction so the assistant does not redact the user's own input back to them (G143).
    *kinds* limits redaction to those pattern names, for callers that must keep
    the rest: a crawled URA page carries the helpline in the ``ug_phone`` shape.
    """
    if not text:
        return text
    allowed_set = {str(v).strip() for v in (allowed_values or []) if str(v).strip()}
    result = _strip_hidden_pii_evasion(text)
    if kinds is None or "email" in kinds:
        parts: list[str] = []
        kept = 0
        for start, end in email_address_spans(result):
            addr = result[start:end]
            if not (is_official_ura_email(addr) or addr in allowed_set):
                parts += (result[kept:start], "[REDACTED_EMAIL]")
                kept = end
        result = "".join(parts) + result[kept:]
    for pii_type, pattern in _PII_PATTERNS:
        if kinds is not None and pii_type not in kinds:
            continue
        if pii_type == "ug_tin":
            def _replace_tin(m: re.Match[str]) -> str:
                val = m.group(0)
                if _is_currency_boundary(m, result):
                    return val
                raw_digits = re.sub(r"\D", "", val)
                if raw_digits in allowed_set or val in allowed_set:
                    return val
                return "[REDACTED_UG_TIN]"

            result = pattern.sub(_replace_tin, result)
        elif pii_type == "ug_nid":
            def _replace_nid(m: re.Match[str]) -> str:
                val = m.group(0)
                if not _is_valid_ug_nid_match(val):
                    return val
                return "[REDACTED_UG_NID]"

            result = pattern.sub(_replace_nid, result)
        else:
            result = pattern.sub(f"[REDACTED_{pii_type.upper()}]", result)
    return result


_NUMERIC_PII_TYPES = frozenset({"ug_tin", "ug_phone", "credit_card", "amex_card"})


def _pii_matches(text: str) -> Iterator[tuple[str, str]]:
    """``(type, value)`` for each identifier in *text*: the one definition of what counts (G491).

    Official URA addresses, amounts that look like a TIN and NIN-shaped strings
    that fail validation are not identifiers. :func:`contains_pii` and
    :func:`pii_values` both read this, so the output guard and the rewriter's
    identifier check cannot disagree about what an identifier is.
    """
    if not text:
        return
    cleaned = _strip_hidden_pii_evasion(text)
    for start, end in email_address_spans(cleaned):
        if not is_official_ura_email(cleaned[start:end]):
            yield "email", cleaned[start:end]
    for pii_type, pattern in _PII_PATTERNS:
        for match in pattern.finditer(cleaned):
            value = match.group(0)
            if pii_type == "ug_tin" and _is_currency_boundary(match, cleaned):
                continue
            if pii_type == "ug_nid" and not _is_valid_ug_nid_match(value):
                continue
            yield pii_type, value


def pii_values(text: str) -> set[str]:
    """Each identifier in *text*, normalised (digits only for numbers, upper-case otherwise)."""
    return {
        re.sub(r"\D", "", value) if pii_type in _NUMERIC_PII_TYPES else re.sub(r"[\s-]", "", value).upper()
        for pii_type, value in _pii_matches(text)
    }


def contains_pii(text: str) -> bool:
    """Return True if *text* contains any PII pattern (excluding official public URA emails)."""
    return next(_pii_matches(text), None) is not None


#: A TIN (10 digits from 1) or a PRN (12-14 digits from 2) typed by the taxpayer.
_USER_STATED_ID_RE = re.compile(r"\b1\d{9}\b|\b2\d{11,13}\b")


#: The redactor's own TIN pattern, so every format it would hide — prefixed
#: ("TIN: …"), hyphenated or spaced in groups of 3-3-4 — is also one a taxpayer
#: can state.
_UG_TIN_RE = next(pattern for kind, pattern in _PII_PATTERNS if kind == "ug_tin")


def user_stated_identifiers(message: str) -> tuple[str, ...]:
    """The TINs and PRNs the taxpayer wrote in *message*, as digits (G143).

    Echoing these back is not a disclosure — the taxpayer just typed them — and
    redacting them made a lookup they asked for unreadable ("EFRIS holds … for
    TIN [REDACTED_UG_TIN]"). A spaced or hyphenated TIN is normalised to its
    digits, which is how the redactor compares it. Everything else stays redacted.
    """
    text = message or ""
    found: list[str] = []
    for value in _USER_STATED_ID_RE.findall(text):
        if value not in found:
            found.append(value)
    for match in _UG_TIN_RE.finditer(text):
        digits = re.sub(r"\D", "", match.group(0))
        if digits and digits not in found:
            found.append(digits)
    return tuple(found)


_STREAM_HOLD_TAIL_RE = re.compile(
    r"(?i)(?:"
    r"\bC[MFR][0-9A-Z -]{0,15}|"
    r"(?<!\d[ \u00a0\u202f-])\b1\d{2}(?:[ -]\d{3})?|"
    r"(?<!\d[ \u00a0\u202f-])\b\d{4}(?:[ -]\d{4}){0,2}|"
    r"(?<!\d[ \u00a0\u202f-])\b3[47]\d{2}(?:[ -]\d{6})?|"
    rf"{_MOBILE_START}(?:\+256(?:{_SEP}7\d\d(?:{_SEP}\d\d\d)?)?|07\d\d(?:{_SEP}\d\d\d)?)|"
    r"\b(?:TIN|Tax[ \t]{1,4}Identification[ \t]{1,4}Number)[ \t:#-]{0,4}"
    r")[ \t]{1,8}$"
)


_STREAM_CONTIGUOUS_TAIL_RE = re.compile(
    r"(?i)(?:\bC[MFR][0-9A-Z-]{0,14}|\b[A-Z]{2}\d{1,7}|\+\d[\d\u00a0\u202f-]{0,14}|\b\d[\d\u00a0\u202f-]{0,18})$"
)


class StreamingDLPFilter:
    """Zero-latency streaming Data Loss Prevention (DLP) filter.

    Buffers a trailing boundary window across chunks so multi-character sensitive
    tokens (14-char Ugandan NINs, 10-digit TINs, phone numbers, credit card sequences)
    that split across streaming token chunk boundaries are caught and sanitized
    without introducing user-perceptible latency.

    *allowed_values* are the identifiers the taxpayer stated this turn
    (:func:`user_stated_identifiers`); the stream used to redact them even
    after the non-streamed paths stopped doing so (G143, G154).
    """

    def __init__(self, buffer_size: int = 24, allowed_values: Iterable[str] = ()) -> None:
        self._buffer_size = buffer_size
        self._buffer = ""
        self._allowed = tuple(allowed_values)

    def process_chunk(self, chunk: str) -> str:
        """Feed a new chunk of streamed text; returns sanitized text ready to yield."""
        if not chunk:
            return ""
        self._buffer += chunk
        # If there is whitespace or punctuation boundary, flush safe complete words immediately
        last_boundary = max(self._buffer.rfind(" "), self._buffer.rfind("\n"), self._buffer.rfind("\t"))
        if last_boundary != -1:
            to_sanitize = self._buffer[:last_boundary + 1]
            hold_match = _STREAM_HOLD_TAIL_RE.search(to_sanitize)
            if (
                hold_match is not None
                and len(self._buffer) <= self._buffer_size * 3
                and not contains_pii(to_sanitize[hold_match.start() :])
            ):
                safe_prefix = to_sanitize[: hold_match.start()]
                self._buffer = self._buffer[hold_match.start() :]
                return redact_pii_text(safe_prefix, self._allowed) if safe_prefix else ""
            sanitized = redact_pii_text(to_sanitize, self._allowed)
            self._buffer = self._buffer[last_boundary + 1:]
            return sanitized
        if len(self._buffer) > self._buffer_size:
            tail_match = _STREAM_CONTIGUOUS_TAIL_RE.search(self._buffer)
            if tail_match is not None and tail_match.start() > 0:
                safe_prefix = self._buffer[: tail_match.start()]
                self._buffer = self._buffer[tail_match.start() :]
                return redact_pii_text(safe_prefix, self._allowed)
            flushable_len = len(self._buffer) - self._buffer_size
            sanitized_full = redact_pii_text(self._buffer, self._allowed)
            emit = sanitized_full[:flushable_len]
            self._buffer = sanitized_full[flushable_len:]
            return emit
        return ""

    def flush(self) -> str:
        """Flush any remaining buffered text at the end of the stream."""
        if not self._buffer:
            return ""
        remaining = redact_pii_text(self._buffer, self._allowed)
        self._buffer = ""
        return remaining


#: A figure as far as the release gate is concerned: any digit, or a number in
#: words. "one" is left out because it is everywhere in ordinary prose. Holding on
#: a false match costs latency; missing a figure shows it unverified.
_RELEASE_FIGURE_RE = re.compile(
    r"\d|\b(?:percent|per\s+cent|hundred|thousand|million|billion|trillion|"
    r"two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety)\b",
    re.IGNORECASE,
)


_RELEASE_CITATION_RE = re.compile(r"\[\d{1,3}\]")


_RELEASE_SENTENCE_END_RE = re.compile(r"[.!?][\"')\]]*\s+|\n+")
