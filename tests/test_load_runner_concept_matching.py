"""The load runners' concept matcher (PR 706 review): whole words, whole numbers, any listed form.

The three runners each score replies with ``concept_present``. A prefix match let
"USD 2,000,000" pass for a 2,000 allowance and "15 days" pass for a 5-day window.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNERS = (
    "tests/load/test_100_multilingual_reliability_edge_cases.py",
    "tests/load/test_500_multilingual_reliability_edge_cases.py",
    "tests/load/test_hardest_statutory_edge_cases.py",
)
WINDOW = "5 days|five days|5-day|five-day|120 hours|120-hour|ennaku 5|ennaku ttaano|siku 5|siku tano"


def _matcher(path: str):
    name = "load_runner_" + Path(path).stem
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module.concept_present


@pytest.mark.parametrize("runner", RUNNERS)
@pytest.mark.parametrize(("concept", "reply", "expected"), [
    ("2,000", "The allowance is USD 2,000 per passenger.", True),
    ("2,000", "The allowance is USD 2,000,000 per passenger.", False),
    ("2,000", "The allowance is USD 12,000.", False),
    ("2,000", "Up to USD 2,000.", True),
    (WINDOW, "You may issue offline for up to 5 days.", True),
    (WINDOW, "Sync within 120 hours of going offline.", True),
    (WINDOW, "Okola offline okumala ennaku  5.", True),
    (WINDOW, "You have 15 days to sync.", False),
    (WINDOW, "Only 5 hours are allowed.", False),
    ("efris", "Register on EFRIS, then issue invoices.", True),
    ("1.5%", "Stamp duty is 1.5% of the value.", True),
    ("1.5%", "Stamp duty is 11.5% of the value.", False),
])
def test_concepts_match_whole(runner: str, concept: str, reply: str, expected: bool) -> None:
    assert _matcher(runner)(concept, reply) is expected
