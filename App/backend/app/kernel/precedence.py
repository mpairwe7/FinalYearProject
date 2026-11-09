"""Which source wins, and noticing when sources disagree (ADR-0033).

Facts resolve in a fixed order: a computed result, then a kernel fact, then an
official document, then a curated FAQ, then the live web. A lower source may
explain a higher one but never overrules it. When a lower source states a
different figure for something the kernel holds, the kernel's figure stands
and the disagreement goes to the human review queue, so a stale passage gets
corrected at its source rather than argued with at every turn.

Detection is deliberately narrow, to keep false alarms rare: a passage must
name the figure (every content word of its display name, within one window)
next to a different percentage, UGX amount or USD amount.

Editions (ADR-0033 audit). A passage from an edition older than the figure's
fiscal year states the figure of its own time: that is *superseded*, expected,
and not sent to reviewers. A passage that is unlabelled, or as new as the
figure, is a *conflict*. :func:`apply_precedence` ranks both below the
passages that agree, so the figure in force is what the answer rests on; it
judges by the figure the passage states, so it works for the two thirds of
URA documents whose filenames carry no fiscal year.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Lower is stronger.
SOURCE_RANK: dict[str, int] = {
    "computed": 0,
    "kernel": 1,
    "official_document": 2,
    "faq": 3,
    "live_web": 4,
}

_FAQ_DOC_TYPES = frozenset({"csv", "faq_jsonl", "faq"})
_WINDOW = 160
#: Unit words are not part of what a passage must name: "US$500" says USD without the word.
_STOP = frozenset({"the", "of", "on", "for", "and", "a", "an", "to", "in", "tax", "rate", "standard", "ugx", "usd"})
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3})?)\s?(?:%|per\s?cent(?:um)?)", re.I)
_UGX_RE = re.compile(r"(?:UGX|Shs\.?|USh)\s?([\d,]{4,}(?:\.\d+)?)", re.I)
_USD_RE = re.compile(r"(?:USD|US\s?\$|US Dollars?)\s?([\d,]{2,}(?:\.\d+)?)", re.I)
#: A UGX amount in words ("UGX 150 million", "UGX 37.5M"): FAQ rows write thresholds this way.
_UGX_WORDS_RE = re.compile(r"(?:UGX|Shs\.?|USh)\s?(\d{1,4}(?:\.\d+)?)\s?(million|m|billion|bn)\b", re.I)
_MAGNITUDE = {"million": 1e6, "m": 1e6, "billion": 1e9, "bn": 1e9}


def source_class(hit: dict[str, Any]) -> str:
    """Classify a retrieval hit into a precedence class."""
    doc_type = str(hit.get("doc_type") or "").lower()
    if doc_type == "graph":
        return "kernel"
    if doc_type in ("calculator", "tool_result"):
        return "computed"
    if hit.get("live_retrieved_at"):
        return "live_web"
    if doc_type in _FAQ_DOC_TYPES:
        return "faq"
    return "official_document"


@dataclass(frozen=True)
class Conflict:
    key: str
    display_name: str
    kernel_value: str
    evidence_value: str
    evidence_source: str
    evidence_class: str
    excerpt: str
    provision: str
    #: The fiscal year of the kernel figure.
    fiscal_year: str = ""
    #: The edition the passage came from, when labelled (``FY2024-25``).
    evidence_edition: str = ""
    #: ``superseded`` (an older edition) or ``conflict`` (unlabelled, or as new as the figure).
    status: str = "conflict"
    #: ``stated`` (a different figure next to the fact's name) or ``former_value`` (a value the
    #: fact's legal basis records as replaced, wherever the passage states it).
    reason: str = "stated"

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


def _terms(display_name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", display_name.lower()) if w not in _STOP and len(w) > 2]


def _kernel_numbers(value: Any, unit: str) -> dict[str, set[float]]:
    """The numbers the kernel value states, by the pattern that finds them in text."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return {}
    if unit == "ratio":
        return {"percent": {round(float(value) * 100, 3)}}
    if unit in ("UGX", "USD"):
        return {unit: {float(value)}}
    return {}


