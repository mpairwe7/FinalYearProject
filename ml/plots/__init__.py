"""ML visualization and reporting module (2026).

Generates publication-quality plots from evaluation JSON metrics.
Every pipeline that outputs ``Results/metrics/*.json`` has a corresponding
plot generator here that reads that JSON and writes PNG/SVG to
``Results/plots/``.

Usage::

    # Generate ALL plots from existing metrics:
    python -m ml.plots.generate_all --metrics-dir Results/metrics --output-dir Results/plots

    # Individual plot families:
    python -m ml.plots.calibration_plots --input Results/metrics/calibration_report.json
    python -m ml.plots.speech_plots --input Results/metrics/speech_metrics.json
    python -m ml.plots.mt_plots --input Results/metrics/mt_metrics.json
    python -m ml.plots.rag_plots --input Results/metrics/rag_evaluation_results.json
    python -m ml.plots.data_quality_plots --input artifacts/training_data/manifest.json
    python -m ml.plots.benchmark_plots --input Results/metrics/mobile_benchmark.json
"""
