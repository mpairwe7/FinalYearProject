# Owners and escalation

Who answers for what, and where a problem goes next.

| When | Who | How |
| --- | --- | --- |
| Any change to code, models or governance records | The project maintainer, the default owner in [CODEOWNERS](../CODEOWNERS) | A pull request. CODEOWNERS requests the maintainer's review on every path. |
| A security vulnerability | The project maintainer | The private report route in [SECURITY.md](../SECURITY.md). Never a public issue. Acknowledgement within 48 hours. |
| Personal data: the DPIA, PDPO registration, a cross-border transfer | URA's data protection officer or legal owner | Complete and sign [dpia.md](systems/ura-ai-chatbot/dpia.md) and [pdpo_registration.md](evidence/approvals/pdpo_registration.md). The owner is not yet named in either record. Production refuses to start until `DPIA_APPROVED=true`, `PDPO_REGISTRATION_STATUS` and `PDPO_REGISTRATION_REFERENCE` are set. |
| Accepting a security risk instead of fixing it | The Engineering Lead | An entry in [accepted-risks.yaml](../docs/security/threat-model/accepted-risks.yaml) with a justification and a review date. CI fails once the review date has passed. |
| The call receptionist misbehaves in production | Any admin on the ops console | Switch the `voice_receptionist` flag off. The change reaches the chat and call processes ([ADR-0044](../docs/adr/0044-flag-changes-reach-every-process.md)). |
| A taxpayer needs a person | A URA officer | The escalation policy offers an officer and sets how fast one should answer ([runbook](../docs/runbooks/escalation-and-handoff.md)). Once an officer takes a call, the AI goes silent ([ADR-0055](../docs/adr/0055-officer-takeover-silences-the-ai.md)). |
