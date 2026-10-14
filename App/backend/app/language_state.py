"""Which language a chat turn is answered in (en / lg / sw).

The answer language is conversation state, not a fresh guess on every message.
:func:`resolve_turn_locale` decides it from, highest precedence first:

1. **An in-message request** — "Njogera mu Luganda", "answer in English".
2. **A language the taxpayer picked** — the client sends ``locale_explicit``.
   A client that omits the flag keeps the pre-2026-10-03 contract: a requested
   ``lg``/``sw`` is a choice, a requested ``en`` is only the picker's default.
   The web client sends ``en`` until someone touches the picker, so honouring a
   bare ``en`` as a choice switched auto-detection off for everyone (PR #529).
3. **Continuity** — the language the conversation was last *answered* in, read
   from the stored turn rather than re-detected from text. It holds until the
   taxpayer clearly switches: a substantive message (``SWITCH_MIN_WORDS``+
   words) with words of the new language and none of the established one.
   Short follow-ups ("What about 150m?") and code-switched Luganda full of
   English tax terms therefore stay put, while a full English sentence after a
   Luganda thread moves the conversation to English — people switch to the
   language they want the answer in.
4. **Detection** of this message, when nothing is established.
5. **The profile's preferred language**, for an ambiguous first message only
   (no word of any language, e.g. "TIN?"). Callers pass it only with
   personalization consent.
6. The client's hint, else English.

Word evidence comes from :func:`app.receptionist.language.lexical_hits`, the
same lists the call receptionist votes with, so both channels read a sentence
the same way. Nothing here calls the network.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .query import SUPPORTED_LOCALES, detect_language
from .receptionist.language import detect_explicit_request, lexical_hits

LocaleSource = Literal[
    "explicit_request",
    "client_explicit",
    "continuity",
    "switched",
    "detected",
    "profile",
    "default",
]

#: Locale sources a client should treat as the taxpayer's own choice.
EXPLICIT_SOURCES: frozenset[str] = frozenset({"explicit_request", "client_explicit"})

#: A message must be at least this long to move an established conversation.
SWITCH_MIN_WORDS = 4
#: ...and carry at least this many words of the language it moves to.
SWITCH_MIN_HITS = 2

_LOCAL = ("lg", "sw")
_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)

# ISO 639-2/3 codes and common aliases for the supported base languages.
_TAG_ALIASES: dict[str, str] = {
    "eng": "en",
    "lug": "lg",
    "swa": "sw",
    "swh": "sw",
}


@dataclass(frozen=True)
class LanguageDecision:
    """The answer language for one turn, and why."""

    locale: str
    source: LocaleSource
    #: The language the conversation was answered in before this turn ("" if none).
    established: str = ""

    @property
    def switched(self) -> bool:
        return bool(self.established) and self.locale != self.established


def normalize_locale_tag(tag: str | None) -> str:
    """A BCP 47 / ISO 639 tag as a supported base language, or "".

    ``sw-UG`` and ``SW`` become ``sw``; ``lug`` becomes ``lg``. The region is
    dropped: routing, memory cues and translation key on the language alone.
    """
    raw = str(tag or "").strip().replace("_", "-")
    if not raw:
        return ""
    base = raw.split("-", 1)[0].lower()
    base = _TAG_ALIASES.get(base, base)
    return base if base in SUPPORTED_LOCALES else ""


def _words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def established_locale(history: Sequence[Mapping[str, Any]] | None) -> str:
    """The language the conversation was last answered in, or "".

    Reads the locale stored with each turn (``conversations.locale``, or the
    ``locale`` a WebSocket session records on its cached turns). Rows written
    before locales were stored fall back to the taxpayer's own words: the
    newest user message with Luganda or Swahili evidence.
    """
    for turn in reversed(list(history or ())):
        if not isinstance(turn, Mapping):
            continue
        if turn.get("role") == "user":
            continue
        stored = normalize_locale_tag(str(turn.get("locale") or ""))
        if stored:
            return stored
    for turn in reversed(list(history or ())[-4:]):
        if not isinstance(turn, Mapping):
            continue
        text = str(turn.get("user_message") or (turn.get("content") if turn.get("role") == "user" else "") or "")
        if len(text.strip()) < 4:
            continue
        detected = detect_language(text, default_lang="en")
        if detected in _LOCAL:
            return detected
    return ""


def _is_ambiguous(message: str, hits: Mapping[str, int]) -> bool:
    """No word of any language, and too short to say otherwise ("TIN?", "ok")."""
    return sum(hits.values()) == 0 and len(_words(message)) <= 3


def _switch_target(message: str, established: str) -> str | None:
    """The language a substantive message moves an *established* thread to."""
    words = _words(message)
    hits = lexical_hits(message)
    if established in _LOCAL:
        if hits.get(established, 0) > 0:
            return None  # still using the established language's own words
        other = "sw" if established == "lg" else "lg"
        if hits.get(other, 0) >= SWITCH_MIN_HITS:
            return other
        if (
            len(words) >= SWITCH_MIN_WORDS
            and hits.get("en", 0) >= SWITCH_MIN_HITS
            and hits.get("lg", 0) == 0
            and hits.get("sw", 0) == 0
        ):
            return "en"
        return None
    # Established English: a Luganda or Swahili message needs real evidence,
    # which detect_language only reports when it has it.
    detected = detect_language(message, default_lang="en")
    return detected if detected in _LOCAL else None


def resolve_turn_locale(
    message: str,
    *,
    requested_locale: str | None = "",
    locale_explicit: bool | None = None,
    history: Sequence[Mapping[str, Any]] | None = None,
    profile_locale: str | None = "",
) -> LanguageDecision:
    """Decide the answer language for *message*. See the module docstring."""
    established = established_locale(history)

    explicit = detect_explicit_request(message)
    if explicit in SUPPORTED_LOCALES:
        return LanguageDecision(explicit, "explicit_request", established)

    requested = normalize_locale_tag(requested_locale)
    if requested:
        chosen = locale_explicit if locale_explicit is not None else requested in _LOCAL
        if chosen:
            return LanguageDecision(requested, "client_explicit", established)

    if established:
        target = _switch_target(message, established)
        if target and target in SUPPORTED_LOCALES and target != established:
            return LanguageDecision(target, "switched", established)
        return LanguageDecision(established, "continuity", established)

    hits = lexical_hits(message)
    detected = detect_language(message, default_lang="en")
    if detected in _LOCAL and detected in SUPPORTED_LOCALES:
        return LanguageDecision(detected, "detected", established)

    preferred = normalize_locale_tag(profile_locale)
    if preferred in _LOCAL and _is_ambiguous(message, hits):
        return LanguageDecision(preferred, "profile", established)

    if not _is_ambiguous(message, hits):
        return LanguageDecision("en", "detected", established)
    return LanguageDecision(requested or "en", "default", established)
