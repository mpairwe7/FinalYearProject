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


def decimal_words(number: str, locale: str) -> str:
    """"2.5" -> "mbili nukta tano" (sw) or "two point five": digits after the point one by one."""
    whole, _, fraction = number.partition(".")
    if locale == "sw":
        return f"{sw_words(int(whole))} nukta " + " ".join(_SW_UNITS[int(d)] for d in fraction)
    return f"{en_words(int(whole))} point " + " ".join(_EN_UNITS[int(d)] for d in fraction)
