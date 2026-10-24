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

from .number_words import ach_words, decimal_words, en_words, en_year, lg_words, nyn_words, sw_words

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

# URA and Ugandan government/MDA official emails and web domains:
# spoken naturally as "<agency> dot go dot u g" / "<agency> dot co dot u g" / "<agency> dot or dot u g"
_URA_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)@(?:www\.)?ura\.go\.ug\b", re.IGNORECASE
)
_URA_DOMAIN_RE = re.compile(
    r"\b(?:https?://)?(?:www\.)?ura\.go\.ug(?:/[^\s.,;:!?)\]]*)?\b", re.IGNORECASE
)
_GOV_EMAIL_RE = re.compile(
    r"\b([A-Za-z0-9._%+-]+)@(?:www\.)?([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)\.(go|co|or)\.ug\b",
    re.IGNORECASE,
)
_GOV_DOMAIN_RE = re.compile(
    r"\b([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)\.(go|co|or)\.ug(?:/[^\s.,;:!?)\]]*)?\b(?![A-Za-z0-9-]|\.[A-Za-z0-9-])",
    re.IGNORECASE,
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
_SECTION_EMOJI_RE = re.compile(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?[ \t]*")

# Markdown bold / italics
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"\*([^*]+)\*|_([^_]+)_")

# URA Taxpayer Identification Number (9 or 10 digits)
_TIN_EXPLICIT_RE = re.compile(
    r"\b(?:T-?I-?N|T\.I\.N\.)(?::|\s+is|\s+number|\s+no\.?)?\s*([1-9]\d{8,9})\b", re.IGNORECASE
)

# Payment Registration Number (PRN)
_PRN_EXPLICIT_RE = re.compile(
    r"\b(?:P-?R-?N|P\.R\.N\.)(?::|\s+is|\s+number|\s+no\.?)?\s*(\d{10,14})\b", re.IGNORECASE
)

# National Identification Number (NIN - Ugandan 14-char standard CM/CF/CR, 13-15 accepted per G247)
_NIN_EXPLICIT_RE = re.compile(
    r"\bNIN(?::|\s+is|\s+number|\s+no\.?)?\s*(C[MFR][0-9A-Za-z]{11,13})\b", re.IGNORECASE
)
_STANDALONE_TIN_OR_PRN_RE = re.compile(
    r"\b(?:(UGX|USD|Shs)\s+)?(1\d{9}|2\d{11,14})\b", re.IGNORECASE
)
_REDACTED_MARKER_RE = re.compile(
    r"\[REDACTED_(UG_NID|UG_TIN|UG_PHONE|EMAIL|CREDIT_CARD|AMEX_CARD|UG_PASSPORT)\]",
    re.IGNORECASE,
)
_REDACTED_SPOKEN_LABELS: dict[str, dict[str, str]] = {
    "UG_NID": {
        "en": "redacted National ID",
        "lg": "ennamba ya NIN ekwekeddwa",
        "sw": "nambari ya kitambulisho iliyofichwa",
        "nyn": "enamba ya NIN esherekirwe",
        "ach": "namba me NIN ma kikano",
    },
    "UG_TIN": {
        "en": "redacted TIN",
        "lg": "ennamba ya TIN ekwekeddwa",
        "sw": "nambari ya TIN iliyofichwa",
        "nyn": "enamba ya TIN esherekirwe",
        "ach": "namba me TIN ma kikano",
    },
    "UG_PHONE": {
        "en": "redacted phone number",
        "lg": "ennamba y'essimu ekwekeddwa",
        "sw": "nambari ya simu iliyofichwa",
        "nyn": "enamba y'esimu esherekirwe",
        "ach": "namba me cim ma kikano",
    },
    "EMAIL": {
        "en": "redacted email address",
        "lg": "endagiriro ya email ekwekeddwa",
        "sw": "anwani ya barua pepe iliyofichwa",
        "nyn": "email esherekirwe",
        "ach": "email ma kikano",
    },
    "CREDIT_CARD": {
        "en": "redacted card number",
        "lg": "ennamba ya kaadi ekwekeddwa",
        "sw": "nambari ya kadi iliyofichwa",
        "nyn": "enamba ya kaadi esherekirwe",
        "ach": "namba me karatac ma kikano",
    },
    "AMEX_CARD": {
        "en": "redacted card number",
        "lg": "ennamba ya kaadi ekwekeddwa",
        "sw": "nambari ya kadi iliyofichwa",
        "nyn": "enamba ya kaadi esherekirwe",
        "ach": "namba me karatac ma kikano",
    },
    "UG_PASSPORT": {
        "en": "redacted passport number",
        "lg": "ennamba ya paasipooti ekwekeddwa",
        "sw": "nambari ya pasipoti iliyofichwa",
        "nyn": "enamba ya paasipooti esherekirwe",
        "ach": "namba me pasipot ma kikano",
    },
}

# Toll-free and customer care numbers
_TOLLFREE_URA_RE = re.compile(r"\b0800\s*([12]17)\s*000\b")
_GENERAL_TOLLFREE_RE = re.compile(r"\b0800\s*(\d{3})\s*(\d{3})\b")

# Numbered steps for cadence and pacing
_NUMBERED_STEP_RE = re.compile(r"^\s*(\d+)\.\s+", re.MULTILINE)

# Currency amounts
_UGX_RE = re.compile(r"\bUGX\s*([0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\b", re.IGNORECASE)
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
    r"(?:If you get stuck at any step|Bw'oba ng'osanze obuzibu|Ikiwa utakabiliwa na changamoto|Ku oraashangye obuzibu|Ka inongo peko)[^\n]*"
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
_YEAR_RANGE_RE = re.compile(r"(?<![0-9-])((?:19|20)[0-9]{2})[-\u2013/]([0-9]{2}|(?:19|20)[0-9]{2})(?![0-9-])")
_ALIAS_ASIDE_RE = re.compile(r"\(([^()/]{1,60}(?:/[^()/]{1,60})+)\)")
# "Withholding Tax (WHT)" once the acronym is expanded: a name said twice.
_ECHO_ASIDE_RE = re.compile(r"[ \t]?\(([^()]{2,60})\)")

# A range, "UGX 335,000 – UGX 410,000", read as a pause without its "to".
_RANGE_RE = re.compile(r"([0-9][0-9,.]{0,24}[ \t]?%?)[ \t]+[\u2013\u2014-][ \t]+(?=(?:UGX|USD)?[ \t]?[0-9])")
_RANGE_WORD = {
    "en": "to",
    "lg": "okutuuka ku",
    "sw": "hadi",
    "nyn": "kuhika aha",
    "ach": "naka i",
}

# Percentages, keeping a word the text already has: "ku bitundu 18%" is not
# "ku bitundu ebitundu 18 ku buli kikumi", nor "asilimia 18%" "asilimia asilimia".
_LG_PCT_RE = re.compile(r"\b(e?bitundu[ \t]+)?([0-9]+(?:\.[0-9]+)?)[ \t]?%(?:[ \t]+ku[ \t]+buli[ \t]+kikumi\b)?", re.IGNORECASE)
_SW_PCT_RE = re.compile(r"\b(?:asilimia[ \t]+)?([0-9]+(?:\.[0-9]+)?)[ \t]?%", re.IGNORECASE)
_NYN_PCT_RE = re.compile(r"\b(e?bicweka[ \t]+)?([0-9]+(?:\.[0-9]+)?)[ \t]?%(?:[ \t]+aha[ \t]+(?:buli[ \t]+)?kikumi\b)?", re.IGNORECASE)
_ACH_PCT_RE = re.compile(r"\b(dul[ \t]+)?([0-9]+(?:\.[0-9]+)?)[ \t]?%(?:[ \t]+i[ \t]+kom[ \t]+miya\b)?", re.IGNORECASE)

# A number standing on its own: not part of a word (118A, 30th), a date, a
# time, a decimal or a range written without spaces. ASCII digits only here
# and in the figure patterns: `\d` also matches other scripts' digits.
_BARE_NUMBER_RE = re.compile(r"(?<![\w.])(?<![0-9][,/:-])([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(\.[0-9]+)?(?!\w)(?![,/:.-][0-9])")

# Acronyms and institutional terms in Ugandan tax and cross-MDA government context
_ACRONYMS = [
    # Pronounceable acronyms spoken as natural, fluent words in Ugandan administration
    # rather than spelled out letter-by-letter:
    (re.compile(r"\bE-?F-?R-?I-?S\b", re.IGNORECASE), "Efris"),
    (re.compile(r"(?:\bN\.E\.M\.A\b\.?|\bN-E-M-A\b|\bNEMA\b)", re.IGNORECASE), "Nema"),
    (re.compile(r"(?:\bN\.I\.R\.A\b\.?|\bN-I-R-A\b|\bNIRA\b)", re.IGNORECASE), "Nira"),
    (re.compile(r"(?:\bI\.R\.A\.S\b\.?|\bI-R-A-S\b|\bIRAS\b)", re.IGNORECASE), "Iras"),
    (re.compile(r"(?:\bB\.W\.I\.M\.S\b\.?|\bB-W-I-M-S\b|\bBWIMS\b)", re.IGNORECASE), "Bwims"),
    (re.compile(r"(?:\bC\.O\.M\.E\.S\.A\b\.?|\bC-O-M-E-S-A\b|\bCOMESA\b)", re.IGNORECASE), "Comesa"),
    (re.compile(r"(?:\bU\.L\.I\.I\b\.?|\bU-L-I-I\b|\bULII\b)", re.IGNORECASE), "Ulii"),
    (re.compile(r"(?:\bI\.R\.I\.M\.S\b\.?|\bI-R-I-M-S\b|\bIRIMS\b)", re.IGNORECASE), "Irims"),
    (re.compile(r"(?:\bN\.I\.T\.A-U\b|\bNITA-U\b)", re.IGNORECASE), "Nita U"),
    (re.compile(r"(?:\bN\.I\.T\.A\b\.?|\bN-I-T-A\b|\bNITA\b)", re.IGNORECASE), "Nita"),
    (re.compile(r"\bASYCUDA(?:\+\+)?\b", re.IGNORECASE), "Asycuda"),
    (re.compile(r"\bTREP\b", re.IGNORECASE), "Trep"),
    # PAYE is universally spoken in Uganda as "payee" (/peɪˈiː/), rather than spelled letter-by-letter.
    (re.compile(r"(?:\bP\.A\.Y\.E\b\.?|\bP-A-Y-E\b|\bPAYE\b|\bpaye\b)", re.IGNORECASE), "Payee"),
    # TIN, NIN, VAT are acronyms naturally pronounced as words ("tin"/"teen", "nin", "vat")
    # in everyday Ugandan tax speech, rather than reading each as a separate letter.
    # Keep TIN/tin as TIN/tin and NIN/nin as NIN/nin (convert dotted/hyphenated T-I-N / N-I-N to TIN / NIN).
    (re.compile(r"(?:\bT\.I\.N\b\.?|\bT-I-N\b)", re.IGNORECASE), "TIN"),
    (re.compile(r"(?:\bN\.I\.N\b\.?|\bN-I-N\b)", re.IGNORECASE), "NIN"),
    (re.compile(r"(?:\bV\.A\.T\b\.?|\bV-A-T\b)", re.IGNORECASE), "VAT"),
    (re.compile(r"\bV-?A-?T-?A\b"), "VAT Act"),
    # URA and Ugandan government/MDA initialisms pronounced letter-by-letter with spaces:
    (re.compile(r"(?:\bU\.R\.A\b\.?|\bU-R-A\b|\bURA\b|\bura\b)", re.IGNORECASE), "U R A"),
    (re.compile(r"(?:\bK\.C\.C\.A\b\.?|\bK-C-C-A\b|\bKCCA\b)", re.IGNORECASE), "K C C A"),
    (re.compile(r"(?:\bU\.R\.S\.B\b\.?|\bU-R-S-B\b|\bURSB\b)", re.IGNORECASE), "U R S B"),
    (re.compile(r"(?:\bU\.N\.B\.S\b\.?|\bU-N-B-S\b|\bUNBS\b)", re.IGNORECASE), "U N B S"),
    (re.compile(r"(?:\bD\.C\.I\.C\b\.?|\bD-C-I-C\b|\bDCIC\b)", re.IGNORECASE), "D C I C"),
    (re.compile(r"(?:\bU\.D\.L\.S\b\.?|\bU-D-L-S\b|\bUDLS\b)", re.IGNORECASE), "U D L S"),
    (re.compile(r"(?:\bM\.L\.H\.U\.D\b\.?|\bM-L-H-U-D\b|\bMLHUD\b)", re.IGNORECASE), "M L H U D"),
    (re.compile(r"(?:\bU\.G\.N\.L\.I\.S\b\.?|\bU-G-N-L-I-S\b|\bUgNLIS\b)", re.IGNORECASE), "U G N L I S"),
    (re.compile(r"(?:\bU\.P\.F\b\.?|\bU-P-F\b|\bUPF\b)", re.IGNORECASE), "U P F"),
    (re.compile(r"(?:\bE\.P\.S\b\.?|\bE-P-S\b|\bEPS\b)", re.IGNORECASE), "E P S"),
    (re.compile(r"(?:\bN\.D\.A\.M\.I\.S\b\.?|\bN-D-A-M-I-S\b|\bNDAMIS\b)", re.IGNORECASE), "N D A M I S"),
    (re.compile(r"(?:\bN\.D\.A\b\.?|\bN-D-A\b|\bNDA\b)", re.IGNORECASE), "N D A"),
    (re.compile(r"(?:\bP\.P\.D\.A\b\.?|\bP-P-D-A\b|\bPPDA\b)", re.IGNORECASE), "P P D A"),
    (re.compile(r"(?:\bI\.F\.M\.S\b\.?|\bI-F-M-S\b|\bIFMS\b)", re.IGNORECASE), "I F M S"),
    (re.compile(r"(?:\bO\.B\.R\.S\b\.?|\bO-B-R-S\b|\bOBRS\b)", re.IGNORECASE), "O B R S"),
    (re.compile(r"(?:\bU\.E\.S\.W\b\.?|\bU-E-S-W\b|\bUESW\b)", re.IGNORECASE), "U E S W"),
    (re.compile(r"(?:\bO\.N\.T\.R\b\.?|\bO-N-T-R\b|\bONTR\b)", re.IGNORECASE), "O N T R"),
    (re.compile(r"(?:\bN\.T\.R\b\.?|\bN-T-R\b|\bNTR\b)", re.IGNORECASE), "N T R"),
    (re.compile(r"(?:\bM\.O\.W\.T\b\.?|\bM-O-W-T\b|\bMoWT\b)", re.IGNORECASE), "M O W T"),
    (re.compile(r"(?:\bM\.O\.F\.P\.E\.D\b\.?|\bM-O-F-P-E-D\b|\bMoFPED\b)", re.IGNORECASE), "M O F P E D"),
    (re.compile(r"(?:\bM\.O\.L\.G\b\.?|\bM-O-L-G\b|\bMoLG\b)", re.IGNORECASE), "M O L G"),
    (re.compile(r"(?:\bM\.O\.I\.A\b\.?|\bM-O-I-A\b|\bMoIA\b)", re.IGNORECASE), "M O I A"),
    (re.compile(r"(?:\bM\.A\.A\.I\.F\b\.?|\bM-A-A-I-F\b|\bMAAIF\b)", re.IGNORECASE), "M A A I F"),
    (re.compile(r"(?:\bU\.C\.D\.A\b\.?|\bU-C-D-A\b|\bUCDA\b)", re.IGNORECASE), "U C D A"),
    (re.compile(r"(?:\bU\.I\.A\b\.?|\bU-I-A\b|\bUIA\b)", re.IGNORECASE), "U I A"),
    (re.compile(r"(?:\bU\.F\.Z\.A\b\.?|\bU-F-Z-A\b|\bUFZA\b)", re.IGNORECASE), "U F Z A"),
    (re.compile(r"(?:\bU\.C\.C\b\.?|\bU-C-C\b|\bUCC\b)", re.IGNORECASE), "U C C"),
    (re.compile(r"(?:\bN\.P\.A\b\.?|\bN-P-A\b|\bNPA\b)", re.IGNORECASE), "N P A"),
    (re.compile(r"(?:\bN\.W\.S\.C\b\.?|\bN-W-S-C\b|\bNWSC\b)", re.IGNORECASE), "N W S C"),
    (re.compile(r"(?:\bU\.N\.R\.A\b\.?|\bU-N-R-A\b|\bUNRA\b)", re.IGNORECASE), "U N R A"),
    (re.compile(r"(?:\bP\.V\.O\.C\b\.?|\bP-V-O-C\b|\bPVoC\b)", re.IGNORECASE), "P V O C"),
    (re.compile(r"(?:\bC\.O\.C\b\.?|\bC-O-C\b|\bCoC\b)", re.IGNORECASE), "C O C"),
    (re.compile(r"(?:\bC\.G\.V\b\.?|\bC-G-V\b|\bCGV\b)", re.IGNORECASE), "C G V"),
    (re.compile(r"(?:\bM\.Z\.O\b\.?|\bM-Z-O\b|\bMZO\b)", re.IGNORECASE), "M Z O"),
    (re.compile(r"(?:\bE\.I\.A\b\.?|\bE-I-A\b|\bEIA\b)", re.IGNORECASE), "E I A"),
    (re.compile(r"(?:\bF\.D\.N\b\.?|\bF-D-N\b|\bFDN\b)", re.IGNORECASE), "F D N"),
    (re.compile(r"(?:\bE\.F\.D\b\.?|\bE-F-D\b|\bEFD\b)", re.IGNORECASE), "E F D"),
    (re.compile(r"(?:\bE\.D\.C\b\.?|\bE-D-C\b|\bEDC\b)", re.IGNORECASE), "E D C"),
    (re.compile(r"(?:\bO\.T\.P\b\.?|\bO-T-P\b|\bOTP\b)", re.IGNORECASE), "O T P"),
    (re.compile(r"(?:\bE\.A\.C\b\.?|\bE-A-C\b|\bEAC\b)", re.IGNORECASE), "E A C"),
    (re.compile(r"(?:\bC\.G\.T\b\.?|\bC-G-T\b|\bCGT\b)", re.IGNORECASE), "C G T"),
    (re.compile(r"(?:\bD\.S\.T\b\.?|\bD-S-T\b|\bDST\b)", re.IGNORECASE), "D S T"),
    (re.compile(r"(?:\bF\.O\.B\b\.?|\bF-O-B\b|\bFOB\b)", re.IGNORECASE), "F O B"),
    (re.compile(r"(?:\bC\.I\.F\b\.?|\bC-I-F\b|\bCIF\b)", re.IGNORECASE), "C I F"),
    (re.compile(r"(?:\bC\.X\.P\b\.?|\bC-X-P\b|\bCXP\b)", re.IGNORECASE), "C X P"),
    (re.compile(r"(?:\bM\.D\.A\.?s\b|\bM-D-As\b|\bMDAs\b)", re.IGNORECASE), "M D As"),
    (re.compile(r"(?:\bM\.D\.A\b\.?|\bM-D-A\b|\bMDA\b)", re.IGNORECASE), "M D A"),
    (re.compile(r"\bB2G\b", re.IGNORECASE), "B 2 G"),
    (re.compile(r"\bB2B\b", re.IGNORECASE), "B 2 B"),
    (re.compile(r"\bB2C\b", re.IGNORECASE), "B 2 C"),
    (re.compile(r"\be-?GP\b", re.IGNORECASE), "E G P"),
    (re.compile(r"\be-?Citie\b", re.IGNORECASE), "E-City"),
    # Tax heads & statutory abbreviations
    (re.compile(r"\bWHT\b|\bW\.H\.T\b\.?", re.IGNORECASE), "Withholding Tax"),
    (re.compile(r"(?:\bC\.I\.T\b\.?|\bC-I-T\b|\bCIT\b)", re.IGNORECASE), "C-I-T"),
    (re.compile(r"(?:\bP\.I\.T\b\.?|\bP-I-T\b|\bPIT\b)"), "P-I-T"),
    (re.compile(r"\bLED\b|\bL\.E\.D\b\.?"), "Local Excise Duty"),
    (re.compile(r"\bDTS\b|\bD\.T\.S\b\.?", re.IGNORECASE), "Digital Tax Stamps"),
    (re.compile(r"(?:\bN\.S\.S\.F\b\.?|\bN-S-S-F\b|\bNSSF\b)", re.IGNORECASE), "N-S-S-F"),
    (re.compile(r"\bLST\b|\bL\.S\.T\b\.?", re.IGNORECASE), "Local Service Tax"),
    (re.compile(r"\bBOU\b|\bB\.O\.U\b\.?"), "Bank of Uganda"),
    (re.compile(r"\bTAT\b|\bT\.A\.T\b\.?"), "Tax Appeals Tribunal"),
    (re.compile(r"\bTPCA\b", re.IGNORECASE), "Tax Procedures Code Act"),
    (re.compile(r"\bITA\b"), "Income Tax Act"),
    (re.compile(r"(?:\bP\.R\.N\b\.?|\bP-R-N\b|\bPRN\b)", re.IGNORECASE), "P R N"),
    (re.compile(r"(?:\bB\.R\.N\b\.?|\bB-R-N\b|\bBRN\b)", re.IGNORECASE), "B-R-N"),
    (re.compile(r"(?:\bT\.C\.C\b\.?|\bT-C-C\b|\bTCC\b)", re.IGNORECASE), "T-C-C"),
    (re.compile(r"(?:\bA\.E\.O\b\.?|\bA-E-O\b|\bAEO\b)", re.IGNORECASE), "A-E-O"),
    (re.compile(r"(?:\bC\.E\.T\b\.?|\bC-E-T\b|\bCET\b)", re.IGNORECASE), "C-E-T"),
    (re.compile(r"\be-?Receipt\b", re.IGNORECASE), "E-Receipt"),
    (re.compile(r"\be-?Invoice\b", re.IGNORECASE), "E-Invoice"),
    (re.compile(r"\be-?Tax\b", re.IGNORECASE), "E-Tax"),
    (re.compile(r"\be-?Passport\b", re.IGNORECASE), "E-Passport"),
    (re.compile(r"\be-?Portal\b", re.IGNORECASE), "E-Portal"),
    (re.compile(r"\be-?Filing\b", re.IGNORECASE), "E-Filing"),
    (re.compile(r"\be-?Immigration\b", re.IGNORECASE), "E-Immigration"),
    (re.compile(r"\bDT-([0-9]{3,5})\b", re.IGNORECASE), r"D-T \1"),
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

    if locale == "nyn":
        millions = int(val // 1_000_000)
        if val >= 1_000_000 and val % 1_000_000 == 0 and millions <= 100:
            return "shiringi akakaikuru kamwe" if millions == 1 else f"shiringi obukaikuru {nyn_words(millions, 'bu')}"
        return f"shiringi {_spoken_number(clean_num, 'nyn', years=False)}"

    if locale == "ach":
        return f"cilingi {_spoken_number(clean_num, 'ach', years=False)} me Uganda"

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
    if locale == "nyn":
        is_year = years and len(number) == 4 and 1100 <= n <= 2099
        return nyn_words(n) or (en_year(n) if is_year else en_words(n))
    if locale == "ach":
        is_year = years and len(number) == 4 and 1100 <= n <= 2099
        return ach_words(n) or (en_year(n) if is_year else en_words(n))
    return en_words(n)


def _lg_percent(match: re.Match[str]) -> str:
    number = match.group(2)
    words = lg_words(int(number), "bi") if "." not in number else None
    return f"{match.group(1) or 'ebitundu '}{words or _spoken_number(number, 'en')} ku buli kikumi"


def _nyn_percent(match: re.Match[str]) -> str:
    number = match.group(2)
    words = nyn_words(int(number), "bi") if "." not in number else None
    return f"{match.group(1) or 'ebicweka '}{words or _spoken_number(number, 'en')} aha kikumi"


def _ach_percent(match: re.Match[str]) -> str:
    number = match.group(2)
    words = ach_words(int(number)) if "." not in number else None
    return f"{match.group(1) or 'dul '}{words or _spoken_number(number, 'en')} i kom miya"


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

    # 0. Convert PII redaction placeholders ([REDACTED_UG_NID], etc.) into natural spoken words
    #    before Markdown italic stripping (_..._) removes underscores inside [REDACTED_UG_NID].
    t = _REDACTED_MARKER_RE.sub(
        lambda m: _REDACTED_SPOKEN_LABELS.get(m.group(1).upper(), {}).get(locale, "redacted identifier"),
        t,
    )

    # 1. Strip markdown links, including a missing opening bracket, then any
    #    bare URL the model left behind. A neural voice reads "www dot" aloud.
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _BROKEN_MD_LINK_RE.sub(r"\1", t)
    t = _DANGLING_URL_TAIL_RE.sub("", t)
    t = _URL_WITH_PREPOSITION_RE.sub("", t)
    t = _URA_EMAIL_RE.sub(r"\1 at U R A dot go dot u g", t)
    t = _URA_DOMAIN_RE.sub("U R A dot go dot u g", t)
    t = _GOV_EMAIL_RE.sub(
        lambda m: f"{m.group(1)} at {m.group(2).replace('.', ' dot ')} dot {m.group(3).lower()} dot u g",
        t,
    )
    t = _BARE_URL_RE.sub("", t)
    t = _GOV_DOMAIN_RE.sub(
        lambda m: f"{m.group(1).replace('.', ' dot ')} dot {m.group(2).lower()} dot u g",
        t,
    )

    # 2. Strip inline citation markers: [1], [1, 2]
    t = _CITATION_RE.sub("", t)

    # 3. Strip code blocks and inline code
    t = _CODE_BLOCK_RE.sub(" ", t)
    t = _INLINE_CODE_RE.sub(r"\1", t)

    # 4. Handle table rows: replace pipes with commas/spaces
    t = _TABLE_PIPE_RE.sub(", ", t)

    # 5. Strip Markdown headers, bullet points, and decorative section emojis
    t = _HEADER_RE.sub("", t)
    t = _BULLET_RE.sub("", t)
    t = _SECTION_EMOJI_RE.sub("", t)

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

    # 7. Normalize 9-digit or 10-digit TINs (pronounce TIN as word, digits with clear cadence)
    def _tin_sub(m: re.Match) -> str:
        digits = " ".join(list(m.group(1)))
        return f"TIN {digits}"

    t = _TIN_EXPLICIT_RE.sub(_tin_sub, t)

    # 7b. Normalize PRNs (spell out digits with pause)
    def _prn_sub(m: re.Match) -> str:
        digits = " ".join(list(m.group(1)))
        return f"P R N {digits}"

    t = _PRN_EXPLICIT_RE.sub(_prn_sub, t)

    # 7c. Normalize 14-character NINs (pronounce NIN as word, characters clearly with pause)
    def _nin_sub(m: re.Match) -> str:
        chars = " ".join(list(m.group(1).upper()))
        return f"NIN {chars}"

    t = _NIN_EXPLICIT_RE.sub(_nin_sub, t)

    # 7c1. Normalize standalone 10-digit TINs and 12-15 digit PRNs so they are never spoken as "one billion..."
    def _standalone_tin_or_prn_sub(m: re.Match) -> str:
        if m.group(1):
            return m.group(0)
        return " ".join(list(m.group(2)))

    t = _STANDALONE_TIN_OR_PRN_RE.sub(_standalone_tin_or_prn_sub, t)

    # 7d. Normalize URA toll-free contact numbers for cadence
    t = _TOLLFREE_URA_RE.sub(r"0 800, \1, 0 0 0", t)
    t = _GENERAL_TOLLFREE_RE.sub(r"0 800, \1, \2", t)

    # 7e. Paced numbered steps for structured guidance
    if locale == "lg":
        t = _NUMBERED_STEP_RE.sub(r"Odaala \1: ", t)
    elif locale == "sw":
        t = _NUMBERED_STEP_RE.sub(r"Hatua \1: ", t)
    elif locale == "nyn":
        t = _NUMBERED_STEP_RE.sub(r"Idaara \1: ", t)
    elif locale == "ach":
        t = _NUMBERED_STEP_RE.sub(r"Rwom \1: ", t)
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
    elif locale == "nyn":
        t = _NYN_PCT_RE.sub(_nyn_percent, t)
    elif locale == "ach":
        t = _ACH_PCT_RE.sub(_ach_percent, t)
    else:
        t = _PCT_RE.sub(r"\1 percent", t)

    # 10. Legal section references: Sec. 118A -> Section 118A
    t = _SEC_RE.sub(r"Section \1", t)

    # 11. Domain acronym expansion for phonetics
    for pattern, replacement in _ACRONYMS:
        t = pattern.sub(replacement, t)
    t = _ECHO_ASIDE_RE.sub(_drop_echo, t)

    # 11b. Vernacular figures as words (see number_words)
    if locale in ("lg", "sw", "nyn", "ach"):
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


_BANTU_ACRONYM_PHONETICS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:\bT\.I\.N\b\.?|\bT-I-N\b)", re.IGNORECASE), "TIN"),
    (re.compile(r"(?:\bN\.I\.N\b\.?|\bN-I-N\b)", re.IGNORECASE), "NIN"),
    (re.compile(r"(?:\bP\.R\.N\b\.?|\bP-R-N\b|\bPRN\b)", re.IGNORECASE), "P R N"),
)


def phoneticize_bantu_acronyms(text: str, locale: str = "lg") -> str:
    """Normalize acronyms (TIN/tin and NIN/nin kept as natural words, PRN -> P R N) for Orpheus Ugandan/East African voices."""
    if not text or (locale or "").lower() not in ("lg", "sw", "nyn", "ach"):
        return text or ""
    out = text
    for pat, repl in _BANTU_ACRONYM_PHONETICS:
        out = pat.sub(repl, out)
    return out
