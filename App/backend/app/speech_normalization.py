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
- Converts percentage figures (18% -> 18 percent / ebitundu kkumi na munaana ku buli kikumi)
- Says Luganda and Swahili figures as words, which Orpheus reads and digits it
  does not (``number_words``)
- Expands legal references (Sec. 118A -> Section 118A)
"""

from __future__ import annotations

import re

from .number_words import decimal_words, en_words, en_year, lg_words, sw_words

# Citation markers: [1], [1, 2], [1; 3], ignoring markdown links [1](url)
_CITATION_RE = re.compile(r"\s*\[\d+(?:\s*[,;]\s*\d+)*\](?!\()")

# Markdown links: [Title](url) -> Title
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
# A model sometimes emits the link without the opening bracket:
# "www.ura.go.ug](http://www.ura.go.ug/)". Keep the visible label, drop the target.
_BROKEN_MD_LINK_RE = re.compile(r"([^\s\]]+)\]\((?:https?://|www\.)[^)\s]*\)")
_DANGLING_URL_TAIL_RE = re.compile(r"\]\((?:https?://|www\.)[^)\s]*\)?")
_BARE_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+", re.IGNORECASE)
# "…the URA portal at https://ura.go.ug." -> "…the URA portal." The address is
# not spoken, and neither is the "at" that introduced it; the full stop stays.
# One leading whitespace character, not a run of them: "\s+at\s+" could be
# retried from every position of a long run of spaces (polynomial backtracking).
_URL_WITH_PREPOSITION_RE = re.compile(
    r"\s(?:at|via)\s+(?:https?://|www\.)[^\s<>\"]*[^\s<>\".,;:!?)\]]", re.IGNORECASE
)

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

# The contact footer answers end with (text_signals.CONTACT_FOOTER and
# localize_reply's Luganda and Swahili copies) is not spoken. Its phone numbers
# are what Orpheus cannot say: every spelling tried, digits or words, looped
# ("zero eight hundred one one one one…"). A caller is already on the line to
# URA, and the written answer keeps the footer. Matched by its opening words,
# so an answer that gives the number because it was asked for is still read.
_CONTACT_FOOTER_RE = re.compile(
    r"(?:If you get stuck at any step|Bw'oba ng'osanze obuzibu|Ikiwa utakabiliwa na changamoto)[^\n]*"
)

# Asides that read badly and make Orpheus babble. The calculator writes a rate
# as "18% (18%)" (a digit anchor for translation) and names a tax as "(VAT /
# omusolo gwa VAT / ushuru wa VAT)"; spoken by the English voice, the VAT
# answer ran on as "…to gwa gwa, isi isi zo…" until the 17 s cap.
# Bounded on purpose: an unbounded run of digits or spaces before the "(" is
# retried from every position in it (CodeQL py/polynomial-redos).
# Translated, the anchor can carry a word: "ebitundu 18% (eza 18%)".
_REPEATED_FIGURE_RE = re.compile(
    r"(?<![\w.,])(\d[\d,.]{0,24}[ \t]?%?)[ \t]?\([ \t]?(?:[^\W\d_]{1,8}[ \t])?\1[ \t]?\)"
)
_FY_ASIDE_RE = re.compile(r"[ \t]?\([ \t]?FY[ \t]?(\d{4})[ \t]?[-\u2013/][ \t]?(\d{2,4})[ \t]?\)", re.IGNORECASE)
_FY_RE = re.compile(r"\bFY\s?(\d{4})\s?[-\u2013/]\s?(\d{2,4})\b", re.IGNORECASE)
# "2026-27" with no "FY": a year range only when the second year follows the
# first within ten years, so an ISO date ("2026-09-30") is not one.
_YEAR_RANGE_RE = re.compile(r"(?<![\d-])((?:19|20)\d{2})[-\u2013/](\d{2}|(?:19|20)\d{2})(?![\d-])")
_ALIAS_ASIDE_RE = re.compile(r"\(([^()/]{1,60}(?:/[^()/]{1,60})+)\)")
# "Withholding Tax (WHT)" once the acronym is expanded: a name said twice.
_ECHO_ASIDE_RE = re.compile(r"[ \t]?\(([^()]{2,60})\)")

# A range, "UGX 335,000 – UGX 410,000", read as a pause without its "to".
_RANGE_RE = re.compile(r"(\d[\d,.]{0,24}[ \t]?%?)[ \t]+[\u2013\u2014-][ \t]+(?=(?:UGX|USD)?[ \t]?\d)")
_RANGE_WORD = {"en": "to", "lg": "okutuuka ku", "sw": "hadi"}

# Percentages, keeping a word the text already has: "ku bitundu 18%" is not
# "ku bitundu ebitundu 18 ku buli kikumi", nor "asilimia 18%" "asilimia asilimia".
_LG_PCT_RE = re.compile(r"\b(e?bitundu[ \t]+)?(\d+(?:\.\d+)?)[ \t]?%(?:[ \t]+ku[ \t]+buli[ \t]+kikumi\b)?", re.IGNORECASE)
_SW_PCT_RE = re.compile(r"\b(?:asilimia[ \t]+)?(\d+(?:\.\d+)?)[ \t]?%", re.IGNORECASE)

# A number standing on its own: not part of a word (118A, 30th), a date, a
# time, a decimal or a range written without spaces.
_BARE_NUMBER_RE = re.compile(r"(?<![\w.])(?<!\d[,/:-])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?!\w)(?![,/:.-]\d)")

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
        millions = int(val // 1_000_000)
        if val >= 1_000_000 and val % 1_000_000 == 0 and millions <= 100:
            return "shilingi akakadde kamu" if millions == 1 else f"shilingi obukadde {lg_words(millions, 'bu')}"
        # Larger or uneven amounts in English words, as money is commonly said.
        return f"shilingi {_spoken_number(clean_num, 'lg', years=False)}"

    if locale == "sw":
        return f"shilingi {_spoken_number(clean_num, 'sw')} za Uganda"

    # English default
    if val >= 1_000_000_000 and val % 1_000_000_000 == 0:
        return f"{int(val // 1_000_000_000)} billion Uganda shillings"
    if val >= 1_000_000 and val % 1_000_000 == 0:
        return f"{int(val // 1_000_000)} million Uganda shillings"
    if val >= 1_000 and val % 1_000 == 0 and val < 100_000:
        return f"{int(val // 1_000)} thousand Uganda shillings"

    return f"{amount_str} Uganda shillings"


def _second_year(first: str, second: str) -> str:
    """"2026", "27" -> "2027"; "2099", "00" -> "2100"."""
    if len(second) < 4:
        full = f"{first[: 4 - len(second)]}{second}"
        if int(full) < int(first):
            full = str(int(full) + 10 ** len(second))
        return full
    return second


def _fiscal_year(match: re.Match[str], locale: str) -> str:
    """ "FY2026-27" -> "2026 to 2027" (Orpheus reads "2026-27" as anything from
    "twenty-six twenty-seven" to "ten thousand and twenty-seven"), with the
    locale's "to"; Luganda and Swahili then say the years as words."""
    first = match.group(1)
    return f"{first} {_RANGE_WORD.get(locale, 'to')} {_second_year(first, match.group(2))}"


