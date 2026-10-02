"""Kiswahili (sw) routing patterns for supervisor and conversational intents."""

from __future__ import annotations

import re

from . import LocalePatterns

_TAX_NOUN = r"\b(kodi|ushuru)\b"
_HOW_MUCH = r"\b(kiasi\s+gani|asilimia\s+ngapi|kiwango\s+gani|ngapi)\b"

_RATE = (
    (
        re.compile(rf"(?=.*{_TAX_NOUN})(?=.*{_HOW_MUCH})", re.IGNORECASE),
        "Rate lookup intent (sw)",
        ["lookup_rate", "list_available_rates"],
    ),
    (
        re.compile(
            rf"(?=.*\b(vat|paye|kodi\s+ya\s+mapato|kodi\s+ya\s+zuio|zuio|forodha|ushuru\s+wa\s+bidhaa)\b)(?=.*{_HOW_MUCH})",
            re.IGNORECASE,
        ),
        "Rate lookup intent (sw: tax heads)",
        ["lookup_rate", "list_available_rates"],
    ),
)

_TEMPORAL = (
    (
        re.compile(r"\b(leo|tarehe\s+ya\s+leo|sasa|wakati\s+huu)\b", re.IGNORECASE),
        "Needs current date (sw)",
        ["get_current_date"],
    ),
    (
        re.compile(r"\b(tarehe\s+ya\s+mwisho|mwisho\s+wa\s+kuwasilisha|lini|tarehe\s+gani)\b", re.IGNORECASE),
        "Upcoming deadlines (sw)",
        ["get_next_deadlines"],
    ),
)

_ESCALATE = (
    (
        re.compile(
            r"\b(zungumza\s+na\s+mtu|ongea\s+na\s+afisa|msaada\s+wa\s+binadamu|afisa\s+wa\s+kodi|nipigie\s+simu|huduma\s+kwa\s+wateja)\b",
            re.IGNORECASE,
        ),
        "Explicit officer request (sw)",
    ),
)

_LEARN_INTENT = (
    re.compile(r"\b(nifunze|nieleze|maana\s+ya|fafanua|eleza|jinsi\s+inavyofanya\s+kazi)\b", re.IGNORECASE),
)

_LEARN_TOPIC = (
    re.compile(r"\b(vat|paye|efris|tin|kodi\s+ya\s+zuio|kodi\s+ya\s+pango|forodha)\b", re.IGNORECASE),
)

_AMOUNT_CUE = (
    re.compile(r"\b(shilingi|ushs|ugx|pesa|mshahara)\s*\d+", re.IGNORECASE),
)

_RATE_CUE = (
    re.compile(r"\b(kiwango|asilimia)\b", re.IGNORECASE),
)

_TEMPORAL_CUE = (
    re.compile(r"\b(tarehe|mwisho|lini|mwezi|mwaka)\b", re.IGNORECASE),
)

_GREETING_WORDS = frozenset({
    "habari", "hujambo", "jambo", "mambo", "shikamoo", "salaam", "salama",
    "alamsiki", "marahaba", "hallow", "halo",
})

_GREETING_PHRASES = frozenset({
    "habari yako", "habari za asubuhi", "habari za mchana", "habari za jioni",
    "habari gani", "mambo vipi", "shikamoo sana", "uhali gani", "habari za leo",
    "hujambo bwana", "hujambo bibi", "jambo sana", "habari za kazi", "habari za mchana",
    "habari ya leo", "habari za kutwa",
})

_GRATITUDE_PHRASES = frozenset({
    "asante", "asante sana", "shukrani", "shukrani sana", "nashukuru",
    "ahsante", "ahsante sana", "asante kwa msaada", "asante mno",
})

_FAREWELL_PHRASES = frozenset({
    "kwaheri", "kwaheri ya kuonana", "tutaonana", "baadaye", "usiku mwema",
    "mchana mwema", "kwaheri sana", "siku njema", "tuonane", "kwa heri",
})

_CLARIFY_STOP_WORDS = frozenset({"nini", "gani", "ipi", "vipi", "wapi", "lini"})

SW_PATTERNS = LocalePatterns(
    locale="sw",
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
    gratitude_phrases=_GRATITUDE_PHRASES,
    farewell_phrases=_FAREWELL_PHRASES,
    clarify_stop_words=_CLARIFY_STOP_WORDS,
)
