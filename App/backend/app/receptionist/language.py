"""Which language a receptionist call is in, and when to change it.

Pure logic — no Pipecat import — so every rule here is unit-tested without an
event loop. :class:`LanguagePolicy` turns per-utterance votes into decisions;
the sentinel (``sentinel.py``) produces the votes and the router
(``router.py``) carries the decisions out.

The call always opens in the default language (English). The first *content*
utterance decides — greetings and one-word replies are too short to vote on —
and after that the language is *locked*: a single confident vote, or two
moderately confident votes in a row, is needed to move it. That asymmetry is
deliberate. Code-switched Luganda is full of English tax terms ("TIN", "VAT",
"PAYE"), and an English caller with a strong Ugandan accent is the likeliest
false Luganda vote; both would make a call flip-flop without hysteresis.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from ..query import _COMMON_ENGLISH_WORDS, _LUGANDA_WORDS, _SW_MARKERS, _SWAHILI_WORDS
from .config import (
    KNOWN_LANGUAGES,
    get_languages,
    get_lg_mix_threshold,
    get_lid_hysteresis_confidence,
    get_lid_hysteresis_turns,
    get_lid_min_speech_s,
    get_lid_switch_confidence,
)

SUPPORTED: tuple[str, ...] = KNOWN_LANGUAGES

LANGUAGE_NAMES: dict[str, str] = {"en": "English", "sw": "Swahili", "lg": "Luganda"}

# Function words and conversational glue that the tax-vocabulary lists in
# query.py do not carry. A Luganda sentence about VAT may contain no Luganda
# tax noun at all ("Nsaba okumanya ku VAT yange") — these are what is left.
# Words shared by both languages ("na" = "and" in each) are left out.
_LUGANDA_FUNCTION_WORDS: frozenset[str] = frozenset({
    "nga", "ku", "mu", "oba", "naye", "ne", "kiki", "ki", "nze", "gwe", "ffe",
    "yange", "wange", "lyange", "kyange", "gwange", "yaffe", "kwe", "nnyinza",
    "njagala", "mbuuza", "okumanya", "ssebo", "nnyabo", "ndowooza", "ntegeeza",
    "nnyonnyola", "okusasula", "nsasula", "ngisasula", "okugisasula", "ddi",
    "wano", "olwaleero", "jjuuzi", "tuyinza", "okwogera", "twogere",
    "tewali", "sirina", "nina", "bwentyo", "ekyo", "kino", "kati", "eky'okukola",
    "oli", "otya", "gyebale", "ko", "bulungi", "nnyo",
    # Question words and copulas — what a code-switched question keeps in
    # Luganda when every noun in it is English ("Rental income tax nsasula
    # mmeka?", "Withholding tax eri ebitundu bimeka?").
    "mmeka", "bimeka", "meka", "etya", "zitya", "gitya", "atya", "lwaki",
    "eri", "guli", "kiri", "ziri", "ngiwaayo", "ngifuna",
})

_SWAHILI_FUNCTION_WORDS: frozenset[str] = frozenset({
    "ni", "ya", "za", "la", "cha", "vya", "kwamba", "lakini", "sana",
    "mimi", "wewe", "yangu", "wangu", "yako", "nataka", "naomba", "tafadhali",
    "ninahitaji", "je", "nini", "gani", "lini", "vipi", "wapi", "ngapi",
    "tuongee", "kiswahili", "hii", "hiyo", "kuna", "sijui", "sawa",
})

_LG_ALL = _LUGANDA_WORDS | _LUGANDA_FUNCTION_WORDS
_SW_ALL = _SWAHILI_WORDS | _SWAHILI_FUNCTION_WORDS
_NATIVE_WORDS: dict[str, frozenset[str]] = {"lg": frozenset(_LG_ALL), "sw": frozenset(_SW_ALL)}
_WORD_RE = re.compile(r"[a-z']+")


def is_native_word(word: str, language: str) -> bool:
    """Whether *word* is one of *language*'s own words rather than an English loan.

    Swahili "nini" ("what") is a letter away from NIN; a clarification gate
    looking for misheard acronyms must not ask about it.
    """
    vocab = _NATIVE_WORDS.get(language)
    return vocab is not None and word.lower() in vocab


@dataclass(frozen=True)
class LanguageVote:
    """One utterance's evidence: acoustic probabilities plus whatever text exists."""

    probs: dict[str, float]
    top: str
    speech_s: float
    text: str = ""
    latency_ms: float = 0.0

    @property
    def confidence(self) -> float:
        return self.probs.get(self.top, 0.0)


@dataclass(frozen=True)
class Decision:
    """What the router should do after a vote. ``none`` changes nothing."""

    action: Literal["none", "switch", "lock"]
    target: str
    reason: str
    confidence: float = 0.0
    source: Literal["auto", "explicit", "override"] = "auto"


