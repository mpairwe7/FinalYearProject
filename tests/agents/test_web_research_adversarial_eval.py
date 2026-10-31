"""Deterministic gate for evals/web_research_scenarios/adversarial.jsonl.

Each row is a hostile, misleading or failed search result and the status the
turn must end in. The set is data so that a new attack can be added as one line.
"""

from __future__ import annotations

import html
import json
from pathlib import Path

import pytest

from app import web_research as wr

SCENARIOS = Path(__file__).resolve().parents[2] / "evals" / "web_research_scenarios" / "adversarial.jsonl"
ROWS = [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]


def _page(results: list[dict]) -> str:
    return "".join(
        f'<a class="result__a" href="{html.escape(row["url"])}">{html.escape(row["title"])}</a>'
        f'<a class="result__snippet">{html.escape(row["snippet"])}</a>'
        for row in results
    )


@pytest.fixture(autouse=True)
def _empty_ledger(tmp_db):
    wr.clear_web_search_cache()


@pytest.mark.parametrize("row", ROWS, ids=lambda r: r["id"])
def test_scenario(row: dict) -> None:
    def fetch(_query: str) -> str:
        if row["results"] is None:
            raise RuntimeError("search is down")
        return _page(row["results"])

    outcome = wr.run_web_research(row["query"], search_http_fetcher=fetch, persist_files=False)

    assert outcome.status == row["expect_status"]
    assert outcome.disclosure == wr.LIVE_DISCLOSURES.get(row["expect_status"], "")
    if row.get("expect_rejected"):
        assert outcome.audit["rejected"].get(row["expect_rejected"], 0) >= 1, outcome.audit
    for result in row["results"] or []:
        # The notice is the one part of the outcome that enters the system prompt.
        assert result["snippet"] not in outcome.live_check_notice


def test_the_set_covers_each_failure_class() -> None:
    assert {row["kind"] for row in ROWS} == {"poisoned", "authority", "stale", "evidence", "outage"}
