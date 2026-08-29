#!/usr/bin/env python3
"""Carbon Footprint Tracker — URA Chatbot Training (2026)

Wraps training/inference with CodeCarbon to measure CO2 emissions.
Produces a JSON report for the governance pipeline.

Standards: week01 ethics concern E9 (environmental impact),
           ISO/IEC 42001:2023 §6.1.3 (AI system impact assessment)

Usage:
    python scripts/carbon_tracker.py --task training --script ml/pipelines/evaluate_rag.py
    python scripts/carbon_tracker.py --task inference --duration 3600

Requires: pip install codecarbon
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def track_training(script: str, args: list[str]) -> dict:
    """Run a training script with CodeCarbon tracking."""
    try:
        from codecarbon import EmissionsTracker
    except ImportError:
        print("codecarbon not installed. Install with: pip install codecarbon")
        print("Generating estimated report instead...")
        return estimate_emissions("training", 3600)

    tracker = EmissionsTracker(
        project_name="ura-chatbot-training",
        output_dir="Results/carbon",
        log_level="warning",
        save_to_file=True,
    )

    tracker.start()
    try:
        result = subprocess.run(
            [sys.executable, script] + args,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        tracker.stop()
        raise
    emissions = tracker.stop()

    return {
        "task": "training",
        "script": script,
        "emissions_kg_co2": round(emissions, 6),
        "duration_s": round(tracker._total_time.total_seconds(), 1),
        "energy_kwh": round(tracker._total_energy.kWh, 6),
        "country": tracker._country_name or "unknown",
        "region": tracker._region or "unknown",
        "cpu_model": tracker._cpu_model or "unknown",
        "gpu_model": tracker._gpu_model or "none",
    }


def estimate_emissions(task: str, duration_s: float) -> dict:
    """Estimate emissions when CodeCarbon is not available.

    Uses conservative estimates based on typical cloud GPU usage.
    """
    # Average estimates for ML workloads (2026 data)
    # CPU-only inference: ~0.05 kW, GPU training: ~0.3 kW
    power_kw = 0.3 if task == "training" else 0.05
    energy_kwh = power_kw * (duration_s / 3600)

    # Global average carbon intensity: ~0.475 kgCO2/kWh (2024)
    # Uganda grid: ~0.03 kgCO2/kWh (mostly hydro — Lake Victoria)
    carbon_intensity = 0.03  # Uganda-specific (hydroelectric)
    emissions = energy_kwh * carbon_intensity

    return {
        "task": task,
        "emissions_kg_co2": round(emissions, 6),
        "duration_s": duration_s,
        "energy_kwh": round(energy_kwh, 6),
        "carbon_intensity_kgco2_kwh": carbon_intensity,
        "note": "Estimated (codecarbon not available). Uganda grid is ~97% renewable (hydro).",
        "country": "Uganda",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Carbon Footprint Tracker")
    parser.add_argument("--task", choices=["training", "inference"], required=True)
    parser.add_argument("--script", help="Training script to run")
    parser.add_argument("--duration", type=float, default=3600, help="Estimation duration (seconds)")
    parser.add_argument("--output", default="Results/carbon/emissions_report.json")
    args = parser.parse_args()

    print(f"Carbon tracking: task={args.task}")

    if args.task == "training" and args.script:
        report = track_training(args.script, [])
    else:
        report = estimate_emissions(args.task, args.duration)

    report["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Context: compare to everyday activities
    kg_co2 = report["emissions_kg_co2"]
    report["equivalents"] = {
        "km_driving": round(kg_co2 / 0.21, 1),  # avg car: 0.21 kgCO2/km
        "hours_streaming": round(kg_co2 / 0.036, 1),  # Netflix: 36g/hr
        "cups_of_coffee": round(kg_co2 / 0.06, 1),  # ~60g CO2 per cup
    }

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2))

    print(f"\nEmissions: {kg_co2:.6f} kg CO2")
    print(f"Energy:    {report['energy_kwh']:.6f} kWh")
    print(f"Equivalent to: {report['equivalents']['km_driving']:.1f} km driving")
    print(f"Report:    {args.output}")


if __name__ == "__main__":
    main()
