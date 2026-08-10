"""Luganda routing patterns.

Vocabulary is taken from ``Data/eval/rag_eval_lg.jsonl`` — twelve real
taxpayer questions — rather than from a dictionary, so the forms here
are the ones people actually type, including the possessive contractions
(``gw'ameka``, ``y'emeka``) that a naive word list misses.

## What this table deliberately does not contain

**No calculation patterns.** A Luganda query carrying an Arabic numeral
already routes correctly: ``detect_calculator_intent`` keys on the tax
noun — which taxpayers write in English even mid-Luganda-sentence — and
``has_money_amount`` keys on digits. ``"VAT ku 500000 y'emeka?"`` was
measured routing to ``calculate_vat`` before this module existed.

Adding a Luganda calculation pattern would actively break the other
case. The calculator table is checked *before* the rate table, so
``"Omusolo gwa VAT gw'ameka?"`` — "what is the VAT rate", carrying no
amount — would be handed to ``calculate_vat``, which has nothing to
calculate. The ``-meka`` patterns therefore live in the **rate** table,
where a query with no amount belongs, and the amount case continues to
be caught earlier by the existing numeric path.

**No ``obudde`` temporal cue.** ``obudde`` is "time", but the corpus
uses it in ``"nga wayiise obudde"`` — "when the time has passed", i.e.
*late payment*. Treating it as a date question would route a penalty
question to the calendar.
"""

from __future__ import annotations

import re

from . import LocalePatterns

#: "-meka" is the how-much/how-many root. It carries a noun-class
#: concord prefix that changes with the subject — ``gw'ameka`` for
#: ``omusolo``, ``y'emeka`` for ``rate``, bare ``ameka`` standalone —
#: so the match is on the root with a lookbehind that allows an
#: apostrophe or a space before it but not a letter, which would mean
#: the root fell inside some other word.
_MEKA = r"(?<![a-z])[ae]meka\b"

#: Tax nouns. ``omusolo`` singular, ``emisolo`` plural, ``musolo`` after
#: a preposition ("gwa musolo").
_TAX_NOUN = r"\b(omusolo|emisolo|musolo|omusolo)\b"


# Rate lookups — "how much is X" with no amount present.
_RATE = (
    (
        re.compile(rf"(?=.*{_TAX_NOUN})(?=.*{_MEKA})", re.IGNORECASE),
        "Rate lookup intent (lg: -meka on a tax noun)",
        ["lookup_rate", "list_available_rates"],
    ),
    (
        # Code-switched: an English tax noun with a Luganda "how much".
        # "Corporate tax rate ya Uganda y'emeka?" is from the corpus.
        re.compile(
            rf"(?=.*\b(vat|paye|corporate\s+tax|corporation\s+tax|withholding|wht|"
            rf"customs|excise|rental\s+tax|capital\s+gains?)\b)(?=.*{_MEKA})",
            re.IGNORECASE,
        ),
        "Rate lookup intent (lg: -meka on an English tax noun)",
        ["lookup_rate", "list_available_rates"],
    ),
)

# Temporal — date-relative questions only.
_TEMPORAL = (
    (
        re.compile(r"\b(leero|olwaleero|kaakati|enkya|jjo)\b", re.IGNORECASE),
        "Needs current date (lg)",
        ["get_current_date"],
    ),
    (
        re.compile(r"\b(omwaka|omwezi)\s+guno\b", re.IGNORECASE),
        "Needs current fiscal-year context (lg)",
        ["get_current_date"],
    ),
)

# Escalation. Every pattern here is a plain alternation or a bounded
# adjacency — no ``.*`` — because these run against the *whole* query
# rather than the truncated probe, and a request for a human has to be
# found however far into a long message it appears.
_ESCALATE = (
    (
        re.compile(
            # "njagala okwogera n'omuntu" — I want to speak with a person.
            r"\b(okwogera|nyogere|twogere|okwogerako)\b"
            r"(?:\s+\S+){0,2}\s*n?['’]?\s*"
            r"\b(omuntu|muntu|omukozi|omuweereza)\b",
            re.IGNORECASE,
        ),
        "User explicitly asked for a human (lg)",
    ),
    (
        re.compile(r"\bnjagala\s+(?:okulaba\s+)?(?:omuntu|muntu|omukozi)\b", re.IGNORECASE),
        "User explicitly asked for a human (lg)",
    ),
    (
        # okuwakanya = to contest/object; okujulira = to appeal;
        # omusango = a legal case. All three are dispute territory.
        re.compile(r"\b(okuwakanya|kuwakanya|nwakanya|okujulira|omusango|loya)\b", re.IGNORECASE),
        "Legal / dispute context needs human handling (lg)",
    ),
)

