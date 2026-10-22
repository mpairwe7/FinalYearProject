"""Fail CI when a dependency advisory appears that was not there before.

Issue #299. `pip-audit` already runs on both requirements files and already
writes a readable step summary — and then ends in `|| true`, so a pull
request that adds a vulnerable package is reported and merged anyway. The
repo currently carries 79 open advisories, so gating on "zero findings"
would fail every build from now until they are all cleared, which in
practice means the gate gets removed rather than the advisories fixed.

This gates on *change* instead. A baseline records the advisory IDs known at
the time it was written; the build fails only on IDs that are not in it.
Fixing something is always free, adding something is not, and the count can
only go down without a deliberate, reviewable commit to the baseline.

Comparing IDs rather than counts matters: a PR that fixes one advisory and
introduces another leaves the count unchanged, and a count-based gate would
wave it through.

A report that is missing, unreadable or scanned nothing fails the gate. When
pip-audit cannot resolve a requirements file it writes no report, and the
scan step ends in `|| true`. Treating that as "no new advisories" let the
backend audit pass for weeks while it was checking nothing: from #633 on,
its file needed Python 3.12 and the job ran 3.11.

Usage
-----
    # gate (CI)
    python scripts/check_dependency_advisories.py --audit pip-audit-*.json

    # accept the current state, e.g. after remediation
    python scripts/check_dependency_advisories.py --audit pip-audit-*.json --update
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = REPO_ROOT / ".security" / "dependency-advisories.json"


def advisories_from_audit(paths: list[Path]) -> dict[str, dict[str, str]]:
    """Map advisory id -> {package, version} across every pip-audit report."""
    found: dict[str, dict[str, str]] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            report: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"::warning::{path} is not valid JSON; skipping", file=sys.stderr)
            continue
        for dep in report.get("dependencies", []) or []:
            for vuln in dep.get("vulns", []) or []:
                vuln_id = str(vuln.get("id") or "").strip()
                if vuln_id:
                    found[vuln_id] = {
                        "package": str(dep.get("name") or "?"),
                        "version": str(dep.get("version") or "?"),
                    }
    return found


def unusable_reports(paths: list[Path]) -> list[str]:
    """Why each report cannot be trusted as a scan, if it cannot."""
    problems: list[str] = []
    for path in paths:
        if not path.is_file():
            problems.append(f"{path}: no report (pip-audit could not resolve the requirements?)")
            continue
        try:
            report: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            problems.append(f"{path}: not valid JSON")
            continue
        if not isinstance(report, dict) or not report.get("dependencies"):
            problems.append(f"{path}: scanned no dependencies")
    return problems


def load_baseline(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("advisories", {}) or {}


def write_baseline(path: Path, advisories: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_comment": (
            "Advisory IDs known to CI. The build fails on IDs absent from this "
            "file. Removing an entry is free; adding one should be a reviewed "
            "decision with an owner. Regenerate with --update."
        ),
        "advisories": dict(sorted(advisories.items())),
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", nargs="+", required=True, help="pip-audit JSON report(s)")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument(
        "--update", action="store_true", help="Rewrite the baseline from the current reports"
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    reports = [Path(p) for p in args.audit]
    # An empty scan is not a pass, and must never become a baseline.
    problems = unusable_reports(reports)
    if problems:
        for problem in problems:
            print(f"::error::{problem}")
        return 2
    current = advisories_from_audit(reports)

    if args.update:
        write_baseline(baseline_path, current)
        print(f"baseline updated: {len(current)} advisories -> {baseline_path}")
        return 0

    known = load_baseline(baseline_path)
    if not known and not baseline_path.is_file():
        # Refuse to pass silently with no baseline: that is indistinguishable
        # from a gate that is switched off.
        print(f"::error::no baseline at {baseline_path}; run with --update to create one")
        return 2

    new = sorted(set(current) - set(known))
    # Deliberately not called "resolved": CI runs this once per requirements
    # file while the baseline spans all of them, so an ID missing from *this*
    # report is usually just out of scope rather than fixed. Only --update,
    # which sees every report at once, can actually retire an entry.
    absent = sorted(set(known) - set(current))

    if not new:
        print(
            f"no new advisories — {len(current)} of {len(known)} baseline IDs "
            f"present in this report, {len(absent)} not in scope or fixed"
        )
        return 0

    print(f"::error::{len(new)} new dependency advisor{'y' if len(new) == 1 else 'ies'}:")
    for vuln_id in new:
        meta = current[vuln_id]
        print(f"::error::  {vuln_id} — {meta.get('package')}=={meta.get('version')}")
    print(
        "::error::Upgrade the package, or accept the risk deliberately by adding "
        "the ID via: python scripts/check_dependency_advisories.py --audit <reports> --update"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
