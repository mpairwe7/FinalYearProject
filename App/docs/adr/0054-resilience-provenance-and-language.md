# ADR-0054: Resilience, provenance and the taxpayer's language

## Status

Accepted, 2026-11-13. Phase 6 of the
[13 November intelligent UI integration audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(G650–G655). The service owner asked for the audit's fixes to be applied phase by
phase in the session that made it.

## Context and Problem Statement

The last phase covers what is left between the AI's output and the taxpayer once
the main paths are right:

- restored history skipped the validators live metadata goes through, and one
  card that threw replaced the whole chat with the error page, which threw again
  on "Try again" (G650, the open remainder of G274);
- web citations lost their provenance: an adviser's search snippet looked like a
  URA document, a fiscal year regexed out of the snippet read as the effective
  date, and a web-grounded answer could be badged "Well grounded" (G651);
- the REST fallback dropped `connectors_used` (G652);
- discrepancy detection ran only on REST, so the web chat never logged a
  taxpayer's correction, and a failed feedback-comment save was silent (G653);
- a document's extracted fields (TINs, NINs) were written to browser storage
  (G654);
- the consent notice, the status announcements and the screen-reader names of
  who spoke were English literals (G655).

## Decision Outcome

1. **Restore validates and contains.** Restored turns go through `citationsFrom`,
   `resourcesFrom`, `workflowFrom` and `score`; `workflowFrom` also rejects a
   non-list `prerequisites`, `parent_goals`, `option_labels`,
   `form_draft.fields` or `journey_gps.parent_goals`, and keeps only objects in
   those lists (strings in `option_labels`), since the stepper reads properties
   off each element. Each message renders inside
   `MessageBoundary`, which replaces a turn that throws with a translated notice
   and leaves the rest of the chat.
2. **Web evidence says what it is.** `build_citations` carries `authority`,
   `evidence` and `stale` from web hits (`Citation` in `models.py`); a search
   summary gets no effective date; the sources list labels each kind, also when a
   citation repeats a listed resource's link; an answer citing web evidence is
   never badged well grounded.
3. `applyTurnMeta` maps `connectors_used`.
4. **Corrections are recorded on both transports.** `record_discrepancy` is
   shared by REST and `run_chat_turn`; the stream reads the previous turn, with
   the sources the challenged answer cited, when its result carries no history,
   and the metadata frame carries the report. A blocked turn (a paused
   principal, G464) records nothing and reads no history, on either hook. A
   comment that does not save says so and is kept.
5. **Analyses are not persisted.** `withoutTransientCards` strips attachment
   analyses before writing to storage; the inspection view says why a reloaded
   document has none.
6. **The taxpayer's language.** The consent notice, every status announcement
   and the screen-reader speaker names are dictionary keys; a source scan test
   keeps literals out of those places. A reply's completion is announced in the
   language the reply adopted, not the one in use when the message was sent. The screen-reader name for an answer is
   "The AI assistant replied".

## Consequences

- Corrupt or older stored cards disappear on reload instead of breaking the
  chat; the answer text stays.
- On the stream, a correction is acknowledged by the badge, not by the sentence
  REST puts before its reply.
- After a reload, "Inspect" on an earlier document has no analysis; the document
  must be attached again.
- 195 literals were counted; this phase translates the ones that carry consent,
  status and identity. The rest of G655 (the Support Case room's remaining
  labels, the document view's field names and others) stays open.
- The consent wording itself is unchanged; whether "No personal data is stored"
  holds with consent given is for the privacy owner (noted in the plan).

## Confirmation

Each test was watched failing before the fix:

- `App/backend/tests/test_discrepancy_and_precedence.py`: a blocked turn on the
  stream reads and records nothing; the fallback history keeps the challenged
  answer's sources.
- `App/frontend/src/__tests__/lib/turnMeta.test.ts`: null elements in the nested
  workflow lists are dropped.
- `App/frontend/src/__tests__/components/SourcesPill.test.tsx`: a citation that
  repeats a resource's link keeps its warnings.
- `App/frontend/e2e/chat-flow.spec.ts`: a completion is announced in the language
  the reply adopted.
- `App/frontend/src/__tests__/store/useChatStore.test.ts`: corrupt stored citations,
  resources, workflows and scores are dropped; no extracted field reaches storage.
- `App/frontend/src/__tests__/components/MessageBoundary.test.tsx`.
- `App/backend/tests/test_web_citation_provenance.py`;
  `App/frontend/src/__tests__/components/SourcesPill.test.tsx`;
  `App/frontend/src/__tests__/components/ChatMessage.test.tsx` (no badge on web
  evidence); `App/frontend/src/__tests__/lib/turnMeta.test.ts`.
- `App/backend/tests/test_discrepancy_and_precedence.py`
  (`TestDiscrepancyOnTheStream`); `App/frontend/src/__tests__/components/FeedbackButtons.test.tsx`.
- `App/frontend/src/__tests__/components/DocumentInspectionModal.test.tsx`.
- `App/frontend/src/__tests__/lib/taxpayerStrings.test.ts` (fails against the old
  code, and found one more literal on the REST path).
