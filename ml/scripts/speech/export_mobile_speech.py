#!/usr/bin/env python3
"""Mobile speech bundle exporter (2026).

Mirrors the design of ``ml/scripts/export_mobile.py`` (the existing LLM
GGUF exporter) for the ASR + MT + TTS stack. Consumes the manifest at
``ml/configs/mobile_bundle.yaml`` and atomically copies every component
into the Android + iOS asset trees under ``MobileApp/ura_chatbot/``.

Pipeline::

    1. Load ``mobile_bundle.yaml``.
    2. For each component (ASR / TTS / MT):
         - verify the artifact exists locally
         - verify the license is in the commercial-safe allowlist
         - compute SHA-256 for each file
         - validate against size_mb_budget
    3. Validate total-bundle size vs ``bundle.total_size_mb_budget``.
    4. Atomically copy into MobileApp asset dirs (write to ``.tmp`` then rename).
    5. Write ``BUNDLE_CARD.md`` describing everything that was shipped.

Usage::

    python -m ml.scripts.speech.export_mobile_speech --dry-run
    python -m ml.scripts.speech.export_mobile_speech          # real run
    python -m ml.scripts.speech.export_mobile_speech --no-deploy  # skip copy

Commercial posture: the exporter refuses to deploy any component whose
license is not in ``license_allowlist`` from the bundle manifest. This
is the canonical enforcement point for the project's "no CC-BY-NC"
policy.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("speech.export_mobile")

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = PROJECT_ROOT / "ml" / "configs" / "mobile_bundle.yaml"
MOBILE_ROOT = PROJECT_ROOT / "MobileApp" / "ura_chatbot"
ANDROID_ROOT = MOBILE_ROOT / "android" / "app" / "src" / "main" / "assets"
IOS_ROOT = MOBILE_ROOT / "ios" / "Runner"
OUT_DIR = PROJECT_ROOT / "artifacts" / "mobile_speech"

SCHEMA_VERSION = "2026.1"
PIPELINE_VERSION = "2026.1.0"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class ComponentReport:
    component_id: str
    family: str                     # asr | tts | mt | llm
    source_dir: str
    size_mb: float
    size_mb_budget: Optional[float]
    license: str
    files: list[dict]               # [{name, size_bytes, sha256}]
    ok: bool
    reason: Optional[str] = None


@dataclass
class BundleReport:
    schema_version: str
    bundle_version: str
    timestamp: str
    components: list[ComponentReport] = field(default_factory=list)
    total_size_mb: float = 0.0
    total_size_mb_budget: Optional[float] = None
    license_allowlist: list[str] = field(default_factory=list)
    ok: bool = False
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "bundle_version": self.bundle_version,
            "timestamp": self.timestamp,
            "total_size_mb": self.total_size_mb,
            "total_size_mb_budget": self.total_size_mb_budget,
            "ok": self.ok,
            "errors": self.errors,
            "components": [asdict(c) for c in self.components],
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML missing — pip install pyyaml") from exc
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _probe_component(
    family: str,
    spec: dict[str, Any],
    license_allowlist: set[str],
) -> ComponentReport:
    comp_id = spec.get("id") or spec.get("component") or spec.get("filename", family)
    license_name = spec.get("license", "unknown")
    source = PROJECT_ROOT / spec.get("source", "")
    report = ComponentReport(
        component_id=str(comp_id),
        family=family,
        source_dir=str(source),
        size_mb=0.0,
        size_mb_budget=spec.get("size_mb_budget"),
        license=license_name,
        files=[],
        ok=False,
    )

    if license_name not in license_allowlist:
        report.reason = (
            f"license {license_name!r} is not in the commercial-safe allowlist "
            f"{sorted(license_allowlist)!r}"
        )
        return report

    if spec.get("status") == "training_required":
        report.reason = f"component status=training_required (no artifact yet)"
        return report

    if not source.exists():
        report.reason = f"source missing: {source}"
        return report

    if source.is_file():
        files = [source]
    else:
        # Use 'files' allowlist if provided, else every file in the dir.
        allow_files = spec.get("files")
        if allow_files:
            files = [source / name for name in allow_files if (source / name).exists()]
        else:
            files = [p for p in sorted(source.rglob("*")) if p.is_file()]

    if not files:
        report.reason = f"no files found under {source}"
        return report

    total = 0
    for fp in files:
        size = fp.stat().st_size
        total += size
        report.files.append(
            {
                "name": str(fp.relative_to(source)) if source.is_dir() else fp.name,
                "size_bytes": size,
                "sha256": _sha256(fp),
            }
        )
    report.size_mb = round(total / 1024 / 1024, 2)

    if report.size_mb_budget and report.size_mb > report.size_mb_budget:
        report.reason = (
            f"size {report.size_mb} MB exceeds budget {report.size_mb_budget} MB"
        )
        return report

    report.ok = True
    return report


def _deploy_component(report: ComponentReport, dest_android: Path, dest_ios: Path) -> None:
    """Atomic copy: write to .tmp sibling, fsync, rename into place."""
    src = Path(report.source_dir)
    for d in (dest_android, dest_ios):
        d.mkdir(parents=True, exist_ok=True)
    for file_info in report.files:
        rel = file_info["name"]
        src_fp = src / rel if src.is_dir() else src
        for dest_root in (dest_android, dest_ios):
            dst_fp = dest_root / rel if src.is_dir() else dest_root / src.name
            dst_fp.parent.mkdir(parents=True, exist_ok=True)
            tmp_fp = dst_fp.with_suffix(dst_fp.suffix + ".tmp")
            shutil.copy2(src_fp, tmp_fp)
            tmp_fp.replace(dst_fp)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run(*, dry_run: bool, deploy: bool, manifest_path: Path) -> BundleReport:
    manifest = _load_manifest(manifest_path)
    allowlist = set(manifest.get("license_allowlist", []))

    report = BundleReport(
        schema_version=manifest.get("schema_version", SCHEMA_VERSION),
        bundle_version=manifest.get("bundle_version", PIPELINE_VERSION),
        timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        total_size_mb_budget=manifest.get("bundle", {}).get("total_size_mb_budget"),
        license_allowlist=sorted(allowlist),
    )

    # LLM (existing, single-file pipeline)
    llm_spec = manifest.get("llm")
    if llm_spec:
        report.components.append(_probe_component("llm", llm_spec, allowlist))

    # ASR / TTS / MT are multi-component families.
    for family in ("asr", "tts", "mt"):
        family_spec = manifest.get(family) or {}
        for comp in family_spec.get("components", []):
            report.components.append(_probe_component(family, comp, allowlist))

    total = sum(c.size_mb for c in report.components if c.ok)
    report.total_size_mb = round(total, 2)

    if report.total_size_mb_budget and report.total_size_mb > report.total_size_mb_budget:
        report.errors.append(
            f"total bundle {report.total_size_mb} MB exceeds budget "
            f"{report.total_size_mb_budget} MB"
        )

    failed = [c for c in report.components if not c.ok]
    if failed:
        for c in failed:
            report.errors.append(f"{c.family}:{c.component_id} failed — {c.reason}")

    report.ok = not report.errors

    if dry_run:
        log.info(
            "[dry-run] %d components probed, %d ok, %.2f MB total",
            len(report.components),
            sum(1 for c in report.components if c.ok),
            report.total_size_mb,
        )
        for c in report.components:
            state = "OK" if c.ok else f"FAIL: {c.reason}"
            log.info("  %-6s %-40s %s", c.family, c.component_id, state)
        return report

    if not deploy:
        log.info("--no-deploy: skipping asset copy")
        return report

    if not report.ok:
        log.error("bundle has errors — refusing to deploy")
        return report

    # Atomically deploy every successful component.
    for c in report.components:
        if not c.ok:
            continue
        family_spec = manifest.get(c.family) or {}
        android_dir = PROJECT_ROOT / family_spec.get(
            "android_asset_dir",
            f"MobileApp/ura_chatbot/android/app/src/main/assets/speech/{c.family}",
        )
        ios_dir = PROJECT_ROOT / family_spec.get(
            "ios_asset_dir",
            f"MobileApp/ura_chatbot/ios/Runner/speech/{c.family}",
        )
        # Each component gets its own subdirectory so multiple variants coexist.
        _deploy_component(
            c,
            android_dir / c.component_id,
            ios_dir / c.component_id,
        )
        log.info("deployed %s -> android+ios", c.component_id)

    # Emit bundle card
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    card_path = OUT_DIR / "BUNDLE_CARD.md"
    _write_bundle_card(report, card_path)
    (OUT_DIR / "bundle_report.json").write_text(
        json.dumps(report.summary(), indent=2) + "\n", encoding="utf-8"
    )
    log.info("bundle card: %s", card_path)
    return report


def _write_bundle_card(report: BundleReport, path: Path) -> None:
    lines = [
        "# URA Chatbot Mobile Speech Bundle",
        "",
        f"- Schema version: `{report.schema_version}`",
        f"- Bundle version: `{report.bundle_version}`",
        f"- Built: {report.timestamp}",
        f"- Total size: **{report.total_size_mb} MB** / budget {report.total_size_mb_budget} MB",
        f"- License allowlist: `{', '.join(report.license_allowlist)}`",
        "",
        "## Components",
        "",
        "| Family | Component | License | Size (MB) | Budget | Status |",
        "|---|---|---|---|---|---|",
    ]
    for c in report.components:
        status = "ok" if c.ok else f"fail — {c.reason}"
        budget = f"{c.size_mb_budget}" if c.size_mb_budget else "-"
        lines.append(
            f"| {c.family} | {c.component_id} | {c.license} | {c.size_mb} | {budget} | {status} |"
        )
    if report.errors:
        lines += ["", "## Errors"]
        for e in report.errors:
            lines.append(f"- {e}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Mobile speech bundle exporter")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=MANIFEST_PATH,
        help="Path to mobile_bundle.yaml",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--no-deploy", action="store_true", help="Probe + validate only; do not copy"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    report = run(
        dry_run=args.dry_run, deploy=not args.no_deploy, manifest_path=args.manifest
    )
    print(json.dumps(report.summary(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