_PATTERNS = {"percent": _PERCENT_RE, "UGX": _UGX_RE, "USD": _USD_RE}
#: Words that make the number after them the passage's current claim for the figure.
_CHANGE_CUE_RE = re.compile(
    r"\b(?:is\s+now|now\s+(?:is|stands\s+at)|increased\s+to|raised\s+to|revised\s+to|changed\s+to|"
    r"reduced\s+to|lowered\s+to|amended\s+to|from\s+\S+\s+to|new\s+rate\s+(?:is|of))\W*$"
)
#: Words that make the number after them a past figure, not the passage's claim ("previously it was 18%").
_HISTORY_CUE_RE = re.compile(
    r"(?:\b(?:previously|formerly|used\s+to\s+be|was\s+(?:previously|formerly|once)|old\s+rate\s+(?:was|of))"
    r"\W*(?:\w+\W+){0,2}|\bfrom\W*)$"
)
#: Where one statement ends and another begins: a change cue after one names a different figure.
_CLAUSE_BREAK_RE = re.compile(r";|(?<!\d)\.(?!\d)|\n|,\s*(?:but|while|whereas)\b|\b(?:while|whereas)\b")
#: "<word> rate", "<word> levy", …: a figure's name; one not built from the fact's words is another figure.
_SUBJECT_RE = re.compile(r"\b([a-z]+)\s+(?:rate|levy|duty|threshold|fee|allowance)\b")
_DETERMINERS = frozenset({"the", "a", "an", "this", "that", "its", "new", "current", "same", "which", "is", "was"})
_SUFFIX = {"percent": "%", "UGX": " UGX", "USD": " USD"}


def _edition(hit: dict[str, Any]) -> tuple[int | None, str]:
    """``(start year, label)`` of the edition *hit* came from, or ``(None, "")`` when unlabelled."""
    try:
        from ..retriever import edition_start_year

        start = edition_start_year(hit)
    except Exception:
        start = None
    return (start, f"FY{start}-{(start + 1) % 100:02d}") if start is not None else (None, "")


def _num(value: float) -> str:
    """A figure as a reviewer reads it: ``6000000``, ``1.5``; never ``6e+06``."""
    return str(int(value)) if float(value).is_integer() else f"{value:.6g}"


def _change_names_the_fact(low: str, start: int, terms: list[str], name_words: set[str]) -> bool:
    """Whether a change cue right before *start* ("is now 20%") is about this fact.

    The fact's words must come before the cue in the same statement, with no other
    figure named in between: in "the standard rate is 18%; the export rate increased
    to 5%" the increase is the export rate's.
    """
    if not _CHANGE_CUE_RE.search(low[max(0, start - 30):start]):
        return False
    positions = [low.rfind(term, 0, start) for term in terms]
    if min(positions) < 0:
        return False
    gap = low[max(positions):start]
    if _CLAUSE_BREAK_RE.search(gap):
        return False
    return not any(word not in name_words and word not in _DETERMINERS for word in _SUBJECT_RE.findall(gap))


def _states_the_figure(matches: list, numbers: dict[str, set[float]], terms: list[str], low: str) -> bool:
    """Whether the passage states one of the kernel's numbers next to the figure's name as current.

    A past mention ("previously it was 18%", "from 18% to 20%") does not count: it
    must not hide the figure the passage now gives.
    """
    for kind, match in matches:
        if _HISTORY_CUE_RE.search(low[max(0, match.start() - 40):match.start()]):
            continue
        window = low[max(0, match.start() - _WINDOW):match.end() + _WINDOW]
        if not all(term in window for term in terms):
            continue
        try:
            number = float(match.group(1).replace(",", ""))
        except ValueError:
            continue
        if any(abs(number - e) < 1e-6 for e in numbers[kind]):
            return True
    return False


