"""Numbers as words, for the voices.

Orpheus reads English digits well but not Luganda or Swahili ones. A round
trip through Whisper-SALT (2026-10-01) heard "ebitundu 18 ku buli kikumi" as
"ebitundu e tini", "shilingi obukadde 10" as "kiringi obukadde" (the ten
gone), and "asilimia 18" as "asilimia aching". Written as words, the same
figures came back intact. So the spoken text carries them as words:

* Swahili, every number, in the standard forms ("asilimia kumi na nane",
  "elfu mia tatu thelathini na tano").
* Luganda, up to 100, and round millions ("obukadde kkumi"). Larger amounts
  are said in English words, as Luganda speakers commonly say money
  ("shilingi three hundred and thirty-five thousand"). Sunflower-14B
  confirmed the small Luganda numbers and got the large ones wrong, so the
  large ones are not guessed.

The Luganda agreement forms (``ebitundu bibiri``, ``obukadde bubiri``) are
drafts for a native speaker's review, like the rest of the Luganda text.
"""

from __future__ import annotations

import re as _re

_EN_UNITS = [
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
_EN_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_EN_SCALES = [(10**9, "billion"), (10**6, "million"), (1000, "thousand")]


def _en_below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    words = [f"{_EN_UNITS[hundreds]} hundred"] if hundreds else []
    if rest:
        if hundreds:
            words.append("and")
        if rest < 20:
            words.append(_EN_UNITS[rest])
        else:
            tens, units = divmod(rest, 10)
            words.append(_EN_TENS[tens] + (f"-{_EN_UNITS[units]}" if units else ""))
    return " ".join(words)


def en_words(n: int) -> str:
    """335000 -> "three hundred and thirty-five thousand"."""
    if n < 0:
        return f"minus {en_words(-n)}"
    if n == 0:
        return "zero"
    words = []
    for scale, name in _EN_SCALES:
        count, n = divmod(n, scale)
        if count:
            words.append(f"{en_words(count) if count >= 1000 else _en_below_1000(count)} {name}")
    if n:
        words.append(("and " if words and n < 100 else "") + _en_below_1000(n))
    return " ".join(words)


def en_year(n: int) -> str:
    """2026 -> "twenty twenty-six", 2005 -> "two thousand and five", as years are said."""
    if 2000 <= n < 2010 or n % 1000 == 0 or not 1100 <= n <= 2099:
        return en_words(n)
    century, rest = divmod(n, 100)
    return f"{en_words(century)} {en_words(rest) if rest >= 10 else ('hundred' if rest == 0 else 'oh ' + en_words(rest))}"


_SW_UNITS = ["sifuri", "moja", "mbili", "tatu", "nne", "tano", "sita", "saba", "nane", "tisa"]
_SW_TENS = ["", "kumi", "ishirini", "thelathini", "arobaini", "hamsini", "sitini", "sabini", "themanini", "tisini"]
_SW_SCALES = [(10**9, "bilioni"), (10**6, "milioni"), (1000, "elfu")]


def _sw_below_100(n: int) -> str:
    if n < 10:
        return _SW_UNITS[n]
    tens, units = divmod(n, 10)
    return _SW_TENS[tens] + (f" na {_SW_UNITS[units]}" if units else "")


def _sw_join(head: str, rest: str) -> str:
    # "na" comes before the last element: 305 "mia tatu na tano", 2500 "elfu
    # mbili na mia tano", but 335 "mia tatu thelathini na tano" and 2026
    # "elfu mbili ishirini na sita", whose own "na" already sits there.
    return f"{head} {rest}" if " na " in rest else f"{head} na {rest}"


def _sw_below_1000(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    if not hundreds:
        return _sw_below_100(rest)
    head = f"mia {_SW_UNITS[hundreds]}"
    return _sw_join(head, _sw_below_100(rest)) if rest else head


def sw_words(n: int) -> str:
    """Standard Swahili: 18 -> "kumi na nane", 335000 -> "elfu mia tatu thelathini na tano"."""
    if n < 0:
        return f"hasi {sw_words(-n)}"
    if n < 1000:
        return _sw_below_1000(n)
    for scale, name in _SW_SCALES:
        if n >= scale:
            count, rest = divmod(n, scale)
            head = f"{name} {sw_words(count)}"
            return _sw_join(head, sw_words(rest)) if rest else head
    raise AssertionError("unreachable")


# Luganda counting forms, which are also the class 9/10 agreement (ennaku
# ssatu, essaawa bbiri); ebitundu (percent) and obukadde (millions) agree
# in the units 1-5.
_LG_UNITS = ["zeero", "emu", "bbiri", "ssatu", "nnya", "ttaano", "mukaaga", "musanvu", "munaana", "mwenda"]
_LG_AGREEING = {
    "bi": ["", "kimu", "bibiri", "bisatu", "bina", "bitaano"],
    "bu": ["", "kamu", "bubiri", "busatu", "buna", "butaano"],
}
_LG_TENS = ["", "kkumi", "amakumi abiri", "amakumi asatu", "amakumi ana", "amakumi ataano",
            "nkaaga", "nsanvu", "kinaana", "kyenda"]


def lg_words(n: int, agree: str = "") -> str | None:
    """Luganda for 0-100: 18 -> "kkumi na munaana", 25 -> "amakumi abiri mu ttaano".

    *agree* is the noun's agreement for units 1-5: ``"bi"`` for ebitundu,
    ``"bu"`` for obukadde. ``None`` above 100: those are said in English.
    """
    if n == 100:
        return "kikumi"
    if not 0 <= n < 100:
        return None
    tens, units = divmod(n, 10)
    unit = _LG_AGREEING[agree][units] if agree and 1 <= units <= 5 else _LG_UNITS[units]
    if not tens:
        return unit
    if not units:
        return _LG_TENS[tens]
    return f"kkumi na {unit}" if tens == 1 else f"{_LG_TENS[tens]} mu {unit}"


# Runyankole counting forms (0-100); ebicweka (percent) and obukaikuru (millions)
# agree in units 1-5.
_NYN_UNITS = ["zeero", "emwe", "ibiri", "ishatu", "ina", "itaano", "mukaaga", "mushanju", "munaana", "mwenda"]
_NYN_AGREEING = {
    "bi": ["", "kimwe", "bibiri", "bishatu", "bina", "bitaano"],
    "bu": ["", "kamwe", "bubiri", "bushatu", "buna", "butaano"],
}
_NYN_TENS = [
    "", "ikumi", "makumi abiri", "makumi ashatu", "makumi ana", "makumi ataano",
    "nkaaga", "nshanju", "kinaana", "kyenda",
]


def nyn_words(n: int, agree: str = "") -> str | None:
    """Runyankole for 0-100: 18 -> "ikumi na munaana", 25 -> "makumi abiri na itaano"."""
    if n == 100:
        return "kikumi"
    if not 0 <= n < 100:
        return None
    tens, units = divmod(n, 10)
    unit = _NYN_AGREEING[agree][units] if agree and 1 <= units <= 5 else _NYN_UNITS[units]
    if not tens:
        return unit
    if not units:
        return _NYN_TENS[tens]
    return f"{_NYN_TENS[tens]} na {unit}"


# Acholi counting forms (0-100).
_ACH_UNITS = ["zero", "acel", "aryo", "adek", "angwen", "abic", "abicel", "abiro", "aboro", "abongwen"]
_ACH_TENS = [
    "", "apar", "pyeraryo", "pyeradek", "pyerangwen", "pyerabic",
    "pyerabicel", "pyerabiro", "pyeraboro", "pyerabongwen",
]


def ach_words(n: int) -> str | None:
    """Acholi for 0-100: 18 -> "apar wiye aboro", 25 -> "pyeraryo wiye abic"."""
    if n == 100:
        return "miya acel"
    if not 0 <= n < 100:
        return None
    tens, units = divmod(n, 10)
    unit = _ACH_UNITS[units]
    if not tens:
        return unit
    if not units:
        return _ACH_TENS[tens]
    return f"{_ACH_TENS[tens]} wiye {unit}"


def decimal_words(number: str, locale: str) -> str:
    """"2.5" -> "mbili nukta tano" (sw) or "two point five": digits after the point one by one."""
    cleaned = (number or "").replace(",", "").strip()
    is_neg = cleaned.startswith("-")
    if cleaned.startswith(("+", "-")):
        cleaned = cleaned[1:].strip()
    whole, _, fraction = cleaned.partition(".")
    whole_int = int(whole) if whole else 0
    frac_digits = [int(d) for d in fraction if d.isdigit()]
    loc = (locale or "en").strip().lower()
    if loc == "sw":
        prefix = "hasi " if is_neg else ""
        if not frac_digits:
            return f"{prefix}{sw_words(whole_int)}"
        return f"{prefix}{sw_words(whole_int)} nukta " + " ".join(_SW_UNITS[d] for d in frac_digits)
    prefix = "minus " if is_neg else ""
    if not frac_digits:
        return f"{prefix}{en_words(whole_int)}"
    return f"{prefix}{en_words(whole_int)} point " + " ".join(_EN_UNITS[d] for d in frac_digits)


# ---------------------------------------------------------------------------
# 5-Locale Vernacular Number-Word & Money Amount Normalizer (lg, sw, nyn, ach)
# ---------------------------------------------------------------------------

VERNACULAR_UNIT_WORDS: dict[str, int] = {
    # 1 (all noun classes across lg, nyn, sw, ach)
    "emu": 1, "kimu": 1, "kamu": 1, "lumu": 1, "gumu": 1, "wamu": 1,
    "emwe": 1, "kimwe": 1, "kamwe": 1, "rumwe": 1, "gumwe": 1, "omwe": 1,
    "moja": 1, "mmoja": 1,
    "acel": 1,
    # 2
    "bbiri": 2, "ebbiri": 2, "biri": 2, "bibiri": 2, "ebibiri": 2, "babiri": 2, "bubiri": 2, "ebiri": 2, "zibiri": 2,
    "ibiri": 2,
    "mbili": 2, "wabili": 2, "miwili": 2,
    "aryo": 2,
    # 3
    "ssatu": 3, "essatu": 3, "satu": 3, "bisatu": 3, "ebisatu": 3, "basatu": 3, "busatu": 3, "esatu": 3,
    "ishatu": 3, "bishatu": 3, "ebishatu": 3, "bushatu": 3, "eshatu": 3,
    "tatu": 3, "watatu": 3, "mitatu": 3,
    "adek": 3,
    # 4
    "nnya": 4, "ennya": 4, "nya": 4, "bina": 4, "ebina": 4, "bana": 4, "buna": 4, "ena": 4,
    "ina": 4,
    "nne": 4, "wane": 4, "mine": 4,
    "angwen": 4,
    # 5
    "ttaano": 5, "ettaano": 5, "taano": 5, "bitaano": 5, "ebitaano": 5, "bataano": 5, "butaano": 5, "etaano": 5,
    "itaano": 5,
    "tano": 5, "watano": 5, "mitano": 5,
    "abic": 5,
    # 6
    "mukaaga": 6, "omukaaga": 6, "sita": 6, "abicel": 6,
    # 7
    "musanvu": 7, "omusanvu": 7, "mushanju": 7, "omushanju": 7, "saba": 7, "abiro": 7,
    # 8
    "munaana": 8, "omunaana": 8, "nane": 8, "minane": 8, "wanane": 8, "aboro": 8,
    # 9
    "mwenda": 9, "omwenda": 9, "tisa": 9, "abongwen": 9,
}

VERNACULAR_TENS_WORDS: dict[str, int] = {
    "kkumi": 10, "ekkumi": 10, "kumi": 10, "ikumi": 10, "ekumi": 10, "apar": 10,
    "abiri": 20, "ishirini": 20, "pyeraryo": 20,
    "asatu": 30, "ashatu": 30, "thelathini": 30, "pyeradek": 30,
    "ana": 40, "arobaini": 40, "pyerangwen": 40,
    "ataano": 50, "hamsini": 50, "pyerabic": 50,
    "nkaaga": 60, "enkaaga": 60, "sitini": 60, "pyerabicel": 60,
    "nsanvu": 70, "ensanvu": 70, "nshanju": 70, "enshanju": 70, "sabini": 70, "pyerabiro": 70,
    "kinaana": 80, "ekinaana": 80, "themanini": 80, "pyeraboro": 80,
    "kyenda": 90, "ekyenda": 90, "tisini": 90, "pyerabongwen": 90,
}

VERNACULAR_HUNDRED_HEADS: frozenset[str] = frozenset({
    "kikumi", "ekikumi", "eigana", "igana",
    "bikumi", "ebikumi", "magana", "amagana",
    "mia", "miya",
})

_CONJUNCTION_TOKENS: frozenset[str] = frozenset({"na", "mu", "ne", "n", "wiye", "ki"})
_TENS_PREFIX_TOKENS: frozenset[str] = frozenset({"amakumi", "makumi"})
_HALF_PHRASE_RE = _re.compile(
    r"^(?:n['’]?ekitundu|ne\s+kitundu|n['’]?ekicweka|ne\s+kicweka|na\s+nusu|ki\s+nucu)\b",
    _re.IGNORECASE,
)

_SINGULAR_ONE_SCALES: frozenset[str] = frozenset({
    "kakadde", "akakadde",
    "kakaikuru", "akakaikuru",
    "kawumbi", "akawumbi",
    "mutwalo", "omutwalo",
    "mutwaro", "omutwaro",
    "lukumi", "olukumi",
    "rukumi", "orukumi",
    "laki", "tutemia",
})

_SCALE_NOUN_RE = _re.compile(
    r"\b(?P<scale>mili+y?oni|bili+y?oni|el?ufu|elfu|tutemia|laki|"
    r"o?bukadde|a?kakadde|o?bukaikuru|a?kakaikuru|"
    r"o?buwumbi|a?kawumbi|e?mitwalo|o?mutwalo|e?mitwaro|o?mutwaro|"
    r"e?nkumi|o?lukumi|o?rukumi)\b",
    _re.IGNORECASE,
)

_WORD_TOKEN_RE = _re.compile(r"\s*([a-zA-Z'’]+)")


def _parse_vernacular_tokens_at(text: str, pos: int) -> tuple[float | None, int]:
    """Parse a vernacular number-word sequence (1..999 + optional .5 half, or sub-1000 digits) starting at *pos*."""
    cur = pos
    total = 0
    matched_any = False

    # Allow numeric digits < 1000 inside a scale segment (e.g. "milioni 3 na elfu 500", "obukadde 3 n'emitwalo 50")
    m_num = _re.match(r"\s*(\d{1,3}(?:\.\d+)?)\b", text[cur:])
    if m_num:
        try:
            d_val = float(m_num.group(1))
            if 0 < d_val < 1000:
                cur += m_num.end()
                m_half_d = _HALF_PHRASE_RE.match(text[cur:].lstrip())
                if m_half_d:
                    ws_len = len(text[cur:]) - len(text[cur:].lstrip())
                    cur += ws_len + m_half_d.end()
                    d_val += 0.5
                return d_val, cur
        except ValueError:
            pass

    def _peek_word(p: int) -> tuple[str, int] | None:
        m = _WORD_TOKEN_RE.match(text, p)
        if not m:
            return None
        return m.group(1), m.end()

    # 1. Optional thousands/hundreds block: lukumi/rukumi (1000 after emitwalo/emitwaro) or kikumi/eigana/igana/mia/miya/bikumi/magana
    w_info = _peek_word(cur)
    if w_info:
        w_low = w_info[0].lower().strip("'’")
        if w_low in ("lukumi", "olukumi", "rukumi", "orukumi"):
            matched_any = True
            total += 1000
            cur = w_info[1]
        elif w_low in VERNACULAR_HUNDRED_HEADS:
            matched_any = True
            cur = w_info[1]
            nxt_info = _peek_word(cur)
            if nxt_info:
                nxt_low = nxt_info[0].lower().strip("'’")
                # In Luganda/Runyankole "bikumi bibiri" / "magana abiri" uses "abiri"=2, "ashatu"=3, "ana"=4, "ataano"=5
                hundred_mult = VERNACULAR_UNIT_WORDS.get(nxt_low) or (
                    {20: 2, 30: 3, 40: 4, 50: 5}.get(VERNACULAR_TENS_WORDS.get(nxt_low, 0))
                    if w_low in ("bikumi", "ebikumi", "magana", "amagana")
                    else None
                )
                if hundred_mult is not None:
                    total += 100 * hundred_mult
                    cur = nxt_info[1]
                else:
                    total += 100
            else:
                total += 100

    # Optional conjunction after hundreds ("na", "mu", "wiye")
    saved_cur = cur
    conj_info = _peek_word(cur)
    if matched_any and conj_info and conj_info[0].lower().strip("'’") in _CONJUNCTION_TOKENS:
        cur = conj_info[1]

    # 2. Optional tens block: (amakumi/makumi + abiri/asatu/...) or single-word tens (kkumi/ikumi/apar/ishirini/pyeraryo/nkaaga...)
    w_info = _peek_word(cur)
    parsed_tens = False
    if w_info:
        w_low = w_info[0].lower().strip("'’")
        if w_low in _TENS_PREFIX_TOKENS:
            nxt_info = _peek_word(w_info[1])
            if nxt_info and nxt_info[0].lower().strip("'’") in VERNACULAR_TENS_WORDS:
                total += VERNACULAR_TENS_WORDS[nxt_info[0].lower().strip("'’")]
                cur = nxt_info[1]
                matched_any = True
                parsed_tens = True
        elif w_low in VERNACULAR_TENS_WORDS:
            total += VERNACULAR_TENS_WORDS[w_low]
            cur = w_info[1]
            matched_any = True
            parsed_tens = True

    if parsed_tens:
        saved_after_tens = cur
        conj_info2 = _peek_word(cur)
        if conj_info2:
            c_raw = conj_info2[0].lower().replace("’", "'")
            if c_raw.startswith("n'") and c_raw[2:] in VERNACULAR_UNIT_WORDS and c_raw not in ("n'ekitundu", "n'ekicweka"):
                total += VERNACULAR_UNIT_WORDS[c_raw[2:]]
                cur = conj_info2[1]
            elif c_raw.strip("'") in _CONJUNCTION_TOKENS:
                u_info = _peek_word(conj_info2[1])
                if u_info and u_info[0].lower().strip("'’") in VERNACULAR_UNIT_WORDS:
                    total += VERNACULAR_UNIT_WORDS[u_info[0].lower().strip("'’")]
                    cur = u_info[1]
                else:
                    cur = saved_after_tens
    else:
        # 3. Standalone unit (1..9) or elided n'<unit> after hundreds ("kikumi n'ebitaano" = 105)
        if w_info:
            w_raw = w_info[0].lower().replace("’", "'")
            w_clean = w_raw.strip("'")
            if w_clean in VERNACULAR_UNIT_WORDS:
                total += VERNACULAR_UNIT_WORDS[w_clean]
                cur = w_info[1]
                matched_any = True
            elif (
                matched_any
                and w_raw.startswith("n'")
                and w_raw[2:] in VERNACULAR_UNIT_WORDS
                and w_raw not in ("n'ekitundu", "n'ekicweka")
            ):
                total += VERNACULAR_UNIT_WORDS[w_raw[2:]]
                cur = w_info[1]
            else:
                cur = saved_cur
        else:
            cur = saved_cur

    if not matched_any:
        return None, pos

    # 4. Optional fractional half ("n'ekitundu", "n'ekicweka", "na nusu", "ki nucu")
    m_half = _HALF_PHRASE_RE.match(text[cur:].lstrip())
    if m_half:
        ws_len = len(text[cur:]) - len(text[cur:].lstrip())
        cur += ws_len + m_half.end()
        return float(total) + 0.5, cur

    return float(total), cur


_SCALE_NOUN_MULTIPLIERS: dict[str, float] = {
    "milioni": 1e6, "miliyoni": 1e6,
    "bilioni": 1e9, "biliyoni": 1e9,
    "elfu": 1e3, "elufu": 1e3, "tutemia": 1e3,
    "laki": 1e5,
    "bukadde": 1e6, "obukadde": 1e6, "kakadde": 1e6, "akakadde": 1e6,
    "bukaikuru": 1e6, "obukaikuru": 1e6, "kakaikuru": 1e6, "akakaikuru": 1e6,
    "buwumbi": 1e9, "obuwumbi": 1e9, "kawumbi": 1e9, "akawumbi": 1e9,
    "mitwalo": 1e4, "emitwalo": 1e4, "mutwalo": 1e4, "omutwalo": 1e4,
    "mitwaro": 1e4, "emitwaro": 1e4, "mutwaro": 1e4, "omutwaro": 1e4,
    "nkumi": 1e3, "enkumi": 1e3, "lukumi": 1e3, "olukumi": 1e3,
    "rukumi": 1e3, "orukumi": 1e3,
}

_DESCENDING_SCALE_LINK_RE = _re.compile(
    r"^\s*(?:(?:na|mu|ne|wiye|ki)\s+|n['’])?(?P<next_scale>mili+y?oni|bili+y?oni|el?ufu|elfu|tutemia|laki|"
    r"o?bukadde|a?kakadde|o?bukaikuru|a?kakaikuru|"
    r"o?buwumbi|a?kawumbi|e?mitwalo|o?mutwalo|e?mitwaro|o?mutwaro|"
    r"e?nkumi|o?lukumi|o?rukumi)\b",
    _re.IGNORECASE,
)
_DESCENDING_SUB_THOUSAND_LINK_RE = _re.compile(
    r"^\s*(?:na|mu|ne|wiye|ki)\s+",
    _re.IGNORECASE,
)


def parse_vernacular_scale_amount_at(text: str, scale_match: _re.Match[str]) -> tuple[float | None, int]:
    """Parse a scale-prefixed vernacular amount (including descending sub-scales) starting at *scale_match*."""
    first_scale = scale_match.group("scale").lower()
    first_mult = _SCALE_NOUN_MULTIPLIERS.get(first_scale, 1.0)
    val1, end_pos = _parse_vernacular_tokens_at(text, scale_match.end())
    if val1 is None:
        if first_scale in _SINGULAR_ONE_SCALES and not _re.match(r"^\s*\d", text[scale_match.end() :]):
            val1 = 1.0
            end_pos = scale_match.end()
        else:
            return None, scale_match.end()

    total_ugx = val1 * first_mult
    prev_mult = first_mult
    cur_end = end_pos

    # Fold strictly descending scale segments joined by na / mu / ne / n' / ki
    # e.g. "milioni tatu na elfu mia tano" (3M + 500k = 3.5M), "obukadde busatu n'emitwalo ataano" (3.5M)
    while True:
        m_link = _DESCENDING_SCALE_LINK_RE.match(text[cur_end:])
        if m_link:
            nxt_scale = m_link.group("next_scale").lower()
            nxt_mult = _SCALE_NOUN_MULTIPLIERS.get(nxt_scale, 1.0)
            if nxt_mult < prev_mult:
                seg_start = cur_end + m_link.end()
                nxt_val, nxt_end = _parse_vernacular_tokens_at(text, seg_start)
                if nxt_val is None and nxt_scale in _SINGULAR_ONE_SCALES and not _re.match(r"^\s*\d", text[seg_start:]):
                    nxt_val = 1.0
                    nxt_end = seg_start
                if nxt_val is not None:
                    total_ugx += nxt_val * nxt_mult
                    prev_mult = nxt_mult
                    cur_end = nxt_end
                    continue
        # Also fold trailing sub-thousand hundred/ten/unit block after a >=1000 scale ("elfu mbili na mia tano" -> 2500)
        if prev_mult >= 1000.0:
            m_sub = _DESCENDING_SUB_THOUSAND_LINK_RE.match(text[cur_end:])
            if m_sub:
                sub_start = cur_end + m_sub.end()
                sub_val, sub_end = _parse_vernacular_tokens_at(text, sub_start)
                if sub_val is not None and sub_val < 1000.0:
                    total_ugx += sub_val
                    prev_mult = 1.0
                    cur_end = sub_end
                    continue
        break

    return total_ugx, cur_end


_CANONICAL_SCALE_NAMES: dict[str, str] = {
    "miliyoni": "milioni",
    "biliyoni": "bilioni",
    "elufu": "elfu",
}


def normalize_vernacular_amount_words(text: str) -> str:
    """Convert Luganda, Swahili, Runyankole, and Acholi number words after scale nouns into digits."""
    if not text:
        return ""
    out: list[str] = []
    last_idx = 0
    for m in _SCALE_NOUN_RE.finditer(text):
        if m.start() < last_idx:
            continue
        out.append(text[last_idx : m.start()])
        scale = m.group("scale")
        canon_scale = _CANONICAL_SCALE_NAMES.get(scale.lower(), scale)
        first_mult = _SCALE_NOUN_MULTIPLIERS.get(scale.lower(), 1.0)
        total_ugx, end_pos = parse_vernacular_scale_amount_at(text, m)
        if total_ugx is not None:
            scaled_val = total_ugx / first_mult
            num_str = str(int(scaled_val)) if scaled_val == int(scaled_val) else f"{scaled_val:g}"
            out.append(f"{canon_scale} {num_str}")
            last_idx = end_pos
        else:
            out.append(m.group(0))
            last_idx = m.end()
    out.append(text[last_idx:])
    return "".join(out)
