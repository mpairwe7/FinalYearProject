# =============================================================================
# Results Directory
# Training Metrics and Evaluation Results
# =============================================================================

This directory stores training metrics, evaluation results, and visualization plots.

## Directory Structure

```
Results/
├── metrics/           # JSON metric files
│   ├── training_metrics.json
│   ├── evaluation_results.json
│   └── cv_results.json
├── plots/             # Visualization PNG files
│   ├── cv_metrics.png
│   ├── confusion_matrix.png
│   ├── evaluation_metrics.png
│   ├── fig2_pipeline_analysis.png
│   └── fig3_model_comparison.png
└── reports/           # Generated reports
    ├── data_validation_report.json
    └── performance_summary.csv
```

## Metrics Files

### training_metrics.json
```json
{
  "cv_accuracy_mean": 0.85,
  "cv_f1_mean": 0.82,
  "test_accuracy": 0.84,
  "test_f1_macro": 0.81,
  "training_time_seconds": 45.2
}
```

### evaluation_results.json
```json
{
  "accuracy": 0.84,
  "f1_macro": 0.81,
  "precision_macro": 0.83,
  "recall_macro": 0.80,
  "latency": {
    "mean_ms": 0.5,
    "p95_ms": 1.2
  }
}
```

## Visualization Plots

| Plot | Description |
|------|-------------|
| `cv_metrics.png` | Cross-validation metrics per fold |
| `confusion_matrix.png` | Normalized confusion matrix |
| `evaluation_metrics.png` | Classification metrics summary |
| `fig2_pipeline_analysis.png` | IEEE-style pipeline latency analysis |
| `fig3_model_comparison.png` | Embedding model comparison |

## Generating Results

Results are automatically generated during training:

```bash
python ml/pipelines/train.py --config ml/configs/training_config.yaml
python ml/pipelines/evaluate.py --model-path Model --output-dir Results
```

## IEEE Paper Export

For academic papers, high-resolution plots are saved as both PNG (300 DPI) 
and PDF formats in the `plots/` subdirectory.
