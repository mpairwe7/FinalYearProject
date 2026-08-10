"""
Data Validation Pipeline
Validates data quality using Great Expectations patterns
"""

import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def validate_csv_files(datasets_dir: Path) -> dict[str, Any]:
    """Validate all CSV files in the datasets directory."""
    results = {
        "valid": True,
        "files_checked": 0,
        "files_passed": 0,
        "files_failed": 0,
        "errors": [],
        "warnings": [],
        "details": [],
    }

    csv_files = list(datasets_dir.glob("*.csv"))
    results["files_checked"] = len(csv_files)

    for csv_path in csv_files:
        file_result = validate_single_csv(csv_path)
        results["details"].append(file_result)

        if file_result["passed"]:
            results["files_passed"] += 1
        else:
            results["files_failed"] += 1
            results["errors"].extend(file_result["errors"])

        results["warnings"].extend(file_result.get("warnings", []))

    results["valid"] = results["files_failed"] == 0
    return results


def validate_single_csv(csv_path: Path) -> dict[str, Any]:
    """Validate a single CSV file."""
    result = {"file": csv_path.name, "passed": True, "errors": [], "warnings": [], "stats": {}}

    try:
        df = pd.read_csv(csv_path)

        # Basic stats
        result["stats"] = {
            "rows": len(df),
            "columns": len(df.columns),
            "column_names": list(df.columns),
            "missing_percentage": df.isna().mean().to_dict(),
        }

        # Validation checks

        # 1. Check minimum rows
        if len(df) < 5:
            result["warnings"].append(f"Low row count: {len(df)} rows")

        # 2. Check for required columns (question/answer variants)
        question_cols = {"question", "questions", "q"}
        answer_cols = {"answer", "answers", "a", "response"}

        cols_lower = {c.lower() for c in df.columns}
        has_question = bool(question_cols & cols_lower)
        has_answer = bool(answer_cols & cols_lower)

        if not has_question and not has_answer and len(df.columns) < 2:
            result["errors"].append("CSV must have at least 2 columns (Q&A)")
            result["passed"] = False

        # 3. Check for empty values
        empty_ratio = df.isna().mean().max()
        if empty_ratio > 0.5:
            result["errors"].append(f"High missing ratio: {empty_ratio:.2%}")
            result["passed"] = False
        elif empty_ratio > 0.2:
            result["warnings"].append(f"Moderate missing ratio: {empty_ratio:.2%}")

        # 4. Check for duplicates
        dup_ratio = df.duplicated().mean()
        if dup_ratio > 0.3:
            result["warnings"].append(f"High duplicate ratio: {dup_ratio:.2%}")

        # 5. Check text lengths
        for col in df.columns:
            if df[col].dtype == "object":
                lengths = df[col].dropna().str.len()
                if lengths.mean() < 10:
                    result["warnings"].append(
                        f"Column '{col}' has very short text (avg {lengths.mean():.0f} chars)"
                    )

    except Exception as e:
        result["passed"] = False
        result["errors"].append(f"Failed to read CSV: {str(e)}")

    return result


def validate_pdf_files(pdf_dir: Path) -> dict[str, Any]:
    """Validate PDF files exist and are readable."""
    results = {
        "valid": True,
        "files_checked": 0,
        "files_passed": 0,
        "files_failed": 0,
        "errors": [],
        "details": [],
    }

    pdf_files = list(pdf_dir.glob("*.pdf"))
    results["files_checked"] = len(pdf_files)

    for pdf_path in pdf_files:
        try:
            # Just check file size as a basic validation
            size_mb = pdf_path.stat().st_size / (1024 * 1024)
            results["details"].append(
                {"file": pdf_path.name, "size_mb": round(size_mb, 2), "passed": True}
            )
            results["files_passed"] += 1
        except Exception as e:
            results["details"].append({"file": pdf_path.name, "error": str(e), "passed": False})
            results["files_failed"] += 1
            results["errors"].append(f"{pdf_path.name}: {str(e)}")

    results["valid"] = results["files_failed"] == 0
    return results


def main():
    """Run data validation and generate report."""
    print("=" * 60)
    print("DATA VALIDATION PIPELINE")
    print("=" * 60)

    datasets_dir = PROJECT_ROOT / "Data" / "dataset"
    pdf_dir = PROJECT_ROOT / "Data" / "pdfs"
    output_dir = PROJECT_ROOT / "Results" / "reports"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Run validations
    csv_results = validate_csv_files(datasets_dir)
    pdf_results = validate_pdf_files(pdf_dir)

    # Combine results
    report = {
        "csv_validation": csv_results,
        "pdf_validation": pdf_results,
        "overall_valid": csv_results["valid"] and pdf_results["valid"],
        "summary": {
            "csv_files_checked": csv_results["files_checked"],
            "csv_files_passed": csv_results["files_passed"],
            "pdf_files_checked": pdf_results["files_checked"],
            "pdf_files_passed": pdf_results["files_passed"],
        },
    }

    # Save report
    report_path = output_dir / "data_validation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # Print summary
    print(f"\n✓ CSV Files: {csv_results['files_passed']}/{csv_results['files_checked']} passed")
    print(f"✓ PDF Files: {pdf_results['files_passed']}/{pdf_results['files_checked']} passed")
    print(f"\nReport saved to: {report_path}")

    if csv_results["errors"]:
        print("\n⚠ CSV Errors:")
        for err in csv_results["errors"][:5]:
            print(f"  - {err}")

    if csv_results["warnings"]:
        print("\n⚠ Warnings:")
        for warn in csv_results["warnings"][:5]:
            print(f"  - {warn}")

    # Exit with error if validation failed
    if not report["overall_valid"]:
        print("\n❌ Data validation FAILED")
        sys.exit(1)
    else:
        print("\n✅ Data validation PASSED")
        sys.exit(0)


if __name__ == "__main__":
    main()
