"""CI gate: the red-team corpus must be refused by InputGuard (G28)."""

from __future__ import annotations

import json
from pathlib import Path

from app.guardrails import InputGuard

_CORPUS = Path(__file__).resolve().parents[3] / "Data" / "eval" / "redteam_corpus.jsonl"


def _rows() -> list[dict]:
    rows = []
    for line in _CORPUS.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def test_refuse_rows_are_blocked() -> None:
    guard = InputGuard()
    missed: list[str] = []
    for row in _rows():
        if row.get("expected") not in ("refuse", "partial_refuse"):
            continue
        result = guard.check(str(row.get("prompt") or ""))
        if result.allowed:
            missed.append(str(row.get("id")))
    assert missed == [], f"red-team rows leaked: {missed}"


def test_corpus_is_non_empty() -> None:
    assert len(_rows()) >= 20
