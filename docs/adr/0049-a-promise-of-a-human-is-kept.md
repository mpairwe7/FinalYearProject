# ADR-0049: A promise of a human is kept

## Status

Accepted, 2026-11-13. Phase 2 of the
[13 November intelligent UI integration audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(G625–G633). It builds on [ADR-0048](0048-figures-and-disclosures-the-taxpayer-sees.md)
and keeps the AGENTS.md rule that every route into the ticket queue honours the
`ticket_queue` flag "rather than promise a handoff". The service owner asked for
the audit's fixes to be applied phase by phase in the session that made it.

## Context and Problem Statement

The chat can hand a taxpayer to a URA officer three ways: the HumanHandoff
button, the "Talk to an officer" chip, and an escalation the assistant decides
itself (the answer judge or the supervisor's ESCALATE route). Only the button
was wired end to end. The audit found the promise of a person broken in nine
places:

- a ticket the assistant opened itself was never followed, so the officer wrote
  into a chat the taxpayer never saw, and a crisis or well-being reply offered
  the officer only through the AI (G625);
- a case that had gone (404, for example after the Space lost its database,
  G616) left the taxpayer "with the officer" for good: every message failed and
  the assistant never answered again in that conversation, across reloads (G626);
- the ESCALATE route said "I've flagged it for a URA officer to review" when no
  ticket existed (G627);
- the Support Case room said an officer was reviewing the case whatever its
  status, a load failure included (G628);
- "New chat" stopped following a case with nothing to say where it was (G629);
- after an officer handed back, the notice implied the assistant knew what had
  been said, though it cannot see the officer chat (G630);
- an officer's message was machine-translated and shown as their own words (G631);
- a late-escalated turn still offered the inferred goal's guided steps (G632);
- a portal task resumed after sign-in posted to the AI while an officer held the
  chat (G633).

The W3C COGA voice and conversational guidance (2026 draft) asks that a human be
reachable and that an error not end the interaction; the UK AI Playbook and the
GOV.UK Service Manual ask that users know how to reach a person and that an
automated response be identified as one.

## Considered Options

For G630, what the assistant knows after a hand-back:

1. **Merge the officer thread into the conversation context.** It would let the
   assistant build on the officer's resolution, but it puts text the model did
   not write, and the officer may have pasted, into the prompt, which is a
   prompt-injection and personal-data question of its own (ADR-0021's rule that
   text from elsewhere reaches the model only as spotlighted evidence would need
   extending). Deferred.
2. **Say plainly that the assistant has not seen the officer chat** (chosen for
   now). The taxpayer knows to repeat what matters.

For G629, a case left in another conversation:

1. **Poll every open case in the background.** More requests on low-bandwidth
   phones and a notification design the chat does not yet have.
2. **Mark the conversation in the rail and say where the case is** (chosen).

## Decision Outcome

1. **The chat follows a ticket a turn opened.** `ticketIdFrom` (`turnMeta.ts`)
   reads `ticket_id` from REST and from the stream's `metadata`, `grounding` and
   `done` frames; a valid id on the current response becomes the conversation's
   active ticket, so `useOfficerChat` polls it as it does for the button. A
   crisis or well-being reply shows the HumanHandoff button.
2. **A case that is gone hands back.** A 404 or 422 from the thread poll or a
   reply ends the officer chat with the translated notice
   `officerChat.caseGone` and clears the case, so the assistant answers again;
   the unsent text stays in the composer.
3. **No ticket, no promise.** Both ESCALATE branches (REST and stream) reply with
   `ESCALATION_NOT_QUEUED_REPLY` when `_maybe_create_ticket` returns nothing. It
   is the same sentence `POST /v1/escalate` uses (`_ESCALATION_QUEUE_OFF_MESSAGE`
   now points at it).
4. **The case room says where the case stands.** Open: waiting for an officer to
   pick it up. Assigned: the team that has it. Won't fix: closed without further
   action, and how to get help. Resolved: marked resolved. A case that cannot be
   loaded shows the failure and promises nothing. All of it is translated.
5. **The rail shows open cases.** A conversation with an active case is marked
   "Case open" or, while an officer is chatting, "Officer chatting"; starting a
   new chat announces where the case still is.
6. **The hand-back notice is honest.** It says the assistant has not seen the
   officer chat.
7. **A machine translation is marked.** `GET /v1/escalate/{id}/messages` returns
   `translated` and `original_text` for an officer's message the taxpayer reads
   in translation; the chat labels it and offers what the officer wrote.
8. **No guided steps on an escalated turn.** The objective card is hidden once a
   turn escalates, as the backend already withdraws its guided-step actions.
9. **Nothing reaches the AI while an officer holds the chat.** A portal task
   submission is not sent, and a resume after sign-in waits until the officer
   chat ends.

## Consequences

- A taxpayer whose turn escalated sees the waiting banner at once and gets the
  officer's messages in the same chat, without pressing a second button.
- A lost case costs the taxpayer one notice instead of a dead conversation; the
  officer side of a lost case is unaffected.
- With the queue off or a failed ticket write, the ESCALATE route now says the
  assistant cannot pass the question on and gives the phone lines. The turn is
  still marked `escalation_required`, so the banner and contacts show.
- The assistant still does not know what the officer said; only the notice
  changed (option 1 above stays open as part of G630).
- New strings are machine-assisted and unreviewed in four languages (G275).

## Confirmation

Each test was watched failing before the fix; for G625 and G627 the fix was also
reverted after it passed and the tests failed again:

- `App/frontend/e2e/officer-chat.spec.ts`: a ticket in the stream's grounding frame
  is followed with no button press: the waiting banner shows, the thread is polled,
  and the officer's message arrives.
- `App/frontend/src/__tests__/hooks/useOfficerChat.test.ts`: a 404 or 422 from the
  poll, or a 404 from a reply, hands back with the `notice-gone-…` notice and clears
  the case; a translated message keeps `machineTranslated` and `originalText`.
- `tests/agents/test_supervisor_route_contract.py`: with no ticket (empty or none)
  the REST reply has no "flagged it for a URA officer" and has the not-queued
  sentence; with a ticket it still promises.
- `App/backend/tests/test_streaming_language_parity.py`
  (`test_supervisor_escalation_promises_no_officer_without_a_ticket`).
- `App/frontend/src/__tests__/components/SupportCaseModal.test.tsx`: open, assigned,
  closed and unloadable cases, and Swahili.
- `App/frontend/src/__tests__/components/ConversationRail.test.tsx`: "Case open" and
  "Officer chatting".
- `App/backend/tests/test_officer_chat.py`: `translated` and `original_text` for a
  translated officer message, and their absence otherwise.
- `App/frontend/src/__tests__/components/ChatMessage.test.tsx`: the officer offered on
  crisis and well-being replies; no guided steps on an escalated turn; the
  translation label and the original on request.