@dataclass(frozen=True)
class PolicyConfig:
    languages: tuple[str, ...] = SUPPORTED
    min_speech_s: float = 1.5
    switch_confidence: float = 0.90
    hysteresis_confidence: float = 0.70
    hysteresis_turns: int = 2
    lg_mix_threshold: float = 0.35
    lg_mix_min_words: int = 2
    #: Words of the new language (and none of the current) that let a vote
    #: between the accumulate and switch bars move a locked call at once.
    text_confirm_words: int = 3

    @classmethod
    def from_env(cls) -> PolicyConfig:
        return cls(
            languages=get_languages(),
            min_speech_s=get_lid_min_speech_s(),
            switch_confidence=get_lid_switch_confidence(),
            hysteresis_confidence=get_lid_hysteresis_confidence(),
            hysteresis_turns=get_lid_hysteresis_turns(),
            lg_mix_threshold=get_lg_mix_threshold(),
        )


def lexical_hits(text: str) -> dict[str, int]:
    """Count words that belong to each language, from local word lists only.

    Never calls a network detector (unlike :func:`app.query.detect_language`,
    which may ask Sunbird): this runs inside a live call turn.
    """
    words = _WORD_RE.findall((text or "").lower())
    lg = sum(1 for w in words if w in _LG_ALL)
    sw = sum(1 for w in words if w in _SW_ALL)
    # query's marker regex also knows a few Swahili words the lists do not;
    # count those without counting a listed word twice.
    sw += sum(1 for m in _SW_MARKERS.findall(text or "") if m.lower() not in _SW_ALL)
    en = sum(1 for w in words if w in _COMMON_ENGLISH_WORDS)
    return {"en": en, "lg": lg, "sw": sw}


def text_language(text: str, current: str, allowed: tuple[str, ...]) -> str:
    """The language a transcript is in, from word lists alone; *current* if unclear.

    Used where only text exists (Gemini's own input transcription on a
    single-engine call). Moving off *current* needs two words of the other
    language and more of them than of any language in *allowed* — one
    borrowed word ("asante", "webale") is not a switch.
    """
    hits = lexical_hits(text)
    ranked = sorted(((hits.get(lang, 0), lang) for lang in allowed), reverse=True)
    if not ranked:
        return current
    best_n, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    if best != current and best_n >= 2 and best_n > runner_up:
        return best
    return current


# Ordered longest-first within a language so "twogere oluganda" is reported
# as itself rather than as the bare "oluganda" it contains. Bare language
# names are deliberately absent for English ("do you have the form in
# English?" is not a request to switch) but present for the two local
# languages, whose names a caller says almost only when asking for them.
_EXPLICIT_REQUESTS: dict[str, tuple[str, ...]] = {
    "lg": (
        "twogere oluganda", "njagala oluganda", "tuyinza okwogera oluganda",
        "speak in luganda", "speak luganda", "switch to luganda", "in luganda",
        "mu luganda", "oluganda",
    ),
    "sw": (
        "tuongee kiswahili", "naomba kiswahili", "tuzungumze kiswahili",
        "speak in swahili", "speak swahili", "switch to swahili", "in swahili",
        "kwa kiswahili", "kiswahili",
    ),
    "en": (
        "speak in english", "speak english", "switch to english", "in english",
        "mu lungereza", "kwa kiingereza", "lungereza",
    ),
}
_EXPLICIT_RES: dict[str, re.Pattern[str]] = {
    lang: re.compile(r"\b(?:" + "|".join(re.escape(p) for p in phrases) + r")\b")
    for lang, phrases in _EXPLICIT_REQUESTS.items()
}


def detect_explicit_request(text: str, languages: tuple[str, ...] = SUPPORTED) -> str | None:
    """The language *text* explicitly asks for, or ``None``.

    When a turn names two ("not in English — in Luganda please"), the one
    said last wins: callers correct themselves forwards.
    """
    low = re.sub(r"[^\w' ]+", " ", (text or "").lower())
    best: tuple[int, str] | None = None
    for lang in languages:
        pattern = _EXPLICIT_RES.get(lang)
        if pattern is None:
            continue
        for match in pattern.finditer(low):
            if best is None or match.end() > best[0]:
                best = (match.end(), lang)
    return best[1] if best else None


def fuse(vote: LanguageVote, text_confidence: float = 0.8) -> LanguageVote:
    """Method C: let the transcript overrule an unsure acoustic vote.

    Only when the acoustic top probability is below 0.8 and the text has at
    least two words of one language — and more of it than of any other.
    The overruling vote carries ``text_confidence``: enough to accumulate
    towards a switch, not enough to switch a locked call on its own.
    """
    if not vote.text or vote.confidence >= 0.8:
        return vote
    hits = lexical_hits(vote.text)
    ranked = sorted(((n, lang) for lang, n in hits.items() if lang in vote.probs), reverse=True)
    if not ranked:
        return vote
    best_n, best_lang = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0
    if best_n < 2 or best_n <= runner_up or best_lang == vote.top:
        return vote
    rest = max(0.0, 1.0 - text_confidence)
    others = [lang for lang in vote.probs if lang != best_lang]
    probs = {lang: round(rest / max(len(others), 1), 4) for lang in others}
    probs[best_lang] = text_confidence
    return LanguageVote(probs, best_lang, vote.speech_s, vote.text, vote.latency_ms)


