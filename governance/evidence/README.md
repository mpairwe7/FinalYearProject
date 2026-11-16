# evidence/

| Slot | State | Artifact |
| --- | --- | --- |
| Approvals | Here | [approvals/pdpo_registration.md](approvals/pdpo_registration.md) is the PDPO registration decision record. Its fields stay blank until the DPO/legal owner completes them. The DPIA signature block is in [dpia.md](../systems/ura-ai-chatbot/dpia.md), and accepted-risk approvals are in [accepted-risks.yaml](../../docs/security/threat-model/accepted-risks.yaml). No ethics committee sign-off exists. |
| Test results | Elsewhere | CI produces them on every pull request. The quality gates in `.github/workflows/ci-ml-pipeline.yml` compare against [quality_baseline.json](../../ml/configs/quality_baseline.json). Eval suites and their reports are under [evals/](../../evals/README.md), and dated benchmark reports are in [docs/Reports/](../../docs/Reports/). |
| Incident log | Missing | The project keeps no log of production incidents. [incident_response_sim.json](../../Results/incident_response_sim.json) is the output of a drill (`scripts/incident_response_sim.py`), not a record of real incidents. |
