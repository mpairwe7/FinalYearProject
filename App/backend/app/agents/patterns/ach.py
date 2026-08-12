"""Acholi routing patterns — conservative seed.

``corpus_backed=False``, on the same terms as :mod:`.nyn`: no Acholi
eval corpus exists, so this is vocabulary rather than observed taxpayer
text, and it stays deliberately narrow.

## One collision worth knowing about

**Acholi ``tin`` means "today".** It is also, in every URA context, the
Tax Identification Number — the single most common noun in this
product's traffic. A temporal pattern matching ``\\btin\\b`` would send
"how do I get a TIN" to the calendar tool, in the one locale where the
user is least able to tell that the answer is off-topic.

``tin`` is therefore **excluded** from the temporal cues here, and
"today" in Acholi is matched only by the unambiguous ``kombedi``
("now"). This is a real loss of coverage accepted on purpose; resolving
it needs the surrounding-word evidence a golden set would provide, not
a cleverer regex.
"""

from __future__ import annotations

import re

from . import LocalePatterns

#: "adi" = how much/how many. ``mucoro`` = tax.
_MUCH = r"\badi\b"
_TAX_NOUN = r"\b(mucoro|mucoro\s+me)\b"


_RATE = (
    (
        re.compile(rf"(?=.*{_TAX_NOUN})(?=.*{_MUCH})", re.IGNORECASE),
        "Rate lookup intent (ach)",
        ["lookup_rate", "list_available_rates"],
    ),
    (
        re.compile(
            rf"(?=.*\b(vat|paye|corporate\s+tax|corporation\s+tax|withholding|wht|"
            rf"customs|excise)\b)(?=.*{_MUCH})",
            re.IGNORECASE,
        ),
        "Rate lookup intent (ach: code-switched)",
        ["lookup_rate", "list_available_rates"],
    ),
)

#: ``kombedi`` only — see the module docstring on why ``tin`` is absent.
_TEMPORAL = (
    (
        re.compile(r"\b(kombedi)\b", re.IGNORECASE),
        "Needs current date (ach)",
        ["get_current_date"],
    ),
)

_ESCALATE = (
    (
        re.compile(
            r"\b(alok|walok|lok)\b(?:\s+\S+){0,2}\s*(?:ki|kede)\s*"
            r"\b(dano|ngat|latic)\b",
            re.IGNORECASE,
        ),
        "User explicitly asked for a human (ach)",
    ),
    (
        re.compile(r"\b(kok|ngol\s+kop|lakwat\s+kop)\b", re.IGNORECASE),
        "Legal / dispute context needs human handling (ach)",
    ),
)

_RATE_CUE = (re.compile(_MUCH, re.IGNORECASE),)
_TEMPORAL_CUE = (re.compile(r"\bkombedi\b", re.IGNORECASE),)
_GRATITUDE_PHRASES = frozenset({"apwoyo", "apwoyo matek"})


ACH_PATTERNS = LocalePatterns(
    locale="ach",
    corpus_backed=False,
    rate=_RATE,
    temporal=_TEMPORAL,
    escalate=_ESCALATE,
    rate_cue=_RATE_CUE,
    temporal_cue=_TEMPORAL_CUE,
    gratitude_phrases=_GRATITUDE_PHRASES,
)
