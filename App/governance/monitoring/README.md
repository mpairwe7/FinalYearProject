# App/infra/monitoring/

The live monitoring configuration is the top-level [App/infra/monitoring/](../../infra/monitoring/AGENTS.md)
folder. Prometheus and `.github/workflows/monitoring-config.yml` read it there.

| Slot | State | Artifact |
| --- | --- | --- |
| Observability | Here | [observability.md](observability.md) lists each signal the system records about itself (metrics, logs, traces, real-user telemetry, security events), whether it can hold personal data, who reads it and how long it is kept. |
| Drift alerts | Elsewhere | [alerting-rules.yml](../../infra/monitoring/alerting-rules.yml). Answer quality: `UraFaithfulnessLow`, `UraEvalRegression`, `UraEvalStale`, `UraAbstentionRateHigh`, `UraEscalationRateHigh`. Corpus: `QdrantIndexDrift`. |
| Bias checks | Elsewhere | Live traffic: `ura_locale_turns_total` counts each turn's outcome by reply language, `UraLanguageAbstentionGap` alerts when a local language abstains more than 15 points above English, and the dashboard row **Language parity and cost** shows abstention and escalation per language. Two offline assessments run on demand. [bias_fairness_audit.py](../../scripts/bias_fairness_audit.py) compares answer quality for English against Luganda and for individual against SME taxpayers (manifest control MEASURE 2.6). The [multilingual retrieval benchmark](../../docs/runbooks/multilingual-retrieval-benchmark.md) measures retrieval per locale. Taxpayer type (individual or SME) is measured offline only. |
| Cost per decision | Here | `ura:llm_tokens_per_turn:rate1d` records model tokens per chat turn. [cost_per_decision.py](cost_per_decision.py) prices it from [cost_per_decision.csv](cost_per_decision.csv), whose prices cite the providers' pages, and prints US dollars per decision. A model without a published price (Sunbird, self-hosted vLLM, `gemini-3.7-flash`) makes the figure a lower bound and is named. |