def _year_range(match: re.Match[str], locale: str) -> str:
    first = match.group(1)
    second = _second_year(first, match.group(2))
    if not 0 < int(second) - int(first) <= 10:
        return match.group(0)
    return f"{first} {_RANGE_WORD.get(locale, 'to')} {second}"


def _first_alias(match: re.Match[str]) -> str:
    """"(VAT / omusolo gwa VAT / ushuru wa VAT)" -> "(VAT)"; other slashed asides stay."""
    names = [name.strip() for name in match.group(1).split("/")]
    head = names[0]
    if head and all(re.search(rf"\b{re.escape(head)}\b", name, re.IGNORECASE) for name in names[1:]):
        return f"({head})"
    return match.group(0)


def _spoken_number(number: str, locale: str, years: bool = True) -> str:
    """"335000" or "2.5" as words: Swahili for sw, Luganda up to 100 for lg, English otherwise.

    In Luganda a bare four-digit number from 1100 to 2099 is read as a year
    ("twenty twenty-six"); *years* is off for amounts of money.
    """
    if "." in number:
        return decimal_words(number, "sw" if locale == "sw" else "en")
    n = int(number)
    if locale == "sw":
        return sw_words(n)
    if locale == "lg":
        is_year = years and len(number) == 4 and 1100 <= n <= 2099
        return lg_words(n) or (en_year(n) if is_year else en_words(n))
    return en_words(n)


