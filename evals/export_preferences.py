"""Export thumbs-down + officer replies as preference pairs (G29 slice).

Does not fine-tune. Writes JSONL a DPO/KTO job can consume later.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "App" / "backend"))

from app import database as db  # noqa: E402


def export_pairs(path: Path) -> int:
    db.init_db()
    rows = db.export_review_feedback() if hasattr(db, "export_review_feedback") else []
    tickets = []
    try:
        tickets = db.list_tickets(status="resolved", limit=500)
    except TypeError:
        tickets = db.list_tickets(status="resolved")
    pairs = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        rejected = str(row.get("bot_reply") or row.get("assistant") or "").strip()
        query = str(row.get("user_query") or row.get("query") or "").strip()
        if query and rejected:
            pairs.append(
                {
                    "prompt": query,
                    "rejected": rejected,
                    "source": "thumbs_down",
                }
            )
    for ticket in tickets or []:
        reply = str(ticket.get("officer_reply") or "").strip()
        query = str(ticket.get("user_query") or "").strip()
        rejected = str(ticket.get("bot_reply") or "").strip()
        if query and reply:
            pairs.append(
                {
                    "prompt": query,
                    "chosen": reply,
                    "rejected": rejected,
                    "source": "officer_reply",
                    "ticket_id": ticket.get("id"),
                }
            )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for item in pairs:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    return len(pairs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output",
        default=str(ROOT / "Data" / "eval" / "preference_pairs.jsonl"),
    )
    args = parser.parse_args()
    n = export_pairs(Path(args.output))
    print(f"wrote {n} pairs to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
