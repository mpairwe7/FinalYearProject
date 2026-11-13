# ADR-0051: Officers see what the AI did

## Status

Accepted, 2026-11-13. Phase 4 of the
[13 November intelligent UI integration audit](../plans/intelligent-ui-integration-audit-2026-11-13.md)
(G640–G644). The service owner asked for the audit's fixes to be applied phase by
phase in the session that made it.

## Context and Problem Statement

An officer taking over a case is the human check on the AI. The workbench gave
them less than the system knew:

- the case showed the AI's reply as "Assistant" text only, though the ticket held
  the judge's verdict (the claim check did not run, a figure was withheld as
  contradicted, a revision) and the handoff listed the sources it reviewed; the
  escalated turn's own sources were stored empty (G640);
- answers the AI gave while the taxpayer waited never reached the ticket, and a
  second escalation reused it without raising its priority, so the officer worked
  from the first snapshot (G641);
- the machine translation of the taxpayer's words was shown as "Taxpayer [EN]"
  (G642);
- an auditor was offered "assign to me", which the API refuses silently (G643);
- "Found helpful by taxpayers" showed 0% "Below target" when nothing was rated or
  the request failed (G644).

A meta-analysis of human–AI decision making (Vaccaro et al., Nature Human
Behaviour 2024) found pairs often do worse than the better of the two alone;
OWASP ASI09 asks that a reviewer see "unverified source" cues and plain-language
risk summaries, not model rationales presented as fact.

## Decision Outcome

1. **"What the AI did"** on every case: the claim check not running, a withheld
   figure, a judge revision (named as a model's), the judge's confidence (a
   model's estimate) and its reasons, and the sources reviewed; with nothing
   recorded, it says to read the answer as unchecked. The escalated turn keeps the
   handoff's `sources_reviewed`.
2. **A reused ticket is brought up to date** (`db.refresh_open_ticket`): the turns
   since its last snapshot are appended, taxpayer follow-ups stay where they are,
   and the priority is raised, never lowered.
3. **A machine translation is labelled** as one ("for search; may differ") and the
   taxpayer's own words stay beside it in every transcript mode.
4. **Only officers are offered "assign to me"** (`isLiveOfficer`), as the API allows.
5. **No data is not zero.** `SloGaugeCard` takes `value: null` with a reason and
   shows no meter or verdict; the satisfaction card passes null with no ratings or
   a failed request.

## Consequences

- An officer sees, before replying, whether the answer they are taking over was
  checked; the judge's verdicts are labelled as a model's, so they inform rather
  than decide.
- A ticket's transcript grows with each re-escalation; the case view says it is
  brought up to date each time.
- The quality timeline's per-answer confidence (noted by the audit) is not yet
  shown; it is outside this phase.

## Confirmation

Each test was watched failing before the fix:

- `App/frontend/src/__tests__/components/TicketCaseChat.test.tsx`: the evidence block
  with every signal and the sources; the "unchecked" note with none; the machine
  translation labelled with the original kept.
- `tests/agents/test_escalation_context.py`: the escalated turn names its sources;
  a reused ticket gets later turns and a higher priority, never a lower one.
- `App/frontend/src/__tests__/app/adminTickets.test.tsx`: no "assign to me" for an
  auditor, and the key does nothing; still offered to an officer.
- `App/frontend/src/__tests__/components/charts/plainLanguage.test.tsx`: a null gauge
  shows its reason and no verdict.
