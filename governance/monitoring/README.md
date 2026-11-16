# monitoring/

The live monitoring configuration is the top-level [monitoring/](../../monitoring/AGENTS.md)
folder. Prometheus and `.github/workflows/monitoring-config.yml` read it there.

| Slot | State | Artifact |
| --- | --- | --- |
| Drift alerts | Elsewhere | [alerting-rules.yml](../../monitoring/alerting-rules.yml). Answer quality: `UraFaithfulnessLow`, `UraEvalRegression`, `UraEvalStale`, `UraAbstentionRateHigh`, `UraEscalationRateHigh`. Corpus: `QdrantIndexDrift`. |
| Bias checks | Missing | The group the project measures is the taxpayer's language. Per-locale retrieval quality comes from the [multilingual retrieval benchmark](../../docs/runbooks/multilingual-retrieval-benchmark.md), run on demand. The [Grafana dashboard](../../monitoring/grafana/dashboards/ura-chatbot-overview.json) has no per-locale panel. |
| Cost per decision | Missing | No metric records the cost of an answer. |
