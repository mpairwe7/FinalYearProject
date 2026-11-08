"""The legal-change pipeline: from a Finance Act to a reviewed kernel release (ADR-0033).

Each year, before 1 July, URA's tax changes reach the kernel this way:

1. **Draft.** An engineer drafts the new fiscal year's rate table in the
   format of ``app/tax/data/FY*.json``.
2. **Propose.** ``propose`` diffs the draft against the kernel and extracts the
   figures each supplied Act's text states (percentages, UGX amounts, currency
   points, day counts). Every changed figure is marked *corroborated* when a
   sentence of an Act names it beside that value, and *uncorroborated*
   otherwise. A proposal goes to the review queue. Nothing is extracted into the
   kernel automatically: the draft table remains the source, and the Act text
   only checks it.
3. **Decide.** A named reviewer approves or rejects the proposal.
4. **Release.** The approved table is committed and a release is recorded
   with the same reviewer, which makes it ``approved``.

Commands::

    python -m app.kernel.legal_change propose --table FY2027-28.json --act finance-act-2027.pdf
    python -m app.kernel.legal_change list
    python -m app.kernel.legal_change approve --id ID --reviewer NAME --role ROLE
    python -m app.kernel.legal_change reject --id ID --reviewer NAME --role ROLE --reason TEXT
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"


def review_dir() -> Path:
    return Path(os.getenv("KERNEL_REVIEW_DIR") or Path(__file__).with_name("review"))


# --------------------------------------------------------------------------- extraction

_SENTENCE_RE = re.compile(r"(?<=[.;:])\s+(?=[A-Z(0-9])")
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("percent", re.compile(r"(\d{1,3}(?:\.\d{1,3})?)\s?(?:%|per\s?cent(?:um)?)", re.I)),
    ("amount_ugx", re.compile(r"(?:UGX|Shs\.?|shillings)\s?([\d,]{3,}(?:\.\d+)?)", re.I)),
    ("currency_points", re.compile(r"(\d{1,6})\s+currency\s+points?", re.I)),
    ("days", re.compile(r"(\d{1,4})\s+(?:working\s+)?days\b", re.I)),
)


@dataclass(frozen=True)
class Candidate:
    kind: str
    value: float
    sentence: str
    source: str
    page: int = 0


def extract_candidates(text: str, *, source: str = "", page: int = 0) -> list[Candidate]:
    """Every figure a statute's text states, with the sentence that states it."""
    found: list[Candidate] = []
    for sentence in _SENTENCE_RE.split(" ".join((text or "").split())):
        for kind, pattern in _PATTERNS:
            for match in pattern.finditer(sentence):
                try:
                    value = float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
                found.append(Candidate(kind, value, sentence[:600], source, page))
    return found


def read_act(path: Path) -> list[Candidate]:
    """Candidates from an Act: plain text or Markdown, or a PDF read page by page."""
    if path.suffix.lower() == ".pdf":
        import pymupdf

        out: list[Candidate] = []
        with pymupdf.open(path) as doc:
            for number, page in enumerate(doc, start=1):
                out += extract_candidates(page.get_text("text") or "", source=path.name, page=number)
        return out
    return extract_candidates(path.read_text(encoding="utf-8"), source=path.name)


# --------------------------------------------------------------------------- diff and corroboration


@dataclass(frozen=True)
class Change:
    key: str
    change: str  # "added", "changed", "removed"
    old: Any
    new: Any
    legal_basis: str
    corroborated: bool = False
    evidence: list[dict[str, Any]] = field(default_factory=list)


