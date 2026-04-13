"""
Model Evaluation Pipeline
Evaluates trained models and generates reports
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import joblib
import matplotlib.pyplot as plt
import numpy as np
import seaborn as sns
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def load_model(model_path: Path) -> tuple:
    """Load trained model and label encoder."""
    clf = joblib.load(model_path / "tag_classifier.joblib")
    encoder = joblib.load(model_path / "label_encoder.joblib")
    return clf, encoder


def evaluate_classification(
    clf, X_test: np.ndarray, y_test: np.ndarray, label_encoder
) -> dict[str, Any]:
    """Run comprehensive model evaluation."""
    y_pred = clf.predict(X_test)

    metrics = {
        "accuracy": accuracy_score(y_test, y_pred),
        "f1_macro": f1_score(y_test, y_pred, average="macro", zero_division=0),
        "f1_weighted": f1_score(y_test, y_pred, average="weighted", zero_division=0),
        "precision_macro": precision_score(y_test, y_pred, average="macro", zero_division=0),
        "recall_macro": recall_score(y_test, y_pred, average="macro", zero_division=0),
    }

    # Per-class metrics
    report = classification_report(
        y_test, y_pred, target_names=label_encoder.classes_, output_dict=True, zero_division=0
    )

    metrics["classification_report"] = report

    # Confusion matrix
    cm = confusion_matrix(y_test, y_pred, normalize="true")
    metrics["confusion_matrix"] = cm.tolist()

    return metrics


def benchmark_latency(clf, X_sample: np.ndarray, n_runs: int = 100) -> dict[str, float]:
    """Benchmark inference latency."""
    import time

    # Warmup
    for _ in range(10):
        clf.predict(X_sample[:1])

    # Single sample latency
    latencies = []
    for _ in range(n_runs):
        start = time.perf_counter()
        clf.predict(X_sample[:1])
        latencies.append((time.perf_counter() - start) * 1000)

    lat = np.array(latencies)

    return {
        "mean_ms": float(lat.mean()),
        "std_ms": float(lat.std()),
        "p50_ms": float(np.percentile(lat, 50)),
        "p95_ms": float(np.percentile(lat, 95)),
        "p99_ms": float(np.percentile(lat, 99)),
    }


def generate_plots(metrics: dict, output_dir: Path, label_encoder) -> None:
    """Generate evaluation visualizations."""
    sns.set_theme(style="whitegrid")

    # Confusion matrix heatmap
    fig, ax = plt.subplots(figsize=(10, 8))
    cm = np.array(metrics["confusion_matrix"])

    # Limit to top classes if too many
    n_classes = len(label_encoder.classes_)
    if n_classes > 15:
        # Show subset
        labels = [c[:15] for c in label_encoder.classes_[:15]]
        cm = cm[:15, :15]
    else:
        labels = [c[:15] for c in label_encoder.classes_]

    sns.heatmap(
        cm, annot=True, fmt=".2f", cmap="Blues", xticklabels=labels, yticklabels=labels, ax=ax
    )
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title("Normalized Confusion Matrix")
    plt.xticks(rotation=45, ha="right")
    plt.tight_layout()
    plt.savefig(output_dir / "confusion_matrix.png", dpi=150)
    plt.close()

    # Metrics bar chart
    fig, ax = plt.subplots(figsize=(8, 5))
    metric_names = ["Accuracy", "F1 (macro)", "Precision", "Recall"]
    metric_values = [
        metrics["accuracy"],
        metrics["f1_macro"],
        metrics["precision_macro"],
        metrics["recall_macro"],
    ]

    bars = ax.bar(metric_names, metric_values, color=["#2E86AB", "#A23B72", "#F18F01", "#C73E1D"])
    ax.bar_label(bars, fmt="%.3f")
    ax.set_ylim(0, 1.1)
    ax.set_ylabel("Score")
    ax.set_title("Model Performance Metrics")
    plt.tight_layout()
    plt.savefig(output_dir / "metrics_summary.png", dpi=150)
    plt.close()


def main():
    parser = argparse.ArgumentParser(description="Evaluate trained model")
    parser.add_argument("--model-path", type=str, default="Model")
    parser.add_argument("--output-dir", type=str, default="Results")
    args = parser.parse_args()

    print("=" * 60)
    print("MODEL EVALUATION PIPELINE")
    print("=" * 60)

    model_path = Path(args.model_path)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load model
    print("\n[1/4] Loading model...")
    clf, label_encoder = load_model(model_path)
    print(f"  Classes: {len(label_encoder.classes_)}")

    # Load test data (recreate from datasets)
    print("\n[2/4] Loading test data...")
    from ml.pipelines.train import create_embeddings, load_config, load_qa_data

    config = load_config(PROJECT_ROOT / "ml/configs/training_config.yaml")
    qa_df = load_qa_data(PROJECT_ROOT / "Data", config)

    supervised_df = qa_df[["question", "context", "tag"]].copy()
    supervised_df["text"] = supervised_df["question"] + " [SEP] " + supervised_df["context"]

    y = label_encoder.transform(supervised_df["tag"])
    X = create_embeddings(supervised_df["text"].tolist(), config["models"]["embedding"]["name"])

    # Split for evaluation
    from sklearn.model_selection import train_test_split

    _, X_test, _, y_test = train_test_split(
        X,
        y,
        test_size=config["data"]["test_size"],
        stratify=y,
        random_state=config["data"]["random_seed"],
    )

    print(f"  Test samples: {len(X_test)}")

    # Evaluate
    print("\n[3/4] Evaluating model...")
    metrics = evaluate_classification(clf, X_test, y_test, label_encoder)

    print(f"  Accuracy: {metrics['accuracy']:.4f}")
    print(f"  F1-macro: {metrics['f1_macro']:.4f}")
    print(f"  Precision: {metrics['precision_macro']:.4f}")
    print(f"  Recall: {metrics['recall_macro']:.4f}")

    # Latency benchmark
    print("\n[4/4] Benchmarking latency...")
    latency = benchmark_latency(clf, X_test)
    metrics["latency"] = latency

    print(f"  Mean latency: {latency['mean_ms']:.3f} ms")
    print(f"  P95 latency: {latency['p95_ms']:.3f} ms")

    # Generate plots
    generate_plots(metrics, output_dir, label_encoder)

    # Save results
    # Remove non-serializable items
    metrics_export = {k: v for k, v in metrics.items() if k != "confusion_matrix"}
    metrics_export["confusion_matrix_shape"] = [
        len(metrics["confusion_matrix"]),
        len(metrics["confusion_matrix"][0]),
    ]

    with open(output_dir / "evaluation_results.json", "w") as f:
        json.dump(metrics_export, f, indent=2, default=str)

    print(f"\n✓ Results saved to: {output_dir}")


if __name__ == "__main__":
    main()
