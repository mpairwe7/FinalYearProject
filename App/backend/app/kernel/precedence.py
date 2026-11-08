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


def _states_the_figure(matches: list, numbers: dict[str, set[float]], terms: list[str], low: str) -> bool:
    """Whether the passage states one of the kernel's numbers next to the figure's name."""
    for kind, match in matches:
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


def find_conflicts(facts: list[dict[str, Any]], passages: list[dict[str, Any]]) -> list[Conflict]:
    """Kernel facts that a lower-ranked passage contradicts.

    *facts* are kernel claims as dicts with ``key``, ``display_name``,
    ``value``, ``unit`` and ``provision``; *passages* are retrieval hits.
    """
    found: list[Conflict] = []
    for fact in facts:
        terms = _terms(str(fact.get("display_name") or ""))
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
            if _states_the_figure(matches, numbers, terms, low):
                # It states the kernel's figure for this name; a different number nearby
                # (the 0% export rate beside the 18% standard rate) is about something else.
                continue
            for kind, match in sorted(matches, key=lambda km: km[1].start()):
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
    return found


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
            if reportable and record:
                record_conflicts(reportable, kernel_version=str(pin.get("kernel_version") or ""))
        return conflicts, by_hit
    except Exception:
        logger.warning("kernel: precedence check skipped", exc_info=True)
        return [], {}


def apply_precedence(graph_hit: dict[str, Any], hits: list[dict[str, Any]], *,
                     record: bool = True) -> list[dict[str, Any]]:
    """*hits* with each passage that contradicts the kernel moved below the rest.

    Order is otherwise kept. *record* is off when ordering again after later
    re-ranking (FAQ promotion), so a conflict is queued once per turn.
    A moved passage is annotated ``kernel_precedence``
    (status, key, kernel and stated values, edition). Never raises: on any error
    *hits* comes back unchanged.
    """
    try:
        _conflicts, by_hit = _check(graph_hit, hits, record=record)
        if not by_hit:
            return hits
        agree, disagree = [], []
        for hit in hits:
            conflict = by_hit.get(id(hit))
            if conflict is None:
                agree.append(hit)
                continue
            hit["kernel_precedence"] = {"status": conflict.status, "key": conflict.key,
                                        "kernel_value": conflict.kernel_value,
                                        "stated_value": conflict.evidence_value,
                                        "edition": conflict.evidence_edition, "fiscal_year": conflict.fiscal_year}
            disagree.append(hit)
        return agree + disagree
    except Exception:
        logger.warning("kernel: precedence ordering skipped", exc_info=True)
        return hits


def record_conflicts(conflicts: list[Conflict], *, kernel_version: str) -> int:
    """Send conflicts to the discrepancy review queue. Returns how many were recorded.

    Never raises: a review-queue write must not take down the turn.
    """
    recorded = 0
    for conflict in conflicts:
        try:
            from ..database import create_or_increment_discrepancy

            create_or_increment_discrepancy(
                bot_statement=f"{conflict.evidence_source}: {conflict.excerpt}"[:900],
                user_correction=(
                    f"Kernel {kernel_version}: {conflict.display_name} = {conflict.kernel_value}"
                    f" ({conflict.provision or 'no provision recorded'})"
                ),
                discrepancy_type="evidence_conflict",
                cited_sources=[conflict.evidence_source] if conflict.evidence_source else [],
            )
            recorded += 1
        except Exception:
            logger.warning("kernel: could not record an evidence conflict for %s", conflict.key, exc_info=True)
    return recorded


def reorder_after_promotion(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order *hits* by precedence again once FAQ promotion has run (CodeRabbit, PR 706).

    Priority and exact-match FAQ rows are put in front after the graph is fused,
    so a FAQ contradicting the kernel could lead again and feed the extractive
    fallback. The graph hit is found in *hits*; without one this is a no-op.
    """
    graph_hit = next((h for h in hits if str(h.get("doc_type") or "") == "graph"), None)
    if graph_hit is None:
        return hits
    return apply_precedence(graph_hit, hits, record=False)
