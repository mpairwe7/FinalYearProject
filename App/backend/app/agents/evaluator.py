"""Evaluator-optimizer — verify an answer before a taxpayer reads it.

The system already regenerates once when faithfulness falls below a
floor. That is a single self-judged pass, and a model grading its own
output reliably catches formatting faults while missing reasoning
faults. Two asymmetries fix that.

**A different judge.**  The draft comes from the agentic tier; the
non-deterministic axes are judged from a higher one. Self-evaluation at
the same capability is close to free and close to worthless.

**Determinism wherever the question allows it.**  Whether a figure is
right is not a judgement call. If the taxpayer asked something the
calculators can answer, :func:`verify_money` re-derives the figure
through the same MCP path the agent used and compares it against the
numbers actually printed in the draft. A mismatch is a hard reject with
no model in the loop at all — no tokens, no latency, no opinion.

That ordering is the point: the cheap, certain check runs first and can
reject on its own. The expensive, uncertain one only runs on what
survives.

## What this deliberately does not do

It does not loop. :class:`RevisionBudget` permits **one** revision, on
turns that carry money or are bound for a human. An unbounded
critique-revise cycle is a cost incident with a quality story attached,
and the agent loop next door already learned that lesson —
:class:`app.agents.loop_control.ToolCallBudget` exists because an
iteration cap is not a budget.

Feature flag: ``FLAG_EVALUATOR_OPTIMIZER``, default off.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..calculator_router import plan_calculation
from ..entailment import _AMOUNT_PREFIX_RE, _AMOUNT_SUFFIX, canonical_amounts, percentages

logger = logging.getLogger(__name__)

#: Citation markers, stripped before any figure is read out of an answer.
#:
#: Every grounded answer ends its sentences with ``[1]``, ``[2]`` … and
#: those parse as the amounts 1 and 2. Left in, an answer that states no
#: figure at all looks like one that states several, so "the model gave
#: a calculation question a figure-free reply" — a real and common
#: failure — would be reported as a *mismatch* instead of as a missing
#: answer, with the wrong revision instruction attached.
_CITATION_MARKER_RE = re.compile(r"\[\d{1,3}\]")

#: Revisions allowed per turn.  One.  A second pass on an answer the
#: first pass could not fix is far more likely to be a prompt problem
#: than something another round would solve.
DEFAULT_MAX_REVISIONS = 1

#: Relative tolerance when comparing a printed figure against the
#: recomputed one.  Answers round — "about UGX 90,000" for 90,000.00 is
#: correct prose — so an exact-equality check would reject good answers.
#: Wide enough for presentation rounding, far too tight to hide a wrong
#: rate or a missing band.
MONEY_TOLERANCE = 0.01

#: Below this, a recomputed figure is not worth matching against the
#: draft: single digits collide with years, section numbers and list
#: indices, so "matching" them proves nothing.
MIN_VERIFIABLE_AMOUNT = 1000.0


@dataclass(frozen=True)
class Verdict:
    """Outcome of one evaluation pass.

    Booleans, not scores. A score invites a threshold, a threshold
    invites tuning, and "0.62 grounded" is not a thing a taxpayer-facing
    system should act on. Each field is a question with an answer.

    ``unverified`` is the honest third state: the checks that could not
    run, kept distinct from the checks that ran and passed. An answer
    nothing could verify must not look identical to a verified one.
    """

    grounded: bool = True
    numerically_consistent: bool = True
    cites_effective_year: bool = True
    tone_appropriate: bool = True
    actionable: bool = True
    revision_note: str = ""
    unverified: tuple[str, ...] = ()
    #: Diagnostics for the audit trail — never shown to the taxpayer.
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return (
            self.grounded
            and self.numerically_consistent
            and self.cites_effective_year
            and self.tone_appropriate
            and self.actionable
        )

    def failures(self) -> list[str]:
        """Names of the checks that rejected, worst first."""
        checks = (
            ("numerically_consistent", self.numerically_consistent),
            ("grounded", self.grounded),
            ("cites_effective_year", self.cites_effective_year),
            ("actionable", self.actionable),
            ("tone_appropriate", self.tone_appropriate),
        )
        return [name for name, ok in checks if not ok]


@dataclass
class RevisionBudget:
    """How many revisions a turn may spend, and on what.

    Shaped after :class:`app.agents.loop_control.ToolCallBudget`: the
    ceilings are constructor arguments, the module defaults are what the
    live path uses, and the budget refuses rather than raises.
    """

    max_revisions: int = DEFAULT_MAX_REVISIONS
    revisions_used: int = 0

    def may_revise(self, *, carries_money: bool, escalation_bound: bool) -> tuple[bool, str]:
        """Whether another revision is allowed, and why not when it is not.

        Revision is spent only where a wrong answer is expensive: a
        figure the taxpayer may act on, or an answer a URA officer will
        read. Rewriting a greeting for tone is not worth a second
        generation at a higher tier.
        """
        if self.revisions_used >= self.max_revisions:
            return False, f"revision budget spent ({self.max_revisions})"
        if not (carries_money or escalation_bound):
            return False, "not money-bearing or escalation-bound"
        return True, "revision allowed"

    def spend(self) -> None:
        self.revisions_used += 1


def _relative_match(expected: float, candidates: set[float]) -> bool:
    """True if any candidate is within :data:`MONEY_TOLERANCE` of *expected*."""
    if expected == 0:
        return 0.0 in candidates
    return any(abs(c - expected) / abs(expected) <= MONEY_TOLERANCE for c in candidates)


def _numeric_fields(result: dict[str, Any]) -> list[tuple[str, float]]:
    """Money-sized numbers a calculator result asserts, largest first.

    Largest first because the headline figure — the tax due, the landed
    cost — is the one a taxpayer acts on and the one an answer is most
    likely to state.
    """
    found: list[tuple[str, float]] = []
    for key, value in result.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if abs(float(value)) >= MIN_VERIFIABLE_AMOUNT:
            found.append((key, float(value)))
    found.sort(key=lambda kv: -abs(kv[1]))
    return found


def verify_money(query: str, answer: str, call_tool: Any) -> tuple[bool | None, dict[str, Any]]:
    """Recompute the query's figure and check the answer against it.

    Returns ``(None, …)`` when the question is not one the calculators
    can settle — no calculation intent, or arguments still missing. That
    is *unverified*, not *passed*, and the caller must keep the two
    apart.

    *call_tool* is injected rather than imported so this is testable
    without an MCP client, and so the caller controls the security
    context the recomputation runs under.
    """
    plan = plan_calculation(query)
    if plan is None:
        return None, {"skipped": "no calculation intent"}
    if plan.missing:
        return None, {"skipped": f"missing inputs: {', '.join(plan.missing)}"}

    try:
        result = call_tool(plan.tool, dict(plan.params))
    except Exception as exc:  # a verifier must never break the turn
        logger.warning("verify_money: recomputation failed: %s", exc)
        return None, {"skipped": f"recomputation failed: {exc}"}

    if not isinstance(result, dict) or result.get("ok") is False:
        return None, {"skipped": "calculator returned no result"}

    expected = _numeric_fields(result)
    if not expected:
        return None, {"skipped": "no verifiable figure in the result"}

    stated = canonical_amounts(_CITATION_MARKER_RE.sub(" ", answer))
    if not stated:
        # The answer to a calculation question printed no figure at all.
        # Not a mismatch — a non-answer — and the caller should see it.
        return False, {"tool": plan.tool, "reason": "answer states no figure",
                       "expected": dict(expected[:3])}

    for key, value in expected:
        if _relative_match(value, stated):
            return True, {"tool": plan.tool, "matched_field": key, "matched_value": value}

    return False, {
        "tool": plan.tool,
        "reason": "no stated figure matches the recomputation",
        "expected": dict(expected[:3]),
        "stated": sorted(stated)[:5],
    }


def verify_rate_currency(answer: str, effective_year: str) -> bool | None:
    """Whether an answer quoting a rate says which year it applies to.

    Rates change on 1 July. A percentage with no fiscal year attached is
    a figure a taxpayer cannot check and may apply to the wrong period.
    Returns ``None`` when the answer quotes no rate at all.
    """
    if not percentages(answer):
        return None
    if not effective_year:
        return True
    haystack = answer.lower()
    # "FY2026-27" should be satisfied by "2026", "2026-27" or the full form.
    tokens = {effective_year.lower(), effective_year.lower().replace("fy", "")}
    tokens |= {part for part in effective_year.replace("FY", "").split("-") if len(part) == 4}
    return any(token and token in haystack for token in tokens)


# ---------------------------------------------------------------------------
# Phase G.2: Tool-backed figure lock & unverified sentence stripping
# ---------------------------------------------------------------------------
_FIGURE_LOCK_TOLERANCE = 0.005  # ±0.5% tolerance (e.g. 7,725,500 == 7.73m)
_DAY_COUNT_RE = re.compile(r"\b(\d{1,3})[-\s]days?\b", re.IGNORECASE)
_PHONE_SCRUB_RE = re.compile(r"\b(?:0800\s?117\s?000|0800\s?217\s?000|0772\s?140\s?000)\b")
_CONDITIONAL_LABEL_RE = re.compile(r"\b(?:conditional|unverified|provisional|if\s+confirmed)\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_FISCAL_YEAR_RE = re.compile(
    r"\bfy\s*(?:19|20)\d{2}(?:\s*[-/]\s*(?:\d{2}|(?:19|20)\d{2}))?\b"
    r"|\b(?:19|20)\d{2}\s*[-/]\s*(?:\d{2}|(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_BARE_YEAR_RE = re.compile(r"\b(?:19\d{2}|20\d{2})\b")
_CURRENCY_MARKER_TAIL_RE = re.compile(
    r"(?:ugx|ug\s?shs?|shs|shillings?|ssente|ensimbi|esente|cente|shilingi|shiringi|cilingi|usd|eur|gbp|kes|\$)\s*$",
    re.IGNORECASE,
)
_AMOUNT_SUFFIX_HEAD_RE = re.compile(
    r"^\s*(?:" + "|".join(sorted((re.escape(k) for k in _AMOUNT_SUFFIX), key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _strip_calendar_years(text: str) -> str:
    """Remove calendar/fiscal-year tokens while preserving year-like numbers used as amounts."""
    without_fy = _FISCAL_YEAR_RE.sub(" ", text or "")
    prefix_spans = [m.span() for m in _AMOUNT_PREFIX_RE.finditer(without_fy)]

    def _replace_year(match: re.Match[str]) -> str:
        start, end = match.span()
        if any(p_start <= start and end <= p_end for p_start, p_end in prefix_spans):
            return match.group(0)
        if _CURRENCY_MARKER_TAIL_RE.search(without_fy[:start]):
            return match.group(0)
        if _AMOUNT_SUFFIX_HEAD_RE.search(without_fy[end:]):
            return match.group(0)
        return " "

    return _BARE_YEAR_RE.sub(_replace_year, without_fy)


def _extract_locked_numbers(text: str) -> tuple[set[float], set[float], set[int]]:
    """Extract (money_or_band_amounts, percentages_as_0_to_100, day_counts) from *text*."""
    cleaned = _PHONE_SCRUB_RE.sub(" ", _CITATION_MARKER_RE.sub(" ", text or ""))
    amount_ready = _strip_calendar_years(cleaned)
    money_vals = {
        float(v) for v in canonical_amounts(amount_ready) if abs(float(v)) >= MIN_VERIFIABLE_AMOUNT
    }
    pct_vals: set[float] = set()
    for p in percentages(cleaned):
        try:
            pct_vals.add(float(p))
        except ValueError:
            pass
    day_vals = {int(m.group(1)) for m in _DAY_COUNT_RE.finditer(cleaned)}
    return money_vals, pct_vals, day_vals


def _collect_tool_numbers(obj: Any, money_out: set[float], pct_out: set[float], days_out: set[int]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        val = float(obj)
        if 0.0 < abs(val) <= 1.0:
            pct_out.add(round(val * 100.0, 4))
        if abs(val) >= MIN_VERIFIABLE_AMOUNT:
            money_out.add(val)
        elif 1 <= int(val) <= 366 and float(int(val)) == val:
            days_out.add(int(val))
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("paye_conditional", "annual_paye_conditional", "chargeable_monthly_conditional"):
                continue
            _collect_tool_numbers(v, money_out, pct_out, days_out)
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            _collect_tool_numbers(item, money_out, pct_out, days_out)


def verify_figure_lock(
    answer: str,
    *,
    query: str = "",
    verified_facts: str = "",
    passages: list[str] | None = None,
    tool_outputs: list[dict[str, Any]] | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    """Verify every money amount, percentage, day count, and band edge in *answer* (§9.G.2).

    Allowed sources: ``tool_outputs ∪ verified_facts ∪ query ∪ passages``.
    Conditional figures (labelled ``conditional on unverified`` in ``verified_facts``
    or ``paye_conditional`` in ``tool_outputs``) are permitted only in sentences
    that explicitly label them conditional/unverified.
    """
    if not answer or not answer.strip():
        return True, [], {"offending_figures": []}

    allowed_money: set[float] = set()
    allowed_pcts: set[float] = set()
    allowed_days: set[int] = set()
    conditional_money: set[float] = set()

    # 1. User query and retrieved passages
    for src_text in [query, *(passages or [])]:
        if src_text:
            m_set, p_set, d_set = _extract_locked_numbers(src_text)
            allowed_money |= m_set
            allowed_pcts |= p_set
            allowed_days |= d_set

    # 1b. Conversation-scoped CaseFacts (so Turn 1 amounts like $6,000 are allowed on Turn 2)
    try:
        from ..service import _ACTIVE_CASE_FACTS

        active_facts = _ACTIVE_CASE_FACTS.get()
        if active_facts is not None:
            for mon in getattr(active_facts, "amounts", None) or []:
                val_m = float(getattr(mon, "amount", 0) or 0)
                if abs(val_m) >= MIN_VERIFIABLE_AMOUNT:
                    allowed_money.add(val_m)
            if getattr(active_facts, "days_in_uganda", None):
                allowed_days.add(int(active_facts.days_in_uganda))
            if getattr(active_facts, "contract_months", None):
                allowed_days.add(int(active_facts.contract_months) * 30)
    except Exception:
        pass

    # 2. Verified facts block (splitting conditional PAYE lines from verified lines)
    if verified_facts:
        for line in verified_facts.splitlines():
            m_set, p_set, d_set = _extract_locked_numbers(line)
            if re.search(r"\bconditional\s+on\s+unverified\b", line, re.IGNORECASE):
                conditional_money |= m_set
            else:
                allowed_money |= m_set
            allowed_pcts |= p_set
            allowed_days |= d_set

    # 3. Structured tool outputs
    for t_out in tool_outputs or []:
        if isinstance(t_out, dict):
            _collect_tool_numbers(t_out, allowed_money, allowed_pcts, allowed_days)
            if t_out.get("unverified_benefits"):
                for ck in ("paye_conditional", "annual_paye_conditional", "chargeable_monthly_conditional"):
                    c_val = t_out.get(ck)
                    if isinstance(c_val, (int, float)) and abs(float(c_val)) >= MIN_VERIFIABLE_AMOUNT:
                        conditional_money.add(float(c_val))

    if not allowed_money and not allowed_pcts and not allowed_days and not conditional_money:
        return True, [], {"offending_figures": [], "skipped": "no reference figures in context"}

    # Expand allowed_money with pairwise sums/differences, total sum, and 12x / /12 equivalents
    if allowed_money:
        base_list = sorted(allowed_money)
        derived_money: set[float] = set()
        for a in base_list:
            if a * 12.0 >= MIN_VERIFIABLE_AMOUNT:
                derived_money.add(a * 12.0)
            if a / 12.0 >= MIN_VERIFIABLE_AMOUNT:
                derived_money.add(a / 12.0)
        for i, a in enumerate(base_list):
            for b in base_list[i + 1 :]:
                if a + b >= MIN_VERIFIABLE_AMOUNT:
                    derived_money.add(a + b)
                if abs(a - b) >= MIN_VERIFIABLE_AMOUNT:
                    derived_money.add(abs(a - b))
        if len(base_list) >= 3:
            derived_money.add(sum(base_list))
        allowed_money |= derived_money

    def _money_matches(val: float, pool: set[float]) -> bool:
        return any(
            abs(val - ref) / max(abs(ref), 1.0) <= _FIGURE_LOCK_TOLERANCE
            for ref in pool
        )

    def _pct_matches(val: float, pool: set[float]) -> bool:
        return any(abs(val - ref) <= max(abs(ref) * _FIGURE_LOCK_TOLERANCE, 0.05) for ref in pool)

    offending: list[str] = []
    for sent in _SENTENCE_SPLIT_RE.split(answer):
        if not sent.strip():
            continue
        s_money, s_pcts, s_days = _extract_locked_numbers(sent)
        is_cond_labelled = bool(_CONDITIONAL_LABEL_RE.search(sent))
        effective_money_pool = allowed_money | (conditional_money if is_cond_labelled else set())

        for m_val in sorted(s_money):
            if allowed_money or conditional_money:
                if not _money_matches(m_val, effective_money_pool):
                    formatted = f"UGX {m_val:,.0f}" if m_val == int(m_val) else f"UGX {m_val:,.2f}"
                    if formatted not in offending:
                        offending.append(formatted)

        for p_val in sorted(s_pcts):
            if allowed_pcts and not _pct_matches(p_val, allowed_pcts):
                formatted_p = f"{p_val:g}%"
                if formatted_p not in offending:
                    offending.append(formatted_p)

        for d_val in sorted(s_days):
            if allowed_days and d_val not in allowed_days:
                formatted_d = f"{d_val} days"
                if formatted_d not in offending:
                    offending.append(formatted_d)

    return (
        len(offending) == 0,
        offending,
        {"offending_figures": offending, "allowed_money_count": len(allowed_money)},
    )


def strip_unverified_figure_sentences(answer: str, offending_figures: list[str]) -> str:
    """Remove sentences containing *offending_figures* and append the URA verification note (§9.G.2)."""
    if not answer or not offending_figures:
        return answer

    offending_money: set[float] = set()
    offending_pcts: set[float] = set()
    offending_days: set[int] = set()
    for item in offending_figures:
        m_s, p_s, d_s = _extract_locked_numbers(item)
        offending_money |= m_s
        offending_pcts |= p_s
        offending_days |= d_s

    kept_lines: list[str] = []
    removed_any = False
    for line in answer.splitlines():
        sentences = _SENTENCE_SPLIT_RE.split(line)
        kept_sents: list[str] = []
        for s in sentences:
            if not s.strip():
                continue
            sm, sp, sd = _extract_locked_numbers(s)
            has_bad_money = any(
                any(abs(v - bad) / max(abs(bad), 1.0) <= _FIGURE_LOCK_TOLERANCE for bad in offending_money)
                for v in sm
            )
            has_bad_pct = any(
                any(abs(v - bad) <= 0.05 for bad in offending_pcts)
                for v in sp
            )
            has_bad_day = bool(sd & offending_days)
            if has_bad_money or has_bad_pct or has_bad_day:
                removed_any = True
            else:
                kept_sents.append(s)
        if kept_sents:
            kept_lines.append(" ".join(kept_sents))
        elif not line.strip():
            kept_lines.append("")

    cleaned = "\n".join(kept_lines).strip()
    note = "Some figures could not be verified; please confirm with URA."
    if removed_any or note not in cleaned:
        cleaned = f"{cleaned}\n\n{note}".strip() if cleaned else note
    return cleaned


def evaluate(
    query: str,
    answer: str,
    *,
    call_tool: Any = None,
    effective_year: str = "",
    faithfulness: float | None = None,
    faithfulness_floor: float = 0.50,
    verified_facts: str = "",
    passages: list[str] | None = None,
    tool_outputs: list[dict[str, Any]] | None = None,
) -> Verdict:
    """Run the deterministic checks over a draft answer.

    Only the checks that can be settled without a model live here. The
    model-judged axes — tone, actionability — are the caller's to add
    from the evaluator tier, and default to passing so that an
    unavailable judge cannot block an otherwise sound answer.
    """
    unverified: list[str] = []
    detail: dict[str, Any] = {}

    numerically_consistent = True
    if call_tool is not None:
        verdict, money_detail = verify_money(query, answer, call_tool)
        detail["money"] = money_detail
        if verdict is None:
            unverified.append("numerically_consistent")
        else:
            numerically_consistent = verdict
    else:
        unverified.append("numerically_consistent")

    if verified_facts or passages or tool_outputs:
        lock_ok, offending_figs, lock_detail = verify_figure_lock(
            answer,
            query=query,
            verified_facts=verified_facts,
            passages=passages,
            tool_outputs=tool_outputs,
        )
        detail["figure_lock"] = lock_detail
        if "numerically_consistent" in unverified and not lock_detail.get("skipped"):
            unverified.remove("numerically_consistent")
        if not lock_ok:
            numerically_consistent = False
            detail["offending_figures"] = offending_figs

    cites_year = verify_rate_currency(answer, effective_year)
    if cites_year is None:
        unverified.append("cites_effective_year")
        cites_year = True

    grounded = True
    if faithfulness is not None:
        grounded = faithfulness >= faithfulness_floor
        detail["faithfulness"] = faithfulness
    else:
        unverified.append("grounded")

    verdict = Verdict(
        grounded=grounded,
        numerically_consistent=numerically_consistent,
        cites_effective_year=cites_year,
        revision_note=_note(numerically_consistent, cites_year, grounded, detail),
        unverified=tuple(unverified),
        detail=detail,
    )
    if not verdict.accepted:
        logger.info("evaluator: rejected — %s", ", ".join(verdict.failures()))
    return verdict


def _note(numeric_ok: bool, year_ok: bool, grounded: bool, detail: dict[str, Any]) -> str:
    """Instruction for the revision pass, or empty when nothing failed.

    Phrased as what to *do*, not as what went wrong: a critique the
    reviser has to interpret is a second chance to get it wrong.
    """
    notes: list[str] = []
    if not numeric_ok:
        money = detail.get("money", {})
        expected = money.get("expected", {})
        if expected:
            if len(expected) == 1:
                figure = next(iter(expected.items()))
                val_str = f"{figure[1]:,.0f}" if isinstance(figure[1], (int, float)) else str(figure[1])
                notes.append(
                    f"State the calculated figure. {money.get('tool', 'The calculator')} "
                    f"returned {figure[0]}={val_str}; use that number, not one of your own."
                )
            else:
                items_str = ", ".join(
                    f"{k}={v:,.0f}" if isinstance(v, (int, float)) else f"{k}={v}"
                    for k, v in expected.items()
                )
                notes.append(
                    f"State the calculated figures. {money.get('tool', 'The calculator')} "
                    f"returned {items_str}; use these numbers, not ones of your own."
                )
        else:
            offending = detail.get("offending_figures") or []
            if offending:
                notes.append(
                    f"Remove or replace unverified figures ({', '.join(offending)}) "
                    "using only figures from the verified facts block and cited passages."
                )
            else:
                notes.append("Call the calculator and state the figure it returns.")
    if not year_ok:
        notes.append("Name the fiscal year the rate applies to; rates change on 1 July.")
    if not grounded:
        notes.append("Remove any claim not supported by a cited passage, or abstain.")
    return " ".join(notes)
