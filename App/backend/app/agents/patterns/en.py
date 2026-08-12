"""English routing patterns — the original supervisor tables.

Moved here **verbatim** from ``app.agents.supervisor`` when routing
became locale-keyed. Every pattern, its ordering, and the comments
explaining the non-obvious ones are unchanged, because English routing
scores 36/36 on the golden set and this refactor must not be the reason
that moves.
"""

from __future__ import annotations

import re

from . import LocalePatterns

# Calculation intents — the model should not guess numbers here.
# Tools should be called for definitive answers.
_CALC = (
    (
        re.compile(
            r"\b(how\s+much|calculate|compute|work\s+out|what\s+is)\b.*\b(vat|v\.a\.t)\b",
            re.IGNORECASE,
        ),
        "VAT calculation intent",
        ["calculate_vat", "lookup_rate"],
    ),
    (
        re.compile(
            r"\b(how\s+much|calculate|work\s+out)\b.*\b(paye|take[- ]?home|net\s+pay|salary\s+tax)\b",
            re.IGNORECASE,
        ),
        "PAYE calculation intent",
        ["calculate_paye"],
    ),
    (
        re.compile(
            r"\b(corporation|corporate|company)\s+tax\b.*\b(calculate|how\s+much|on|for)\b",
            re.IGNORECASE,
        ),
        "Corporation tax calculation intent",
        ["calculate_corporation_tax"],
    ),
    (
        re.compile(
            # Order-agnostic lookahead: needs "capital gains" AND a
            # calc/transaction trigger word anywhere in the query.
            r"(?=.*\bcapital\s+gains?\b)"
            r"(?=.*\b(calculate|how\s+much|sold|sell(?:ing)?|gain|profit|cgt)\b)",
            re.IGNORECASE,
        ),
        "Capital gains calculation intent",
        ["calculate_capital_gains"],
    ),
    (
        re.compile(
            # Order-agnostic: needs a customs noun AND a calc trigger
            # anywhere in the query (covers both "how much customs
            # duty" and "customs duty for X how much").
            r"(?=.*\b(import(?:ing)?|landed\s+cost|customs\s+duty|customs|cif)\b)"
            r"(?=.*\b(how\s+much|cost|calculate|estimate)\b)",
            re.IGNORECASE,
        ),
        "Customs duty calculation intent",
        ["calculate_customs_duty", "lookup_rate"],
    ),
    (
        re.compile(
            r"(?=.*\brent(?:al)?\b)"
            r"(?=.*\b(how\s+much|calculate|compute|work\s+out|tax\s+on)\b)",
            re.IGNORECASE,
        ),
        "Rental income tax calculation intent",
        ["calculate_rental_tax", "lookup_rate"],
    ),
    (
        re.compile(
            r"(?=.*\b(withholding|wht)\b)"
            r"(?=.*\b(how\s+much|calculate|compute|work\s+out|deduct)\b)",
            re.IGNORECASE,
        ),
        "Withholding tax calculation intent",
        ["calculate_withholding", "lookup_rate"],
    ),
)

# Learning intents — "explain VAT", "how does PAYE work". These share
# vocabulary with the calculators ("what is VAT" vs "what is the VAT on
# 5 million"), so the guards below decide which. Every guard *defers*:
# a query carrying an amount, a rate word or a temporal word is handed
# to the existing calculator / rate / calendar patterns untouched, so
# adding this route cannot change where any prior query went.
#
# Every alternative below is anchored on literal words with a single
# ``\s+`` between them.  An optional group next to ``\s*`` — the shape
# ``what'?s\s+(?:a|an|the)?\s*\w*\s*mean`` — lets a run of spaces be
# split between quantifiers in exponentially many ways, and this regex
# runs on raw user input on every request.  "What does X mean" is
# already covered by the ``what\s+(?:is|are|does)`` alternative, so the
# ambiguous branch bought nothing.
_LEARN_INTENT = re.compile(
    r"\b(explain|teach|learn|understand(?:ing)?|"
    r"what\s+(?:is|are|does)|"
    r"how\s+(?:does|do)\b.*\bwork|difference\s+between|meaning\s+of|"
    r"tell\s+me\s+about|walk\s+me\s+through)\b",
    re.IGNORECASE,
)
_LEARN_TOPIC = re.compile(
    r"\b(vat|v\.a\.t|paye|tin|withholding|wht|"
    r"tax\s+brackets?|tax\s+bands?|progressive|marginal|"
    r"rental\s+tax|corporation\s+tax|corporate\s+tax|company\s+tax|"
    r"capital\s+gains?|customs|import\s+duty|landed\s+cost|"
    r"fiscal\s+year|tax\s+year|filing|taxation|tax)\b",
    re.IGNORECASE,
)
#: A figure in the query means the user wants arithmetic, not a concept.
#: Bare digits count, and so do the ways people write amounts in words.
_AMOUNT_CUE = re.compile(
    r"\d|\b(million|billion|thousand|ugx|shillings?)\b",
    re.IGNORECASE,
)
#: "What is the VAT rate" is a rate lookup; the rate table answers it.
_RATE_CUE = re.compile(r"\b(rates?|percentages?|percent|%)\b", re.IGNORECASE)
#: "Tell me about the current fiscal year" is a calendar question.
_TEMPORAL_CUE = re.compile(
    r"\b(today|tomorrow|current|now|this\s+(?:month|year|quarter)|"
    r"deadline|due|next)\b",
    re.IGNORECASE,
)

