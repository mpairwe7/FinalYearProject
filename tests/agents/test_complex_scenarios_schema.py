"""Schema and coverage validation tests for evals/complex_scenarios (Phase A.4)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "evals" / "complex_scenarios"
DEV_PATH = SCENARIO_DIR / "dev.jsonl"
HELDOUT_PATH = SCENARIO_DIR / "heldout.jsonl"
SHA_PATH = SCENARIO_DIR / "HELDOUT.sha256"

REQUIRED_TAX_HEADS: set[str] = {
    "paye",
    "vat",
    "withholding",
    "rental",
    "corporation",
    "capital_gains",
    "customs",
    "excise",
    "stamp_duty",
    "objections",
    "penalties",
    "digital_services",
    "gaming_betting",
    "advance_tax",
    "procedures",
    "jurisdiction",
}

ALLOWED_ROUTES: set[str] = {"calculator", "workflow", "rag", "declined"}
ALLOWED_DIFFICULTIES: set[str] = {"easy", "medium", "hard"}

# Reject hardcoded money amounts in must_include / premise_cues (Rule 2: figures must use tool specs).
_HARDCODED_MONEY_RE = re.compile(
    r"(?:\bUGX\s*\d|\bShs\.?\s*\d|\b\d{1,3}(?:,\d{3}){2,}\b|\b\d+\s*million\b)",
    re.IGNORECASE,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    assert path.exists(), f"Missing scenario file: {path}"
    rows = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows, f"Empty scenario file: {path}"
    return rows


def test_heldout_sha256_matches_recorded_digest() -> None:
    assert SHA_PATH.exists(), "Missing HELDOUT.sha256"
    recorded = SHA_PATH.read_text(encoding="utf-8").strip().split()[0]
    actual = hashlib.sha256(HELDOUT_PATH.read_bytes()).hexdigest()
    assert actual == recorded, f"HELDOUT.sha256 mismatch: {actual} != {recorded}"


def test_unique_ids_across_dev_and_heldout() -> None:
    dev_rows = _load_jsonl(DEV_PATH)
    ho_rows = _load_jsonl(HELDOUT_PATH)
    all_ids = [r["id"] for r in dev_rows] + [r["id"] for r in ho_rows]
    assert len(all_ids) == len(set(all_ids)), "Duplicate scenario IDs found across splits"


def test_scenario_schema_and_basis_completeness() -> None:
    for expected_split, path in (("dev", DEV_PATH), ("heldout", HELDOUT_PATH)):
        rows = _load_jsonl(path)
        for row in rows:
            sid = row.get("id")
            assert isinstance(sid, str) and sid, "Scenario missing string id"
            assert row.get("split") == expected_split, f"{sid}: wrong split {row.get('split')}"
            assert row.get("tax_head") in REQUIRED_TAX_HEADS, (
                f"{sid}: unknown tax_head {row.get('tax_head')}"
            )
            assert row.get("difficulty") in ALLOWED_DIFFICULTIES, (
                f"{sid}: invalid difficulty {row.get('difficulty')}"
            )
            assert isinstance(row.get("reviewed"), bool), f"{sid}: 'reviewed' must be boolean"
            assert isinstance(row.get("description"), str) and row["description"].strip(), (
                f"{sid}: missing description"
            )
            turns = row.get("turns")
            assert isinstance(turns, list) and len(turns) >= 1, f"{sid}: must have >=1 turn"

            for t_idx, turn in enumerate(turns, 1):
                msg = turn.get("message")
                assert isinstance(msg, str) and len(msg.strip()) >= 10, (
                    f"{sid} turn {t_idx}: invalid message"
                )
                expect = turn.get("expect")
                assert isinstance(expect, dict), f"{sid} turn {t_idx}: missing expect dict"
                assert expect.get("route") in ALLOWED_ROUTES, (
                    f"{sid} turn {t_idx}: invalid route {expect.get('route')}"
                )
                basis = expect.get("basis")
                assert isinstance(basis, list) and len(basis) >= 1, (
                    f"{sid} turn {t_idx}: every turn must cite corpus basis"
                )
                for b in basis:
                    assert isinstance(b, str) and (
                        b.startswith("Data/") or b.startswith("App/backend/app/tax/data/")
                    ), f"{sid} turn {t_idx}: basis must reference tracked source file: {b}"

                # Check must_include / premise_cues for hardcoded money figures
                for field_name in ("must_include", "premise_cues"):
                    for group in expect.get(field_name) or []:
                        assert isinstance(group, dict) and isinstance(group.get("any"), list), (
                            f"{sid} turn {t_idx}: {field_name} items must be {{'any': [...]}}"
                        )
                        for alt in group["any"]:
                            assert not _HARDCODED_MONEY_RE.search(str(alt)), (
                                f"{sid} turn {t_idx}: hardcoded money figure {alt!r} in {field_name}; "
                                "use 'figures' tool specs instead"
                            )

                # Validate figure specs if present
                for fig in expect.get("figures") or []:
                    assert isinstance(fig.get("tool"), str) and fig["tool"]
                    assert isinstance(fig.get("params"), dict)
                    assert isinstance(fig.get("field"), str) and fig["field"]
                    claim = fig.get("claim") or {}
                    cues = (claim.get("label_cues") or {}).get("any")
                    assert isinstance(cues, list) and len(cues) >= 1, (
                        f"{sid} turn {t_idx}: figure spec must include claim.label_cues.any"
                    )


def test_coverage_distribution_dev_and_heldout() -> None:
    dev_rows = _load_jsonl(DEV_PATH)
    ho_rows = _load_jsonl(HELDOUT_PATH)

    assert len(dev_rows) >= 25, f"Expected ~30 dev scenarios, got {len(dev_rows)}"
    assert len(ho_rows) >= 70, f"Expected ~75 heldout scenarios, got {len(ho_rows)}"

    dev_heads = Counter(r["tax_head"] for r in dev_rows)
    ho_heads = Counter(r["tax_head"] for r in ho_rows)

    for head in REQUIRED_TAX_HEADS:
        assert dev_heads[head] >= 1, f"dev.jsonl missing tax_head '{head}'"
        assert ho_heads[head] >= 3, (
            f"heldout.jsonl needs >=3 scenarios for '{head}', got {ho_heads[head]}"
        )

    for name, rows in (("dev", dev_rows), ("heldout", ho_rows)):
        multiturn = sum(1 for r in rows if len(r["turns"]) >= 2)
        premise = sum(
            1
            for r in rows
            if any(t.get("expect", {}).get("premise_flag") for t in r["turns"])
        )
        assert multiturn / len(rows) >= 0.25, (
            f"{name} multi-turn share {multiturn}/{len(rows)} < 25%"
        )
        assert premise / len(rows) >= 0.30, (
            f"{name} premise-challenge share {premise}/{len(rows)} < 30%"
        )
