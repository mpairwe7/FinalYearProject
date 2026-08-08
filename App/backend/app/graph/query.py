"""Traversals that answer the questions flat retrieval gets wrong.

Every function here returns **structured claims with provenance**, not
prose. That is the point of routing a question through the graph rather
than through a passage: the answer arrives already atomized and already
attributed, which is far better input for ``claim_verifier`` and
``entailment`` than a paragraph.

A claim that cannot name the provision it came from is not emitted. An
``unverified`` figure keeps that mark all the way to the caller, because
"the system is not certain of this one" is itself part of the answer.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .schema import (
    EdgeKind,
    Node,
    NodeKind,
    class_id,
    parse_fiscal_year_start,
    tax_type_id,
)
from .store import InMemoryGraphStore

logger = logging.getLogger(__name__)


@dataclass
class Claim:
    """One resolved fact plus why it is true."""

    subject: str
    predicate: str
    value: Any
    fiscal_year: str = ""
    provision: str = ""
    taxpayer_class: str = ""
    unverified: bool = False
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        out = {
            "subject": self.subject,
            "predicate": self.predicate,
            "value": self.value,
        }
        for name in ("fiscal_year", "provision", "taxpayer_class", "note"):
            value = getattr(self, name)
            if value:
                out[name] = value
        if self.unverified:
            out["unverified"] = True
        return out


@dataclass
class GraphAnswer:
    """A set of claims, the path that produced them, and its shape."""

    claims: list[Claim] = field(default_factory=list)
    hops: list[str] = field(default_factory=list)
    matched: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": self.matched,
            "claims": [c.to_dict() for c in self.claims],
            "hops": self.hops,
            "reason": self.reason,
            "unverified": any(c.unverified for c in self.claims),
        }


# ---------------------------------------------------------------------------
# Entity linking
# ---------------------------------------------------------------------------
# Deterministic and lexical. A dense entity linker would be better at
# paraphrase, but this runs on the request path and the vocabulary is
# small and closed — there are eight taxes and four taxpayer classes,
# not an open ontology.

_TAX_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("paye", re.compile(r"\b(paye|take[- ]?home|net\s+pay|salary\s+tax|employment\s+income)\b", re.I)),
    ("vat", re.compile(r"\b(vat|v\.a\.t|value\s+added)\b", re.I)),
    ("rental", re.compile(r"\b(rent|rental|letting|landlord|tenant)\b", re.I)),
    ("corporation_tax", re.compile(r"\b(corporation\s+tax|corporate\s+tax|company\s+tax)\b", re.I)),
    ("capital_gains", re.compile(r"\b(capital\s+gains?|cgt|sold|disposal)\b", re.I)),
    # Taxpayers use the *verb*: "what do I withhold", "what is deducted
    # when I pay X". Requiring the noun "withholding" missed both, and
    # a question that names no tax gets no graph answer at all — so the
    # narrow cue was not a precision trade, it was a silent miss.
    (
        "withholding",
        re.compile(r"\b(withholding|withhold|wht|deduct(?:ed|s|ing)?)\b", re.I),
    ),
    ("customs", re.compile(r"\b(customs|import(?:ing)?|duty|cif|tariff|landed\s+cost)\b", re.I)),
    ("nssf", re.compile(r"\bnssf\b", re.I)),
)

_CLASS_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Non-resident first: "non-resident" contains "resident", and
    # matching the wrong one reproduces the exact defect the graph is
    # meant to fix.
    ("non_resident", re.compile(r"\bnon[-\s]?resident\b", re.I)),
    ("resident", re.compile(r"\bresident\b", re.I)),
    ("company", re.compile(r"\b(company|corporate|business|ltd|limited)\b", re.I)),
    ("individual", re.compile(r"\b(individual|private|personal|i\s+am\s+a|as\s+an?\s+individual)\b", re.I)),
)

#: Payment-type cues that narrow a tax to one of its several rates.
#:
#: Without these, "what withholding do I deduct on a management fee"
#: returned all nine withholding rates. A model handed nine figures for
#: a one-figure question will pick one, and the odds are against it —
#: which is the same class of failure as flat retrieval, reproduced
#: inside the graph. Most specific first.
_SUBTYPE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("withholding_management_fees", re.compile(r"\bmanagement\s+fees?\b", re.I)),
    ("withholding_dividend", re.compile(r"\bdividends?\b", re.I)),
    ("withholding_royalty", re.compile(r"\broyalt(?:y|ies)\b", re.I)),
    ("withholding_betting_winnings", re.compile(r"\b(betting|winnings?|gaming)\b", re.I)),
    ("withholding_telecom_commission", re.compile(r"\b(telecom|mobile[-\s]?money)\s+commission\b", re.I)),
    ("withholding_public_entertainer", re.compile(r"\b(entertainer|performer|artiste)\b", re.I)),
    ("withholding_foreign_interest", re.compile(r"\b(debenture|foreign\s+interest)\b", re.I)),
    ("withholding_goods", re.compile(r"\b(goods|supplies|supply\s+of\s+goods)\b", re.I)),
    ("withholding_services", re.compile(r"\b(services?|consultan\w+|contractor)\b", re.I)),
    ("environmental_levy_used_clothing", re.compile(r"\b(used|second[-\s]?hand|worn)\s+cloth\w*\b", re.I)),
)

#: Questions about *change over time* want the supersession chain, not
#: the current figure. "Has the threshold changed, and from when?"
#: cannot be answered by one year's value however correct that value is.
#:
#: Bare ``change`` is excluded. "Does the rental tax I paid change it?"
#: uses the verb in the present tense about an *amount*, not about a
#: rate's history, and treating it as a history question answered a
#: disposal question with a list of past rates. Only the past-tense and
#: explicitly historical forms qualify.
_CHANGE_RE = re.compile(
    r"\b(changed|used\s+to\s+be|previously|history|"
    r"since\s+when|from\s+when|increased|raised|reduced)\b",
    re.I,
)

#: A comparison asks for every matched rate, not the most specific one.
#:
#: "A management fee to a consultant" is one payment matching two cues,
#: so the specific one wins. "A dividend versus a payment for goods" is
#: two payments, and answering with one of them is answering half the
#: question. The difference is stated in the query, so it is read rather
#: than guessed.
_COMPARISON_RE = re.compile(
    r"\b(versus|vs\.?|compared\s+(?:to|with)|difference\s+between|"
    r"as\s+opposed\s+to|rather\s+than)\b",
    re.I,
)

_FY_RE = re.compile(r"\bFY\s?(\d{4})[-/]?(\d{2,4})?\b", re.I)
_YEAR_RE = re.compile(r"\b(20\d{2})\b")


def link_entities(query: str) -> dict[str, Any]:
    """Map a natural-language query to graph seeds.

    Returns the tax, taxpayer class and fiscal year the question is
    about, each empty when the query does not say.
    """
    text = query or ""
    taxes = [name for name, pat in _TAX_CUES if pat.search(text)]
    classes = [name for name, pat in _CLASS_CUES if pat.search(text)]
    subtypes = [key for key, pat in _SUBTYPE_CUES if pat.search(text)]
    return {
        "taxes": taxes,
        "taxpayer_class": classes[0] if classes else "",
        "subtypes": subtypes,
        "fiscal_year": _extract_fiscal_year(text),
        "asks_about_change": bool(_CHANGE_RE.search(text)),
        "is_comparison": bool(_COMPARISON_RE.search(text)),
    }


def _extract_fiscal_year(text: str) -> str:
    """Pull an explicit fiscal year out of the query, or ``""``.

    Ugandan fiscal years run 1 July → 30 June, so a bare calendar year
    is ambiguous: "in 2026" could mean FY2025-26 or FY2026-27. This
    resolves only the unambiguous forms and leaves the rest to the
    caller's default, rather than guessing on the taxpayer's behalf.
    """
    match = _FY_RE.search(text)
    if match:
        start = match.group(1)
        end = match.group(2) or ""
        if len(end) == 4:
            end = end[2:]
        return f"FY{start}-{end}" if end else f"FY{start}"
    # "year to June 2026" / "to June 2026" names the *end* of a year.
    if re.search(r"\b(?:to|ending|ended)\s+june\s+(20\d{2})\b", text, re.I):
        year = int(re.search(r"\bjune\s+(20\d{2})\b", text, re.I).group(1))
        return f"FY{year - 1}-{str(year)[2:]}"
    return ""


# ---------------------------------------------------------------------------
# Traversals
# ---------------------------------------------------------------------------


def _rates_for(
    store: InMemoryGraphStore, tax: str, *, fiscal_year: str = ""
) -> list[Node]:
    """Every rate node attached to *tax*, newest first."""
    tax_node = store.get(tax_type_id(tax))
    if tax_node is None:
        return []
    rates = [
        node
        for _edge, node in store.neighbours(tax_node.id, (EdgeKind.RATED_FOR,), outgoing=False)
        if node.kind in (NodeKind.RATE, NodeKind.THRESHOLD)
    ]
    if fiscal_year:
        rates = [n for n in rates if n.props.get("fiscal_year") == fiscal_year]
    return sorted(
        rates,
        key=lambda n: (parse_fiscal_year_start(str(n.props.get("fiscal_year", ""))) or _dt.date.min),
        reverse=True,
    )


def _class_of(store: InMemoryGraphStore, node: Node) -> str:
    for _edge, other in store.neighbours(node.id, (EdgeKind.APPLIES_TO,)):
        return str(other.props.get("name", ""))
    return ""


def _provision_of(store: InMemoryGraphStore, node: Node) -> str:
    for _edge, other in store.neighbours(node.id, (EdgeKind.IMPOSED_BY,)):
        return other.label
    return str(node.props.get("legal_basis", ""))


def _best_subtype(subtypes: set[str], nodes: list[Node]) -> str:
    """The most specific matched subtype present among *nodes*.

    Specificity is the declaration order of :data:`_SUBTYPE_CUES` — the
    table is written most-specific first precisely so this can be a
    lookup rather than a heuristic.
    """
    if not subtypes:
        return ""
    available = {str(n.props.get("key", "")) for n in nodes}
    for key, _pat in _SUBTYPE_CUES:
        if key in subtypes and key in available:
            return key
    return ""


def _keys_for_taxes(store: InMemoryGraphStore, taxes: list[str]) -> list[str]:
    """Distinct rate keys belonging to *taxes*, for chain lookups."""
    keys: list[str] = []
    for tax in taxes:
        for node in _rates_for(store, tax):
            key = str(node.props.get("key", ""))
            if key and key not in keys:
                keys.append(key)
    return keys


def _claim(store: InMemoryGraphStore, node: Node) -> Claim:
    return Claim(
        # The human label, not the table key: a claim is read by a model
        # and quoted to a taxpayer, and neither can say
        # "environmental_levy_used_clothing" back to anyone.
        subject=str(node.props.get("display_name") or node.props.get("key") or node.label),
        predicate="threshold" if node.kind is NodeKind.THRESHOLD else "rate",
        value=node.props.get("value"),
        fiscal_year=str(node.props.get("fiscal_year", "")),
        provision=_provision_of(store, node),
        taxpayer_class=_class_of(store, node),
        unverified=node.unverified,
        note=str(node.props.get("note", "")),
    )


def resolve(
    query: str,
    *,
    store: InMemoryGraphStore | None = None,
    default_fiscal_year: str = "",
) -> GraphAnswer:
    """Answer a compositional question about rates and who they bind.

    This is the traversal the multi-hop golden set exercises: a tax, a
    taxpayer class and a fiscal year resolved together, with the
    thresholds that gate the rate and the charges it stacks on.
    """
    from . import get_graph

    store = store or get_graph()
    links = link_entities(query)
    if not links["taxes"]:
        return GraphAnswer(reason="no tax type recognised in the query")

    # A question about *change* wants the chain, not one year's figure.
    # Answering "has the threshold changed?" with the current value is
    # not a partial answer — it is the wrong one.
    if links["asks_about_change"] and not links["fiscal_year"]:
        keys = links["subtypes"] or _keys_for_taxes(store, links["taxes"])
        chains = [history(key, store=store) for key in keys[:3]]
        merged = GraphAnswer(matched=any(c.matched for c in chains))
        for chain in chains:
            merged.claims.extend(chain.claims)
            merged.hops.extend(chain.hops)
        if merged.matched:
            merged.reason = "supersession chain"
            return merged

    fiscal_year = links["fiscal_year"] or default_fiscal_year
    wanted_class = links["taxpayer_class"]
    subtypes = set(links["subtypes"])
    answer = GraphAnswer(matched=True)
    answer.hops.append(f"tax={','.join(links['taxes'])}")
    if wanted_class:
        answer.hops.append(f"class={wanted_class}")
    if fiscal_year:
        answer.hops.append(f"fiscal_year={fiscal_year}")

    for tax in links["taxes"]:
        nodes = _rates_for(store, tax, fiscal_year=fiscal_year)
        if not nodes:
            # The year asked about may predate the tables. Say so rather
            # than silently answering with a different year's figure.
            available = _rates_for(store, tax)
            if fiscal_year and available:
                answer.reason = (
                    f"no {tax} figures for {fiscal_year}; "
                    f"available: {sorted({str(n.props.get('fiscal_year')) for n in available})}"
                )
            continue

        # A named payment type settles which of a tax's several rates
        # applies. When one is named, the others are not "extra
        # context" — they are wrong answers standing next to the right
        # one, so they are dropped rather than ranked.
        #
        # Several cues can fire at once: "a management fee to a
        # consultant" matches both the management-fee rate (15%) and the
        # services rate (6%). ``_SUBTYPE_CUES`` is ordered most-specific
        # first and ``_best_subtype`` takes only the first match within
        # this tax, so the specific payment type wins over the generic
        # one instead of both being offered.
        if links["is_comparison"]:
            named = [n for n in nodes if str(n.props.get("key", "")) in subtypes]
        else:
            best = _best_subtype(subtypes, nodes)
            named = [n for n in nodes if str(n.props.get("key", "")) == best] if best else []
        if named:
            answer.hops.append(f"subtype={','.join(sorted(subtypes))}")
            chosen = named
        else:
            scoped = (
                [n for n in nodes if _class_of(store, n) == wanted_class]
                if wanted_class
                else []
            )
            # A rate with no class edge applies to everyone, so it stays
            # in scope for a class-specific question. Dropping it would
            # lose the VAT rate from a non-resident importer's question.
            general = [n for n in nodes if not _class_of(store, n)]
            chosen = (scoped + general) if wanted_class else nodes

        for node in chosen:
            answer.claims.append(_claim(store, node))
            for _edge, gate in store.neighbours(node.id, (EdgeKind.GATES,), outgoing=False):
                answer.claims.append(_claim(store, gate))
                answer.hops.append("gates")
            for _edge, base in store.neighbours(node.id, (EdgeKind.COMPUTED_ON,)):
                answer.claims.append(
                    Claim(
                        subject=str(node.props.get("key", node.label)),
                        predicate="computed_on",
                        value=str(base.props.get("key", base.label)),
                        fiscal_year=str(node.props.get("fiscal_year", "")),
                        provision=_provision_of(store, base),
                        note=(
                            "charged on a base that already includes this, "
                            "so the two cannot be computed independently and added"
                        ),
                    )
                )
                answer.hops.append("computed_on")

    if not answer.claims:
        answer.matched = False
        answer.reason = answer.reason or "no figures matched the query"
    # Deduplicate while preserving order — a tax can be reached twice.
    seen: set[tuple] = set()
    unique: list[Claim] = []
    for claim in answer.claims:
        key = (claim.subject, claim.predicate, claim.fiscal_year, str(claim.value))
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    answer.claims = unique
    return answer


def history(key: str, *, store: InMemoryGraphStore | None = None) -> GraphAnswer:
    """Walk a figure's ``SUPERSEDES`` chain, newest first.

    "Has the VAT registration threshold changed, and from when?" is one
    traversal here and two lucky retrievals otherwise.
    """
    from . import get_graph

    store = store or get_graph()
    nodes = [
        node
        for node in store.by_kind(NodeKind.RATE) + store.by_kind(NodeKind.THRESHOLD)
        if node.props.get("key") == key
    ]
    if not nodes:
        return GraphAnswer(reason=f"unknown rate key {key!r}")

    ordered = sorted(
        nodes,
        key=lambda n: (parse_fiscal_year_start(str(n.props.get("fiscal_year", ""))) or _dt.date.min),
        reverse=True,
    )
    answer = GraphAnswer(matched=True, hops=[f"key={key}", "supersedes"])
    answer.claims = [_claim(store, node) for node in ordered]
    values = {str(c.value) for c in answer.claims}
    answer.reason = (
        "unchanged across the years held" if len(values) == 1 else "figure changed between years"
    )
    return answer


def effective_on(
    key: str, day: _dt.date, *, store: InMemoryGraphStore | None = None
) -> GraphAnswer:
    """The figure in force for *key* on *day*.

    Ugandan rates change on 1 July. A taxpayer asking about a past
    period needs that period's figure, and answering with today's is a
    compliance failure the system caused.
    """
    from . import get_graph

    store = store or get_graph()
    chain = history(key, store=store)
    if not chain.matched:
        return chain

    for claim in chain.claims:
        fy_node = store.get(f"fy:{claim.fiscal_year}")
        if fy_node is None:
            continue
        start = fy_node.props.get("effective_from", "")
        end = fy_node.props.get("effective_to", "")
        try:
            from_date = _dt.date.fromisoformat(start) if start else None
            to_date = _dt.date.fromisoformat(end) if end else None
        except ValueError:
            continue
        if from_date and day < from_date:
            continue
        if to_date and day > to_date:
            continue
        return GraphAnswer(
            matched=True,
            claims=[claim],
            hops=[f"key={key}", f"on={day.isoformat()}"],
            reason=f"in force for {claim.fiscal_year}",
        )

    return GraphAnswer(
        reason=f"no figure for {key!r} covers {day.isoformat()}",
        hops=[f"key={key}"],
    )


def neighbourhood(
    seed_query: str, *, hops: int = 2, store: InMemoryGraphStore | None = None
) -> list[Node]:
    """Nodes within *hops* of whatever *seed_query* mentions.

    Used by the retrieval fusion leg, which wants a set of relevant
    provisions rather than a resolved answer.
    """
    from . import get_graph

    store = store or get_graph()
    links = link_entities(seed_query)
    seeds = [tax_type_id(t) for t in links["taxes"]]
    if links["taxpayer_class"]:
        seeds.append(class_id(links["taxpayer_class"]))
    if not seeds:
        return []
    return store.walk(seeds, hops=hops)
