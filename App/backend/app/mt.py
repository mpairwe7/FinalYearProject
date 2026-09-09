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

Two mechanisms address that, in this order.

:func:`protect_figures` masks every digit group behind an opaque sentinel
before the text is handed to a translator, and :func:`restore_figures` puts
the original digits back afterwards. A translator that never sees a digit
cannot paraphrase one, so mutation stops being a thing that has to be
detected. Only the digits are masked: the currency code and the percent sign
stay visible because they are the cue the target language needs to build the
right construction — Luganda states a rate as "ebitundu 18 ku buli kikumi",
and it can only do that if it can still see that 18 was a percentage.

:func:`figures_survived` remains, now as the assertion rather than the
mechanism. It compares the money amounts and percentages on both sides so the
caller can refuse a translation that changed them, and it still fires when a
translator drops a sentinel outright rather than mutating it. The behaviour
for a refused translation is unchanged: serve the English text, which is a
worse read but never a wrong number.

An earlier attempt (``heal_vernacular_figures``, PRs #481/#482) tried to
repair a bad translation *after* the fact by re-inserting statutory figures
into the output. Repairing after the fact means guessing where the number
belonged, and guessing wrong writes a figure into a sentence that never had
one. Narrowing it until it could not guess left it unable to fire at all —
by then a figure only counted as missing when its digits were absent, and
every remaining insertion path required those digits to be present. Masking
before the fact needs no guess, which is why it replaced it.

Both are per-process and deliberately so: this is a hot-path memo, not a
system of record, and replicas do not need to agree about it.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
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

#: Mask figures before translation and restore them after. On by default
#: because it is the only mechanism here that prevents a mutated figure
#: rather than detecting one. It is a kill switch rather than a rollout
#: flag: a translation tier that cannot carry the sentinels degrades to the
#: unprotected path on its own (see ``service.localize_reply``), so this
#: exists for the case where that degradation is itself the problem and an
#: operator needs it off without a redeploy.
MT_PROTECT_FIGURES = os.getenv("MT_PROTECT_FIGURES", "true").lower() in ("1", "true", "yes", "on")

#: Sentinel core. Deliberately not a substring of any English word, so
#: leftover sentinel fragments can be counted with a plain search without
#: matching "figure", "config" or "number" in ordinary prose.
_SENTINEL_CORE = "NMBR"

#: A figure as it is written: "150,000,000", "1 500 000", "1.5", "18".
#: The currency code and the percent sign are outside the span on purpose —
#: see the module docstring. A trailing sentence period is not consumed
#: because the decimal branch requires digits after the point.
_FIGURE_SPAN_RE = re.compile(r"\d+(?:[,\u00a0 ]\d{3})*(?:\.\d+)?")

#: Any surviving sentinel fragment. Case-insensitive because a translator
#: that lowercases the sentence lowercases the sentinel with it.
_SENTINEL_RESIDUE_RE = re.compile(_SENTINEL_CORE, re.IGNORECASE)


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


#: Tokens that mark an amount as money, in any of the three languages served.
#: ``ssente`` (Luganda) and ``shilingi`` (Kiswahili) are read from
#: ``Data/eval/rag_eval_lg.jsonl`` and the reviewed probes in
#: ``tests/load/tax_education_accuracy_eval.py`` — no vocabulary is coined here.
_CURRENCY_TOKEN_RE = re.compile(
    r"\b(?:UGX|USh(?:s)?|Shs?|shillings?|shilingi|ssente|sente)\b",
    re.IGNORECASE,
)


#: Shortest a translation may be, as a fraction of the source it renders.
#:
#: Measured, not chosen. Across the 23,838 aligned English→Luganda and
#: English→Kiswahili pairs in ``Data/online_corpora/salt/`` (human
#: translations, sentences of 20 characters or more):
#:
#: ===========  ======  ======  ======  ======  ======
#: direction    p0.1    p1      p50     p95     p99
#: ===========  ======  ======  ======  ======  ======
#: en→lg        0.449   0.603   1.048   1.463   1.717
#: en→sw        0.434   0.582   0.973   1.302   1.500
#: ===========  ======  ======  ======  ======  ======
#:
#: Below 0.4 lies 0.042% of Luganda pairs and 0.050% of Kiswahili ones; below
#: 0.3, fewer than one in eight thousand. A whole answer is many sentences and
#: its ratio concentrates harder around the median than any single pair, so
#: 0.35 is looser for the text this actually guards than the table suggests.
#:
#: The guard it replaces was ``len(candidate) < max(12, len(text) // 10)`` — a
#: floor at one tenth, which passes a translation that dropped nine tenths of
#: the answer. It was written to catch a collapsed MT response and does; what
#: it does not catch is a truncated one, which reads as a complete answer that
#: happens to omit the taxpayer's obligations.
MT_MIN_LENGTH_RATIO = float(os.getenv("MT_MIN_LENGTH_RATIO", "0.35"))


def length_plausible(source: str, translated: str) -> bool:
    """True when *translated* is long enough to be a rendering of *source*.

    Only a floor. There is no ceiling: a translation running long is a
    stylistic matter, and the p99 above (1.7) shows how ordinary that is.
    """
    source_length = len((source or "").strip())
    if source_length < 40:
        # Too short to take a ratio of — a greeting or a one-line abstention,
        # where a legitimate rendering can be a single word.
        return len((translated or "").strip()) > 0
    return len((translated or "").strip()) >= source_length * MT_MIN_LENGTH_RATIO


#: A citation marker as the answer carries it. Same shape ``claim_verifier``
#: reads, and deliberately so: the two must agree on what a citation is or the
#: verification report describes a different text than the one shipped.
_CITATION_MARKER_RE = re.compile(r"\[(\d{1,3})\]")


def citations_survived(source: str, translated: str) -> bool:
    """True when *translated* still carries the citation markers *source* had.

    The markers are what tie each claim to the URA passage that supports it,
    and ``claim_verifier`` reads them to decide whether a claim was verified at
    all. That verification runs on the English draft, *before* this module sees
    the text — so a translator that drops ``[2]``, renumbers it, or merges two
    sentences and their markers ships an answer whose provenance no longer
    matches the report that approved it. The taxpayer loses the source link and
    the audit trail loses its subject.

    Set equality, not order: a translation may reorder clauses, and a marker
    that moved with its clause is still attached to the right claim. What may
    not happen is a marker appearing or disappearing.
    """
    source_markers = set(_CITATION_MARKER_RE.findall(source or ""))
    if not source_markers:
        return True
    return set(_CITATION_MARKER_RE.findall(translated or "")) == source_markers


def units_survived(source: str, translated: str) -> bool:
    """True when *translated* still marks its figures as rates and amounts.

    ``figures_survived`` compares digits, and ``protect_figures`` masks only
    digits — the percent sign and the currency code are left visible on
    purpose, because they are the cue the target language needs to build
    "ebitundu 18 ku buli kikumi". Both decisions are right and together they
    leave one thing unchecked: a translation that keeps every digit and drops
    the unit. "18%" arriving as a bare "18", or "UGX 300,000,000" as
    "300,000,000", passes every guard above and hands a taxpayer a number with
    no idea what it counts.

    Deliberately one-directional, and only on total loss. The check fires when
    the source marked a figure and the translation marks none of that kind at
    all — not when the sets differ, which is what ``figures()`` pools
    categories to tolerate. A translator that renders one of two rates as a
    word keeps its marker for the other and passes here, as it should:
    ``localize_reply`` answers a failure by falling back to English, so a
    stricter test buys precision on a rare fault by costing vernacular answers
    on a common one.
    """
    if percentages(source) and not percentages(translated):
        return False
    if _CURRENCY_TOKEN_RE.search(source or "") and not _CURRENCY_TOKEN_RE.search(translated or ""):
        return False
    return True


def _sentinel_label(index: int) -> str:
    """``A``, ``B`` … ``Z``, ``AA`` — letters, never digits.

    The index has to survive a translator that rewrites numbers, which is the
    exact failure this whole mechanism exists to stop. A numeric index would
    be as exposed as the figure it stands in for.
    """
    label = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def protect_figures(text: str) -> tuple[str, dict[str, str]]:
    """Mask every digit group in *text*, returning the masked text and its map.

    ``"The threshold is UGX 150,000,000."`` becomes
    ``"The threshold is UGX #NMBRA#."`` with ``{"#NMBRA#": "150,000,000"}``.

    Each occurrence gets its own sentinel even when two of them read the same,
    so restoration is positional and a translator that reorders a sentence
    cannot swap one figure for another.

    Text with no figures returns unchanged with an empty map, which is the
    common case and lets the caller skip the round trip through
    :func:`restore_figures` entirely.
    """
    mapping: dict[str, str] = {}

    def _mask(match: re.Match[str]) -> str:
        token = f"#{_SENTINEL_CORE}{_sentinel_label(len(mapping))}#"
        mapping[token] = match.group(0)
        return token

    return _FIGURE_SPAN_RE.sub(_mask, text or ""), mapping


def restore_figures(text: str, mapping: dict[str, str]) -> tuple[str, int]:
    """Put the original digits back, returning the text and any residue count.

    Matching is deliberately tolerant. A translator hands the sentinel back
    lowercased, spaced out, or stripped of one or both hashes, and all of
    those are still the sentinel; a Bantu translator may also glue a
    noun-class prefix onto the front of it, so no left boundary is required.
    The right boundary is required, or ``#NMBRA#`` would match inside
    ``#NMBRAA#`` and restore the wrong figure.

    The second element is the number of sentinel fragments still in the text
    afterwards — a translator that echoed one twice, or mangled it past
    recognition. It is never zero-and-fine to ignore: a fragment left in the
    output is visible garbage in a taxpayer's answer, so the caller must
    treat any residue as a failed round trip rather than shipping it.
    """
    restored = text or ""
    for token, original in mapping.items():
        label = token[1 + len(_SENTINEL_CORE) : -1]
        pattern = re.compile(
            rf"(?:#\s*)?{_SENTINEL_CORE}\s*{label}(?![A-Za-z])(?:\s*#)?",
            re.IGNORECASE,
        )
        restored = pattern.sub(lambda _match, _original=original: _original, restored, count=1)
    return restored, len(_SENTINEL_RESIDUE_RE.findall(restored))


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

    A translation whose figures did not survive is returned to the caller
    *and* not cached, so the caller applies its own policy (all of them serve
    the English text) without this function deciding that for it. Callers that
    want the figures protected rather than merely checked mask the text with
    :func:`protect_figures` before building *translate*.
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
    if figures_survived(key_text, result):
        cache.put(source_lang, target_lang, key_text, result)
    return result
