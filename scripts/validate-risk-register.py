#!/usr/bin/env python3
"""Validate the threat model risk register and produce CI-friendly output.

Reads docs/security/threat-model/risk-register.yaml and:
  1. Validates schema (required fields, allowed values)
  2. Checks for duplicate IDs
  3. Checks accepted risks have review dates not in the past
  4. Produces a summary table for GitHub Step Summary
  5. Exits non-zero if critical open risks exceed threshold

Usage:
  python scripts/validate-risk-register.py [--fail-on-critical N] [--output-format summary|json]
"""

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("::error::PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REGISTER_PATH = Path("docs/security/threat-model/risk-register.yaml")

VALID_SEVERITIES = {"Critical", "High", "Medium", "Low"}
VALID_STATUSES = {"Open", "Accepted", "Mitigated", "Closed"}
VALID_FRAMEWORKS = {"STRIDE", "MAESTRO", "LINDDUN"}


def load_register(path: Path) -> list[dict]:
    if not path.exists():
        print(f"::error::Risk register not found: {path}")
        sys.exit(1)
    with open(path) as f:
        data = yaml.safe_load(f)
    return data.get("risks", [])


def validate(risks: list[dict]) -> list[str]:
    errors = []
    ids_seen = set()

    for i, r in enumerate(risks):
        loc = f"risk[{i}] ({r.get('id', 'NO_ID')})"

        # Required fields
        for field in ("id", "element", "framework", "threat", "severity", "status"):
            if field not in r:
                errors.append(f"{loc}: missing required field '{field}'")

        rid = r.get("id", "")
        if rid in ids_seen:
            errors.append(f"{loc}: duplicate ID '{rid}'")
        ids_seen.add(rid)

        sev = r.get("severity", "")
        if sev not in VALID_SEVERITIES:
            errors.append(f"{loc}: invalid severity '{sev}' (must be one of {VALID_SEVERITIES})")

        status = r.get("status", "")
        if status not in VALID_STATUSES:
            errors.append(f"{loc}: invalid status '{status}' (must be one of {VALID_STATUSES})")

        fw = r.get("framework", "")
        if fw not in VALID_FRAMEWORKS:
            errors.append(f"{loc}: invalid framework '{fw}' (must be one of {VALID_FRAMEWORKS})")

        # Accepted risks must have review_date
        if status == "Accepted":
            if "review_date" not in r:
                errors.append(f"{loc}: accepted risk missing 'review_date'")
            if "accepted_by" not in r:
                errors.append(f"{loc}: accepted risk missing 'accepted_by'")

    return errors


def check_expired_reviews(risks: list[dict]) -> list[str]:
    warnings = []
    today = date.today()
    for r in risks:
        if r.get("status") == "Accepted" and "review_date" in r:
            rd = r["review_date"]
            if isinstance(rd, str):
                rd = date.fromisoformat(rd)
            elif isinstance(rd, datetime):
                rd = rd.date()
            if rd < today:
                warnings.append(
                    f"Accepted risk '{r['id']}' has expired review date: {r['review_date']}"
                )
    return warnings


def summarize(risks: list[dict]) -> dict:
    by_severity = Counter(r["severity"] for r in risks)
    by_status = Counter(r["status"] for r in risks)
    by_framework = Counter(r["framework"] for r in risks)
    open_critical = sum(
        1 for r in risks if r["severity"] == "Critical" and r["status"] == "Open"
    )
    open_high = sum(
        1 for r in risks if r["severity"] == "High" and r["status"] == "Open"
    )
    return {
        "total": len(risks),
        "by_severity": dict(by_severity),
        "by_status": dict(by_status),
        "by_framework": dict(by_framework),
        "open_critical": open_critical,
        "open_high": open_high,
    }


def write_github_summary(summary: dict, risks: list[dict], warnings: list[str]):
    """Write markdown to GITHUB_STEP_SUMMARY."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_file:
        return

    lines = [
        "## Risk Register Summary",
        "",
        f"**Total risks: {summary['total']}**",
        "",
        "### By Severity",
        "",
        "| Severity | Count | Open |",
        "|----------|------:|-----:|",
    ]
    for sev in ("Critical", "High", "Medium", "Low"):
        total = summary["by_severity"].get(sev, 0)
        open_count = sum(
            1 for r in risks if r["severity"] == sev and r["status"] == "Open"
        )
        marker = " :red_circle:" if sev == "Critical" and open_count > 0 else ""
        lines.append(f"| {sev}{marker} | {total} | {open_count} |")

    lines += [
        "",
        "### By Framework",
        "",
        "| Framework | Count |",
        "|-----------|------:|",
    ]
    for fw in ("STRIDE", "MAESTRO", "LINDDUN"):
        lines.append(f"| {fw} | {summary['by_framework'].get(fw, 0)} |")

    lines += [
        "",
        "### By Status",
        "",
        "| Status | Count |",
        "|--------|------:|",
    ]
    for st in ("Open", "Accepted", "Mitigated", "Closed"):
        count = summary["by_status"].get(st, 0)
        if count > 0:
            lines.append(f"| {st} | {count} |")

    if warnings:
        lines += [
            "",
            "### :warning: Expired Review Dates",
            "",
        ]
        for w in warnings:
            lines.append(f"- {w}")

    open_criticals = [r for r in risks if r["severity"] == "Critical" and r["status"] == "Open"]
    if open_criticals:
        lines += [
            "",
            "### :red_circle: Open Critical Risks",
            "",
            "| ID | Element | Threat | Target |",
            "|----|---------|--------|--------|",
        ]
        for r in open_criticals:
            lines.append(
                f"| {r['id']} | {r['element']} | {r['threat']} | {r.get('mitigation_target', 'TBD')} |"
            )

    with open(summary_file, "a") as f:
        f.write("\n".join(lines) + "\n")


def write_github_outputs(summary: dict):
    """Write outputs for subsequent workflow steps."""
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a") as f:
        f.write(f"total={summary['total']}\n")
        f.write(f"open_critical={summary['open_critical']}\n")
        f.write(f"open_high={summary['open_high']}\n")


def main():
    parser = argparse.ArgumentParser(description="Validate threat model risk register")
    parser.add_argument(
        "--fail-on-critical",
        type=int,
        default=0,
        help="Exit non-zero if open critical risks exceed this threshold (default: 0 = gate check)",
    )
    parser.add_argument(
        "--output-format",
        choices=["summary", "json"],
        default="summary",
        help="Output format",
    )
    parser.add_argument(
        "--register",
        type=Path,
        default=REGISTER_PATH,
        help="Path to risk register YAML",
    )
    args = parser.parse_args()

    risks = load_register(args.register)
    errors = validate(risks)

    if errors:
        for e in errors:
            print(f"::error::{e}")
        print(f"\n{len(errors)} validation error(s) found.")
        sys.exit(1)

    warnings = check_expired_reviews(risks)
    for w in warnings:
        print(f"::warning::{w}")

    summary = summarize(risks)

    if args.output_format == "json":
        print(json.dumps(summary, indent=2))
    else:
        print("=" * 60)
        print("THREAT MODEL RISK REGISTER SUMMARY")
        print("=" * 60)
        print(f"Total threats: {summary['total']}")
        print(f"  Critical: {summary['by_severity'].get('Critical', 0)} ({summary['open_critical']} open)")
        print(f"  High:     {summary['by_severity'].get('High', 0)} ({summary['open_high']} open)")
        print(f"  Medium:   {summary['by_severity'].get('Medium', 0)}")
        print(f"  Low:      {summary['by_severity'].get('Low', 0)}")
        print(f"  Mitigated:{summary['by_status'].get('Mitigated', 0)}")
        print(f"  Accepted: {summary['by_status'].get('Accepted', 0)}")
        print(f"  Frameworks: {', '.join(f'{k}={v}' for k, v in summary['by_framework'].items())}")
        print("=" * 60)

    write_github_summary(summary, risks, warnings)
    write_github_outputs(summary)

    if args.fail_on_critical and summary["open_critical"] > args.fail_on_critical:
        print(
            f"\n::error::Open critical risks ({summary['open_critical']}) "
            f"exceed threshold ({args.fail_on_critical})"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
