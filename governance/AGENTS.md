# governance/

The AI governance registry. [REGISTRY_README.md](REGISTRY_README.md) is the
auditor's entry point. Each module README lists its slots and marks each one
Here, Elsewhere or Missing.

| Path | Role |
| --- | --- |
| `systems/ura-ai-chatbot/manifest.yaml` | NIST AI RMF + ISO 42001 + OWASP + EU AI Act mapping and the AIBOM |
| `systems/ura-ai-chatbot/model_card.md` | Model card (EU AI Act Art. 53) |
| `systems/ura-ai-chatbot/dpia.md`, `privacy_controls.md` | Data protection records for issue #306 |
| `evidence/approvals/pdpo_registration.md` | PDPO registration decision record |
| `policies/`, `evidence/`, `controls/`, `monitoring/`, `audit/`, `vendors/` | Module READMEs that point to records kept where code, CI or Prometheus reads them |
| `OWNERS.md` | Who answers for what |
| `compliance_check.py` | CI gate. Do not skip it in a merge |

Rules:

- A new governance record goes into its module here. A control, alert rule or
  test stays where it runs, and its module README links to it.
- When a linked file moves, update the README. `tests/test_governance_evidence.py`
  fails on a broken link.
- Threat-model code lives in `threat-model/`. Security claims must match
  `SECURITY.md` and the manifest, not marketing copy.
