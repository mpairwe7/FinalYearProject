"""Offline kernel refinement from trajectories (ADR-0033, phase 3).

In the spirit of OaK (Sutton's Options and Knowledge architecture), what the
agent experiences becomes proposals to improve what it knows. Nothing is
learned online, and nothing is applied: every proposal goes to the review queue
(:mod:`app.kernel.legal_change`), and only a reviewed rate-table change and a
release reach the kernel.

Three signals are read:

* **Misses** (``kernel_trace.misses`` in the audit rows): a concept asked for
  in at least ``min_turns`` turns that the kernel does not declare, or holds
  for another year only. The proposal is to declare it, or to add the year.
* **Uncited figures served** (``kernel_trace.uncited_served``): a figure with no
  legal basis that answered at least ``min_turns`` turns. The proposal is to
  cite it. The busiest come first.
* **Recurring evidence conflicts** (``evidence_conflict`` items in the
  discrepancy queue): a figure that documents keep contradicting. The
  proposal is to check it against the statute.

``python -m app.kernel.refine --tenant default --min-turns 3``
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
from collections import Counter, defaultdict
from typing import Any

from .legal_change import PENDING, review_dir

DEFAULT_MIN_TURNS = 3


def _turn_traces(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        if row.get("event_type") != "generate":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        trace = payload.get("kernel_trace")
        if isinstance(trace, dict):
            out.append(trace)
    return out


def suggestions(rows: list[dict[str, Any]], conflicts: list[dict[str, Any]] | None = None, *,
                min_turns: int = DEFAULT_MIN_TURNS) -> list[dict[str, Any]]:
    """Ranked refinement suggestions; each counts the distinct turns behind it."""
    misses: Counter[tuple[str, str]] = Counter()
    uncited: Counter[str] = Counter()
    years: dict[str, set[str]] = defaultdict(set)
    for trace in _turn_traces(rows):
        for item in {(m.get("concept", ""), m.get("reason", "")) for m in trace.get("misses") or []}:
            misses[item] += 1
        for item in {(u.get("key", ""), u.get("fiscal_year", "")) for u in trace.get("uncited_served") or []}:
            uncited[item[0]] += 1
            years[item[0]].add(item[1])

    out: list[dict[str, Any]] = []
    for (concept, reason), turns in misses.most_common():
        if turns >= min_turns and concept:
            action = "add the fiscal year" if reason == "not_in_year" else "declare the concept"
            out.append({"kind": "kernel_miss", "concept": concept, "reason": reason, "turns": turns,
                        "proposal": f"{action}: {concept} was asked for in {turns} turns and the kernel lacked it"})
    for key, turns in uncited.most_common():
        if turns >= min_turns and key:
            out.append({"kind": "uncited_figure", "concept": key, "fiscal_years": sorted(years[key]), "turns": turns,
                        "proposal": f"cite {key}: served without a legal basis in {turns} turns"})
    for item in conflicts or []:
        if item.get("discrepancy_type") != "evidence_conflict":
            continue
        count = int(item.get("frequency_count") or 0)
        if count >= min_turns:
            out.append({"kind": "evidence_conflict", "concept": str(item.get("bot_statement") or "")[:200],
                        "turns": count, "report_id": item.get("id", ""),
                        "proposal": f"check against the statute: documents contradicted this figure {count} times"})
    return sorted(out, key=lambda s: (-int(s["turns"]), s["kind"], s["concept"]))


def propose(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Queue *items* as one refinement proposal; ``None`` when there is nothing to propose."""
    if not items:
        return None
    digest = hashlib.sha256(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()
    proposal = {
        "id": f"refine-{digest[:12]}",
        "type": "refinement",
        "created": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "changes": items,
        "summary": {
            "changes": len(items),
            "kernel_miss": sum(1 for i in items if i["kind"] == "kernel_miss"),
            "uncited_figure": sum(1 for i in items if i["kind"] == "uncited_figure"),
            "evidence_conflict": sum(1 for i in items if i["kind"] == "evidence_conflict"),
        },
        "status": PENDING,
        "decision": {},
    }
    folder = review_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{proposal['id']}.json"
    if not path.exists():
        path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return proposal


def run(tenant_id: str = "default", *, min_turns: int = DEFAULT_MIN_TURNS, page: int = 200) -> dict[str, Any] | None:
    """Read the ledger and the discrepancy queue, and queue what recurs."""
    from .. import database as db
    from ..audit.ledger import get_ledger

    ledger = get_ledger()
    rows: list[dict[str, Any]] = []
    since = 0
    while True:
        batch = ledger.read(tenant_id=tenant_id, since_seq=since, limit=page)
        if not batch:
            break
        rows += batch
        since = int(batch[-1]["seq"])
    conflicts = db.list_discrepancies(status="pending", limit=200)
    return propose(suggestions(rows, conflicts, min_turns=min_turns))


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(description="Propose kernel refinements from trajectories (ADR-0033).")
    parser.add_argument("--tenant", default="default")
    parser.add_argument("--min-turns", type=int, default=DEFAULT_MIN_TURNS)
    args = parser.parse_args(argv)
    proposal = run(args.tenant, min_turns=args.min_turns)
    print(json.dumps(proposal["summary"] if proposal else {"changes": 0}, indent=2))  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
