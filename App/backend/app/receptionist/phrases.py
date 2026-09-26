"""Everything the receptionist says itself, in English, Luganda and Swahili.

Answers come from the RAG brain; this table is the rest — the greeting,
fillers while an answer is fetched, clarification prompts, the transfer and
timeout lines, and the confirmation spoken when the call changes language.
``brain.py`` and ``clarify.py`` read from it by ``room.state.locale``.

REVIEW STATUS: the Luganda and Swahili strings are drafts. They were written
from the project's existing, reviewed copy where it existed (``glossary.py``,
the ``lib/i18n`` dictionaries, ``text_signals`` courtesy replies) and have
**not yet been checked by a native speaker**. Do not demo them before that
review — see ``docs/runbooks/voice-receptionist-demo.md``. A missing key in a
language falls back to English rather than failing a call, and
``test_receptionist_phrases.py`` fails the build if any key is missing.
"""

from __future__ import annotations

import random
import re
from functools import lru_cache

#: Languages whose strings still need a native speaker's sign-off.
UNREVIEWED: frozenset[str] = frozenset({"lg", "sw"})

_PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "greeting": (
            "Hi, thanks for contacting URA. I'm your assistant today. Ask your question "
            "in your preferred language — English, Luganda or Swahili — and I'll give you "
            "the answer in that language. How can I help you today?"
        ),
        "more_detail": "Would you like more detail?",
        "timeout_transfer": (
            "I'm sorry, checking the database is taking longer than expected. "
            "Let me connect you to an officer."
        ),
        "error_transfer": (
            "I encountered an error looking up that tax information. "
            "Let me connect you to an officer."
        ),
        "transfer": "I'm connecting you to a URA officer, please hold.",
        "queue_disabled": "I can't transfer you right now; please call 0800 117 000 during working hours.",
        "officers_busy": "All our officers are busy. Your reference is {ref}; an officer will call you back.",
        "clarify_term": "Excuse me, did you say {term}?",
        "clarify_confirm": "So if I got it right, you're asking about {highlight} — is that right?",
        "clarify_repeat_after": "Sorry, I didn't catch the word after '{prev}' — could you say it again?",
        "clarify_repeat": "Sorry, I didn't catch that — could you say it again?",
        "clarify_restart": "Sorry about that — please tell me your question again.",
        "switched": "Sure — let's continue in English.",
        "officer_offer": "Would you like to speak to an officer about this?",
        # Call Desk: an officer joins the call, holds it, reconnects, and ends it.
        "officer_joining": "You're now connected to {name}.",
        "officer_reconnecting": "Please hold, I'm reconnecting you.",
        "on_hold": "Please hold.",
        "officer_closing": "Thank you for calling URA. Goodbye.",
    },
    "lg": {
        "greeting": (
            "Gyebale ko, weebale okutuukirira URA. Nze muyambi wo leero. Buuza ekibuuzo kyo "
            "mu lulimi lw'oyagala — Lungereza, Luganda oba Kiswahili — nange nja kukuddamu "
            "mu lulimi olwo. Nnyinza kukuyamba ntya leero?"
        ),
        "more_detail": "Oyagala okumanya ebisingawo?",
        "timeout_transfer": (
            "Nsonyiwa, okunoonya kutwala ekiseera kiwanvu okusinga bwe kyandibadde. "
            "Ka nkukwataganye n'omukozi wa URA."
        ),
        "error_transfer": (
            "Nsonyiwa, waliwo ekizibu mu kunoonya ebikwata ku musolo ogwo. "
            "Ka nkukwataganye n'omukozi wa URA."
        ),
        "transfer": "Nkukwataganya n'omukozi wa URA, nsaba olindeko.",
        "queue_disabled": (
            "Kati sisobola kukukwataganya n'omukozi; nsaba okube ku 0800 117 000 "
            "mu ssaawa z'okukola."
        ),
        "officers_busy": (
            "Abakozi baffe bonna balina emirimu. Namba yo ey'okujuliza eri {ref}; "
            "omukozi ajja kukukubira essimu."
        ),
        "clarify_term": "Nsonyiwa, ogambye {term}?",
        "clarify_confirm": "Kale, obuuza ku {highlight} — kituufu?",
        "clarify_repeat_after": "Nsonyiwa, ekigambo ekiddirira '{prev}' sikiwulidde — oyinza okukiddamu?",
        "clarify_repeat": "Nsonyiwa, sikuwulidde bulungi — oyinza okuddamu?",
        "clarify_restart": "Nsonyiwa ku ekyo — nsaba oddemu ekibuuzo kyo.",
        "switched": "Kale — tweyongere mu Luganda.",
        "officer_offer": "Wandiyagadde okwogera n'omukozi ku nsonga eno?",
        "officer_joining": "Kati oyogera ne {name}.",
        "officer_reconnecting": "Nsaba olindeko, nkyakukwataganya n'omukozi.",
        "on_hold": "Nsaba olindeko.",
        "officer_closing": "Webale okukuba essimu eri URA. Weeraba.",
    },
    "sw": {
        "greeting": (
            "Habari, asante kwa kuwasiliana na URA. Mimi ni msaidizi wako leo. Uliza swali "
            "lako kwa lugha unayopendelea — Kiingereza, Luganda au Kiswahili — nami "
            "nitakujibu kwa lugha hiyo. Nikusaidie vipi leo?"
        ),
        "more_detail": "Ungependa maelezo zaidi?",
        "timeout_transfer": (
            "Samahani, kutafuta kunachukua muda mrefu kuliko kawaida. "
            "Nitakuunganisha na afisa wa URA."
        ),
        "error_transfer": (
            "Samahani, kumetokea hitilafu katika kutafuta taarifa hiyo ya kodi. "
            "Nitakuunganisha na afisa wa URA."
        ),
        "transfer": "Ninakuunganisha na afisa wa URA, tafadhali subiri.",
        "queue_disabled": (
            "Siwezi kukuunganisha sasa hivi; tafadhali piga 0800 117 000 "
            "wakati wa saa za kazi."
        ),
        "officers_busy": (
            "Maafisa wetu wote wana shughuli. Nambari yako ya kumbukumbu ni {ref}; "
            "afisa atakupigia simu."
        ),
        "clarify_term": "Samahani, ulisema {term}?",
        "clarify_confirm": "Kwa hiyo, kama nimeelewa vizuri, unauliza kuhusu {highlight} — ni sahihi?",
        "clarify_repeat_after": "Samahani, sikusikia neno baada ya '{prev}' — unaweza kulirudia?",
        "clarify_repeat": "Samahani, sikusikia vizuri — unaweza kurudia?",
        "clarify_restart": "Samahani kwa hilo — tafadhali niambie swali lako tena.",
        "switched": "Sawa — tuendelee kwa Kiswahili.",
        "officer_offer": "Je, ungependa kuzungumza na afisa kuhusu hili?",
        "officer_joining": "Sasa umeunganishwa na {name}.",
        "officer_reconnecting": "Tafadhali subiri, ninaendelea kukuunganisha na afisa.",
        "on_hold": "Tafadhali subiri.",
        "officer_closing": "Asante kwa kupiga simu URA. Kwaheri.",
    },
}

