"""Cost of one decision (one chat turn) in US dollars.

Prometheus records model tokens per turn as ``ura:llm_tokens_per_turn:rate1d``.
This prices each provider and model from ``cost_per_decision.csv``. A model
with tokens but no published price is listed as unpriced, and the total is
then a lower bound.

Usage: python3 App/governance/monitoring/cost_per_decision.py [--prometheus URL]
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from pathlib import Path

PRICES = Path(__file__).with_name("cost_per_decision.csv")
QUERY = "ura:llm_tokens_per_turn:rate1d"


def load_prices(path: Path = PRICES) -> dict[tuple[str, str], dict[str, float]]:
    """``(provider, model) -> {"input": usd_per_token, "output": usd_per_token}`` for priced rows."""
    prices: dict[tuple[str, str], dict[str, float]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["usd_per_million_input_tokens"] and row["usd_per_million_output_tokens"]:
                prices[(row["provider"], row["model"])] = {
                    "input": float(row["usd_per_million_input_tokens"]) / 1_000_000,
                    "output": float(row["usd_per_million_output_tokens"]) / 1_000_000,
                }
    return prices


def cost_per_decision(
    samples: list[dict], prices: dict[tuple[str, str], dict[str, float]]
) -> tuple[float, list[str]]:
    """Price Prometheus instant-vector samples; return dollars per turn and the unpriced models."""
    usd, unpriced = 0.0, set()
    for sample in samples:
        labels = sample["metric"]
        tokens = float(sample["value"][1])
        model = (labels.get("provider", ""), labels.get("model", ""))
        if not math.isfinite(tokens):
            raise ValueError(f"{'/'.join(model)} has a non-finite token rate ({tokens}); there were no turns to divide by")
        price = prices.get(model, {}).get(labels.get("type", ""))
        if price is not None:
            usd += tokens * price
        elif tokens > 0:
            unpriced.add("/".join(model))
    return usd, sorted(unpriced)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prometheus", default=os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    args = parser.parse_args()
    if not args.prometheus.startswith(("http://", "https://")):
        sys.exit("--prometheus must be an http(s) URL")

    import httpx

    response = httpx.get(f"{args.prometheus.rstrip('/')}/api/v1/query", params={"query": QUERY}, timeout=30)
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "success":
        sys.exit(f"Prometheus query failed: {body}")
    samples = body["data"]["result"]
    if not samples:
        sys.exit(f"No token data yet: {QUERY} is empty.")
    try:
        usd, unpriced = cost_per_decision(samples, load_prices())
    except ValueError as exc:
        sys.exit(f"No estimate: {exc}")
    print(f"USD per decision: {usd:.6f}" + (" (lower bound: some models are unpriced)" if unpriced else ""))
    for name in unpriced:
        print(f"unpriced: {name}")


if __name__ == "__main__":
    main()
