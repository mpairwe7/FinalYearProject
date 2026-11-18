# AI governance registry

This folder is the governance record for the URA AI Chatbot. An auditor starts
here and reaches every governance artifact the project keeps, or learns that it
does not exist yet.

## How to use it

1. Read [OWNERS.md](OWNERS.md) to learn who answers for what.
2. Open the system's [manifest](systems/ura-ai-chatbot/manifest.yaml). It states
   the system's purpose, users, languages and risk classification, the control
   status for NIST AI RMF, ISO/IEC 42001, OWASP and the EU AI Act, and the
   CycloneDX AI bill of materials (`aibom_inventory`). Each control names the
   evidence or the test that backs it.
3. Walk the modules below. Each module README lists the artifacts a governance
   registry expects and where each one is.

| Module | Holds |
| --- | --- |
| [systems/](systems/README.md) | One folder per AI system in production: manifest, model card, DPIA, FRIA, change log |
| [policies/](policies/README.md) | Acceptable use, prohibited uses, risk appetite |
| [evidence/](evidence/README.md) | Approvals, pre-deployment test results, incident log |
| [controls/](controls/README.md) | Data-loss prevention, content filters, human review gates |
| [monitoring/](monitoring/README.md) | Drift alerts, bias checks, cost per decision |
| [audit/](audit/README.md) | Access logs, decision logs, retention schedule |
| [vendors/](vendors/README.md) | AI providers: contract, data flows, breach notification |

## What a slot can say

A module README gives every slot one of three states.

- **Here.** The artifact lives in this folder.
- **Elsewhere.** The artifact exists, but it stays where code, CI or Prometheus
  reads it. The README links to it and names the reader.
- **Missing.** No artifact exists. The README says what is absent.

## Checks

- `python3 App/governance/compliance_check.py` fails CI when a required artifact is
  missing, or when a manifest control's `verified_by` names a test that does not
  exist.
- `App/tests/test_governance_evidence.py` fails when a relative link in any Markdown
  file under `App/governance/` stops resolving.

The layout is recorded in
[ADR-0058](../docs/adr/0058-ai-governance-registry-layout.md).
