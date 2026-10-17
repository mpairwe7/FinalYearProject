"""Deterministic, network-free scorer for complex tax scenarios.

Implements the scoring specification in
``docs/plans/sovereign-opus-level-accuracy-plan.md`` (§3.A.4 & §12.4):

- ``route_ok``: expected route (``calculator`` / ``workflow`` / ``rag`` /
  ``declined``) compared against ``response["retrieval_mode"]`` or
  ``response["agent_role"]``.
- ``figures``: dynamic tool-backed figure evaluation bound to the specific
  claim sentence/clause (via ``claim.label_cues``), rejecting negated matches,
  unrelated-sentence matches, and swapped/conflicting claim figures.
- ``facts``: recall over ``must_include`` groups (``{"any": [...]}``).
- ``forbidden``: ``must_not_include`` checks; any ``severity: "critical"`` hit
  zeroes the turn score.
- ``premise``: when ``expect.premise_flag`` is true, checks ``premise_cues``.
- ``complete``: checks ``debug.finish_reason`` (when exposed) and verifies the
  reply does not end mid-word or without terminal punctuation.
- ``sources_relevant``: for ``rag`` routes, checks that cited sources are non-empty
  and do not cite unrelated sector guides unless the scenario is about that sector;
  ``None`` (weight redistributed) for ``calculator`` / ``workflow`` / ``declined``.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

ToolRunner = Callable[[str, dict[str, Any]], dict[str, Any]]

# Mapping from scenario expected route -> backend retrieval_mode / agent_role values.
ROUTE_MODE_MAP: dict[str, set[str]] = {
    "calculator": {"calculator"},
    "workflow": {"workflow"},
    "rag": {
        "hybrid",
        "dense",
        "bm25",
        "sparse",
        "lexical",
        "rag",
        "faq_priority",
        "education",
        "tin_registration_guidance",
        "prn_generation",
        "tax_refund",
        "installment_agreement",
        "customs_provisional_release",
        "mining_taxation",
        "transit_emergency",
        "wht_clarification",
        "document_audit",
        "real_estate_advisory",
        "vehicle_search_tracking",
        "graph_hybrid",
        "graph_dense",
        "graph_bm25",
    },
    "declined": {
        "out_of_jurisdiction",
        "out_of_scope",
        "abstained",
        "blocked",
        "guardrail_blocked",
        "false_premise_rejected",
    },
}

SECTOR_GUIDE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "agriculture",
        re.compile(
            r"GUIDE-TO-TAXATION-OF-(?:THE-)?AGRICULTURAL-SECTOR|ura_agriculture_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "hotel",
        re.compile(
            r"Hotel-and-Accomodation|ura_hotel_and_hospitality_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "manufacturing",
        re.compile(
            r"GUIDE-TO-TAXATION-OF-THE-MANUFACTURING-SECTOR|ura_manufacturing_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "wholesale_retail",
        re.compile(
            r"Wholesale-And-Retail-Sector|ura_wholesale_and_retail_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "construction",
        re.compile(
            r"CONSTRUCTION-SECTOR|ura_construction_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "education",
        re.compile(
            r"TAXATION-OF-THE-EDUCATION-SECTOR|ura_education_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "entertainment",
        re.compile(
            r"TAXATION-OF-THE-ENTERTAINMENT-SECTOR|ura_entertainment_events_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "real_estate",
        re.compile(
            r"TAXATION-OF-THE-REAL-ESTATE-SECTOR|ura_real_estate_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "ngo",
        re.compile(
            r"TAX-OBLIGATIONS-FOR-NGOs|ura_tax_obligations_ngos",
            re.IGNORECASE,
        ),
    ),
    (
        "religious",
        re.compile(
            r"Religious-Institution|Taxation of Religious Institutions|ura_religious_institution",
            re.IGNORECASE,
        ),
    ),
    (
        "mining",
        re.compile(
            r"ura_mining_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "petroleum",
        re.compile(
            r"Petroleum Sector Q and A|ura_oil_and_gas_petroleum",
            re.IGNORECASE,
        ),
    ),
    (
        "health",
        re.compile(
            r"Overview of the Health and Medical Sector|Hospitals, Medical Centers|ura_health_and_medical_sector",
            re.IGNORECASE,
        ),
    ),
    (
        "fishing",
        re.compile(
            r"Overview of the Fishing Sector|Fish processing|Fisherman and fishmonger|ura_fishing_sector",
            re.IGNORECASE,
        ),
    ),
)

SECTOR_KEYWORDS: dict[str, tuple[str, ...]] = {
    "agriculture": ("agricultur", "farm", "crop", "livestock", "ranch", "poultry"),
    "hotel": ("hotel", "hospitality", "accommodat", "lodge", "touris", "restaurant"),
    "manufacturing": ("manufactur", "factory", "agro-processing", "industrial plant"),
    "wholesale_retail": ("wholesal", "retail", "supermarket", "shopkeeper"),
    "construction": ("construct", "civil works", "building contractor"),
    "education": ("school", "university", "education institution", "college", "tuition"),
    "entertainment": ("entertainment", "concert", "promoter", "performer", "entertainer"),
    "real_estate": ("real estate", "land dealer", "property developer"),
    "ngo": ("ngo", "non-governmental", "charity", "humanitarian", "donor"),
    "religious": ("church", "mosque", "religious institution", "diocese"),
    "mining": ("mining", "quarry", "mineral", "exploration licence"),
    "petroleum": ("petroleum", "oil and gas", "upstream", "midstream"),
    "health": ("hospital", "clinic", "medical", "pharmacy", "health sector"),
    "fishing": ("fish", "fisherman", "fishmonger", "aquaculture"),
}

_FX_PLACEHOLDER_RE = re.compile(r"^\$\{FX:([A-Z]{3}):([0-9]+(?:\.[0-9]+)?)\}$")

_NEGATION_PREFIX_RE = re.compile(
    r"\b(?:"
    r"not|never|neither|nor|"
    r"isn['’]?t|aren['’]?t|wasn['’]?t|weren['’]?t|doesn['’]?t|don['’]?t|didn['’]?t|cannot|can['’]?t|"
    r"instead\s+of|rather\s+than|other\s+than|as\s+opposed\s+to|"
    r"wrongly|incorrectly|mistakenly|false(?:ly)?"
    r")\b",
    re.IGNORECASE,
)

# Matches currency/number expressions, e.g. "UGX 7,725,500", "7.73 million", "18%", "335,000"
_AMOUNT_TOKEN_RE = re.compile(
    r"(?P<prefix>\b(?:UGX|UShs?|Shs\.?|USD|EUR|GBP|KES)\s*|\$)?"
    r"(?P<num>"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?"
    r")"
    r"(?:\s*(?P<mult>billion|bn|million|mn|m|thousand|k)\b)?"
    r"(?:\s*(?P<pct>%|\bpercent\b|\bper\s+cent\b))?"
    r"(?:\s*(?P<suffix>\b(?:UGX|UShs?|Shs\.?|shillings?|days?|months?|years?)\b|/=-?))?",
    re.IGNORECASE,
)

_MULTIPLIERS: dict[str, float] = {
    "billion": 1_000_000_000.0,
    "bn": 1_000_000_000.0,
    "million": 1_000_000.0,
    "mn": 1_000_000.0,
    "m": 1_000_000.0,
    "thousand": 1_000.0,
    "k": 1_000.0,
}

_TERMINAL_PUNCT_RE = re.compile(r'[.!?)\]"’\'_*`~>]\s*$')
_DANGLING_END_RE = re.compile(
    r"(?:[,:;—–\-]|\b(?:and|or|to|of|in|for|with|the|a|an|is|are|at|by|from|under|that|which|as))\s*$",
    re.IGNORECASE,
)


@dataclass
class ParsedAmount:
    """A numeric or monetary expression parsed from a reply clause."""

    value: float
    raw: str
    start: int
    end: int
    is_percent: bool
    is_money: bool
    negated: bool
    rounded: bool = False


@dataclass
class FigureScore:
    """Evaluation outcome for a single tool-backed figure specification."""

    tool: str
    field: str
    status: str  # "hit" | "miss" | "tool_missing" | "fx_unavailable"
    expected: float | None = None
    matched_value: float | None = None
    required: bool = True
    reason: str = ""


@dataclass
class TurnScore:
    """Deterministic component and composite scores for a single conversation turn."""

    score: float
    route_ok: bool | None
    expected_route: str | None
    actual_route: str | None
    figures: float | None
    figure_details: list[FigureScore] = field(default_factory=list)
    facts: float | None = None
    fact_details: list[dict[str, Any]] = field(default_factory=list)
    forbidden_hits: list[dict[str, Any]] = field(default_factory=list)
    critical_fail: bool = False
    premise: float | None = None
    premise_ok: bool | None = None
    complete: bool = True
    truncation_reason: str | None = None
    sources_relevant: float | None = None
    sector_guide_hits: list[str] = field(default_factory=list)
    latency_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize the turn score dataclass into a plain dictionary."""
        return asdict(self)


