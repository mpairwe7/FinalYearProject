"""Runyankole routing patterns — conservative seed.

``corpus_backed=False``. Unlike Luganda, there is no Runyankole eval
corpus in ``Data/eval/``, so this table is assembled from vocabulary
rather than from observed taxpayer text. It contains only terms whose
meaning is unambiguous, and it deliberately stops short of the
learning-intent and greeting coverage the Luganda table carries —
a wrong greeting pattern silently swallows a real question.

**This table must not be trusted until a native speaker reviews it and
a Runyankole golden set exists.** ``eval_routing.locale_gate()`` refuses
to pass a locale that is not corpus-backed, which is what keeps the
``multilingual_routing`` flag from opening on guesses.
"""

from __future__ import annotations

import re

from . import LocalePatterns

#: "shangahi" = how much. ``omushoro``/``emishoro`` = tax/taxes.
_MUCH = r"\bshangahi\b"
_TAX_NOUN = r"\b(omushoro|emishoro|mushoro)\b"


_RATE = (
    (
        re.compile(rf"(?=.*{_TAX_NOUN})(?=.*{_MUCH})", re.IGNORECASE),
        "Rate lookup intent (nyn)",
        ["lookup_rate", "list_available_rates"],
    ),
    (
        re.compile(
            rf"(?=.*\b(vat|paye|corporate\s+tax|corporation\s+tax|withholding|wht|"
            rf"customs|excise)\b)(?=.*{_MUCH})",
            re.IGNORECASE,
        ),
        "Rate lookup intent (nyn: code-switched)",
        ["lookup_rate", "list_available_rates"],
    ),
)

_TEMPORAL = (
    (
        re.compile(r"\b(erizooba|obwire\s+obu|hati)\b", re.IGNORECASE),
        "Needs current date (nyn)",
        ["get_current_date"],
    ),
)

_ESCALATE = (
    (
        re.compile(
            r"\b(kugamba|ngambe|kwegamba)\b(?:\s+\S+){0,2}\s*na?\s*"
            r"\b(omuntu|muntu|omukozi)\b",
            re.IGNORECASE,
        ),
        "User explicitly asked for a human (nyn)",
    ),
    (
        re.compile(r"\b(okujurira|kujurira|orubanja)\b", re.IGNORECASE),
        "Legal / dispute context needs human handling (nyn)",
    ),
)

_RATE_CUE = (re.compile(_MUCH, re.IGNORECASE),)
_TEMPORAL_CUE = (re.compile(r"\b(erizooba|hati)\b", re.IGNORECASE),)
_GRATITUDE_PHRASES = frozenset({"webare", "webare munonga", "webale munonga"})


NYN_PATTERNS = LocalePatterns(
    locale="nyn",
    corpus_backed=False,
    rate=_RATE,
    temporal=_TEMPORAL,
    escalate=_ESCALATE,
    rate_cue=_RATE_CUE,
    temporal_cue=_TEMPORAL_CUE,
    gratitude_phrases=_GRATITUDE_PHRASES,
)
