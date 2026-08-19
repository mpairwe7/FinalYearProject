# DPIA decision record — issue #306

**Status: awaiting DPO/legal completion and signature.** Engineering must not
mark this assessment approved or set `DPIA_APPROVED=true` until the fields
below are completed and the signed record is stored in the approved governance
repository. This template supports the PDPO expectation for a DPIA before
high-risk processing; it is not a substitute for PDPO/DPO review.

| Field | Decision / evidence |
| --- | --- |
| Controller / DPO | URA — name, contact, appointment reference: `________________` |
| System / release | URA Chatbot, issue #306, release/commit: `________________` |
| Assessment date / review date | `________________` / `________________` |
| Processing scope | Chat, identity/consent, uploads, memory, analytics, feedback, voice metadata, escalation tickets. |
| Subjects | Taxpayers, prospective taxpayers, staff/officers, upload/document subjects. |
| Sensitive/high-risk data | TINs, contact details, tax/document data, voice/transcript content, free-text tickets. |
| Necessity / proportionality | Confirm purpose limitation, data minimisation, alternatives, and final lawful basis per ROPA activity. |
| Processors / locations | Complete supplier, country, hosting region, DPA, transfer mechanism, security assessment, and data-flow diagram for every enabled provider. |
| Residual risk decision | `accept / reduce / prohibit`, owner and deadline: `________________` |

## Risks and implemented mitigations

| Risk | Control | Residual decision required |
| --- | --- | --- |
| PII in chat, ticket, feedback, analytics or logs | PII redaction at ticket/event boundaries; production blocks raw prompts; logs retain IDs/lengths, not free text. | Confirm pattern coverage, log platform retention, and incident response. |
| Data remains after its purpose | TTLs plus scheduled deletion across DB, documents/spool, memory, and voice audit stores; self-service erasure. | Confirm statutory preservation exceptions and backup/deletion process. |
| Secondary analytics use | Durable analytics requires explicit consent; withdrawal/erasure removes linked analytics, sessions, feedback. | Approve purpose statement and consent wording/version. |
| Unauthorised access to uploads/tickets | Authenticated owner/session binding for uploads; role-gated officer routes; redacted ticket payloads. | Review access roles and suppliers. |
| External LLM/translation/voice processing | Production identifies enabled external providers and fails closed until transfer approval ID exists. | Complete country, adequacy/safeguard, DPA and vendor due diligence per provider. |
| Subject request not met | Export and erasure cover application-owned stores; operational target and intake process documented. | Confirm identity verification, 30-day tracking and exception handling. |

## Approval

| Role | Name | Signature / approved-system reference | Date |
| --- | --- | --- | --- |
| Data Protection Officer |  |  |  |
| Legal / compliance owner |  |  |  |
| Information security owner |  |  |  |
| Product/process owner |  |  |  |

After approval, place the immutable evidence identifier—not the DPIA contents
or a person’s name—in `DPIA_APPROVAL_REFERENCE` and set
`DPIA_APPROVED=true` only for the approved deployment.