def default_tool_runner(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run a registered calculator tool in-process against the current rate table."""
    from app.tools import ToolRegistry
    import app.tools.calculators  # noqa: F401 — ensure calculators are registered

    tool = ToolRegistry.get(tool_name)
    if tool is None:
        return {"ok": False, "error": f"Tool '{tool_name}' is not registered", "tool_missing": True}
    return ToolRegistry.call(tool_name, params)


def _lookup_field(payload: dict[str, Any], field_path: str) -> Any:
    """Resolve a dotted field path inside a calculator tool output dictionary."""
    cur: Any = payload
    for part in field_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _split_into_claim_clauses(text: str) -> list[str]:
    """Split reply text into sentences, table rows, and coordinate sub-clauses."""
    if not text:
        return []
    lines: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        # Split coordinate clauses where distinct claims often appear side by side
        sents = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9*(\-\[])", stripped)
        for sent in sents:
            subclauses = re.split(
                r"\s*(?:;|\||—|–|\b(?:whereas|while|compared\s+to|versus|vs\.?)\b"
                r"|,\s+(?:and|but|while|whereas)\s+"
                r"|(?<=\d),\s+(?=[A-Za-z]))\s*",
                sent,
                flags=re.IGNORECASE,
            )
            for sc in subclauses:
                if sc.strip():
                    lines.append(sc.strip())
    return lines


def parse_amounts_from_clause(clause: str) -> list[ParsedAmount]:
    """Extract numeric/money/percentage amounts from a clause with negation and rounding flags."""
    results: list[ParsedAmount] = []
    for m in _AMOUNT_TOKEN_RE.finditer(clause):
        raw_num = m.group("num").replace(",", "")
        try:
            val = float(raw_num)
        except ValueError:
            continue
        mult_key = (m.group("mult") or "").lower()
        is_rounded = mult_key in _MULTIPLIERS
        if is_rounded:
            val *= _MULTIPLIERS[mult_key]

        is_pct = bool(m.group("pct"))
        has_prefix = bool(m.group("prefix"))
        suffix = (m.group("suffix") or "").lower()
        is_money = (
            has_prefix
            or suffix in ("ugx", "ush", "ushs", "shs", "shs.", "shilling", "shillings", "/=", "/=-")
            or is_rounded
            or ("," in m.group("num") and val >= 1000)
        )

        # Ignore bare 4-digit years like "2025", "2026", "2027" when preceded by FY/year/Act
        before_window = clause[max(0, m.start() - 40) : m.start()]
        if (
            not is_money
            and not is_pct
            and 1990 <= val <= 2035
            and re.search(r"(?:FY\s*|Act\s*,?\s*|in\s+|year\s+|-\s*|\b20\d{2}\s*[-/]\s*)$", before_window, re.I)
        ):
            continue
        # Ignore bare 2-digit fiscal year tails in "FY2026-27" or "2025/26"
        if not is_money and not is_pct and 18 <= val <= 35 and re.search(r"20\d{2}\s*[-/]\s*$", before_window):
            continue
        # Ignore section/schedule references like "s.20", "Section 119", "Schedule 4", "Cap 338"
        if not is_money and not is_pct and re.search(
            r"\b(?:s\.|sec(?:tion)?|schedule|part|cap\.?|act|item|para(?:graph)?|method)\s*$",
            before_window,
            re.IGNORECASE,
        ):
            continue

        # Check negation in the immediate preceding window within the same sub-clause
        neg_window = clause[max(0, m.start() - 45) : m.start()]
        last_punct = max(neg_window.rfind(","), neg_window.rfind(":"), neg_window.rfind("("))
        if last_punct != -1:
            neg_window = neg_window[last_punct + 1 :]
        negated = bool(_NEGATION_PREFIX_RE.search(neg_window))

        results.append(
            ParsedAmount(
                value=val,
                raw=m.group(0).strip(),
                start=m.start(),
                end=m.end(),
                is_percent=is_pct,
                is_money=is_money,
                negated=negated,
                rounded=is_rounded,
            )
        )
    return results


def _values_match(expected: float, candidate: ParsedAmount) -> bool:
    """Compare expected numeric value against a parsed candidate (±0.5% when rounded, ±1 UGX when exact)."""
    if 0 < abs(expected) < 1.0:
        cand_val = candidate.value / 100.0 if candidate.is_percent and candidate.value >= 1.0 else candidate.value
        tol = max(abs(expected) * 0.005, 1e-4)
        return abs(cand_val - expected) <= tol

    if candidate.is_percent and 1.0 <= abs(expected) <= 100.0:
        tol = max(abs(expected) * 0.005, 0.05)
        return abs(candidate.value - expected) <= tol

    if candidate.is_percent and abs(expected) > 100.0:
        return False

    tol = max(abs(expected) * 0.005, 1.0) if candidate.rounded else 1.0
    return abs(candidate.value - expected) <= tol


def _is_working_or_subsidiary_amount(
    clause: str,
    amt: ParsedAmount,
    field_name: str,
) -> bool:
    """Return True when ``amt`` is inside parenthesised working math, an arithmetic expression, or a headroom note."""
    prefix = clause[: amt.start]
    suffix = clause[amt.end :]
    # 1. Parenthesised working figure, e.g. "(180m × 30%)" or "(headroom UGX 60,000,000)"
    if prefix.count("(") > prefix.count(")") and ")" in suffix:
        return True
    # 2. Arithmetic operand adjacent to ×, ÷, or single * (excluding Markdown **)
    if re.search(r"(?:[×÷]|(?<!\*)\*(?!\*))\s*$", prefix) or re.match(
        r"^\s*(?:[×÷]|(?<!\*)\*(?!\*))", suffix
    ):
        return True
    # 3. Subsidiary headroom figure when the spec is not asking for headroom
    if field_name != "headroom" and re.search(
        r"\bheadroom\b[^,;:.]{0,20}$", prefix, re.IGNORECASE
    ):
        return True
    return False


def _resolve_params(
    raw_params: dict[str, Any],
    tool_runner: ToolRunner,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve ``${FX:CUR:AMT}`` placeholders via ``convert_currency`` tool."""
    resolved = copy.deepcopy(raw_params)
    for key, val in list(resolved.items()):
        if isinstance(val, str):
            m = _FX_PLACEHOLDER_RE.match(val.strip())
            if m:
                currency, amount_str = m.group(1), m.group(2)
                fx_res = tool_runner(
                    "convert_currency",
                    {"amount": float(amount_str), "currency": currency},
                )
                if not fx_res.get("ok") or fx_res.get("ugx_amount") is None:
                    return None, "fx_unavailable"
                resolved[key] = fx_res["ugx_amount"]
    return resolved, None


def _score_single_figure(
    spec: dict[str, Any],
    reply: str,
    tool_runner: ToolRunner,
) -> FigureScore:
    """Evaluate a single figure specification against ``reply`` using ``tool_runner``."""
    tool_name = str(spec["tool"])
    field_name = str(spec.get("field", "tax"))
    required = bool(spec.get("required", True))
    raw_params = dict(spec.get("params") or {})

    resolved_params, fx_err = _resolve_params(raw_params, tool_runner)
    if fx_err == "fx_unavailable":
        return FigureScore(
            tool=tool_name,
            field=field_name,
            status="fx_unavailable",
            required=required,
            reason="FX conversion tool unavailable or returned no rate",
        )

    assert resolved_params is not None
    tool_out = tool_runner(tool_name, resolved_params)
    if tool_out.get("tool_missing") or not tool_out.get("ok"):
        return FigureScore(
            tool=tool_name,
            field=field_name,
            status="tool_missing",
            required=required,
            reason=str(tool_out.get("error") or "tool_missing"),
        )

    raw_expected = _lookup_field(tool_out, field_name)
    if raw_expected is None or not isinstance(raw_expected, (int, float)):
        return FigureScore(
            tool=tool_name,
            field=field_name,
            status="tool_missing",
            required=required,
            reason=f"Field '{field_name}' missing or non-numeric in tool output",
        )
    expected_val = float(raw_expected)

    input_values: list[float] = [
        float(v)
        for v in resolved_params.values()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]

    cues_obj = (spec.get("claim") or {}).get("label_cues") or {}
    cues: list[str] = list(cues_obj.get("any") or [])
    if not cues:
        cues = [field_name.replace("_", " ")]

    clauses = _split_into_claim_clauses(reply)
    matching_clauses: list[tuple[str, int]] = []
    for clause in clauses:
        cue_positions: list[int] = []
        for c in cues:
            m = re.search(rf"\b{re.escape(c)}\b", clause, re.IGNORECASE)
            if m:
                cue_positions.append(m.start())
        if cue_positions:
            matching_clauses.append((clause, min(cue_positions)))

    if not matching_clauses:
        return FigureScore(
            tool=tool_name,
            field=field_name,
            status="miss",
            expected=expected_val,
            required=required,
            reason="No sentence/clause matched claim label_cues",
        )

    expect_is_rate = 0 < abs(expected_val) < 1.0
    expect_is_money = abs(expected_val) >= 1000.0

    saw_negated_match = False
    saw_conflicting = False
    matched_val: float | None = None

    for clause, cue_pos in matching_clauses:
        amounts = parse_amounts_from_clause(clause)
        if not amounts:
            continue

        direct_assertions: list[ParsedAmount] = []
        for amt in amounts:
            is_input_echo = any(
                _values_match(inp, amt) and not _values_match(expected_val, amt)
                for inp in input_values
            )
            if is_input_echo:
                continue
            if _is_working_or_subsidiary_amount(clause, amt, field_name):
                continue
            if expect_is_rate and not amt.is_percent and amt.value >= 1.0:
                continue
            if expect_is_money and (amt.is_percent or amt.value < 100.0):
                continue
            direct_assertions.append(amt)

        for amt in direct_assertions:
            if _values_match(expected_val, amt):
                if amt.negated:
                    saw_negated_match = True
                else:
                    matched_val = amt.value
            else:
                if not amt.negated:
                    after_cue = [a for a in direct_assertions if a.start >= cue_pos and not a.negated]
                    if amt.start < cue_pos and any(_values_match(expected_val, a) for a in after_cue):
                        continue
                    saw_conflicting = True

    if matched_val is not None and not saw_conflicting:
        return FigureScore(
            tool=tool_name,
            field=field_name,
            status="hit",
            expected=expected_val,
            matched_value=matched_val,
            required=required,
            reason="Matched asserted figure in claim clause",
        )

    if saw_conflicting:
        reason = "Conflicting amount asserted in claim clause"
    elif saw_negated_match:
        reason = "Expected figure appeared only in a negated/rejected context"
    else:
        reason = "No matching amount asserted in claim clause"

    return FigureScore(
        tool=tool_name,
        field=field_name,
        status="miss",
        expected=expected_val,
        matched_value=matched_val,
        required=required,
        reason=reason,
    )


def _check_completeness(reply: str, response: dict[str, Any]) -> tuple[bool, str | None]:
    """Check whether a reply is complete and not truncated mid-sentence or by token limit."""
    debug = response.get("debug") or {}
    finish_reason = debug.get("finish_reason") or response.get("finish_reason")
    if finish_reason == "length":
        return False, "finish_reason=length"

    stripped = (reply or "").strip()
    if not stripped:
        return False, "empty_reply"

    if _DANGLING_END_RE.search(stripped):
        return False, "dangling_ending"

    if not _TERMINAL_PUNCT_RE.search(stripped):
        return False, "missing_terminal_punctuation"

    return True, None


def _score_sources_relevant(
    expect_route: str | None,
    response: dict[str, Any],
    context_text: str,
) -> tuple[float | None, list[str]]:
    """Compute share of cited sources that do not cite unrelated sector guides."""
    if expect_route in ("calculator", "workflow", "declined"):
        return None, []

    raw_sources = response.get("sources") or response.get("citations") or []
    if not raw_sources:
        return 0.0, []

    lower_ctx = context_text.lower()
    allowed_sectors = {
        sector
        for sector, kw_list in SECTOR_KEYWORDS.items()
        if any(kw in lower_ctx for kw in kw_list)
    }

    offending: list[str] = []
    relevant_count = 0
    for src in raw_sources:
        if isinstance(src, dict):
            label = " ".join(
                str(src.get(k) or "")
                for k in ("source", "title", "file", "filename", "document", "id", "category")
            ).strip()
        else:
            label = str(src)

        matched_disallowed = False
        for sector, pattern in SECTOR_GUIDE_PATTERNS:
            if sector not in allowed_sectors and pattern.search(label):
                offending.append(label)
                matched_disallowed = True
                break
        if not matched_disallowed:
            relevant_count += 1

    return relevant_count / len(raw_sources), offending


def score_turn(
    expect: dict[str, Any],
    response: dict[str, Any],
    tool_runner: ToolRunner | None = None,
    *,
    scenario_context: str = "",
) -> TurnScore:
    """Deterministically score a single turn response against ``expect``."""
    runner = tool_runner or default_tool_runner
    reply = str(response.get("reply") or response.get("answer") or response.get("message") or "")
    actual_mode = str(response.get("retrieval_mode") or response.get("agent_role") or "").strip()
    latency_s = float(response.get("latency_s") or response.get("seconds") or 0.0)

    # 1. Route check
    expected_route = expect.get("route")
    route_ok: bool | None = None
    if expected_route:
        allowed_modes = ROUTE_MODE_MAP.get(expected_route, {expected_route})
        route_ok = actual_mode in allowed_modes

    # 2. Figures check
    figure_specs = list(expect.get("figures") or [])
    figure_details: list[FigureScore] = []
    for spec in figure_specs:
        figure_details.append(_score_single_figure(spec, reply, runner))

    active_figs = [
        f
        for f in figure_details
        if not (f.status == "fx_unavailable" and not f.required)
    ]
    figures_score: float | None = None
    if active_figs:
        hits = sum(1 for f in active_figs if f.status == "hit")
        figures_score = hits / len(active_figs)

    # 3. Facts (must_include)
    must_include = list(expect.get("must_include") or [])
    fact_details: list[dict[str, Any]] = []
    facts_score: float | None = None
    lower_reply = reply.lower()
    if must_include:
        fact_hits = 0
        for group in must_include:
            alternatives = group.get("any") if isinstance(group, dict) else [str(group)]
            matched_alt = next(
                (alt for alt in (alternatives or []) if str(alt).lower() in lower_reply),
                None,
            )
            ok = matched_alt is not None
            if ok:
                fact_hits += 1
            fact_details.append({"any": alternatives, "ok": ok, "matched": matched_alt})
        facts_score = fact_hits / len(must_include)

    # 4. Forbidden (must_not_include)
    must_not_include = list(expect.get("must_not_include") or [])
    forbidden_hits: list[dict[str, Any]] = []
    critical_fail = False
    for item in must_not_include:
        if isinstance(item, dict):
            phrase = str(item.get("text") or "")
            severity = str(item.get("severity") or "critical")
        else:
            phrase = str(item)
            severity = "critical"
        if phrase and phrase.lower() in lower_reply:
            forbidden_hits.append({"text": phrase, "severity": severity})
            if severity == "critical":
                critical_fail = True

    # 5. Premise challenge check
    premise_flag = bool(expect.get("premise_flag", False))
    premise_score: float | None = None
    premise_ok: bool | None = None
    if premise_flag:
        premise_cues = list(expect.get("premise_cues") or [])
        if premise_cues:
            cue_hits = 0
            for group in premise_cues:
                alts = group.get("any") if isinstance(group, dict) else [str(group)]
                if any(str(a).lower() in lower_reply for a in (alts or [])):
                    cue_hits += 1
            premise_ok = cue_hits == len(premise_cues)
            premise_score = cue_hits / len(premise_cues)
        else:
            premise_ok = True
            premise_score = 1.0

    # 6. Completeness / truncation check
    complete, trunc_reason = _check_completeness(reply, response)

    # 7. Sources relevance
    sources_relevant, sector_guide_hits = _score_sources_relevant(
        expected_route,
        response,
        scenario_context,
    )

    # Weighted combination with proportional redistribution for None components
    # Turn score = 0.35*figures + 0.30*facts + 0.15*premise + 0.10*route_ok + 0.10*sources_relevant
    components: list[tuple[float, float | None]] = [
        (0.35, figures_score),
        (0.30, facts_score),
        (0.15, premise_score),
        (0.10, (1.0 if route_ok else 0.0) if route_ok is not None else None),
        (0.10, sources_relevant),
    ]
    active_components = [(w, val) for w, val in components if val is not None]
    if active_components:
        total_w = sum(w for w, _ in active_components)
        raw_score = sum(w * float(val) for w, val in active_components) / total_w
    else:
        raw_score = 1.0

    if critical_fail:
        final_score = 0.0
    elif not complete:
        final_score = raw_score * 0.5
    else:
        final_score = raw_score

    return TurnScore(
        score=round(final_score, 6),
        route_ok=route_ok,
        expected_route=expected_route,
        actual_route=actual_mode,
        figures=round(figures_score, 6) if figures_score is not None else None,
        figure_details=figure_details,
        facts=round(facts_score, 6) if facts_score is not None else None,
        fact_details=fact_details,
        forbidden_hits=forbidden_hits,
        critical_fail=critical_fail,
        premise=round(premise_score, 6) if premise_score is not None else None,
        premise_ok=premise_ok,
        complete=complete,
        truncation_reason=trunc_reason,
        sources_relevant=round(sources_relevant, 6) if sources_relevant is not None else None,
        sector_guide_hits=sector_guide_hits,
        latency_s=latency_s,
    )


def score_scenario(
    scenario: dict[str, Any],
    turn_responses: list[dict[str, Any]],
    tool_runner: ToolRunner | None = None,
) -> dict[str, Any]:
    """Score all turns of a scenario and return the aggregate scenario score."""
    turns = scenario.get("turns") or []
    ctx_parts = [
        str(scenario.get("description") or ""),
        str(scenario.get("tax_head") or ""),
        str(scenario.get("sector") or ""),
    ]
    for t in turns:
        ctx_parts.append(str(t.get("message") or ""))
    scenario_context = " ".join(ctx_parts)

    turn_scores: list[TurnScore] = []
    for idx, turn in enumerate(turns):
        resp = turn_responses[idx] if idx < len(turn_responses) else {}
        ts = score_turn(
            turn.get("expect") or {},
            resp,
            tool_runner=tool_runner,
            scenario_context=scenario_context,
        )
        turn_scores.append(ts)

    mean_score = (
        sum(ts.score for ts in turn_scores) / len(turn_scores)
        if turn_scores
        else 0.0
    )
    return {
        "id": scenario.get("id"),
        "tax_head": scenario.get("tax_head"),
        "difficulty": scenario.get("difficulty"),
        "score": round(mean_score, 6),
        "score_100": round(mean_score * 100.0, 2),
        "critical_fail": any(ts.critical_fail for ts in turn_scores),
        "truncated_turns": sum(1 for ts in turn_scores if not ts.complete),
        "turns": [ts.to_dict() for ts in turn_scores],
    }
