# Issue #306 privacy control record

This is the engineering evidence pack for the Uganda Data Protection and
Privacy Act, 2019 (UDPA) controls raised in issue #306. It is not legal advice
and it does not assert a completed DPIA signature, a PDPO registration, or
approval of a foreign transfer. Those decisions belong to URA's DPO/legal
owner and are production startup gates, not developer self-certification.

The relevant statutory requirements include notice and collection conditions
(sections 12–13), retention/deletion (section 18), cross-border transfers
(section 19), and access requests (section 24) in the [UDPA
text](https://ulii.org/en/akn/ug/act/2019/9/eng%402019-05-03). The PDPO also
identifies registration, DPO appointment, DPIAs for high-risk processing, and
foreign-processor due diligence as controller obligations in its
[organisation guidance](https://www.pdpo.go.ug/information-center/organisation).

## Engineering status

| Control | Evidence and implementation state |
| --- | --- |
| DPIA | Decision-ready assessment and sign-off block: [DPIA_306.md](DPIA_306.md). **DPO/legal signature remains required.** |
| ROPA | Processing inventory below, including purpose, basis to be confirmed by DPO, access, recipient, and retention. |
| Automated deletion | `app.retention.run_retention_cleanup` runs at startup and every `RETENTION_CLEANUP_INTERVAL_SECONDS` (default 1 hour). It deletes expired core records, transient uploads, memory, and voice audit data. |
| Subject rights | Authenticated `/v1/me/export` returns core identity/consents, conversation/ticket, analytics/session/feedback, facts, and unexpired upload data. `/v1/me` removes those stores; `/v1/me/consents/withdraw` immediately removes analytics when analytics consent is withdrawn. |
| Consent | Durable sessions, feedback, and analytics events require an authenticated subject and active `analytics` receipt. Analytics withdrawal deletes all linked records. Personalization memory is already consent-gated and is purged on withdrawal. |
| Redaction | Ticket creation, staff notes, officer replies, transcript/handoff fields, client analytics payloads, and application logs are redacted or logged as metadata only. Raw prompt storage is blocked in production. |
| Cross-border | Production detects configured Sunbird, Cloudflare/Gemini/Workers AI, and non-local vLLM processors. It fails closed without approved transfer evidence. See [DPIA_306.md](DPIA_306.md). |
| PDPO registration | The required determination, registration reference, or documented `not_required` decision is tracked in [PDPO_REGISTRATION_306.md](PDPO_REGISTRATION_306.md) and required as a production attestation. |

## Record of processing activities (ROPA)

| Processing activity / data | Purpose and lawful basis to confirm | Access / recipient | Retention and deletion |
| --- | --- | --- | --- |
| Account identity, profile, consent receipt | Authenticate, tenant-scope, evidence consent; consent / statutory-public-service basis must be confirmed per flow | Subject, authorised URA staff, identity provider | Removed through `/v1/me`; consent withdrawal is retained as an audit receipt where required. |
| Chat turn, sources, context, ticket transcript | Provide a grounded tax-information service and human escalation | Subject, authorised officers for an assigned ticket | `CONVERSATION_TTL_DAYS` (7 default); closed/resolved ticket `TICKET_TTL_DAYS` (90 default); job deletes expired records. |
| Analytics event, session, feedback | Service quality/usage measurement | Authorised operational staff in aggregate/dashboard view | Active analytics consent only; analytics `365`, session `30`, feedback `90` days by default. Withdrawal and erasure delete linked rows immediately. |
| Uploaded document extraction and derived analysis | Answer an attachment-grounded request | Subject while authenticated/bound to upload session; no analytics store | `DOCUMENT_TTL_SECONDS` (2 hours default); job removes memory and spool records. Export includes active extraction data; erasure removes it immediately. Source-file bytes are not retained. |
| Personalization facts and episodic/working memory | Tailor answers after explicit personalization consent | Subject through assistant; authorised support only where separately permitted | Working/episodic/semantic configured TTLs (90/90/365 defaults); withdrawal and erasure purge immediately. |
| Voice consent/audit metadata | Prove voice processing choice and maintain service controls | Subject and authorised operational staff | `VOICE_ANALYTICS_TTL_DAYS` (365 default) through the same scheduled job; raw audio is disabled by default. |
| Escalation reason, note, officer reply | Resolve a support request | Authorised URA officers with queue access | Closed ticket TTL above; PII redaction is enforced before persistence and output. |

The DPO must finalise the lawful basis, controller/processor role, recipients,
international transfer countries, and any statutory minimum retention. The
PDPO registration form specifically asks for transferred countries and
retention periods; use the official [registration form](https://www.pdpo.go.ug/media/2022/01/31122021140148-Form_2_-_Application_for_Registration_Renewal_of_Registration.pdf), not this document, for filing.

## Operational test protocol

Before a release, record the test run, actor, environment, and result:

1. Grant `analytics`; submit an event, feedback and a chat request. Verify all
   three records carry only the authenticated OIDC subject and that a PII
   pattern in event/ticket data is redacted.
2. Withdraw `analytics`; verify the endpoint returns deletion counts and no
   analytics/session/feedback records remain for the subject. Attempt a new
   event without consent and expect `ignored`.
3. Grant and withdraw `personalization`; verify semantic, episodic and working
   memory are empty. Run `/v1/me/export`, then `/v1/me`, and verify all listed
   stores—including any active upload—are exported then erased.
4. Create a deliberately expired conversation, analytics record, upload, memory
   item and resolved ticket in non-production test data. Run the retention job
   and verify deletion; verify an old *open* ticket is retained.
5. Review application logs from an escalation containing test PII. Confirm the
   log contains only IDs, labels, and lengths—not message text, transcript,
   staff note, or officer reply.
6. For each enabled remote provider, attach the signed transfer assessment,
   processor agreement/DPA, country, safeguards, purpose, and owner to the
   deployment change. Do not set the production transfer attestation first.

The statutory access response target is 30 days under section 24; operational
owners should track request receipt and completion timestamps even though the
self-service export is immediate. PDPO annual compliance reporting also has
its own [published guidance](https://pdpo.go.ug/media/2024/01/Guidance-Note-on-Completion-of-the-Annual-DPP-Compliance-Report.pdf).
