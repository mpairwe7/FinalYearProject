"""
Process Kaggle Output Script
Processes and validates model outputs from Kaggle training
"""

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent


def process_kaggle_output(input_dir: str, output_dir: str) -> None:
    """
    Process Kaggle kernel output and prepare for deployment.
    
    Args:
        input_dir: Directory containing Kaggle outputs
        output_dir: Target directory for processed artifacts
    """
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    print("=" * 60)
    print("PROCESSING KAGGLE OUTPUT")
    print("=" * 60)
    
    # Expected output files from Kaggle
    expected_files = [
        'tag_classifier.joblib',
        'label_encoder.joblib',
        'tag_classifier.pth',
        'tag_classifier.onnx',
    ]
    
    found_files = []
    missing_files = []
    
    # Search for model files
    for expected in expected_files:
        # Search recursively
        matches = list(input_path.rglob(expected))
        if matches:
            found_files.append((expected, matches[0]))
        else:
            missing_files.append(expected)
    
    print(f"\nFound {len(found_files)}/{len(expected_files)} expected files:")
    for name, path in found_files:
        print(f"  ✓ {name}")
    
    if missing_files:
        print(f"\nMissing files:")
        for name in missing_files:
            print(f"  ✗ {name}")
    
    # Copy found files to output
    print(f"\nCopying artifacts to {output_path}...")
    for name, src_path in found_files:
        dst_path = output_path / name
        shutil.copy(src_path, dst_path)
        size_kb = dst_path.stat().st_size / 1024
        print(f"  {name}: {size_kb:.2f} KB")
    
    # Look for metrics files
    metrics_files = list(input_path.rglob('*metrics*.json'))
    if metrics_files:
        metrics_dir = output_path.parent / 'metrics'
        metrics_dir.mkdir(exist_ok=True)
        for mf in metrics_files:
            shutil.copy(mf, metrics_dir / mf.name)
            print(f"  Metrics: {mf.name}")
    
    # Look for class labels
    label_files = list(input_path.rglob('class_labels.json'))
    if label_files:
        shutil.copy(label_files[0], output_path / 'class_labels.json')
        print(f"  Labels: class_labels.json")
    
    # Validate outputs
    print("\n" + "=" * 60)
    print("VALIDATION")
    print("=" * 60)
    
    # Check minimum required files
    required = ['tag_classifier.joblib', 'label_encoder.joblib']
    all_required = all((output_path / f).exists() for f in required)
    
    if all_required:
        print("✅ Required model files present")
        
        # Quick validation by loading
        try:
            import joblib
            clf = joblib.load(output_path / 'tag_classifier.joblib')
            encoder = joblib.load(output_path / 'label_encoder.joblib')
            print(f"✅ Model loaded successfully")
            print(f"   Classes: {len(encoder.classes_)}")
        except Exception as e:
            print(f"⚠️ Model load test failed: {e}")
    else:
        print("❌ Missing required model files!")
        sys.exit(1)
    
    # Generate manifest
    manifest = {
        'files': [f for f, _ in found_files],
        'output_dir': str(output_path),
        'validation': 'passed' if all_required else 'failed'
    }
    
    with open(output_path / 'manifest.json', 'w') as f:
        json.dump(manifest, f, indent=2)
    
    print(f"\n✓ Processing complete. Artifacts in: {output_path}")


def main():
    parser = argparse.ArgumentParser(description='Process Kaggle kernel output')
    parser.add_argument('--input-dir', type=str, required=True,
                        help='Directory containing Kaggle outputs')
    parser.add_argument('--output-dir', type=str, default='artifacts/models',
                        help='Target directory for processed artifacts')
    args = parser.parse_args()
    
    process_kaggle_output(args.input_dir, args.output_dir)


if __name__ == "__main__":
    main()
