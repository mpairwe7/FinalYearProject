# monitoring/

The live monitoring configuration is the top-level [monitoring/](../../monitoring/AGENTS.md)
folder. Prometheus and `.github/workflows/monitoring-config.yml` read it there.

| Slot | State | Artifact |
| --- | --- | --- |
| Drift alerts | Elsewhere | [alerting-rules.yml](../../monitoring/alerting-rules.yml). Answer quality: `UraFaithfulnessLow`, `UraEvalRegression`, `UraEvalStale`, `UraAbstentionRateHigh`, `UraEscalationRateHigh`. Corpus: `QdrantIndexDrift`. |
| Bias checks | Missing | Two offline assessments run on demand. [bias_fairness_audit.py](../../scripts/bias_fairness_audit.py) compares answer quality for English against Luganda and for individual against SME taxpayers (manifest control MEASURE 2.6). The [multilingual retrieval benchmark](../../docs/runbooks/multilingual-retrieval-benchmark.md) measures retrieval per locale. Neither monitors live traffic, and the [Grafana dashboard](../../monitoring/grafana/dashboards/ura-chatbot-overview.json) has no per-locale panel. |
| Cost per decision | Missing | No metric records the cost of an answer. |
