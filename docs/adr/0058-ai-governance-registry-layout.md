# ADR-0058: The governance records live in one registry, laid out by module

## Status

Accepted, 2026-11-17. The project owner asked for the AI governance files to
follow a governance registry layout: `systems/{system_id}/`, `policies/`,
`evidence/`, `controls/`, `monitoring/`, `audit/`, `vendors/{vendor}/`, a
registry README and an owners file.

## Context and Problem Statement

The governance records were spread across three folders. The risk manifest was
`governance/ai_risk_manifest.yaml`. The model card was `docs/MODEL_CARD.md`. The
DPIA, the privacy control record and the PDPO registration record were in
`docs/compliance/`. Controls, alert rules, the audit ledger and the risk register
live in code and configuration across the repository. An auditor had no single
place to start, and nothing said which expected artifacts did not exist.

## Considered Options

1. Make `governance/` the registry. Move the records that nothing reads by path
   into their module, and give each module a README that marks every slot Here,
   Elsewhere or Missing.
2. Move everything that matches a slot into `governance/`, including alert
   rules, the risk register and the guardrail code.
3. Leave the files in place and add one index page.

Option 2 breaks the readers. Prometheus loads `monitoring/alerting-rules.yml`, the
threat-model validators read the risk register at its path, and the guardrails
are Python modules. Option 3 does not give the auditor the layout that was asked
for. Option 1 moves the records and keeps every reader working.

## Decision Outcome

Option 1. The registry root keeps the existing name `governance/`, so
`from governance.compliance_check import check`, the CI step and the nested
`AGENTS.md` routing are unchanged. `scripts/restructure_governance_registry.py`
performs the moves and repoints every reference. Rerunning it changes nothing.

| Old path | New path |
| --- | --- |
| `governance/ai_risk_manifest.yaml` | `governance/systems/ura-ai-chatbot/manifest.yaml` |
| `docs/MODEL_CARD.md` | `governance/systems/ura-ai-chatbot/model_card.md` |
| `docs/compliance/DPIA_306.md` | `governance/systems/ura-ai-chatbot/dpia.md` |
| `docs/compliance/PRIVACY_COMPLIANCE_306.md` | `governance/systems/ura-ai-chatbot/privacy_controls.md` |
| `docs/compliance/PDPO_REGISTRATION_306.md` | `governance/evidence/approvals/pdpo_registration.md` |

New files: `governance/REGISTRY_README.md`, `governance/OWNERS.md` and one
README per module. The capstone records (`docs/capstone/week0*.md`) stay in the
numbered portfolio, and the module READMEs link to them.

## Consequences

- An auditor starts at `governance/REGISTRY_README.md` and reaches every record,
  or reads that it is missing. The Missing slots are a FRIA, an incident log,
  bias monitoring per locale, cost per decision, and vendor contracts and breach
  terms.
- No record was invented to fill a slot. A slot fills when its record is written.
- Instructions outside this repository that name the old paths go stale. The
  repository's own skills and `AGENTS.md` files now name the new ones.
- The registry READMEs point to code that can move. A broken link fails CI.

## Confirmation

- `python3 governance/compliance_check.py` passes and requires
  `governance/REGISTRY_README.md` and `governance/OWNERS.md`.
- `tests/test_governance_evidence.py::test_every_registry_link_resolves` fails
  when a relative link under `governance/` breaks.
- `tests/test_monorepo_architecture_governance.py` and
  `tests/test_governance_evidence.py` read the manifest at its new path.
- Running the codemod a second time changes no file.
