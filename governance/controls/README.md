# controls/

The controls are code, not YAML. They run on every turn and their tests run in
CI. A YAML copy here would drift from what runs.

| Slot | State | Artifact |
| --- | --- | --- |
| Data-loss prevention | Elsewhere | `OutputGuard.redact_pii` ([output_checks.py](../../App/backend/app/guardrails/output_checks.py)) and `redact_pii_text` ([pii.py](../../App/backend/app/guardrails/pii.py)) strip personal data from answers and logs. `App/backend/tests/test_streaming_dlp.py` covers the streaming path. Web agents fetch through one pinned client in [egress.py](../../App/backend/app/egress.py) (ADR-0035). Production refuses a cross-border processor that lacks approved transfer evidence ([privacy_controls.md](../systems/ura-ai-chatbot/privacy_controls.md)). |
| Content filters | Elsewhere | `InputGuard` screens prompt injection and obfuscation, and `OutputGuard` checks grounding, abstention and escalation, in [input_checks.py](../../App/backend/app/guardrails/input_checks.py) and [output_checks.py](../../App/backend/app/guardrails/output_checks.py). `App/backend/tests/test_redteam_corpus.py` is the red-team gate (ADR-0029). |
| Human review gates | Elsewhere | [escalation_policy.py](../../App/backend/app/escalation_policy.py) decides when a person is offered and how fast one should answer ([runbook](../../docs/runbooks/escalation-and-handoff.md)). Held tool actions wait for approval (ADR-0022). An officer who takes a call silences the AI (ADR-0055). |
