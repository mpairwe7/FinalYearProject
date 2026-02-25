"""
Quality Gates Pipeline
Validates model meets minimum quality thresholds before deployment
"""

import argparse
import json
import sys
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_config(config_path: str) -> dict:
    with open(config_path, 'r') as f:
        return yaml.safe_load(f)


def check_quality_gates(metrics: dict, gates: dict) -> dict:
    """Check if model metrics meet quality gates."""
    results = {
        'passed': True,
        'checks': []
    }
    
    checks = [
        ('accuracy', 'test_accuracy', gates.get('min_accuracy', 0.85)),
        ('f1_score', 'test_f1_macro', gates.get('min_f1_score', 0.75)),
        ('precision', 'test_precision', gates.get('min_precision', 0.75)),
        ('recall', 'test_recall', gates.get('min_recall', 0.70)),
    ]
    
    for name, metric_key, threshold in checks:
        value = metrics.get(metric_key, 0)
        passed = value >= threshold
        
        results['checks'].append({
            'name': name,
            'value': value,
            'threshold': threshold,
            'passed': passed
        })
        
        if not passed:
            results['passed'] = False
    
    # Latency check (if available)
    if 'latency' in metrics:
        latency = metrics['latency'].get('p95_ms', 0)
        max_latency = gates.get('max_latency_ms', 100)
        passed = latency <= max_latency
        
        results['checks'].append({
            'name': 'latency_p95',
            'value': latency,
            'threshold': max_latency,
            'passed': passed
        })
        
        if not passed:
            results['passed'] = False
    
    return results


def main():
    parser = argparse.ArgumentParser(description='Check model quality gates')
    parser.add_argument('--metrics-path', type=str, default='Results/metrics/training_metrics.json')
    parser.add_argument('--config', type=str, default='ml/configs/training_config.yaml')
    args = parser.parse_args()
    
    print("=" * 60)
    print("QUALITY GATES CHECK")
    print("=" * 60)
    
    # Load metrics
    metrics_path = PROJECT_ROOT / args.metrics_path
    if not metrics_path.exists():
        print(f"ERROR: Metrics file not found: {metrics_path}")
        sys.exit(1)
    
    with open(metrics_path, 'r') as f:
        metrics = json.load(f)
    
    # Load config
    config = load_config(PROJECT_ROOT / args.config)
    gates = config.get('quality_gates', {})
    
    # Check gates
    results = check_quality_gates(metrics, gates)
    
    # Print results
    print("\nQuality Gate Results:")
    print("-" * 40)
    
    for check in results['checks']:
        status = "✓ PASS" if check['passed'] else "✗ FAIL"
        print(f"  {check['name']:15} {check['value']:.4f} >= {check['threshold']:.4f}  {status}")
    
    print("-" * 40)
    
    if results['passed']:
        print("✅ All quality gates PASSED")
        sys.exit(0)
    else:
        print("❌ Quality gates FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
