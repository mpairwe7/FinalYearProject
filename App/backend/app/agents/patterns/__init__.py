"""Locale-keyed supervisor pattern tables.

The supervisor's routing tables were English regex, and only English.
Measured against the twelve real Luganda questions in
``Data/eval/rag_eval_lg.jsonl``, **all twelve** fell through every table
to the default retrieval route — including ``"Njagala okwogera
n'omuntu"`` ("I want to speak to a person"), which is an explicit
request for a human that the English escalation patterns cannot see.

Code-switched queries carrying an Arabic numeral already routed
correctly, because :func:`app.calculator_router.has_money_amount` keys
on digits and the tax nouns taxpayers use ("VAT", "PAYE", "corporate
tax") are English even in a Luganda sentence. So the missing piece is
not translation of the whole table — it is the **trigger vocabulary**:
the question words and verbs that say *how much*, *what is*, *when*,
and *I want a person*.

## Why locale patterns are additive

``for_locale()`` returns the English tables **plus** the locale's own,
English first. Two reasons, and the second is the load-bearing one:

1. Ugandan taxpayers code-switch constantly. ``"Withholding tax kye
   ki?"`` and ``"Corporate tax rate ya Uganda y'emeka?"`` are both from
   the real corpus. A table that replaced English would stop matching
   the English half of the taxpayer's own sentence.
2. Adding cannot regress. ``for_locale("en")`` returns the English
   tables unchanged, and for any other locale the English patterns are
   still tried first, in their original order. The 36/36 English
   routing score is therefore structurally protected rather than
   re-measured and hoped for.

## Confidence in the vocabulary

The Luganda tables are derived from ``Data/eval/rag_eval_lg.jsonl`` —
real questions, not invented ones. The Runyankole and Acholi tables are
a **conservative seed** of terms only, and are marked as such on each
table. They need native-speaker review before ``multilingual_routing``
opens for those locales; the per-locale golden set in
:mod:`app.agents.eval_routing` is the gate that enforces it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

#: ``(pattern, reason, tools)`` — a route with a tool whitelist.
CalcRule = tuple[re.Pattern[str], str, list[str]]
#: ``(pattern, reason)`` — a route with no whitelist.
ReasonRule = tuple[re.Pattern[str], str]


@dataclass(frozen=True)
class LocalePatterns:
    """Every table the supervisor iterates, for one locale.

    Frozen and built once per locale — these compile regexes, and the
    supervisor runs on every request.
    """

    #: Human label, for logging and the eval report.
    locale: str
    #: True when the vocabulary has been checked against real user text
    #: rather than assembled from a dictionary. Gates the flag.
    corpus_backed: bool = False

    calc: tuple[CalcRule, ...] = ()
    temporal: tuple[CalcRule, ...] = ()
    rate: tuple[CalcRule, ...] = ()
    customs: tuple[re.Pattern[str], ...] = ()
    escalate: tuple[ReasonRule, ...] = ()

    #: Learning-intent detection: an intent cue AND a topic cue, with
    #: the three guards below deferring to the numeric/rate/temporal
    #: routes. Tuples are matched with ``any()``.
    learn_intent: tuple[re.Pattern[str], ...] = ()
    learn_topic: tuple[re.Pattern[str], ...] = ()
    amount_cue: tuple[re.Pattern[str], ...] = ()
    rate_cue: tuple[re.Pattern[str], ...] = ()
    temporal_cue: tuple[re.Pattern[str], ...] = ()

    greeting_words: frozenset[str] = field(default_factory=frozenset)
    greeting_phrases: frozenset[str] = field(default_factory=frozenset)
    gratitude_phrases: frozenset[str] = field(default_factory=frozenset)
    farewell_phrases: frozenset[str] = field(default_factory=frozenset)
    clarify_stop_words: frozenset[str] = field(default_factory=frozenset)

    def merge(self, other: LocalePatterns) -> LocalePatterns:
        """Return self with *other*'s tables appended after its own.

        Order is the contract: an English pattern is always tried
        before the locale extension, so a locale can add coverage but
        cannot pre-empt an existing English decision.
        """
        return LocalePatterns(
            locale=other.locale,
            corpus_backed=other.corpus_backed,
            calc=self.calc + other.calc,
            temporal=self.temporal + other.temporal,
            rate=self.rate + other.rate,
            customs=self.customs + other.customs,
            escalate=self.escalate + other.escalate,
            learn_intent=self.learn_intent + other.learn_intent,
            learn_topic=self.learn_topic + other.learn_topic,
            amount_cue=self.amount_cue + other.amount_cue,
            rate_cue=self.rate_cue + other.rate_cue,
            temporal_cue=self.temporal_cue + other.temporal_cue,
            greeting_words=self.greeting_words | other.greeting_words,
            greeting_phrases=self.greeting_phrases | other.greeting_phrases,
            gratitude_phrases=self.gratitude_phrases | other.gratitude_phrases,
            farewell_phrases=self.farewell_phrases | other.farewell_phrases,
            clarify_stop_words=self.clarify_stop_words | other.clarify_stop_words,
        )


def any_match(patterns: tuple[re.Pattern[str], ...], text: str) -> bool:
    """True if any of *patterns* matches *text*."""
    return any(p.search(text) for p in patterns)


@lru_cache(maxsize=16)
def _merged(key: str) -> LocalePatterns:
    """Build the tables for an already-normalized locale *key*.

    Cached because the merge concatenates tuples and the result is
    immutable — the supervisor asks for this on every request.
    """
    from .en import EN_PATTERNS

    extension = _EXTENSIONS.get(key)
    if extension is None:
        return EN_PATTERNS
    return EN_PATTERNS.merge(extension)


def for_locale(locale: str) -> LocalePatterns:
    """Tables for *locale*, English-only for unknown or empty locales.

    Normalization happens *before* the cache so that ``"lg"``,
    ``"lg-UG"``, ``"lg_UG"`` and ``"LG"`` share one entry and one built
    object, rather than filling a bounded cache with copies of the same
    tables under different regional tags.
    """
    key = (locale or "en").strip().lower()
    # "lg-UG" and "lg_UG" are the same routing locale as "lg".
    key = re.split(r"[-_]", key)[0]
    return _merged(key)


def supported_locales() -> list[str]:
    """Locales with their own tables, English first."""
    return ["en", *sorted(_EXTENSIONS)]


def _load_extensions() -> dict[str, LocalePatterns]:
    from .ach import ACH_PATTERNS
    from .lg import LG_PATTERNS
    from .nyn import NYN_PATTERNS

    return {p.locale: p for p in (LG_PATTERNS, NYN_PATTERNS, ACH_PATTERNS)}


_EXTENSIONS: dict[str, LocalePatterns] = _load_extensions()

__all__ = [
    "CalcRule",
    "LocalePatterns",
    "ReasonRule",
    "any_match",
    "for_locale",
    "supported_locales",
]
