"""A remembered amount fills a calculator slot as a number (#740).

The graph read semantic memory through ``tax.money.parse_money_amount``, which
does not exist. The ImportError was swallowed, so every remembered value reached
the calculator as raw text.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.agents.graphs import main_graph
from app.agents.graphs.state import AgentGraphState
from app.memory import service as memory_service


def _bind(monkeypatch: pytest.MonkeyPatch, remembered: dict[str, str], required: list[str]) -> dict | None:
    facts = [SimpleNamespace(predicate=key, category=key, object_value=value) for key, value in remembered.items()]
    memsvc = SimpleNamespace(
        resolution_ledger=SimpleNamespace(get_verified_slots=lambda *args, **kwargs: {}),
        read_facts=lambda *args, **kwargs: facts,
    )
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: memsvc)
    state = AgentGraphState(query="work out my PAYE", user_id="user-1")
    return main_graph._bind_from_memory_or_dag("calculate_paye", required, state)


@pytest.mark.parametrize(
    ("remembered", "expected"),
    [("UGX 1,200,000", 1_200_000.0), ("1.2m", 1_200_000.0), ("shs 500k", 500_000.0)],
)
def test_a_remembered_amount_binds_as_a_number(monkeypatch: pytest.MonkeyPatch, remembered: str, expected: float) -> None:
    assert _bind(monkeypatch, {"salary": remembered}, ["monthly_salary"]) == {"monthly_salary": expected}


@pytest.mark.parametrize("remembered", ["USD 5,000", "$5,000", "salary 1,200,000 and allowance 300,000"])
def test_a_foreign_or_ambiguous_amount_is_never_read_as_shillings(monkeypatch: pytest.MonkeyPatch, remembered: str) -> None:
    assert _bind(monkeypatch, {"salary": remembered}, ["monthly_salary"]) == {"monthly_salary": remembered}


def test_a_remembered_tin_stays_as_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _bind(monkeypatch, {"tin": "1000123456"}, ["tin"]) == {"tin": "1000123456"}
