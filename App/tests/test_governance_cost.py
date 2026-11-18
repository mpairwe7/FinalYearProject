"""App/governance/monitoring/cost_per_decision.py prices tokens per turn from a sourced price sheet."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHEET = ROOT / "App" / "governance" / "monitoring" / "cost_per_decision.csv"


@pytest.fixture(scope="module")
def cost():
    spec = importlib.util.spec_from_file_location("cost_per_decision", SHEET.with_suffix(".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample(provider: str, model: str, kind: str, tokens: float) -> dict:
    return {"metric": {"provider": provider, "model": model, "type": kind}, "value": [0, str(tokens)]}


def test_a_turn_is_priced_per_token_and_unpriced_models_are_named(cost):
    usd, unpriced = cost.cost_per_decision(
        [
            _sample("cloudflare.workers_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "input", 400),
            _sample("cloudflare.workers_ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "output", 100),
            _sample("sunbird", "sunflower-9b", "input", 900),
            _sample("vllm", "Sunbird/Sunflower-Qwen3.5-9B", "output", 0),
        ],
        cost.load_prices(),
    )
    assert usd == pytest.approx(400 * 0.293e-6 + 100 * 2.253e-6)
    assert unpriced == ["sunbird/sunflower-9b"]


@pytest.mark.parametrize("model", ["@cf/meta/llama-3.3-70b-instruct-fp8-fast", "sunflower-9b"])
def test_a_day_without_turns_is_no_estimate(cost, model):
    provider = "sunbird" if model == "sunflower-9b" else "cloudflare.workers_ai"
    with pytest.raises(ValueError, match="non-finite"):
        cost.cost_per_decision([_sample(provider, model, "input", float("nan"))], cost.load_prices())


def test_every_price_is_sourced_and_names_a_model_the_api_calls():
    backend = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "App" / "backend" / "app").rglob("*.py"))
    rows = list(csv.DictReader(SHEET.open(encoding="utf-8")))
    assert rows
    for row in rows:
        assert row["provider"] in {"cloudflare.workers_ai", "gcp.gemini", "sunbird", "vllm"}, row
        if row["usd_per_million_input_tokens"]:
            assert row["source"].startswith("https://") and row["as_of"], row
            assert f'"{row["model"]}"' in backend, f"{row['model']} is priced but the API never calls it"
