# ADR-0018: Bind intelligent UI actions to current, completed responses

- **Date:** 2026-10-30
- **Scope:** Taxpayer frontend; REST/SSE consumption and guided/portal cards.

## Status

Accepted for implementation; deployment and Phase 4 acceptance remain pending.

## Context and Problem Statement

The [UI integration audit](../plans/intelligent-ui-integration-audit-2026-10.md)
found metadata drift between transports, stale confirmation rendering, actionable
historical cards, and streams that appeared successful without a completion frame.
A chat POST can mutate workflow state or queue help, so repeating it after an
ambiguous transport failure cannot be assumed safe. Reply callbacks also wrote
to the last turn of whichever conversation was open at that moment.

## Decision Outcome

1. Use `applyTurnMeta` for both REST and SSE and shallow React memoization over
   immutable turns. Explicitly withdrawn evidence and actions must not survive.
2. Validate navigation URLs at the rendering boundary. Portal account actions
   require HTTPS URA domains; reference links accept parsed HTTP(S) URLs without
   credentials or ambiguous control characters.
3. Offer workflow/portal mutations only from current, settled responses; restored
   history needs a fresh server turn. The API continues to own authorization.
4. Require a delimited SSE `done` frame, bounded event/response sizes, and writes
   bound to conversation plus response ID. Mark partial replies as incomplete;
   do not automatically narrate them or replay ambiguous requests. Only explicit
   unsupported-transport/authentication rejection permits the existing REST path.
5. Compute the displayed-fields digest locally before approval. Unknown execution
   responses cannot be displayed as success. Preserve failed edits without a
   second submission through chat.

## Consequences

The UI can expose failures that it previously concealed. A taxpayer may need to
check a portal task's status or ask for refreshed workflow guidance after a
reload or on reopening a conversation. Web Crypto must be available to approve
a card. Portal links outside the URA domain policy need explicit review before
being introduced.

Frames after `done` are ignored. On the extractive fallback paths that send no
`grounding` frame, the server flushes its buffered `agent_trace` after `done`,
so those replies, which are not built from tool results, show no connector note.

Client controls do not replace server validation, prove legal correctness, or
establish exactly-once execution. Nested payload schemas, mandatory API digest
enforcement and distributed/staging validation remain open in the audit report.
No model, feature-flag default, backend route or role permission changes here.

## Confirmation

- `App/frontend/src/__tests__/lib/chatStream.test.ts`
- `App/frontend/src/__tests__/lib/safeUrl.test.ts`
- `App/frontend/src/__tests__/lib/turnMeta.test.ts`
- `App/frontend/src/__tests__/components/ChatMessage.test.tsx`
- `App/frontend/src/__tests__/components/WorkflowStepper.test.tsx`
- `App/frontend/src/__tests__/components/PortalCopilotCard.test.tsx`
- `App/frontend/src/__tests__/store/useChatStore.test.ts`
- `App/frontend/e2e/chat-flow.spec.ts`

Results and limitations: [verification runbook](../runbooks/intelligent-ui-integration.md).
