"""Freeze exact retrieval + prompt contexts for each dev complex scenario turn (Phase B.4).

Runs inside ``ura-app-api`` (or any container with access to Qdrant and the
loaded retriever) to capture, for every turn in ``evals/complex_scenarios/dev.jsonl``:
- ``passages`` from ``retriever.search_planned(...)``
- ``calc_context`` from ``service._evaluate_calculation_context(query)``
- ``messages`` from ``llm._build_messages(...)``

Usage:
    docker exec -i ura-app-api /opt/venv/bin/python - < scripts/bakeoff_freeze_contexts.py \\
      > evals/complex_scenarios/frozen_contexts_dev.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _load_dev_scenarios() -> list[dict[str, Any]]:
    """Load dev.jsonl from the repo or container mount."""
    candidates = [
        Path("evals/complex_scenarios/dev.jsonl"),
        Path("/repo/evals/complex_scenarios/dev.jsonl"),
        Path("/app/evals/complex_scenarios/dev.jsonl"),
        Path("/tmp/dev.jsonl"),
    ]
    for p in candidates:
        if p.exists():
            return [
                json.loads(line)
                for line in p.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
    raise FileNotFoundError("Could not find evals/complex_scenarios/dev.jsonl")


def freeze_all_dev_turns(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build frozen LLM message contexts for every turn across ``scenarios``."""
    import logging
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    from app import llm as llm_module
    from app.query import rewrite
    from app.retriever import HybridRetriever
    from app.service import _evaluate_calculation_context

    retriever = HybridRetriever()
    retriever.initialize()

    frozen_rows: list[dict[str, Any]] = []
    for scen in scenarios:
        history: list[dict[str, str]] = []
        for t_idx, turn in enumerate(scen.get("turns") or [], 1):
            raw_msg = str(turn["message"])
            rewritten = rewrite(raw_msg, history=history or None)
            passages = retriever.search_planned(
                rewritten,
                top_k=6,
                history=[h["user_message"] for h in history] if history else None,
                locale="en",
            )
            calc_ctx = (
                _evaluate_calculation_context(rewritten)
                or _evaluate_calculation_context(raw_msg)
                or _evaluate_calculation_context(
                    " ".join([h["user_message"] for h in history] + [raw_msg])
                )
            )
            messages = llm_module._build_messages(
                query=rewritten,
                passages=passages,
                conversation_history=history or None,
                locale="en",
                personalization_context=calc_ctx,
            )
            frozen_rows.append(
                {
                    "scenario_id": scen["id"],
                    "tax_head": scen["tax_head"],
                    "difficulty": scen["difficulty"],
                    "turn_index": t_idx,
                    "message": raw_msg,
                    "rewritten": rewritten,
                    "expect": turn["expect"],
                    "calc_context": calc_ctx,
                    "sources": [
                        {
                            "source": p.get("source", ""),
                            "section": p.get("section", ""),
                            "page": p.get("page", ""),
                        }
                        for p in (passages or [])
                    ],
                    "messages": messages,
                }
            )
            # Record a neutral placeholder assistant turn so multi-turn history is deterministic
            history.append({"user_message": raw_msg, "bot_reply": "Please see the URA statutory guidance above."})
    return frozen_rows


def main() -> int:
    """Entry point writing JSONL rows to stdout or --out."""
    scenarios = _load_dev_scenarios()
    rows = freeze_all_dev_turns(scenarios)
    for row in rows:
        sys.stdout.write(json.dumps(row, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
