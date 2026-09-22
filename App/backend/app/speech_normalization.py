"""Speech text normalization for URA taxpayer chatbot (2026).

Prepares assistant reply text for speech synthesis (TTS) across all backends
(Spark-TTS-SALT, Edge-TTS, Sunbird, Piper).

Transforms written statutory text, tax jargon, numbers, and markdown
into natural spoken language:
- Strips citation markers [1], [1, 2] so the narrator does not read them mid-sentence
- Strips raw markdown syntax (headings, bold, italics, tables, code blocks, links)
- Expands Ugandan tax acronyms (EFRIS, URA, PAYE, WHT, VAT) for clear phonetics
- Spells out 10-digit URA Taxpayer Identification Numbers (TINs) digit by digit
- Normalizes currency amounts (UGX 50,000,000 -> 50 million Uganda shillings)
- Converts percentage figures (18% -> 18 percent / ebitundu 18 ku buli kikumi)
- Expands legal references (Sec. 118A -> Section 118A)
"""

from __future__ import annotations

import re

# Citation markers: [1], [1, 2], [1; 3], ignoring markdown links [1](url)
_CITATION_RE = re.compile(r"\s*\[\d+(?:\s*[,;]\s*\d+)*\](?!\()")

# Markdown links: [Title](url) -> Title
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")

# Code blocks and inline code
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# Markdown tables (lines starting with | or containing multiple |)
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE)
_TABLE_PIPE_RE = re.compile(r"\s*\|\s*")

# Headers and list bullets
_HEADER_RE = re.compile(r"^\s*#{1,6}\s+", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)

# Markdown bold / italics
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"\*([^*]+)\*|_([^_]+)_")

# URA Taxpayer Identification Number (9 or 10 digits)
_TIN_EXPLICIT_RE = re.compile(
    r"\bTIN(?::|\s+is|\s+number|\s+no\.?)?\s*([1-9]\d{8,9})\b", re.IGNORECASE
)

# Payment Registration Number (PRN)
_PRN_EXPLICIT_RE = re.compile(
    r"\bPRN(?::|\s+is|\s+number|\s+no\.?)?\s*(\d{10,14})\b", re.IGNORECASE
)

# Toll-free and customer care numbers
_TOLLFREE_URA_RE = re.compile(r"\b0800\s*([12]17)\s*000\b")
_GENERAL_TOLLFREE_RE = re.compile(r"\b0800\s*(\d{3})\s*(\d{3})\b")

# Numbered steps for cadence and pacing
_NUMBERED_STEP_RE = re.compile(r"^\s*(\d+)\.\s+", re.MULTILINE)

# Currency amounts
_UGX_RE = re.compile(r"\bUGX\s*(\d+(?:,\d{3})*(?:\.\d+)?)\b", re.IGNORECASE)
_USD_RE = re.compile(r"\bUSD\s*(\d+(?:,\d{3})*(?:\.\d+)?)\b", re.IGNORECASE)

# Percentages
_PCT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)

# Legal citations
_SEC_RE = re.compile(r"\bSec(?:tion)?\.?\s*(\d+[A-Za-z]?)\b", re.IGNORECASE)

# Acronyms and institutional terms in Ugandan tax context
_ACRONYMS = [
    (re.compile(r"\bEFRIS\b"), "E-F-R-I-S"),
    (re.compile(r"\bURA\b"), "U-R-A"),
    (re.compile(r"\bPAYE\b"), "P-A-Y-E"),
    (re.compile(r"\bWHT\b"), "Withholding Tax"),
    (re.compile(r"\bVAT\b"), "V-A-T"),
    (re.compile(r"\bCIT\b"), "C-I-T"),
    (re.compile(r"\bPIT\b"), "P-I-T"),
    (re.compile(r"\bLED\b"), "Local Excise Duty"),
    (re.compile(r"\bDTS\b"), "Digital Tax Stamps"),
    (re.compile(r"\bNSSF\b"), "N-S-S-F"),
    (re.compile(r"\bLST\b"), "Local Service Tax"),
    (re.compile(r"\bURSB\b"), "U-R-S-B"),
    (re.compile(r"\bBOU\b"), "Bank of Uganda"),
    (re.compile(r"\bTAT\b"), "Tax Appeals Tribunal"),
    (re.compile(r"\bTPCA\b"), "Tax Procedures Code Act"),
    (re.compile(r"\bITA\b"), "Income Tax Act"),
    (re.compile(r"\bVATA\b"), "V-A-T Act"),
    (re.compile(r"\bPRN\b"), "P-R-N"),
    (re.compile(r"\bTIN\b"), "T-I-N"),
    (re.compile(r"\bNIN\b"), "N-I-N"),
    (re.compile(r"\bBRN\b"), "B-R-N"),
    (re.compile(r"\be-Receipt\b", re.IGNORECASE), "E-Receipt"),
    (re.compile(r"\be-Invoice\b", re.IGNORECASE), "E-Invoice"),
    (re.compile(r"\bDT-(\d{3,4})\b"), r"D-T \1"),
]


