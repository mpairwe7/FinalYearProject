# `app.workflows` — multi-step flow engine (Phase 18)

> **Status:** scaffolded — the directory, loader, and state shape
> exist; concrete YAML workflows land in Phase 18.

## What this package will do

Multi-step tax tasks like *"Register for a TIN"*, *"File my VAT
return"*, *"Declare imported goods"* — anything that needs slot
filling across multiple turns and resumes across sessions.

## Target architecture (Phase 18)

```
workflows/
├── __init__.py         # registry of loaded flows
├── registry.py         # loader + runtime
├── loader.py           # YAML → StateGraph builder
├── flows/
│   ├── tin_registration.yaml
│   ├── vat_filing.yaml
│   ├── customs_declaration.yaml
│   └── objection_filing.yaml
├── slots.py            # Pydantic-AI slot validators
└── tests/
```

## YAML workflow shape (proposed)

```yaml
id: tin_registration
name: Register for a TIN
version: "2026-04"
requires_auth: true
steps:
  - id: collect_type
    question: "Are you registering as an individual, company, or NGO?"
    slot: taxpayer_type
    validator: enum[individual,company,ngo]
  - id: collect_nin_or_reg
    when: taxpayer_type == "individual"
    question: "What is your National ID (NIN)?"
    slot: nin
    validator: regex[^C[MF]\d{2}[A-Z]{5}\d{5}[A-Z]$]
  - id: confirm
    question: "Ready to submit your application?"
    slot: confirm
    validator: boolean
  - id: submit
    tool: ura_actions.submit_tin_application
    args:
      taxpayer_type: "{{ taxpayer_type }}"
      nin: "{{ nin }}"
    confirmation_required: true
```

## Dependencies

- Phase 14 auth
- Phase 15 Lite MCP + LangGraph orchestrator
- Phase 17 `mcp_ura_account` + `mcp_ura_actions`
- Phase 21 audit ledger

## Effort

4 weeks once dependencies land.
