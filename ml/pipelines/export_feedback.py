"""Export production feedback into labeled review sets for retriever tuning.

Routes thumbs-down responses, low-grounding answers, and escalation cases
into structured JSONL files for:
- Retriever relevance judgment tuning
- Reranker preference pair generation
- Regression test set expansion

Usage:
    python -m ml.pipelines.export_feedback --output-dir Data/eval/feedback
    python -m ml.pipelines.export_feedback --days 7 --min-reviews 5
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)


def export_review_set(days: int = 30, output_dir: Path = Path("Data/eval/feedback")) -> dict:
    """Query the analytics DB and export review-worthy entries."""
    from App.backend.app import database as db

    db.init_db()
    entries = db.export_review_feedback(days=days)

    output_dir.mkdir(parents=True, exist_ok=True)

    # Split into categories
    thumbs_down = [e for e in entries if e.get("review_reason") == "thumbs_down"]
    low_conf = [e for e in entries if e.get("review_reason") == "low_confidence"]

    # Write retriever tuning set (negative relevance judgments)
    retriever_path = output_dir / "retriever_negatives.jsonl"
    with open(retriever_path, "w") as f:
        for entry in thumbs_down:
            f.write(
                json.dumps(
                    {
                        "query": entry.get("user_query", ""),
                        "negative_response": entry.get("bot_reply", ""),
                        "user_comment": entry.get("comment", ""),
                        "label": "negative",
                        "timestamp": entry.get("created_at", 0),
                    }
                )
                + "\n"
            )

    # Write regression test candidates
    regression_path = output_dir / "regression_candidates.jsonl"
    with open(regression_path, "w") as f:
        for entry in entries:
            f.write(
                json.dumps(
                    {
                        "question": entry.get("user_query", ""),
                        "bad_answer": entry.get("bot_reply", ""),
                        "reason": entry.get("review_reason", ""),
                        "comment": entry.get("comment", ""),
                    }
                )
                + "\n"
            )

    stats = {
        "total_exported": len(entries),
        "thumbs_down": len(thumbs_down),
        "low_confidence": len(low_conf),
        "retriever_negatives_path": str(retriever_path),
        "regression_candidates_path": str(regression_path),
    }
    logger.info("Exported feedback review set: %s", stats)
    return stats


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="Export feedback for review")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--output-dir", type=str, default="Data/eval/feedback")
    args = parser.parse_args()

    stats = export_review_set(days=args.days, output_dir=Path(args.output_dir))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