# Temporal intents — anything relative to "now", "today", "this year".
# Requires the calendar tool for a correct answer.
_TEMPORAL = (
    (
        re.compile(
            r"\b(today|tomorrow|yesterday|current\s+(?:date|year|fiscal)|now)\b",
            re.IGNORECASE,
        ),
        "Needs current date",
        ["get_current_date"],
    ),
    (
        re.compile(
            r"\b(deadline|due\s+date|when\s+is.*\s+due|next\s+filing|when\s+do\s+i\s+file)\b",
            re.IGNORECASE,
        ),
        "Needs upcoming deadlines",
        ["get_next_deadlines", "get_current_date"],
    ),
    (
        re.compile(
            r"\b(this\s+(month|year|fiscal\s+year|quarter)|fy\d{4})\b",
            re.IGNORECASE,
        ),
        "Needs current fiscal-year context",
        ["get_current_date"],
    ),
)

# Rate-lookup intents — "what's the VAT rate", "current CIT".  These
# should always go through the deterministic rate table, never Qwen.
_RATE = (
    (
        re.compile(
            r"\b(what(?:'s|\s+is)\s+the|current|applicable)\b.*\b(rate|percentage|%)\b",
            re.IGNORECASE,
        ),
        "Rate lookup intent",
        ["lookup_rate", "list_available_rates"],
    ),
    (
        re.compile(
            r"\blist\s+(?:all\s+)?(?:tax\s+)?rates\b",
            re.IGNORECASE,
        ),
        "Full rate listing",
        ["list_available_rates"],
    ),
)

# Customs specialist triggers — narrower scope, specific vocabulary.
_CUSTOMS = (
    re.compile(
        r"\b(import|export|customs|bill\s+of\s+lading|cif|eac\s+cet|tariff|clearance)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(declaration|entry|port\s+of\s+entry|goods\s+at\s+the\s+border)\b", re.IGNORECASE
    ),
)

# Escalation triggers — sensitive topics or explicit human requests.
_ESCALATE = (
    (
        re.compile(
            r"\b(speak\s+to|talk\s+to|contact|call)\s+(?:a|an|the)?\s*(?:human|person|officer|agent|someone)\b",
            re.IGNORECASE,
        ),
        "User explicitly asked for a human",
    ),
    (
        re.compile(
            r"\b(dispute|objection|audit|assessment\s+is\s+wrong|appeal|court|lawyer|fraud)\b",
            re.IGNORECASE,
        ),
        "Legal / dispute context needs human handling",
    ),
    (
        re.compile(
            r"\b(my\s+tin|my\s+filing|my\s+return|my\s+account|my\s+balance)\b",
            re.IGNORECASE,
        ),
        "Account-specific query — needs authenticated lookup or human",
    ),
)

# Clarification triggers — queries too short or vague to answer.
_CLARIFY_STOP_WORDS = frozenset(
    {"how", "what", "where", "when", "who", "why", "help", "tell", "info"}
)

# Greeting triggers — warm welcome without retrieval.
_GREETING_WORDS = frozenset({"hi", "hello", "hey", "howdy", "greetings", "yo"})
_GREETING_PHRASES = frozenset(
    {"good morning", "good afternoon", "good evening", "good day"}
)

# Closing courtesy — exact phrases only, so "thanks for nothing" (sarcasm)
# and "thanks, but the portal is down" (a real problem) still reach the
# distress detector and retrieval instead of a cheery sign-off.
_GRATITUDE_PHRASES = frozenset({
    "thanks", "thank you", "thank u", "thanks a lot", "thanks so much",
    "thank you so much", "thank you very much", "many thanks",
    "ok thanks", "okay thanks", "great thanks", "perfect thanks",
    "asante", "asante sana", "webale", "weebale",
})
_FAREWELL_PHRASES = frozenset({
    "bye", "goodbye", "good bye", "bye bye", "see you", "see you later",
    "good night", "goodnight", "thanks bye", "thank you bye",
})


EN_PATTERNS = LocalePatterns(
    locale="en",
    corpus_backed=True,
    calc=_CALC,
    temporal=_TEMPORAL,
    rate=_RATE,
    customs=_CUSTOMS,
    escalate=_ESCALATE,
    learn_intent=(_LEARN_INTENT,),
    learn_topic=(_LEARN_TOPIC,),
    amount_cue=(_AMOUNT_CUE,),
    rate_cue=(_RATE_CUE,),
    temporal_cue=(_TEMPORAL_CUE,),
    greeting_words=_GREETING_WORDS,
    greeting_phrases=_GREETING_PHRASES,
    gratitude_phrases=_GRATITUDE_PHRASES,
    farewell_phrases=_FAREWELL_PHRASES,
    clarify_stop_words=_CLARIFY_STOP_WORDS,
)