def _fy_start(fiscal_year: str) -> int | None:
    match = re.fullmatch(r"FY(\d{4})-\d{2}", fiscal_year or "")
    return int(match.group(1)) if match else None


_FORMER_VALUE_RE = re.compile(
    r"\b(?:from|previously|formerly|replacing|replaced|was)\s+(?:(USD|UGX)\s?([\d,]+(?:\.\d+)?)|([\d.]+)\s?%)",
    re.IGNORECASE,
)
#: Words too common to tell one statutory subject from another.
_GENERIC = frozenset({
    "uganda", "taxes", "section", "schedule", "effect", "raised", "amended", "amendment", "goods", "value",
    "rates", "years", "under", "which", "shall", "their", "above", "below", "about", "other", "there",
    "these", "those", "where", "while", "every", "first", "second", "third", "fourth", "fifth", "sixth",
})


#: What puts a passage on a fact's subject, by fact key: (groups, excluded). Every group needs one
#: of its words (a word start: "regist" matches "registered") within 240 characters of the value,
#: and no excluded word may stand within 150. A number alone cannot say which figure it is:
#: UGX 150,000,000 is the replaced VAT threshold and also the presumptive-tax ceiling in force (G535).
#: Every fact whose legal basis records a replaced value needs an entry; a test enforces it.
_SUBJECT_CUES: dict[str, tuple[tuple[tuple[str, ...], ...], tuple[str, ...]]] = {
    "passenger_baggage_allowance_usd": (
        (("baggage", "passenger", "traveller", "traveler", "personal effects", "accompanied"),
         # The allowance, not another per-passenger charge.
         ("allowance", "exempt", "duty-free", "duty free", "free of", "exceed", "customs value")), ()),
    "vat_registration_threshold_annual": ((("vat", "value added tax", "taxable supplies"), ("regist", "threshold")),
                                          ("presumptive",)),
    "efris_e_receipt_refund_qualifying_threshold_ugx": ((("refund",), ("receipt", "invoice", "efris")), ()),
}


def _cue(word: str, text: str) -> bool:
    return re.search(r"\b" + re.escape(word), text) is not None


def _on_subject(fact: dict[str, Any], text: str, start: int, end: int) -> bool:
    """Whether the amount at *start*:*end* of *text* is about *fact*'s subject."""
    low = text.lower()
    cues = _SUBJECT_CUES.get(str(fact.get("key") or ""))
    if cues is not None:
        groups, excluded = cues
        near = low[max(0, start - 240):end + 240]
        close = low[max(0, start - 150):end + 150]
        return all(any(_cue(w, near) for w in group) for group in groups) and not any(_cue(w, close) for w in excluded)
    # No cues recorded: two of the fact's distinctive words (its name or legal basis) near the value.
    near_stems = _stems(text[max(0, start - 240):end + 240])
    subject = _stems(f"{fact.get('display_name') or ''} {fact.get('legal_basis') or ''}")
    return len(near_stems & subject) >= 2


def _states_current(fact: dict[str, Any], text: str, numbers: dict[str, set[float]]) -> bool:
    """Whether *text* states *fact*'s current value on its subject, in figures or words ("UGX 300 million")."""
    for kind, values in numbers.items():
        if kind not in _PATTERNS:
            continue
        for match, number in _amounts(kind, text):
            if any(abs(number - v) < 1e-6 for v in values) and _on_subject(fact, text, match.start(), match.end()):
                return True
    return False


def _stems(text: str) -> set[str]:
    """Five-letter stems of the distinctive words in *text* ("registration" and "register" share one).

    Words under five letters ("from", "July", "with") say nothing about a subject.
    """
    return {w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 5 and w not in _GENERIC}


