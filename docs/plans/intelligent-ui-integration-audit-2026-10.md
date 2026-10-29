# Intelligent UI integration audit — 30 October 2026

Baseline: `dev` at `87460457`. Remediation branch:
`fix/intelligent-ui-integration-audit-2026-10`. Implementation decision:
[ADR-0018](../adr/0018-intelligent-ui-completion-and-action-boundaries.md).

This is a source review and local regression audit of taxpayer chat, guided
workflows, sources, portal confirmation cards and their REST/SSE integration.
It is not a certification of the whole service or a live URA deployment audit.
Staff authorization, model accuracy, infrastructure and external portal contracts
require the separate gates already documented in [PRODUCTION_GATES](../PRODUCTION_GATES.md).

## Findings and phased fixes

Severity reflects potential taxpayer impact, not a measured exploit probability.
“Implemented” means included in this change; deployment remains a separate step.

| ID / phase | Severity | Evidence at baseline | Remediation and regression evidence |
|---|---|---|---|
| UI-01 / 1 | High | `page.tsx:applySyncReply` duplicated metadata mapping and omitted `objective_hypothesis`; `ChatMessage`'s custom comparator omitted portal payloads and most workflow details. | REST uses `applyTurnMeta`, as SSE does. Standard shallow memoization observes the immutable turn; stable action callbacks avoid unnecessary changes. Component rerender tests and the JSON/browser workflow test cover parity. No latency improvement is claimed without profiling. |
| UI-02 / 1 | Medium | Null grounding scores retained an older reassuring score through `??`; source fields were unchecked casts. | Explicit withdrawal clears scores/cards/actions. Invalid scores, citation rows, resource rows and objective cards are rejected by the metadata mapper. `turnMeta.test.ts` covers invalid, withdrawn and absent fields. Full nested schema coverage remains UI-08. |
| UI-03 / 1 | High | Workflow and screenshot portal actions used payload URLs directly; source URLs relied on a permissive regex. | Parse URLs; disallow credentials, control characters and backslashes. Portal actions accept HTTPS `ura.go.ug` and its subdomains on the default port. Ordinary reference links retain valid HTTP(S) destinations. URL and component regressions cover spoofed hosts and malformed input. This is navigation validation, not proof that a referenced document supports an answer. |
| UI-04 / 2 | High | Old workflow options and direct portal execution remained usable after subsequent messages. Restored history could revive workflow controls. | Actions are disabled for historical, restored, busy and interrupted turns and while an officer is handling the conversation. Completed/cancelled workflows cannot trigger prerequisite or registration actions. Portal handlers enforce their disabled state; current-step markup exposes `aria-current="step"`. |
| UI-05 / 2 | High | Browser digest failure fell back to the server-supplied digest; any otherwise unrecognised successful HTTP body could be shown as executed; failed prepare fell through to a new chat submission. | Approval requires local SHA-256 calculation. Unknown outcomes display an error. Failed prepare preserves edits in the form and stops. In-flight decisions cannot be double-dispatched by the handler. Form IDs are unique per card. `PortalCopilotCard.test.tsx` exercises these failures. API-wide mandatory digest enforcement remains UI-09. |
| UI-06 / 3 | High | EOF with partial text was treated as success. Chunk-split CRLF could create premature event delimiters. Recovery repeated a stateful chat POST after an ambiguous failure. | Bounded UTF-8 SSE reader requires a delimited `done` event; handles compact fields, multiline data, split CRLF and Unicode; ignores post-completion data. Limits: 262,144 decoded characters per event and 4,194,304 per response. Interrupted replies retain a persisted warning, cannot offer action controls or reassuring grounding badges, and are not automatically narrated. Fallback is limited to explicit 404/405/401 responses; ambiguous failures are not replayed. |
| UI-07 / 3 | High | Stream and JSON callbacks updated the current last turn even after the taxpayer switched conversations. | Bind writes to the originating conversation and response ID; abort on a conversation change. New responses remain incomplete until successfully settled. The browser test switches chats during a pending request and checks that the old reply cannot overwrite the new chat. |

## Verified standards and research

Primary sources were retrieved during this audit. Publication dates below refer
to the source, not to a new October edition of every standard. The mappings are
engineering judgments, not claims that the cited organisations endorse this app.

