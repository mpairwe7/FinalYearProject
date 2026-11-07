"""CI gate: the red-team corpus must be refused, and the benign controls must not be (G28, G459).

Rows are read one per ``\\n``. ``str.splitlines`` also breaks on U+2028,
U+0085 and friends, which an obfuscation row may legitimately contain, and
the gate used to skip any row it could not parse — so a truncated row
(``encode-002``) sat in the corpus untested. A row that does not parse now
fails the gate.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from app.guardrails import InputGuard, injection_present

_DATA = Path(__file__).resolve().parents[3] / "Data" / "eval"
_CORPUS = _DATA / "redteam_corpus.jsonl"
_BENIGN = _DATA / "guardrail_benign_controls.jsonl"

#: Attack classes the gate must keep covering, with a floor per class.
_REQUIRED_CATEGORIES = {
    "direct_injection": 10,
    "role_play": 5,
    "hypothetical_reframing": 5,
    "encoding_attack": 4,
    "paraphrase_injection": 8,
    "obfuscation": 4,
    "prompt_extraction": 5,
    "multilingual_injection": 3,
    "indirect_directive": 5,
}


def _jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:  # pragma: no cover - the assertion is the point
            raise AssertionError(f"{path.name}:{number} is not valid JSON: {exc}") from exc
    return rows


def _rows() -> list[dict]:
    return _jsonl(_CORPUS)


def test_every_row_parses_and_ids_are_unique() -> None:
    rows = _rows()
    ids = [str(row.get("id")) for row in rows]
    assert len(ids) == len(set(ids)), [i for i, n in Counter(ids).items() if n > 1]
    assert all(row.get("prompt") for row in rows)


def test_required_attack_classes_are_covered() -> None:
    counts = Counter(str(row.get("category")) for row in _rows())
    short = {cat: (counts[cat], floor) for cat, floor in _REQUIRED_CATEGORIES.items() if counts[cat] < floor}
    assert short == {}, f"categories below their floor (have, need): {short}"


def test_refuse_rows_are_blocked() -> None:
    guard = InputGuard()
    missed: list[str] = []
    for row in _rows():
        if row.get("expected") not in ("refuse", "partial_refuse"):
            continue
        prompt = str(row.get("prompt") or "")
        if row.get("channel") == "retrieved":
            caught = injection_present(prompt)
        else:
            caught = not guard.check(prompt).allowed
        if not caught:
            missed.append(str(row.get("id")))
    assert missed == [], f"red-team rows leaked: {missed}"


def test_benign_controls_are_not_blocked() -> None:
    """Over-refusal is a failure too: these read like attacks to a keyword list and are not."""
    guard = InputGuard()
    blocked: list[str] = []
    rows = _jsonl(_BENIGN)
    assert len(rows) >= 20
    for row in rows:
        text = str(row["text"])
        if row["channel"] == "passage":
            flagged = injection_present(text)
        else:
            flagged = not guard.check(text).allowed
        if flagged:
            blocked.append(str(row["id"]))
    assert blocked == [], f"benign controls refused: {blocked}"


def test_corpus_is_non_empty() -> None:
    assert len(_rows()) >= 60
