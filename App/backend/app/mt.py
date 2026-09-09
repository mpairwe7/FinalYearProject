"""Shared machine-translation plumbing: a bounded cache and a figure guard.

Two problems this module exists for, both reported from the field.

**Latency.** A non-English turn pays for machine translation at least three
times: ``english_retrieval_query`` in the router, the same call again inside
the hybrid retriever, and ``localize_reply`` on the way out. Each one is a
local generation pass or a Sunbird round trip of roughly one to three seconds,
so a Luganda question took two to three times as long as the identical English
one — the "answers in another language take longer" report. Two of those three
calls translate *the very same string*, and a taxpayer FAQ assistant asks the
same questions over and over besides, so a small in-process cache removes most
of that cost without touching a model or a server.

**Figures.** Machine translation is paraphrastic and rewrites numbers. A reply
saying "UGX 235,000" can come back saying "UGX 253,000", or lose the amount
entirely — and unlike a clumsy phrasing, a wrong figure on a revenue
authority's assistant is indistinguishable from the assistant making it up.
:func:`figures_survived` compares the money amounts and percentages on both
sides so the caller can refuse a translation that changed them; the existing
behaviour for a refused translation is to serve the English text, which is a
worse read but never a wrong number.

Both are per-process and deliberately so: this is a hot-path memo, not a
system of record, and replicas do not need to agree about it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from collections.abc import Callable

from .entailment import canonical_amounts, percentages

logger = logging.getLogger(__name__)

#: Entries held per direction pair. Sized for a working set of repeated FAQ
#: questions and their answers rather than a whole corpus — 0 disables.
MT_CACHE_SIZE = int(os.getenv("MT_CACHE_SIZE", "512"))

#: Text longer than this is not cached. A long reply is unlikely to repeat
#: verbatim and would evict many short entries that do.
MT_CACHE_MAX_CHARS = int(os.getenv("MT_CACHE_MAX_CHARS", "4000"))


def _key(source_lang: str, target_lang: str, text: str) -> tuple[str, str, str]:
    """Cache key. The text is hashed, not stored.

    Taxpayer questions reach this module and can carry a TIN or a name, so the
    key holds a digest rather than the message itself: the cache is then a
    lookup structure and not a second copy of the conversation sitting in
    memory for the life of the process. Collisions on SHA-256 are not a
    practical concern; ``blake2b`` at 16 bytes is used because it is faster and
    the values are equally unique for this purpose.
    """
    digest = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest()
    return (source_lang, target_lang, digest)


class _TranslationCache:
    """A bounded LRU of translations, safe to share across request threads."""

    def __init__(self, max_entries: int) -> None:
        self._max = max(0, max_entries)
        self._entries: OrderedDict[tuple[str, str, str], str] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, source_lang: str, target_lang: str, text: str) -> str | None:
        if self._max <= 0:
            return None
        key = _key(source_lang, target_lang, text)
        with self._lock:
            value = self._entries.get(key)
            if value is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return value

    def put(self, source_lang: str, target_lang: str, text: str, translated: str) -> None:
        if self._max <= 0 or not translated:
            return
        if len(text) > MT_CACHE_MAX_CHARS or len(translated) > MT_CACHE_MAX_CHARS:
            return
        key = _key(source_lang, target_lang, text)
        with self._lock:
            self._entries[key] = translated
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "capacity": self._max,
                "hits": self.hits,
                "misses": self.misses,
            }


#: Process-wide cache. Both directions share it; the language pair is part of
#: the key, so an into-English lookup can never serve an out-of-English one.
cache = _TranslationCache(MT_CACHE_SIZE)


def figures(text: str) -> set[float]:
    """Every figure in *text*, as plain numbers.

    Money amounts and percentages are deliberately pooled into one set rather
    than compared category by category, because the category does not survive
    translation even when the number does. Luganda states a rate as "ebitundu
    18 ku buli kikumi" — eighteen parts per hundred, with no percent sign — so
    a per-category comparison sees the source's percentage disappear and the
    translation grow an amount, and rejects a translation that is exactly
    right. The number is what has to be preserved; how the target language
    marks it is the translator's business.

    Formatting is normalised by ``canonical_amounts``: "UGX 1,500,000",
    "1.5m" and "1500000" all reduce to the same value.
    """
    values = canonical_amounts(text)
    values |= {float(value) for value in percentages(text)}
    return values


def figures_survived(source: str, translated: str) -> bool:
    """True when *translated* states the same figures as *source*.

    Equality, not containment, in both directions: a translation may drop
    nothing and invent nothing, because either one is a factual change to a
    tax figure. A source with no figures at all passes trivially, which is the
    common case and costs two cheap regex scans.
    """
    source_figures = figures(source)
    if not source_figures:
        # Nothing to lose — but the translation must still not have grown a
        # figure of its own, which is the invention case.
        return not figures(translated)
    return figures(translated) == source_figures


def heal_vernacular_figures(source: str, translated: str, locale: str = "lg") -> str:
    """Attempt deterministic slot-healing of statutory numbers when translation drops or mutates figures."""
    import re
    src_figs = figures(source)
    trans_figs = figures(translated)
    missing = src_figs - trans_figs
    if not missing:
        return translated

    healed = translated
    # 1. Statutory percentages (e.g. 18%, 6%, 12%, 15%, 30%)
    pct_matches = re.findall(r"(\d+(?:\.\d+)?)\s*%", source)
    for pct_val in pct_matches:
        try:
            f_val = float(pct_val)
            if f_val in missing:
                pattern = rf"\b(ebitundu|asilimia)?\s*({re.escape(pct_val)})\b(?!\s*%)"
                if re.search(pattern, healed, re.IGNORECASE):
                    healed = re.sub(
                        pattern,
                        r"\g<0>%",
                        healed,
                        flags=re.IGNORECASE,
                    )
                elif f"{pct_val}%" not in healed:
                    if locale == "lg" and re.search(r"\b(omusolo|bbeeyi|omuwendo|vat)\b", healed, re.IGNORECASE):
                        healed = re.sub(r"(\b(?:omusolo|bbeeyi|omuwendo|vat)[^.,;\n]*)", rf"\1 ({pct_val}%)", healed, count=1, flags=re.IGNORECASE)
                    elif locale == "sw" and re.search(r"\b(kodi|ushuru|kiwango|vat)\b", healed, re.IGNORECASE):
                        healed = re.sub(r"(\b(?:kodi|ushuru|kiwango|vat)[^.,;\n]*)", rf"\1 ({pct_val}%)", healed, count=1, flags=re.IGNORECASE)
                    else:
                        healed = re.sub(r"([.!?])", rf" ({pct_val}%)\1", healed, count=1)
        except ValueError:
            continue

    # 2. Canonical money amounts (e.g. UGX 150,000,000)
    money_matches = re.findall(r"(?:UGX|Shs\.?|USh)\s*([\d,]+(?:\.\d+)?)", source, re.IGNORECASE)
    for m_val in money_matches:
        cleaned_num = m_val.replace(",", "")
        try:
            val_float = float(cleaned_num)
            if val_float in missing:
                canonical = f"UGX {m_val}"
                if canonical not in healed:
                    if locale == "lg" and re.search(r"\b(ekkomo|ssente|omuwendo|obukadde)\b", healed, re.IGNORECASE):
                        healed = re.sub(
                            r"(\b(?:ekkomo|ssente|omuwendo|obukadde)[^.,;\n]*)",
                            rf"\1 ({canonical})",
                            healed,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    elif locale == "sw" and re.search(r"\b(kiwango|fedha|gharama|milioni)\b", healed, re.IGNORECASE):
                        healed = re.sub(
                            r"(\b(?:kiwango|fedha|gharama|milioni)[^.,;\n]*)",
                            rf"\1 ({canonical})",
                            healed,
                            count=1,
                            flags=re.IGNORECASE,
                        )
                    else:
                        healed = re.sub(r"([.!?])", rf" ({canonical})\1", healed, count=1)
        except ValueError:
            continue

    return healed


def translate_cached(
    text: str,
    source_lang: str,
    target_lang: str,
    translate: Callable[[], str | None],
) -> str | None:
    """Return a cached translation of *text*, or run *translate* and cache it.

    *translate* takes no arguments and returns the translated string, ``None``
    or ``""`` on failure — the same contract every MT tier in this codebase
    already uses. Failures are never cached: a Sunbird timeout must not pin an
    empty answer for the life of the process.

    A translation whose figures did not survive is checked for deterministic
    slot-healing before returning or discarding.
    """
    key_text = (text or "").strip()
    if not key_text:
        return None

    hit = cache.get(source_lang, target_lang, key_text)
    if hit is not None:
        return hit

    out = translate()
    if not out or not out.strip():
        return out or None

    result = out.strip()
    if not figures_survived(key_text, result):
        healed = heal_vernacular_figures(key_text, result, target_lang)
        if figures_survived(key_text, healed):
            result = healed
            cache.put(source_lang, target_lang, key_text, result)
    else:
        cache.put(source_lang, target_lang, key_text, result)
    return result