# Learning intents. "X kye ki" = what is X; "ekola etya" = how does it
# work; "nnyonnyola" = explain.
#
# Bare ``kiki`` is deliberately excluded. It is "what", but the corpus
# uses it in ``"kiki ekibaawo"`` — "what happens" — which is a
# consequence question ("what happens if you pay late"), not a request
# for a definition. Including it sent two penalty questions to the
# education tool instead of to retrieval, which holds the actual
# 2%-per-month answer. The definitional form is the postposed ``kye ki``.
_LEARN_INTENT = (
    re.compile(
        r"\b(kye\s+ki|bye\s+ki|ekola\s+etya|nnyonnyola|nnyonnyolako|"
        r"amakulu\s+ga|tegeeza)\b",
        re.IGNORECASE,
    ),
)
#: Only the Luganda tax nouns. EFRIS, TIN and the other URA system names
#: are deliberately absent: the education tool teaches tax *concepts*,
#: and retrieval answers system questions better. A learning intent on a
#: system name should keep falling through to RAG.
_LEARN_TOPIC = (re.compile(_TAX_NOUN, re.IGNORECASE),)

#: ``-meka`` is a rate cue, so a learning intent carrying it defers to
#: the rate route — the same deferral the English ``rate|percentage``
#: cue performs.
_RATE_CUE = (re.compile(_MEKA, re.IGNORECASE),)
#: Luganda magnitude words, so "obukadde bubiri" (two million) reads as
#: an amount and defers a learning intent to the calculators.
_AMOUNT_CUE = (re.compile(r"\b(akakadde|obukadde|olukumi|enkumi)\b", re.IGNORECASE),)
_TEMPORAL_CUE = (re.compile(r"\b(leero|olwaleero|kaakati|enkya)\b", re.IGNORECASE),)

#: ``ki`` is absent on purpose: alone it means "what?", which is a
#: request for clarification, not a greeting. It appears in
#: ``clarify_stop_words`` instead.
_GREETING_WORDS = frozenset({"gyebale", "gyebaleko", "mwasuze"})
_CLARIFY_STOP_WORDS = frozenset({"ki", "otya", "ntya"})
_GREETING_PHRASES = frozenset({
    "oli otya", "oli otya nno", "wasuze otya", "wasuze otya nno",
    "osiibye otya", "osiibye otya nno", "mwasuze mutya", "mwasiibye mutya",
    "ki kati", "agafayo",
})
_GRATITUDE_PHRASES = frozenset({
    "webale nyo", "weebale nnyo", "weebale nyo", "webale nnyo",
    "mwebale", "mwebale nyo", "webale ky'okoze",
})
_FAREWELL_PHRASES = frozenset({
    "weeraba", "tunaalabagana", "siiba bulungi", "sula bulungi", "mpaka",
})


LG_PATTERNS = LocalePatterns(
    locale="lg",
    corpus_backed=True,
    rate=_RATE,
    temporal=_TEMPORAL,
    escalate=_ESCALATE,
    learn_intent=_LEARN_INTENT,
    learn_topic=_LEARN_TOPIC,
    amount_cue=_AMOUNT_CUE,
    rate_cue=_RATE_CUE,
    temporal_cue=_TEMPORAL_CUE,
    greeting_words=_GREETING_WORDS,
    greeting_phrases=_GREETING_PHRASES,
    clarify_stop_words=_CLARIFY_STOP_WORDS,
    gratitude_phrases=_GRATITUDE_PHRASES,
    farewell_phrases=_FAREWELL_PHRASES,
)