def _former_values(legal_basis: str) -> dict[str, set[float]]:
    """Values a fact's legal basis records as replaced ("Raised from USD 500"), by pattern kind.

    "from", "previously", "formerly", "replacing", "replaced" and "was" introduce
    one. A currency or a percent sign is required, so "with effect from 1 July
    2024" is a date and not a former value.
    """
    out: dict[str, set[float]] = {}
    for currency, amount, percent in _FORMER_VALUE_RE.findall(legal_basis or ""):
        try:
            if percent:
                out.setdefault("percent", set()).add(round(float(percent), 3))
            else:
                out.setdefault(currency.upper(), set()).add(float(amount.replace(",", "")))
        except ValueError:
            continue
    return out


def find_conflicts(facts: list[dict[str, Any]], passages: list[dict[str, Any]]) -> list[Conflict]:
    """Kernel facts that a lower-ranked passage contradicts.

    *facts* are kernel claims as dicts with ``key``, ``display_name``,
    ``value``, ``unit``, ``provision`` and ``legal_basis``; *passages* are
    retrieval hits.

    Two tests, in order. A different figure next to the fact's name. Then, for
    a fact whose legal basis records a replaced value, that value stated
    anywhere in a passage retrieved alongside it: a paraphrase ("personal
    effects … not exceeding USD 500") never names the fact, and dense retrieval
    finds exactly such paraphrases (ADR-0033 amendment, G534).
    """
    found: list[Conflict] = []
    for fact in facts:
        terms = _terms(str(fact.get("display_name") or ""))
        name_words = set(re.findall(r"[a-z]+", str(fact.get("display_name") or "").lower()))
        numbers = _kernel_numbers(fact.get("value"), str(fact.get("unit") or ""))
        if not terms or not numbers:
            continue
        fiscal_year = str(fact.get("fiscal_year") or "")
        fact_start = _fy_start(fiscal_year)
        for hit in passages:
            klass = source_class(hit)
            if SOURCE_RANK.get(klass, 99) <= SOURCE_RANK["kernel"]:
                continue
            text = str(hit.get("text") or hit.get("answer") or "")
            low = text.lower()
            matches = [(kind, m) for kind in numbers for m in _PATTERNS[kind].finditer(text)]
            states_it = _states_the_figure(matches, numbers, terms, low)
            for kind, match in sorted(matches, key=lambda km: km[1].start()):
                if states_it and not _change_names_the_fact(low, match.start(), terms, name_words):
                    # It states the kernel's figure for this name; a different number nearby
                    # (the 0% export rate beside the 18% standard rate) is about something
                    # else, unless the passage says the figure changed to it ("is now 20%").
                    continue
                window = low[max(0, match.start() - _WINDOW):match.end() + _WINDOW]
                if not all(term in window for term in terms):
                    continue
                try:
                    number = float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
                expected = numbers[kind]
                if any(abs(number - e) < 1e-6 for e in expected):
                    continue
                start, label = _edition(hit)
                older = start is not None and fact_start is not None and start < fact_start
                found.append(Conflict(
                    key=str(fact.get("key") or ""),
                    display_name=str(fact.get("display_name") or ""),
                    kernel_value=f"{_num(sorted(expected)[0])}{_SUFFIX[kind]}",
                    evidence_value=f"{_num(number)}{_SUFFIX[kind]}",
                    evidence_source=str(hit.get("source") or hit.get("url") or ""),
                    evidence_class=klass,
                    excerpt=" ".join(text[max(0, match.start() - 80):match.end() + 80].split()),
                    provision=str(fact.get("provision") or ""),
                    fiscal_year=fiscal_year,
                    evidence_edition=label,
                    status="superseded" if older else "conflict",
                ))
                break
            else:
                # A passage that states the current figure is current, even if it also
                # says what the figure was ("raised from USD 500 to USD 2,000").
                current = states_it or _states_current(fact, text, numbers)
                conflict = None if current else _former_value_conflict(
                    fact, hit, klass, text, numbers, fiscal_year, fact_start)
                if conflict is not None:
                    found.append(conflict)
    return found