def _format_ugx_amount(amount_str: str, locale: str) -> str:
    """Format a UGX currency figure into speech-friendly words."""
    clean_num = amount_str.replace(",", "")
    try:
        val = float(clean_num)
    except ValueError:
        return f"{amount_str} Uganda shillings"

    if locale == "lg":
        if val >= 1_000_000 and val % 1_000_000 == 0:
            return f"shilingi obukadde {int(val // 1_000_000)}"
        return f"shilingi za Yuganda {amount_str}"

    if locale == "sw":
        if val >= 1_000_000 and val % 1_000_000 == 0:
            return f"shilingi milioni {int(val // 1_000_000)} za Uganda"
        return f"shilingi za Uganda {amount_str}"

    # English default
    if val >= 1_000_000_000 and val % 1_000_000_000 == 0:
        return f"{int(val // 1_000_000_000)} billion Uganda shillings"
    if val >= 1_000_000 and val % 1_000_000 == 0:
        return f"{int(val // 1_000_000)} million Uganda shillings"
    if val >= 1_000 and val % 1_000 == 0 and val < 100_000:
        return f"{int(val // 1_000)} thousand Uganda shillings"

    return f"{amount_str} Uganda shillings"


def clean_text_for_speech(text: str, locale: str = "en") -> str:
    """Pre-process text for natural, audible TTS synthesis in the tax domain.

    Args:
        text: Raw text or Markdown response from LLM / ChatModel.
        locale: Target language code ('en', 'lg', 'sw', 'nyn', 'ach').

    Returns:
        A cleaned, punctuation-normalised string ready for speech synthesis.
    """
    if not text:
        return ""

    t = text

    # 1. Strip markdown links: [e-Services Portal](https://...) -> e-Services Portal
    t = _MD_LINK_RE.sub(r"\1", t)

    # 2. Strip inline citation markers: [1], [1, 2]
    t = _CITATION_RE.sub("", t)

    # 3. Strip code blocks and inline code
    t = _CODE_BLOCK_RE.sub(" ", t)
    t = _INLINE_CODE_RE.sub(r"\1", t)

    # 4. Handle table rows: replace pipes with commas/spaces
    t = _TABLE_PIPE_RE.sub(", ", t)

    # 5. Strip Markdown headers and bullet points
    t = _HEADER_RE.sub("", t)
    t = _BULLET_RE.sub("", t)

    # 6. Strip bold / italic markers
    t = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2) or "", t)
    t = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2) or "", t)

    # 7. Normalize 9-digit or 10-digit TINs (spell out digits clearly with pause)
    def _tin_sub(m: re.Match) -> str:
        digits = " ".join(list(m.group(1)))
        return f"T I N {digits}"

    t = _TIN_EXPLICIT_RE.sub(_tin_sub, t)

    # 7b. Normalize PRNs (spell out digits with pause)
    def _prn_sub(m: re.Match) -> str:
        digits = " ".join(list(m.group(1)))
        return f"P R N {digits}"

    t = _PRN_EXPLICIT_RE.sub(_prn_sub, t)

    # 7c. Normalize URA toll-free contact numbers for cadence
    t = _TOLLFREE_URA_RE.sub(r"0 800, \1, 0 0 0", t)
    t = _GENERAL_TOLLFREE_RE.sub(r"0 800, \1, \2", t)

    # 7d. Paced numbered steps for structured guidance
    if locale == "lg":
        t = _NUMBERED_STEP_RE.sub(r"Odaala \1: ", t)
    elif locale == "sw":
        t = _NUMBERED_STEP_RE.sub(r"Hatua \1: ", t)
    else:
        t = _NUMBERED_STEP_RE.sub(r"Step \1: ", t)

    # 8. Currency amounts (UGX and USD)
    t = _UGX_RE.sub(lambda m: _format_ugx_amount(m.group(1), locale), t)
    t = _USD_RE.sub(r"\1 US dollars", t)

    # 9. Percentages
    if locale == "lg":
        t = _PCT_RE.sub(r"ebitundu \1 ku buli kikumi", t)
    elif locale == "sw":
        t = _PCT_RE.sub(r"asilimia \1", t)
    else:
        t = _PCT_RE.sub(r"\1 percent", t)

    # 10. Legal section references: Sec. 118A -> Section 118A
    t = _SEC_RE.sub(r"Section \1", t)

    # 11. Domain acronym expansion for phonetics
    for pattern, replacement in _ACRONYMS:
        t = pattern.sub(replacement, t)

    # 12. Punctuation and whitespace hygiene
    # Remove dangling symbols: *, ~, >, #, |, ^
    t = re.sub(r"[*~>#|^]", "", t)
    # Collapse multiple commas or colons: ", ," or ": :"
    t = re.sub(r"[,;]\s*[,;]+", ",", t)
    t = re.sub(r":\s*:+", ":", t)
    t = re.sub(r"\([ \t]*\)", "", t)
    # Collapse repeated whitespace
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n+", "\n\n", t)

    return t.strip()
