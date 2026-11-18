# evidence/

| Slot | State | Artifact |
| --- | --- | --- |
| Approvals | Here | [approvals/pdpo_registration.md](approvals/pdpo_registration.md) is the PDPO registration decision record. Its fields stay blank until the DPO/legal owner completes them. The DPIA signature block is in [dpia.md](../systems/ura-ai-chatbot/dpia.md), and accepted-risk approvals are in [accepted-risks.yaml](../../docs/security/threat-model/accepted-risks.yaml). No ethics committee sign-off exists. |
| Test results | Elsewhere | CI produces them on every pull request. The quality gates in `.github/workflows/ci-ml-pipeline.yml` compare against [quality_baseline.json](../../ml/configs/quality_baseline.json). Eval suites and their reports are under [App/evals/](../../evals/README.md), and dated benchmark reports are in [App/docs/Reports/](../../docs/Reports/). |
| Incident log | Here | [incident_log/](incident_log/README.md) records each production incident: what broke, when, the impact, the fix and the evidence. |
| Red team | Here | [red_team.md](red_team.md) lists the attacks tried against the guardrails, by class, and what held. |