def _amounts(kind: str, text: str) -> list[tuple[re.Match[str], float]]:
    """Every amount of *kind* in *text*, in order, with UGX amounts in words ("150 million") too."""
    found: list[tuple[re.Match[str], float]] = []
    for match in _PATTERNS[kind].finditer(text):
        try:
            found.append((match, float(match.group(1).replace(",", ""))))
        except ValueError:
            continue
    if kind == "UGX":
        for match in _UGX_WORDS_RE.finditer(text):
            found.append((match, float(match.group(1)) * _MAGNITUDE[match.group(2).lower()]))
    return sorted(found, key=lambda pair: pair[0].start())


def _former_value_conflict(fact: dict[str, Any], hit: dict[str, Any], klass: str, text: str,
                           numbers: dict[str, set[float]], fiscal_year: str,
                           fact_start: int | None) -> Conflict | None:
    """A passage stating a value the fact's legal basis records as replaced, or ``None``."""
    former = _former_values(str(fact.get("legal_basis") or fact.get("provision") or ""))
    for kind, values in former.items():
        if kind not in _PATTERNS or kind not in numbers:
            continue  # only the fact's own unit: a former USD value says nothing about a rate
        values = {v for v in values if not any(abs(v - n) < 1e-6 for n in numbers[kind])}  # never the current one
        for match, number in _amounts(kind, text):
            if not any(abs(number - v) < 1e-6 for v in values):
                continue
            if _HISTORY_CUE_RE.search(text.lower()[max(0, match.start() - 40):match.start()]):
                continue  # "previously USD 500": history, not a claim
            # The same subject, not just the same number: UGX 150,000,000 is also the
            # presumptive-tax ceiling in force (_SUBJECT_CUES).
            if not _on_subject(fact, text, match.start(), match.end()):
                continue
            start, label = _edition(hit)
            older = start is not None and fact_start is not None and start < fact_start
            return Conflict(
                key=str(fact.get("key") or ""),
                display_name=str(fact.get("display_name") or ""),
                kernel_value=f"{_num(sorted(numbers[kind])[0])}{_SUFFIX[kind]}",
                evidence_value=f"{_num(number)}{_SUFFIX[kind]}",
                evidence_source=str(hit.get("source") or hit.get("url") or ""),
                evidence_class=klass,
                excerpt=" ".join(text[max(0, match.start() - 80):match.end() + 80].split()),
                provision=str(fact.get("provision") or ""),
                fiscal_year=fiscal_year,
                evidence_edition=label,
                status="superseded" if older else "conflict",
                reason="former_value",
            )
    return None