@dataclass
class LanguagePolicy:
    """State machine over the call's language. See the module docstring."""

    active: str = "en"
    config: PolicyConfig = field(default_factory=PolicyConfig)
    locked: bool = False
    override: str | None = None
    pending: list[LanguageVote] = field(default_factory=list)
    switch_count: int = 0

    def set_override(self, language: str) -> Decision:
        """The caller picked a language on screen: lock it for the call."""
        if language not in self.config.languages:
            return Decision("none", self.active, "unsupported_language", source="override")
        self.override = language
        self.pending.clear()
        return self._move(language, "ui_override", 1.0, "override")

    def observe(self, vote: LanguageVote) -> Decision:
        cfg = self.config

        # 1-2. An explicit spoken request beats everything, overrides included
        #      — a caller who picked Luganda on screen and then says "can we
        #      speak English?" is still asking.
        requested = detect_explicit_request(vote.text, cfg.languages) if vote.text else None
        if requested is not None:
            if self.override is not None:
                self.override = requested
            self.pending.clear()
            return self._move(requested, "explicit_request", 1.0, "explicit")
        if self.override is not None:
            return Decision("none", self.active, "override_locked", source="override")

        # 3. Greetings, "yes", "hello?" — too little speech to vote on.
        if vote.speech_s < cfg.min_speech_s or not vote.top:
            return Decision("none", self.active, "too_short")

        # 4. Code-switched Luganda. The acoustic model separates English from
        #    Bantu speech well but confuses Luganda with Swahili, and English
        #    tax nouns drag a Luganda sentence towards English. The text keeps
        #    Luganda's function words either way, so Luganda wins when the
        #    words say so and the acoustics either give Luganda a real chance
        #    or say "Bantu" at all.
        #    A call already locked in Luganda needs no acoustic support at all:
        #    "Withholding tax eri ebitundu bimeka?" sounds English (P(en) 0.89
        #    measured) and is still a Luganda caller asking in Luganda.
        top, confidence = vote.top, vote.confidence
        hits = lexical_hits(vote.text) if vote.text else {"en": 0, "lg": 0, "sw": 0}
        if "lg" in cfg.languages and vote.text and not (top == "lg" and confidence >= cfg.switch_confidence):
            p_lg = vote.probs.get("lg", 0.0)
            bantu = p_lg + vote.probs.get("sw", 0.0)
            in_luganda_call = self.locked and self.active == "lg"
            if (
                hits["lg"] >= cfg.lg_mix_min_words
                and hits["lg"] > hits["sw"]
                and (in_luganda_call or p_lg >= cfg.lg_mix_threshold or bantu >= 0.5)
            ):
                top, confidence = "lg", max(p_lg, cfg.switch_confidence)
        if top not in cfg.languages:
            return Decision("none", self.active, "unsupported_language")

        # 5. First content utterance: lock. Moving off the opening language
        #    needs at least the accumulate threshold; a weak vote for another
        #    language leaves the call unlocked so the next utterance decides.
        if not self.locked:
            if top == self.active:
                return self._move(top, "first_content", confidence, "auto")
            if confidence >= cfg.hysteresis_confidence:
                return self._move(top, "first_content", confidence, "auto")
            return Decision("none", self.active, "weak_first_vote", confidence)

        # 6. Same language again: any half-built case for switching is void.
        if top == self.active:
            self.pending.clear()
            return Decision("none", self.active, "same_language", confidence)

        # 7. Locked, different language. One vote suffices when it is
        #    confident, or when it clears the accumulate bar and the words
        #    agree: several of the new language, none of the current one.
        if confidence >= cfg.switch_confidence:
            self.pending.clear()
            return self._move(top, "confident_vote", confidence, "auto")
        if (
            confidence >= cfg.hysteresis_confidence
            and hits.get(top, 0) >= cfg.text_confirm_words
            and hits.get(self.active, 0) == 0
        ):
            self.pending.clear()
            return self._move(top, "text_confirmed", confidence, "auto")
        if confidence < cfg.hysteresis_confidence:
            self.pending.clear()
            return Decision("none", self.active, "below_hysteresis", confidence)
        if self.pending and self.pending[-1].top != top:
            self.pending.clear()
        self.pending.append(LanguageVote(vote.probs, top, vote.speech_s, vote.text, vote.latency_ms))
        if len(self.pending) >= cfg.hysteresis_turns:
            self.pending.clear()
            return self._move(top, "hysteresis", confidence, "auto")
        return Decision("none", self.active, "accumulating", confidence)

    def _move(self, target: str, reason: str, confidence: float, source: str) -> Decision:
        was_locked = self.locked
        self.locked = True
        if target != self.active:
            self.active = target
            self.switch_count += 1
            return Decision("switch", target, reason, confidence, source)  # type: ignore[arg-type]
        if not was_locked:
            return Decision("lock", target, reason, confidence, source)  # type: ignore[arg-type]
        return Decision("none", target, reason, confidence, source)  # type: ignore[arg-type]
