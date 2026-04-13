"""
Process Kaggle Output Script
Processes and validates model outputs from Kaggle training.

Features:
  - SHA256 checksum generation for all artifacts (in manifest.json)
  - Optional summary output for GitHub step summary integration
  - Structured validation with clear pass/fail reporting
"""

import argparse
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _sha256(filepath: Path) -> str:
    """Compute SHA256 hex digest of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def process_kaggle_output(
    input_dir: str,
    output_dir: str,
    summary_output: str | None = None,
) -> None:
    """
    Process Kaggle kernel output and prepare for deployment.

    Args:
        input_dir: Directory containing Kaggle outputs
        output_dir: Target directory for processed artifacts
        summary_output: Optional path to write a markdown summary
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("PROCESSING KAGGLE OUTPUT")
    print("=" * 60)

    # Expected output files from Kaggle
    expected_files = [
        "tag_classifier.joblib",
        "label_encoder.joblib",
        "tag_classifier.pth",
        "tag_classifier.onnx",
    ]

    found_files = []
    missing_files = []

    # Search for model files
    for expected in expected_files:
        matches = list(input_path.rglob(expected))
        if matches:
            found_files.append((expected, matches[0]))
        else:
            missing_files.append(expected)

    print(f"\nFound {len(found_files)}/{len(expected_files)} expected files:")
    for name, _path in found_files:
        print(f"  + {name}")

    if missing_files:
        print("\nMissing files:")
        for name in missing_files:
            print(f"  - {name}")

    # Copy found files to output and compute checksums
    print(f"\nCopying artifacts to {output_path}...")
    checksums = {}
    for name, src_path in found_files:
        dst_path = output_path / name
        shutil.copy(src_path, dst_path)
        size_kb = dst_path.stat().st_size / 1024
        checksum = _sha256(dst_path)
        checksums[name] = checksum
        print(f"  {name}: {size_kb:.2f} KB (sha256: {checksum[:16]}...)")

    # Look for metrics files
    metrics_files = list(input_path.rglob("*metrics*.json"))
    if metrics_files:
        metrics_dir = output_path.parent / "metrics"
        metrics_dir.mkdir(exist_ok=True)
        for mf in metrics_files:
            dst = metrics_dir / mf.name
            shutil.copy(mf, dst)
            checksums[f"metrics/{mf.name}"] = _sha256(dst)
            print(f"  Metrics: {mf.name}")

    # Look for class labels
    label_files = list(input_path.rglob("class_labels.json"))
    if label_files:
        dst = output_path / "class_labels.json"
        shutil.copy(label_files[0], dst)
        checksums["class_labels.json"] = _sha256(dst)
        print("  Labels: class_labels.json")

    # Validate outputs
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)

    required = ["tag_classifier.joblib", "label_encoder.joblib"]
    all_required = all((output_path / f).exists() for f in required)
    validation_status = "passed" if all_required else "partial"
    classes_count = 0

    if all_required:
        print("Required model files present")
        try:
            import joblib

            joblib.load(output_path / "tag_classifier.joblib")
            encoder = joblib.load(output_path / "label_encoder.joblib")
            classes_count = len(encoder.classes_)
            print(f"Model loaded successfully — {classes_count} classes")
        except Exception as e:
            print(f"Model load test failed: {e}")
            validation_status = "load_failed"
    else:
        print("Some required model files missing, continuing anyway...")

    # Generate manifest with checksums
    manifest = {
        "files": [f for f, _ in found_files],
        "checksums": checksums,
        "output_dir": str(output_path),
        "validation": validation_status,
        "classes_count": classes_count,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    manifest_path = output_path / "manifest.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Summary of all files found
    print("\n" + "=" * 60)
    print("ALL KAGGLE OUTPUT FILES")
    print("=" * 60)
    all_files = list(input_path.rglob("*"))
    for fp in sorted(all_files):
        if fp.is_file():
            size_kb = fp.stat().st_size / 1024
            print(f"  {fp.relative_to(input_path)} ({size_kb:.1f} KB)")

    print(f"\nProcessing complete. Artifacts in: {output_path}")

    # Write markdown summary if requested
    if summary_output:
        summary_path = Path(summary_output)
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "## Kaggle Output Processing",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Expected files | {len(expected_files)} |",
            f"| Found files | {len(found_files)} |",
            f"| Missing files | {len(missing_files)} |",
            f"| Validation | {validation_status} |",
            f"| Classes | {classes_count} |",
            "",
        ]
        if checksums:
            lines.extend(
                [
                    "### Artifact Checksums",
                    "",
                    "| File | SHA256 (prefix) |",
                    "|------|-----------------|",
                ]
            )
            for name, sha in checksums.items():
                lines.append(f"| `{name}` | `{sha[:16]}...` |")

        with open(summary_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        print(f"Summary written to {summary_path}")


def main():
    parser = argparse.ArgumentParser(description="Process Kaggle kernel output")
    parser.add_argument(
        "--input-dir",
        type=str,
        required=True,
        help="Directory containing Kaggle outputs",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="artifacts/models",
        help="Target directory for processed artifacts",
    )
    parser.add_argument(
        "--summary-output",
        type=str,
        default=None,
        help="Path to write a markdown summary file",
    )
    args = parser.parse_args()

    process_kaggle_output(args.input_dir, args.output_dir, args.summary_output)


if __name__ == "__main__":
    main()
