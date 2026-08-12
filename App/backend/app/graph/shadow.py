"""Score the graph without letting it reach an answer.

``FLAG_GRAPH_FUSION`` stays closed until the graph is measurably better
than flat retrieval on the multi-hop set. Shadow mode is how that gets
measured: the traversal runs, its claims are rendered, and the result is
scored — but nothing reaches a taxpayer.

The renderer is deliberately plain. It is not trying to write a good
answer; it exists so the *claims* can be scored by the same harness that
scores a real answer. If the graph's claims contain the joins the golden
set asks for, a language model given those claims can write the answer.
If they do not, no amount of prompting will recover them, and that is
exactly the thing worth knowing before wiring fusion.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Fiscal year assumed when a question does not name one. Matches what
#: the rate tables treat as current.
DEFAULT_FY = "FY2026-27"

#: Accuracy the graph must reach before ``FLAG_GRAPH_FUSION`` may open.
#: Set in docs/NEXTGEN_ARCHITECTURE_PROPOSAL_2026.md §7.2.
FUSION_GATE = 0.75


def render(answer: Any) -> str:
    """Flatten a :class:`~app.graph.query.GraphAnswer` into scoreable text.

    Percentages are written both as a decimal and as a percent figure,
    because the tables store ``0.12`` and a taxpayer reads "12%". A
    scorer looking for "12" must find it whichever way the claim was
    stored.
    """
    if not getattr(answer, "matched", False):
        return ""

    lines: list[str] = []
    for claim in answer.claims:
        value = claim.value
        rendered = _render_value(value)
        parts = [f"{claim.subject}: {rendered}"]
        if claim.taxpayer_class:
            parts.append(f"applies to {claim.taxpayer_class.replace('_', '-')}")
        if claim.fiscal_year:
            parts.append(claim.fiscal_year)
        if claim.predicate == "computed_on":
            parts.append(f"computed on the {value}-inclusive value")
        if claim.predicate == "threshold":
            parts.append("threshold")
        if claim.provision:
            parts.append(f"[{claim.provision}]")
        if claim.note:
            parts.append(claim.note)
        if claim.unverified:
            parts.append("(figure not yet reconciled against primary legislation)")
        lines.append(" — ".join(parts))
    return "\n".join(lines)


def _render_value(value: Any) -> str:
    """Both forms of a number, so either can be matched or read."""
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float)):
        if 0 < float(value) < 1:
            pct = float(value) * 100
            shown = f"{pct:.10g}"
            return f"{shown}% ({value})"
        return f"{value:,.0f}" if float(value) >= 1000 else str(value)
    if isinstance(value, list):
        # Progressive bands: render each threshold and its rate.
        chunks = []
        for band in value:
            if isinstance(band, (list, tuple)) and len(band) >= 3:
                lo, hi, rate = band[0], band[1], band[2]
                pct = f"{float(rate) * 100:.10g}%"
                upper = f"{hi:,.0f}" if hi is not None else "above"
                chunks.append(f"{lo:,.0f}–{upper}: {pct}")
        return "; ".join(chunks) if chunks else str(value)
    return str(value)


def graph_answer_for(question: str) -> str:
    """``question -> rendered claims``, for the multi-hop harness."""
    from .query import resolve

    try:
        return render(resolve(question, default_fiscal_year=DEFAULT_FY))
    except Exception as exc:
        logger.warning("graph shadow: %s", exc)
        return ""


def score() -> dict[str, Any]:
    """Run the multi-hop golden set against the graph and report.

    Deterministic and offline — no model, no index — so it runs in CI on
    every change rather than nightly against sampled traffic.
    """
    from ..agents.eval_multihop import run_multihop_eval

    report = run_multihop_eval(graph_answer_for)
    return report.to_dict()


def main() -> int:  # pragma: no cover - CLI
    """Print the shadow score; non-zero exit below the fusion gate.

    Reachable as ``python -m app.graph.shadow``, mirroring
    ``app.agents.eval_routing``. This is the number the decision to open
    ``FLAG_GRAPH_FUSION`` rests on, so it needs to be runnable by whoever
    is making that decision rather than only from a test.
    """
    import json

    report = score()
    print(json.dumps(report, indent=2))
    for miss in report.get("misses", []):
        print(f"  MISS {miss}")
    accuracy = report.get("accuracy", 0.0)
    print(f"\naccuracy {accuracy:.0%} against a {FUSION_GATE:.0%} gate")
    if accuracy < FUSION_GATE:
        print("BELOW GATE — FLAG_GRAPH_FUSION must stay closed.")
        return 1
    print(
        "At or above the gate on the authored set. That set has been tuned\n"
        "against; expand it with unseen questions before opening fusion."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
