"""Short summary titles for conversations, for the chat sidebar.

The sidebar used to show the first message cut at sixty characters, which read
as a fragment ("Verify TIN <ten digits> for my tax ...") and put a TIN on screen
in a list anyone glancing at the device can read. ChatGPT, Claude and Gemini
name a conversation instead: one small model call after the first exchange,
asked for a few words that say what the conversation is about. This is that
call.

Nothing the model returns is trusted as is. The opening exchange is checked by
the input guard and stripped of personal data before the model sees it, and the
reply is cut to one short line and rejected outright if it still looks like a
refusal or carries an identifier. A rejected or missing title is reported as
``None``: the client already shows a provisional title of its own, and a bad
name is worse than that one.
"""

from __future__ import annotations

import re

from . import llm
from .guardrails import InputGuard, contains_pii, redact_pii_text

MAX_WORDS = 6
# Translations run longer than English; a localized title gets a little room.
MAX_WORDS_LOCALIZED = 8
MAX_CHARS = 60
_EXCERPT_CHARS = 600

_SYSTEM_PROMPT = (
    "You name conversations between a taxpayer and the Uganda Revenue Authority (URA) "
    "assistant, the way a chat app names them in its sidebar. Reply with a title of 2 to 6 "
    "words that says what the conversation is about. Use sentence case. No quotes, no "
    "emojis, no full stop. Never include names, TINs, ID numbers, phone numbers, amounts "
    "or dates. Write the title in English. Reply with the title only.\n"
    "Examples:\n"
    "Please verify my TIN, I need tax clearance -> TIN verification for tax clearance\n"
    "We are hiring a foreign technical specialist, which taxes apply? -> "
    "Taxes on hiring a foreign specialist\n"
    "when do i pay paye -> PAYE payment deadline"
)

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_PREFIX_RE = re.compile(r"^(?:chat\s+)?(?:title|topic|name)\s*[:\-–—]\s*", re.IGNORECASE)
_ARROW_RE = re.compile(r"^.*?(?:->|→)\s*")
_REDACTION_RE = re.compile(r"\[REDACTED_[A-Z_]+\]")
_URL_RE = re.compile(r"https?://\S+|\b\S+@\S+\.\S+")
_WRAPPERS = "\"'`*_#“”‘’«»"
_TRAILING = " .,;:!?-–—…"
_REFUSAL_RE = re.compile(
    r"\b(?:i\s+can(?:not|'t|’t)|i\s+am\s+unable|i'm\s+unable|sorry|as\s+an\s+ai|"
    r"no\s+title|untitled|conversation\s+title)\b",
    re.IGNORECASE,
)
# Five or more digits in a row is an identifier or an amount, never a topic.
_DIGIT_RUN_RE = re.compile(r"\d[\d\s-]{3,}\d")
_TRAILING_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with", "my", "your"}
)


def clean_title(raw: str, *, max_words: int = MAX_WORDS) -> str:
    """One short line from a model reply, or "" when it is not a usable title."""
    text = _THINK_RE.sub("", raw or "")
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if not line:
        return ""
    line = _ARROW_RE.sub("", line) if ("->" in line or "→" in line) else line
    line = _PREFIX_RE.sub("", line.strip().strip(_WRAPPERS).strip())
    line = _REDACTION_RE.sub(" ", line)
    line = _URL_RE.sub(" ", line)
    line = re.sub(r"\s+", " ", line).strip().strip(_WRAPPERS).strip(_TRAILING)
    if not line or _REFUSAL_RE.search(line) or _DIGIT_RUN_RE.search(line) or contains_pii(line):
        return ""

    words = line.split(" ")[:max_words]
    while len(words) > 1 and words[-1].lower().strip(_TRAILING) in _TRAILING_STOPWORDS:
        words.pop()
    title = " ".join(words).strip(_TRAILING)
    if len(title) > MAX_CHARS:
        title = title[:MAX_CHARS].rsplit(" ", 1)[0].strip(_TRAILING)
    if len(title) < 3:
        return ""
    # A shouted title ("TAX CLEARANCE CERTIFICATE") reads as sentence case
    # would; a single acronym ("PAYE") is left alone.
    if title.isupper() and " " in title:
        title = title.capitalize()
    return title[0].upper() + title[1:]


def _excerpt(text: str) -> str:
    """Personal data out, then a bounded slice. ``redact_pii_text`` takes TINs,
    NINs and emails; phone numbers and account numbers are digit runs, masked
    here because a title never needs one."""
    text = _DIGIT_RUN_RE.sub("[number]", redact_pii_text(text or ""))
    return re.sub(r"\s+", " ", text).strip()[:_EXCERPT_CHARS]


def generate_title(user_text: str, assistant_text: str = "", locale: str = "en") -> tuple[str | None, str]:
    """``(title, source)`` for a conversation's opening exchange.

    ``source`` is ``"model"`` when the title came from the model, ``"blocked"``
    when the input guard refused the opening message, and ``"unavailable"``
    when no usable title came back (model off, failed, or its reply rejected).
    """
    if not InputGuard().check(user_text or "").allowed:
        return None, "blocked"

    user = _excerpt(user_text)
    if not user:
        return None, "unavailable"
    assistant = _excerpt(assistant_text)
    conversation = f"Taxpayer: {user}"
    if assistant:
        conversation += f"\nAssistant: {assistant}"

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"{conversation}\n\nTitle:"},
    ]
    title = clean_title(llm.summarize_title(messages))
    if not title:
        return None, "unavailable"

    if locale and locale != "en":
        # Generated in English and localized in the one place answers are.
        # localize_reply hands back the English on any failure.
        from .service import localize_reply  # noqa: PLC0415 — service imports are heavy

        localized = clean_title(localize_reply(title, locale), max_words=MAX_WORDS_LOCALIZED)
        if localized:
            title = localized
    return title, "model"