def facts_from_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve graph claims (display name, fiscal year) back to kernel facts with key and unit."""
    from ..graph.schema import FACT_KINDS
    from .functions import kernel_store

    store = kernel_store()
    index = {
        (str(n.props.get("display_name")), str(n.props.get("fiscal_year"))): n
        for kind in FACT_KINDS for n in store.by_kind(kind)
    }
    out = []
    for claim in claims:
        node = index.get((str(claim.get("subject") or ""), str(claim.get("fiscal_year") or "")))
        if node is None:
            continue
        out.append({
            "key": node.props.get("key"),
            "display_name": node.props.get("display_name"),
            "value": node.props.get("value"),
            "unit": node.props.get("unit"),
            "provision": claim.get("provision") or node.props.get("legal_basis") or "",
            # The full basis, which records replaced values ("Raised from USD 500").
            "legal_basis": node.props.get("legal_basis") or "",
            "fiscal_year": node.props.get("fiscal_year") or "",
        })
    return out


def check_fused(graph_hit: dict[str, Any], hits: list[dict[str, Any]]) -> list[Conflict]:
    """Compare a fused graph hit's facts with the other hits; record and return conflicts.

    Never raises: a precedence check must not take down retrieval.
    """
    return _check(graph_hit, hits)[0]


def _check(graph_hit: dict[str, Any], hits: list[dict[str, Any]], *,
           record: bool = True) -> tuple[list[Conflict], dict[int, Conflict]]:
    """Conflicts, and the first one per contradicting hit (by ``id()``). Records only true conflicts."""
    try:
        facts = facts_from_claims(list(graph_hit.get("graph_claims") or []))
        by_hit: dict[int, Conflict] = {}
        conflicts: list[Conflict] = []
        for hit in hits:
            if hit is graph_hit:
                continue
            found = find_conflicts(facts, [hit])
            if found:
                by_hit[id(hit)] = found[0]
                conflicts.extend(found)
        if conflicts:
            from .pin import active_pin

            pin = active_pin() or {}
            graph_hit["kernel_conflicts"] = [c.to_dict() for c in conflicts]
            reportable = [c for c in conflicts if c.status == "conflict"]
            logger.warning("kernel: %d passage(s) disagree with the kernel (%d superseded editions): %s",
                           len(conflicts), len(conflicts) - len(reportable),
                           ", ".join(sorted({c.key for c in conflicts})))
            # A conflict is queued once per turn: the signatures already recorded travel on
            # the graph hit, so ordering again after FAQ blending records only new ones.
            done = graph_hit.setdefault("_kernel_conflicts_recorded", [])
            fresh = [c for c in reportable if _signature(c) not in done]
            for conflict in fresh if record else ():
                # Marked only once written: a failed write is tried again on the next pass.
                if record_conflicts([conflict], kernel_version=str(pin.get("kernel_version") or "")) == 1:
                    done.append(_signature(conflict))
        return conflicts, by_hit
    except Exception:
        logger.warning("kernel: precedence check skipped", exc_info=True)
        return [], {}


def apply_precedence(graph_hit: dict[str, Any], hits: list[dict[str, Any]], *,
                     record: bool = True) -> list[dict[str, Any]]:
    """*hits* with stale passages left out and unresolved conflicts labelled last.

    * **Stale** (an older edition, or a value the fact's legal basis records as
      replaced): left out of the answer's evidence, and listed once on the graph
      hit under ``kernel_precedence_dropped`` for the audit. A dense hit stating
      the replaced USD 500 baggage allowance was quoted before this (G534).
    * **Unresolved** (an unlabelled or current passage stating a different
      figure): kept, moved below the rest, and annotated ``kernel_precedence``.
      The passage header says the figure disagrees with the rate table, so the
      answer reports both and advises confirming with URA. The kernel can be the
      stale side after a new Finance Act, and dropping would hide that.

    A conflict is queued once per turn however often this runs (FAQ promotion
    orders again); *record* off queues nothing. Never raises: on any error *hits*
    comes back unchanged.
    """
    try:
        _conflicts, by_hit = _check(graph_hit, hits, record=record)
        if not by_hit:
            return hits
        agree, unresolved = [], []
        dropped = graph_hit.setdefault("kernel_precedence_dropped", [])
        for hit in hits:
            conflict = by_hit.get(id(hit))
            if conflict is None:
                agree.append(hit)
                continue
            if conflict.status == "superseded" or conflict.reason == "former_value":
                entry = {"status": conflict.status, "reason": conflict.reason, "key": conflict.key,
                         "kernel_value": conflict.kernel_value, "stated_value": conflict.evidence_value,
                         "edition": conflict.evidence_edition, "fiscal_year": conflict.fiscal_year,
                         "source": conflict.evidence_source, "passage": _passage_identity(hit)}
                if not any(d.get("passage") == entry["passage"] and d.get("key") == entry["key"] for d in dropped):
                    dropped.append(entry)  # once per passage, however often the order is rebuilt
                continue
            hit["kernel_precedence"] = {"status": conflict.status, "key": conflict.key,
                                        "display_name": conflict.display_name,
                                        "kernel_value": conflict.kernel_value,
                                        "stated_value": conflict.evidence_value,
                                        "edition": conflict.evidence_edition, "fiscal_year": conflict.fiscal_year}
            unresolved.append(hit)
        return agree + unresolved
    except Exception:
        logger.warning("kernel: precedence ordering skipped", exc_info=True)
        return hits


def _facts_with_former_values(fiscal_year: str) -> list[dict[str, Any]]:
    """The kernel facts in force for *fiscal_year* whose legal basis records a replaced value."""
    from ..graph.schema import FACT_KINDS
    from .functions import kernel_store

    store = kernel_store()
    cached = _FORMER_FACTS_CACHE.get(fiscal_year)
    if cached is not None and cached[0] is store:
        return cached[1]  # the same store object: an id() could be reused by a reloaded one
    facts = []
    for kind in FACT_KINDS:
        for node in store.by_kind(kind):
            props = node.props
            if str(props.get("fiscal_year") or "") != fiscal_year or not _former_values(str(props.get("legal_basis") or "")):
                continue
            facts.append({"key": props.get("key"), "display_name": props.get("display_name"), "value": props.get("value"),
                          "unit": props.get("unit"), "provision": props.get("legal_basis") or "",
                          "legal_basis": props.get("legal_basis") or "", "fiscal_year": fiscal_year})
    _FORMER_FACTS_CACHE.clear()  # one kernel and one year in force at a time
    _FORMER_FACTS_CACHE[fiscal_year] = (store, facts)
    return facts


#: fiscal year -> (the store the facts came from, the facts). Holding the store keeps the check by
#: identity sound: a reloaded table is a new object, so its facts are read again.
_FORMER_FACTS_CACHE: dict[str, tuple[Any, list[dict[str, Any]]]] = {}


def _former_value_in(hit: dict[str, Any], facts: list[dict[str, Any]]) -> Conflict | None:
    """The first in-force fact whose replaced value *hit* states, or ``None``."""
    klass = source_class(hit)
    if SOURCE_RANK.get(klass, 99) <= SOURCE_RANK["kernel"] or str(hit.get("doc_type") or "") == "graph":
        return None
    text = " ".join(str(hit.get(field) or "") for field in ("text", "answer") if hit.get(field))
    if not text:
        return None
    low = text.lower()
    for fact in facts:
        numbers = _kernel_numbers(fact.get("value"), str(fact.get("unit") or ""))
        terms = _terms(str(fact.get("display_name") or ""))
        if not numbers or not terms:
            continue
        matches = [(kind, m) for kind in numbers for m in _PATTERNS[kind].finditer(text)]
        if _states_the_figure(matches, numbers, terms, low) or _states_current(fact, text, numbers):
            continue  # it states the current figure too, in figures or words
        conflict = _former_value_conflict(fact, hit, klass, text, numbers, str(fact.get("fiscal_year") or ""),
                                          _fy_start(str(fact.get("fiscal_year") or "")))
        if conflict is not None:
            return conflict
    return None


def screen_former_values(hits: list[dict[str, Any]], *, fiscal_year: str | None = None) -> list[dict[str, Any]]:
    """*hits* without the passages that state a value the kernel in force records as replaced (G535).

    :func:`apply_precedence` needs a graph hit carrying the fact, and the graph
    leg finds one only when the question names it. "Under the EACCMA Fifth
    Schedule, what value of accompanied goods is exempt?" got none, and a FAQ
    row with the replaced USD 500 allowance reached the model whether the dense
    leg was on or off. This runs on every path, graph hit or not, for the few
    facts whose legal basis records a replaced value.

    A question about a past year is answered from that year's rate table, not
    from passages, so a passage giving last year's figure is left out here too.
    Each dropped passage is logged and, when a graph hit is present, listed under
    its ``kernel_precedence_dropped``. Never raises: on any error *hits* comes
    back unchanged.
    """
    if not hits:
        return hits
    try:
        if fiscal_year is None:
            from ..tax import tables

            fiscal_year = tables.resolve_fiscal_year()
        facts = _facts_with_former_values(fiscal_year)
        if not facts:
            return hits
        kept: list[dict[str, Any]] = []
        dropped: list[tuple[dict[str, Any], Conflict]] = []
        for hit in hits:
            conflict = _former_value_in(hit, facts)
            if conflict is None:
                kept.append(hit)
            else:
                dropped.append((hit, conflict))
        if not dropped:
            return hits
        graph_hit = next((h for h in kept if str(h.get("doc_type") or "") == "graph"), None)
        for hit, conflict in dropped:
            logger.info("precedence: left out a replaced value key=%s stated=%s source=%s",
                        conflict.key, conflict.evidence_value, conflict.evidence_source)
            if graph_hit is not None:
                listed = graph_hit.setdefault("kernel_precedence_dropped", [])
                entry = {"status": conflict.status, "reason": conflict.reason, "key": conflict.key,
                         "kernel_value": conflict.kernel_value, "stated_value": conflict.evidence_value,
                         "edition": conflict.evidence_edition, "fiscal_year": conflict.fiscal_year,
                         "source": conflict.evidence_source, "passage": _passage_identity(hit)}
                if not any(d.get("passage") == entry["passage"] and d.get("key") == entry["key"] for d in listed):
                    listed.append(entry)
        return kept
    except Exception:  # noqa: BLE001 - a precedence check must never take retrieval down
        logger.warning("precedence: former-value screen failed; hits unchanged", exc_info=True)
        return hits


def _passage_identity(hit: dict[str, Any]) -> str:
    """A stable identity for a passage across precedence passes: its id, or its source and text."""
    if hit.get("id"):
        return str(hit["id"])
    text = str(hit.get("text") or hit.get("answer") or "")
    return f"{hit.get('source') or ''}|{hashlib.sha256(text.encode('utf-8', 'replace')).hexdigest()[:16]}"


def record_conflicts(conflicts: list[Conflict], *, kernel_version: str) -> int:
    """Send conflicts to the discrepancy review queue. Returns how many were recorded.

    Never raises: a review-queue write must not take down the turn.
    """
    recorded = 0
    for conflict in conflicts:
        try:
            from ..database import create_or_increment_discrepancy

            outcome = create_or_increment_discrepancy(
                bot_statement=f"{conflict.evidence_source}: {conflict.excerpt}"[:900],
                user_correction=(
                    f"Kernel {kernel_version}: {conflict.display_name} = {conflict.kernel_value}"
                    f" ({conflict.provision or 'no provision recorded'})"
                ),
                discrepancy_type="evidence_conflict",
                cited_sources=[conflict.evidence_source] if conflict.evidence_source else [],
            )
            if isinstance(outcome, dict) and outcome.get("status") == "failed":
                # The Postgres store reports a failed write instead of raising.
                logger.warning("kernel: the review queue refused an evidence conflict for %s", conflict.key)
                continue
            recorded += 1
        except Exception:
            logger.warning("kernel: could not record an evidence conflict for %s", conflict.key, exc_info=True)
    return recorded


def _signature(conflict: Conflict) -> str:
    return f"{conflict.key}|{conflict.evidence_source}|{conflict.evidence_value}"


def reorder_after_promotion(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order *hits* by precedence again once FAQ promotion has run (CodeRabbit, PR 706).

    Priority and exact-match FAQ rows are put in front after the graph is fused,
    so a FAQ contradicting the kernel could lead again and feed the extractive
    fallback. Every hit is first screened for replaced values
    (:func:`screen_former_values`); the graph hit's checks then need the graph
    hit, found in *hits*.
    """
    # FAQ rows blended in after retrieval are screened too, graph hit or not (G535).
    hits = screen_former_values(hits)
    graph_hit = next((h for h in hits if str(h.get("doc_type") or "") == "graph"), None)
    if graph_hit is None:
        return hits
    # Recording on: a FAQ blended in after the first check is queued now, and one
    # already queued is not queued again (_kernel_conflicts_recorded).
    return apply_precedence(graph_hit, hits)