_FILLERS: dict[str, tuple[str, ...]] = {
    "en": (
        "mm, one sec",
        "okay, so",
        "let me see",
        "right, checking now",
        "one moment",
        "let me check that",
        "just a second",
        "Let me check that for you.",
    ),
    "lg": (
        "Lindako katono.",
        "Ka nkebere.",
        "Nkebera kati.",
        "Akaseera katono.",
        "Ka ndabe.",
        "Ka nkukeberere.",
    ),
    "sw": (
        "Subiri kidogo.",
        "Ngoja niangalie.",
        "Naangalia sasa.",
        "Sekunde moja.",
        "Hebu nione.",
        "Ngoja nikuangalie.",
    ),
}

# Yes / no for the clarification loop, beyond the English words clarify.py
# already knows. Short particles that double as ordinary grammar ("nga",
# Swahili "la") are left out: a false "no" restarts the question.
_YES_WORDS: dict[str, tuple[str, ...]] = {
    "lg": ("yee", "ye", "weewaawo", "kituufu", "bwe kiri", "kyekyo", "kye kyo"),
    "sw": ("ndiyo", "ndio", "sawa", "kweli", "sahihi", "hasa", "naam"),
}
_NO_WORDS: dict[str, tuple[str, ...]] = {
    "lg": ("nedda", "si kyo", "si kituufu", "ssi"),
    "sw": ("hapana", "sio", "si sahihi"),
}

KEYS: tuple[str, ...] = tuple(_PHRASES["en"])


def phrase(key: str, language: str, **fmt: str) -> str:
    """The *key* line in *language*, falling back to English."""
    table = _PHRASES.get(language) or _PHRASES["en"]
    text = table.get(key) or _PHRASES["en"][key]
    return text.format(**fmt) if fmt else text


def fillers(language: str) -> tuple[str, ...]:
    return _FILLERS.get(language) or _FILLERS["en"]


def pick_filler(language: str, last: str | None = None) -> str:
    """A filler for *language* that is not the one just used."""
    pool = fillers(language)
    choices = [f for f in pool if f != last]
    return random.choice(choices) if choices else pool[0]  # noqa: S311 — variety, not security


@lru_cache(maxsize=8)
def yes_no_patterns(language: str) -> tuple[re.Pattern[str] | None, re.Pattern[str] | None]:
    """Extra yes / no regexes for *language* (``None`` where English suffices)."""

    def build(words: tuple[str, ...]) -> re.Pattern[str] | None:
        if not words:
            return None
        return re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b", re.IGNORECASE)

    return build(_YES_WORDS.get(language, ())), build(_NO_WORDS.get(language, ()))


def prewarm_phrases(language: str) -> list[str]:
    """Fixed lines worth synthesising before the first call reaches them.

    Formatted lines (``{ref}``, ``{term}``…) are left out — their text is
    only known mid-call.
    """
    fixed = [text for text in _PHRASES.get(language, {}).values() if "{" not in text]
    return [*fillers(language), *fixed]
