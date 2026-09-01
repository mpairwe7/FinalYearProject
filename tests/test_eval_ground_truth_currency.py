"""The accuracy harness's expected figures must track the live rate table.

``tests/load/tax_education_accuracy_eval.py`` hard-codes the statutory amounts a
correct answer has to contain. Those amounts are law, and law changes: the VAT
(Amendment) Act 2026 doubled the registration threshold and the Income Tax
(Amendment) Act 2026 substituted the PAYE bands, but the harness kept asserting
the FY2025-26 figures.

That is worse than a stale test. The harness scores the *system*, so stale
ground truth marks a correct answer wrong and depresses the published accuracy
number — measured here at 45.0% for the VAT threshold query and 35.0% for the
PAYE threshold query, both of which the service answered correctly from the
FY2026-27 table it cites.

This guard fails the moment the two drift apart again.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_PATH = REPO_ROOT / "tests" / "load" / "tax_education_accuracy_eval.py"
RATES_DIR = REPO_ROOT / "App" / "backend" / "app" / "tax" / "data"


def _current_fiscal_year_table() -> tuple[str, dict]:
    """The newest FY*.json in the rate-table directory, with its name."""
    tables = sorted(RATES_DIR.glob("FY*.json"))
    assert tables, f"no rate tables under {RATES_DIR}"
    newest = tables[-1]
    return newest.stem, json.loads(newest.read_text())["rates"]


def _eval_source() -> str:
    return EVAL_PATH.read_text(encoding="utf-8")


def _grouped(value: int) -> str:
    return f"{value:,}"


def test_vat_registration_threshold_matches_the_rate_table() -> None:
    fy, rates = _current_fiscal_year_table()
    expected = _grouped(rates["vat_registration_threshold_annual"])
    source = _eval_source()

    block = re.search(
        r'query_id="vat_registration_threshold_en".*?required_numerical_values=\[(.*?)\]',
        source,
        re.DOTALL,
    )
    assert block, "vat_registration_threshold_en ground truth not found in the harness"
    assert expected in block.group(1), (
        f"{fy} sets the VAT registration threshold to UGX {expected}, but the accuracy "
        f"harness still requires {block.group(1).strip()}. A correct answer is being "
        f"scored as wrong."
    )


def test_paye_tax_free_threshold_matches_the_rate_table() -> None:
    fy, rates = _current_fiscal_year_table()
    bands = rates["paye_bands_resident"]
    # The nil band's upper bound is the monthly tax-free threshold.
    nil_band = next(b for b in bands if b[2] == 0.0)
    expected = _grouped(nil_band[1])
    source = _eval_source()

    block = re.search(
        r'query_id="paye_threshold_en".*?required_numerical_values=\[(.*?)\]',
        source,
        re.DOTALL,
    )
    assert block, "paye_threshold_en ground truth not found in the harness"
    assert expected in block.group(1), (
        f"{fy} puts the PAYE nil band's ceiling at UGX {expected}, but the accuracy "
        f"harness still requires {block.group(1).strip()}."
    )


def _harness_blocks(source: str) -> list[tuple[str, str]]:
    """Split the harness into (topic, text) blocks.

    A single-query block declares ``topic="vat"``; a journey declares
    ``"journey_id": "journey_vat_onboarding"``. Either way the block names the
    tax it is about, which is what lets the sweep below tell a superseded VAT
    threshold apart from an unrelated figure that merely shares its digits.
    """
    markers = [m.start() for m in re.finditer(r'TaxGroundTruth\(|"journey_id":', source)]
    blocks: list[tuple[str, str]] = []
    for i, begin in enumerate(markers):
        end = markers[i + 1] if i + 1 < len(markers) else len(source)
        text = source[begin:end]
        topic = re.search(r'topic="([^"]+)"', text) or re.search(r'"(journey_[a-z_]+)"', text)
        blocks.append((topic.group(1) if topic else "", text))
    return blocks


def test_no_superseded_figure_survives_anywhere_in_the_harness() -> None:
    """A figure the newest table replaced must not still be asserted for that tax.

    Catches the sites the two targeted checks above do not name — the
    multi-turn journeys especially, where a threshold change can invert a
    turn's correct answer rather than merely restate it. Scoped by topic so an
    unrelated amount that happens to share the digits (the presumptive-tax
    ceiling is also 150,000,000, and the VAT amendment did not touch the Income
    Tax Act's Second Schedule) is not mistaken for a stale one.
    """
    tables = sorted(RATES_DIR.glob("FY*.json"))
    if len(tables) < 2:
        pytest.skip("need at least two fiscal-year tables to identify superseded figures")

    newest = json.loads(tables[-1].read_text())["rates"]
    previous = json.loads(tables[-2].read_text())["rates"]
    blocks = _harness_blocks(_eval_source())

    stale: list[str] = []
    for key, old_value in previous.items():
        if key not in newest or newest[key] == old_value:
            continue
        if not isinstance(old_value, int) or old_value < 1000:
            continue  # rates and small scalars are too collision-prone to match on
        domain = key.split("_", 1)[0]
        needle = _grouped(old_value)
        for topic, text in blocks:
            if domain not in topic:
                continue
            if re.search(rf"(?<![0-9,]){re.escape(needle)}(?![0-9,])", text):
                stale.append(f"{key}: {needle} still asserted under topic {topic!r} "
                             f"(superseded by {_grouped(newest[key])})")

    assert not stale, (
        "the accuracy harness still asserts figures the current rate table has "
        "superseded:\n  " + "\n  ".join(stale)
    )