def _expected(key: str, value: Any) -> list[tuple[str, float]]:
    """What a statute would write for this figure: ``(candidate kind, value)``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return []
    if key.endswith("_currency_points"):
        return [("currency_points", float(value))]
    if "_days" in key:
        return [("days", float(value))]
    if isinstance(value, float) and 0 < value <= 1:
        return [("percent", round(value * 100, 3))]
    return [("amount_ugx", float(value))]


def _terms(key: str) -> list[str]:
    from ..graph.build import display_name

    # Generic and unit words name no figure: "30 days" alone corroborates no time limit.
    stop = {"the", "of", "on", "for", "and", "tax", "rate", "ugx", "minimum", "monthly", "annual", "days",
            "hours", "years", "amount", "floor", "value", "period", "notice", "statutory"}
    return [w for w in re.findall(r"[a-z]+", display_name(key).lower()) if len(w) > 3 and w not in stop]


def corroborate(key: str, value: Any, candidates: list[Candidate]) -> list[Candidate]:
    """Act sentences that state *value* and name the figure."""
    terms = _terms(key)
    hits = []
    for kind, expected in _expected(key, value):
        for cand in candidates:
            if cand.kind == kind and abs(cand.value - expected) < 1e-6:
                low = cand.sentence.lower()
                if not terms or any(t in low for t in terms):
                    hits.append(cand)
    return hits


def diff(draft: dict[str, Any], candidates: list[Candidate]) -> list[Change]:
    """How the drafted table's figures differ from the kernel's latest fiscal year."""
    from ..tax import tables

    latest = tables.get_table(tables.latest_fiscal_year())
    # Compare as JSON: the parsed table holds bands as tuples, a draft as lists.
    old = json.loads(json.dumps(dict(latest.rates), default=list))
    new = json.loads(json.dumps(dict(draft.get("rates") or {})))
    basis = dict(draft.get("legal_basis") or {})
    out: list[Change] = []
    for key in sorted(set(old) | set(new)):
        if key in new and key not in old:
            kind = "added"
        elif key in old and key not in new:
            out.append(Change(key, "removed", old[key], None, ""))
            continue
        elif old[key] != new[key]:
            kind = "changed"
        else:
            continue
        hits = corroborate(key, new[key], candidates)
        out.append(Change(key, kind, old.get(key), new[key], basis.get(key, ""), bool(hits),
                          [asdict(h) for h in hits[:3]]))
    return out


# --------------------------------------------------------------------------- review queue


def propose(draft_path: Path, act_paths: list[Path]) -> dict[str, Any]:
    draft = json.loads(draft_path.read_text(encoding="utf-8"))
    candidates: list[Candidate] = []
    for act in act_paths:
        candidates += read_act(act)
    changes = diff(draft, candidates)
    digest = hashlib.sha256(draft_path.read_bytes()).hexdigest()
    proposal = {
        "id": f"{draft.get('fiscal_year', 'draft')}-{digest[:12]}",
        "created": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
        "fiscal_year": draft.get("fiscal_year", ""),
        "draft": str(draft_path),
        "draft_sha256": digest,
        "acts": [p.name for p in act_paths],
        "changes": [asdict(c) for c in changes],
        "summary": {
            "changes": len(changes),
            "corroborated": sum(1 for c in changes if c.corroborated),
            "uncorroborated": sum(1 for c in changes if not c.corroborated and c.change != "removed"),
            "removed": sum(1 for c in changes if c.change == "removed"),
            "uncited": sum(1 for c in changes if c.change != "removed" and not c.legal_basis),
        },
        "status": PENDING,
        "decision": {},
    }
    folder = review_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{proposal['id']}.json").write_text(json.dumps(proposal, indent=2, ensure_ascii=False) + "\n",
                                                   encoding="utf-8")
    return proposal


def list_proposals() -> list[dict[str, Any]]:
    folder = review_dir()
    if not folder.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def decide(proposal_id: str, *, approve: bool, reviewer: str, role: str, reason: str = "") -> dict[str, Any]:
    """Record a named reviewer's decision. A decided proposal cannot be decided again."""
    if not reviewer.strip() or not role.strip():
        raise ValueError("a decision needs the reviewer's name and role")
    if not approve and not reason.strip():
        raise ValueError("a rejection needs a reason")
    path = review_dir() / f"{proposal_id}.json"
    proposal = json.loads(path.read_text(encoding="utf-8"))
    if proposal["status"] != PENDING:
        raise ValueError(f"proposal {proposal_id} is already {proposal['status']}")
    # A refinement proposal (app.kernel.refine) has no draft table.
    draft = Path(proposal["draft"]) if proposal.get("draft") else None
    if draft is not None and (
        not draft.exists() or hashlib.sha256(draft.read_bytes()).hexdigest() != proposal["draft_sha256"]
    ):
        raise ValueError("the draft table changed after it was proposed, or is missing; propose it again")
    proposal["status"] = APPROVED if approve else REJECTED
    proposal["decision"] = {
        "reviewer": reviewer.strip(),
        "role": role.strip(),
        "reason": reason.strip(),
        "decided_at": _dt.datetime.now(_dt.UTC).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(proposal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return proposal


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - CLI
    import argparse

    parser = argparse.ArgumentParser(description="Kernel legal-change pipeline (ADR-0033).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--table", type=Path, required=True)
    p.add_argument("--act", type=Path, action="append", default=[])
    sub.add_parser("list")
    for name in ("approve", "reject"):
        d = sub.add_parser(name)
        d.add_argument("--id", required=True)
        d.add_argument("--reviewer", required=True)
        d.add_argument("--role", required=True)
        d.add_argument("--reason", default="")
    args = parser.parse_args(argv)
    if args.cmd == "propose":
        result: Any = propose(args.table, args.act)
        print(json.dumps(result["summary"], indent=2))  # noqa: T201 - CLI output
        return 0
    if args.cmd == "list":
        result = [{"id": p["id"], "status": p["status"], **p["summary"]} for p in list_proposals()]
    else:
        result = decide(args.id, approve=args.cmd == "approve", reviewer=args.reviewer, role=args.role,
                        reason=args.reason)
    print(json.dumps(result, indent=2))  # noqa: T201 - CLI output
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