def _lg_percent(match: re.Match[str]) -> str:
    number = match.group(2)
    words = lg_words(int(number), "bi") if "." not in number else None
    return f"{match.group(1) or 'ebitundu '}{words or _spoken_number(number, 'en')} ku buli kikumi"


def _bare_number(match: re.Match[str], locale: str) -> str:
    """A number left in Luganda or Swahili text, as words; digit runs stay as they are."""
    text, start, end = match.string, match.start(), match.end()
    digits = match.group(1)
    before = text[max(0, start - 8) : start]
    # A TIN, PRN or toll-free number read digit by digit ("0 800, 117, 0 0 0"),
    # a phone number (a leading zero), and a form name ("D-T 2027") are not amounts.
    if (
        (len(digits) > 1 and digits.startswith("0") and not match.group(2))
        or re.search(r"\d[ \t]$|\d,[ \t]$", before)
        or re.match(r"[ \t]\d|,[ \t]\d", text[end : end + 3])
        or before.endswith("D-T ")
    ):
        return match.group(0)
    number = digits.replace(",", "") + (match.group(2) or "")
    if before.endswith("Section "):
        return _spoken_number(number, "en")
    return _spoken_number(number, locale)


def _drop_echo(match: re.Match[str]) -> str:
    """"Withholding Tax (Withholding Tax)" -> "Withholding Tax"."""
    said = match.group(1).strip()
    preceding = match.string[: match.start()].rstrip()
    # A whole-word echo only: "118 percent (18 percent)" names two figures.
    echoed = said and preceding.lower().endswith(said.lower())
    return "" if echoed and not preceding[-len(said) - 1 : -len(said)].isalnum() else match.group(0)


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

    t = _CONTACT_FOOTER_RE.sub("", text)

    # 1. Strip markdown links, including a missing opening bracket, then any
    #    bare URL the model left behind. A neural voice reads "www dot" aloud.
    t = _BROKEN_MD_LINK_RE.sub(r"\1", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _DANGLING_URL_TAIL_RE.sub("", t)
    t = _URL_WITH_PREPOSITION_RE.sub("", t)
    t = _BARE_URL_RE.sub("", t)

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

    # 6b. Asides: a figure said once, a tax named once, the fiscal year as words
    t = _REPEATED_FIGURE_RE.sub(r"\1", t)
    t = _ALIAS_ASIDE_RE.sub(_first_alias, t)
    if locale == "en":
        t = _FY_ASIDE_RE.sub(lambda m: f", for the {_fiscal_year(m, locale)} financial year", t)
    else:
        t = _FY_ASIDE_RE.sub("", t)
    t = _FY_RE.sub(lambda m: _fiscal_year(m, locale), t)
    t = _YEAR_RANGE_RE.sub(lambda m: _year_range(m, locale), t)

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

    # 7e. A range's dash is a "to"
    t = _RANGE_RE.sub(lambda m: f"{m.group(1)} {_RANGE_WORD.get(locale, 'to')} ", t)

    # 8. Currency amounts (UGX and USD)
    t = _UGX_RE.sub(lambda m: _format_ugx_amount(m.group(1), locale), t)
    t = _USD_RE.sub(r"\1 US dollars", t)

    # 9. Percentages
    if locale == "lg":
        t = _LG_PCT_RE.sub(_lg_percent, t)
    elif locale == "sw":
        t = _SW_PCT_RE.sub(lambda m: f"asilimia {_spoken_number(m.group(1), 'sw')}", t)
    else:
        t = _PCT_RE.sub(r"\1 percent", t)

    # 10. Legal section references: Sec. 118A -> Section 118A
    t = _SEC_RE.sub(r"Section \1", t)

    # 11. Domain acronym expansion for phonetics
    for pattern, replacement in _ACRONYMS:
        t = pattern.sub(replacement, t)
    t = _ECHO_ASIDE_RE.sub(_drop_echo, t)

    # 11b. Luganda and Swahili figures as words (see number_words)
    if locale in ("lg", "sw"):
        t = _BARE_NUMBER_RE.sub(lambda m: _bare_number(m, locale), t)

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
