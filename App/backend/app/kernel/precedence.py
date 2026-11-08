"""Which source wins, and noticing when sources disagree (ADR-0033).

Facts resolve in a fixed order: a computed result, then a kernel fact, then an
official document, then a curated FAQ, then the live web. A lower source may
explain a higher one but never overrules it. When a lower source states a
different figure for something the kernel holds, the kernel's figure stands
and the disagreement goes to the human review queue, so a stale passage gets
corrected at its source rather than argued with at every turn.

Detection is deliberately narrow, to keep false alarms rare: a passage must
name the figure (every content word of its display name, within one window)
next to a different percentage or UGX amount.
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
_STOP = frozenset({"the", "of", "on", "for", "and", "a", "an", "to", "in", "tax", "rate", "standard", "ugx"})
_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d{1,3})?)\s?(?:%|per\s?cent(?:um)?)", re.I)
_UGX_RE = re.compile(r"(?:UGX|Shs\.?|USh)\s?([\d,]{4,}(?:\.\d+)?)", re.I)


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

    def to_dict(self) -> dict[str, str]:
        return dict(self.__dict__)


def _terms(display_name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", display_name.lower()) if w not in _STOP and len(w) > 2]


def _kernel_numbers(value: Any, unit: str) -> tuple[set[float], set[float]]:
    """``(percents, amounts)`` the kernel value states."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return set(), set()
    if unit == "ratio":
        return {round(float(value) * 100, 3)}, set()
    if unit == "UGX":
        return set(), {float(value)}
    return set(), set()


def find_conflicts(facts: list[dict[str, Any]], passages: list[dict[str, Any]]) -> list[Conflict]:
    """Kernel facts that a lower-ranked passage contradicts.

    *facts* are kernel claims as dicts with ``key``, ``display_name``,
    ``value``, ``unit`` and ``provision``; *passages* are retrieval hits.
    """
    found: list[Conflict] = []
    for fact in facts:
        terms = _terms(str(fact.get("display_name") or ""))
        percents, amounts = _kernel_numbers(fact.get("value"), str(fact.get("unit") or ""))
        if not terms or not (percents or amounts):
            continue
        for hit in passages:
            klass = source_class(hit)
            if SOURCE_RANK.get(klass, 99) <= SOURCE_RANK["kernel"]:
                continue
            text = str(hit.get("text") or hit.get("answer") or "")
            low = text.lower()
            for match in list(_PERCENT_RE.finditer(text)) + list(_UGX_RE.finditer(text)):
                window = low[max(0, match.start() - _WINDOW):match.end() + _WINDOW]
                if not all(term in window for term in terms):
                    continue
                is_percent = match.re is _PERCENT_RE
                try:
                    number = float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
                expected = percents if is_percent else amounts
                if not expected or any(abs(number - e) < 1e-6 for e in expected):
                    continue
                found.append(Conflict(
                    key=str(fact.get("key") or ""),
                    display_name=str(fact.get("display_name") or ""),
                    kernel_value=f"{sorted(expected)[0]:g}{'%' if is_percent else ' UGX'}",
                    evidence_value=f"{number:g}{'%' if is_percent else ' UGX'}",
                    evidence_source=str(hit.get("source") or hit.get("url") or ""),
                    evidence_class=klass,
                    excerpt=" ".join(text[max(0, match.start() - 80):match.end() + 80].split()),
                    provision=str(fact.get("provision") or ""),
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
        })
    return out


def check_fused(graph_hit: dict[str, Any], hits: list[dict[str, Any]]) -> list[Conflict]:
    """Compare a fused graph hit's facts with the other hits; record and return conflicts.

    Never raises: a precedence check must not take down retrieval.
    """
    try:
        facts = facts_from_claims(list(graph_hit.get("graph_claims") or []))
        conflicts = find_conflicts(facts, [h for h in hits if h is not graph_hit])
        if conflicts:
            from .pin import active_pin

            pin = active_pin() or {}
            graph_hit["kernel_conflicts"] = [c.to_dict() for c in conflicts]
            logger.warning("kernel: %d evidence conflict(s) with the kernel: %s",
                           len(conflicts), ", ".join(sorted({c.key for c in conflicts})))
            record_conflicts(conflicts, kernel_version=str(pin.get("kernel_version") or ""))
        return conflicts
    except Exception:
        logger.warning("kernel: precedence check skipped", exc_info=True)
        return []


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