| Source | Status/date | Application in this change |
|---|---|---|
| [W3C WCAG 2.2](https://www.w3.org/TR/WCAG22/) and [SC 3.3.4](https://www.w3.org/WAI/WCAG22/Understanding/error-prevention-legal-financial-data.html) | Recommendation; retrieved version dated 12 December 2024 | Keep consequential submissions reviewable and correctable. Preserve failed edits and disable obsolete confirmations. |
| [W3C SC 4.1.3](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html) | WCAG 2.2 AA guidance | Announce interrupted results with a status role; do not announce an incomplete answer as ready. Screen-reader usability still needs manual review. |
| [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) | Published 9 December 2025, version 2026 | ASI08 cascading failures and ASI09 human-agent trust inform explicit completion, bounded consumption and confirmation integrity. These are partial controls, not complete coverage of either risk. |
| [NIST AI 600-1](https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence) | Published 26 July 2024; voluntary GenAI risk profile | Keep evidence and residual risks traceable; distinguish incomplete output from reliable guidance. |
| [2025 AI Agent Index, v2](https://arxiv.org/abs/2602.17753v2) | Research revised 6 May 2026 | The study of 30 deployed agents reports uneven disclosure of safety/evaluation information. Our inference: publish explicit test evidence and remaining limitations instead of inferring readiness from feature availability. This observational research does not validate this application's controls. |
| [WHATWG SSE interpretation](https://html.spec.whatwg.org/multipage/server-sent-events.html#event-stream-interpretation) | Living Standard; retrieved page dated 26 October 2026 | Respect UTF-8, CR/LF handling, optional field-value space and blank-line dispatch. Application-level `done` is this API's additional completion contract. |
| [React `memo`](https://react.dev/reference/react/memo) | Current React documentation, retrieved during audit | Use immutable prop identity; custom comparisons must account for every behavioural prop. Avoid per-render string signatures that omit newly added fields. |

## Phase 4 — remaining release work

These items are deliberately not marked fixed by the narrower controls above.

| ID | Priority / owner | Evidence and acceptance criterion |
|---|---|---|
| UI-08 | High / frontend + API | `WorkflowState`, `PortalCopilotPayload`, stored history and document analysis still contain unchecked nested structures. Introduce versioned runtime schemas at every ingress, including history; exercise corrupt arrays, object-valued labels, oversized payloads and unknown widget types. Invalid cards must fail independently without losing the conversation. |
| UI-09 | High / portal API | `execute_task_from_request_state` checks the displayed-fields digest only when the client supplies it. The browser now requires its own digest, but another client can omit it. Require it for approvals under an explicit API compatibility policy; prove missing/mismatched digests fail before execution, with decline, prepare and signed read-only paths still covered. Signed-state authentication remains a separate existing control. |
| UI-10 | High / QA + security | Run authenticated staging journeys against the real proxy and backend: token expiry, officer takeover during a request, lost execution receipts, repeated requests across replicas and reload during a portal confirmation. The current browser tests mock the backend and cannot establish distributed exactly-once execution. |
| UI-11 | Medium / accessibility + language reviewers | New failure strings use dictionary keys with the existing English fallback. Obtain reviewed lg/sw/nyn/ach translations; manually verify keyboard flow, screen-reader status announcements and financial confirmation comprehension on low-bandwidth mobile devices. Automated axe results alone do not establish WCAG conformance. |
| UI-12 | High / build and release | The pinned dependency audit reports 25 findings (7 high, 14 moderate, 4 low), through development/test tool chains. Triage reachability and update compatible dependencies in a dedicated dependency change, then run a frozen install and repeat audit/tests. Do not apply forced major-version overrides without consumer compatibility evidence. Retain the audit output with the release evidence. |
| UI-13 | Release gate / retrieval | Retrieval freshness fails because `App/Model/index_freshness.json` is absent; this checkout's corpus hash is the SHA-256 of empty input. Generate freshness evidence from the actual indexed corpus. Creating an empty snapshot would not establish corpus freshness. An earlier repository-hygiene failure came from a protected `.agents` path mounted by a local agent sandbox; the hygiene test passes outside it (runbook results). |

## Validation and rollout

Detailed commands and operational probes are in the
[runbook](../runbooks/intelligent-ui-integration.md). Local results are recorded
there after the final verification run. Tests use the repository's Bun 1.4.3
frozen lockfile; no hosted LLM or live taxpayer data is used.

Release order: review this PR; run staging probes and close/accept the Phase 4
risks with named owners; deploy a canary; compare existing chat error and
completion telemetry with the prior release before wider rollout. No numerical
latency or capacity claim is made by this audit. Roll back the frontend artifact
if completion warnings spike on valid streams, current confirmations become
unusable, or legitimate configured URA portal links disappear. Investigate the
protocol/contract mismatch before restoring ambiguous automatic POST retries.
