# Fundamental rights impact assessment

## Status

Draft, 2026-11-17. Engineering prepared it from the project's own records. It
is not approved. The DPO/legal owner reviews and signs it together with the
[DPIA](dpia.md).

EU AI Act Article 27 asks a public body that deploys a high-risk system for this
assessment. The [manifest](manifest.yaml) classifies this system as limited
risk, so the assessment is voluntary, with one open question below.

## Open classification question

`App/backend/app/receptionist/prosody.py` infers a caller's frustration or
distress from acoustic features of their voice (energy, zero-crossing rate,
energy variance, speech rate), fused with what they say. The result slows the
spoken reply and adds empathy guidance. Two flags enable it,
`emotion_aware_prosody` and `eq_multimodal_fusion`. Both are off by default.

Inferring emotion from voice features may make this component an emotion
recognition system (Article 3(39)). Annex III point 1(c) lists such systems as
high risk, and Article 50(3) requires the deployer to tell the people exposed
to it. The DPO/legal owner decides the classification. Until then, both flags
stay off in production. The open gap is G664 in
[App/docs/GAPS_AND_AGENTIC_ROADMAP.md](../../../docs/GAPS_AND_AGENTIC_ROADMAP.md).

## (a) The process the system serves

URA's taxpayer service. The assistant answers tax and non-tax revenue
questions in chat and on calls, guides taxpayers through filing journeys and
hands a conversation to an officer. It does not assess tax, audit, enforce or
decide anything about a taxpayer. Its answers are information, not legal or tax
advice ([model card](model_card.md), "Out-of-Scope Uses").

## (b) Period and frequency

The assistant answers at any hour. Officers take conversations over during
their working hours, 07:00 to 23:00 EAT
([escalation runbook](../../../docs/runbooks/escalation-and-handoff.md)).

## (c) People affected

- Taxpayers and prospective taxpayers, individuals and small businesses, and
  their tax agents.
- Taxpayers who write or speak Luganda, Swahili, Runyankole or Acholi.
- Callers, including people with low literacy or poor connectivity who use voice
  or the low-bandwidth mode.
- Taxpayers in financial distress, whom the assistant may offer a person.
- URA officers who take over conversations.

## (d) Risks to rights, and the controls

Rights are cited from the Constitution of Uganda, 1995. Harm IDs are from the
[harm register](../../../docs/capstone/week03-harm-register.md).

| Right | Risk | Controls |
| --- | --- | --- |
| Access to information (Art. 41) | A wrong or superseded figure (H1, H10). | Figures come from the statutory knowledge kernel and are held until verified (ADR-0022, ADR-0033). Answers cite their source, and the output guard abstains without grounding. CI blocks a quality drop against [quality_baseline.json](../../../ml/configs/quality_baseline.json). |
| Equality and freedom from discrimination (Art. 21) | Worse service in a local language (H9) or for small businesses (H4). | `UraLanguageAbstentionGap` alerts on a language gap in live traffic ([monitoring](../../monitoring/README.md)). [bias_fairness_audit.py](../../../scripts/bias_fairness_audit.py) compares individual and SME taxpayers offline. |
| Privacy (Art. 27) | Personal data in prompts, logs, tickets or voice. | The [DPIA](dpia.md), [privacy controls](privacy_controls.md), PII redaction and TTL deletion ([audit](../../audit/README.md)). |
| Just and fair treatment in administrative decisions (Art. 42) | A taxpayer relies on an answer as if URA had decided (H5). | Disclaimers, the AI disclosure in chat and in the call greeting ([security/ai_disclosure.py](../../../backend/app/security/ai_disclosure.py)), and a route to a person on every turn. |
| Dignity | Emotion inferred from a caller's voice. | Off by default. See the open classification question. |

## (e) Human oversight

- An officer can take over any call or chat, and the AI then goes silent
  (ADR-0055).
- The escalation policy offers a person and sets how fast one should answer
  ([tickets/escalation_policy.py](../../../backend/app/tickets/escalation_policy.py)).
- Tool actions with effects wait for the taxpayer's approval (ADR-0022).
- An admin can switch the call receptionist off from the ops console. The change
  reaches every process that shares the database, which on the HF Space is the
  chat and the call process. A replica with its own database needs
  `FLAG_VOICE_RECEPTIONIST` set on it (ADR-0044).
- Every answered turn is written to a tamper-evident audit ledger
  ([audit](../../audit/README.md)).

## (f) When a risk materialises

- Alerts route to the owning team ([monitoring](../../monitoring/README.md)).
- Production incidents are recorded in the
  [incident log](../../evidence/incident_log/README.md).
- A taxpayer can complain and appeal through the complaint workflow in section 3
  of [week04-privacy-and-aup.md](../../../docs/capstone/week04-privacy-and-aup.md).
- [OWNERS.md](../../OWNERS.md) names who answers for each kind of problem.

## Approval

| Role | Name | Decision and reference | Date |
| --- | --- | --- | --- |
| Data Protection Officer |  |  |  |
| Legal / compliance owner |  |  |  |
| Product/process owner |  |  |  |
