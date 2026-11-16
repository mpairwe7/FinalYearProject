# Risk appetite

These are the numbers CI, the SLOs and the alerts enforce today. Together they
are the appetite in practice. The product owner approves them or sets others,
and a change to any of them changes the file that enforces it.

Status: not yet approved by the product owner.

| Risk | Appetite | Enforced by |
| --- | --- | --- |
| Answer quality drops | No RAG metric may fall more than 0.03 below its baseline. Baseline: faithfulness 0.858, citation accuracy 0.9815, safety probe pass rate 1.0, abstention precision 1.0. | `quality_gates.py --max-regression 0.03` in `.github/workflows/ci-ml-pipeline.yml`, against [quality_baseline.json](../../ml/configs/quality_baseline.json) |
| Ungrounded answers in live traffic | Mean faithfulness under 0.5 alerts. | `UraFaithfulnessLow` in [alerting-rules.yml](../../monitoring/alerting-rules.yml) |
| Taxpayers left without an answer | Abstention above 25% for an hour alerts. | `UraAbstentionRateHigh` |
| Unequal service by language | Abstention in a local language more than 15 points above English for two hours, on at least 20 turns an hour, alerts. | `UraLanguageAbstentionGap` |
| Too many handoffs | More than 10% of turns escalated for 30 minutes alerts. | `UraEscalationRateHigh` |
| Outage | 99.9% of API requests succeed over 30 days. | [OpenSLO](../../monitoring/slo/ura-chatbot.openslo.yaml) and the burn-rate alerts |
| Slow answers | 95% of chat turns finish within 3 seconds over 30 days. | The same SLO file |
| Security threats | Every threat in the register has a severity, a CVSS score and an annual risk estimate. A threat is mitigated, or accepted with an approver, a justification and a review date. Today 41 of 42 are mitigated and one Medium is accepted, reviewed every 181 days. | [risk-register.yaml](../../docs/security/threat-model/risk-register.yaml), [accepted-risks.yaml](../../docs/security/threat-model/accepted-risks.yaml), `scripts/validate-risk-register.py` (fails on an expired review) |
