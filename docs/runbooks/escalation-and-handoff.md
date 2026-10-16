# Runbook — escalation policy and structured text handoffs

How the text chat decides to offer an officer, how the ticket it raises is
prioritised and briefed, and how to roll the two Phase 38 flags out and back.
Design and findings: [`docs/plans/agentic-cx-phase38-plan.md`](../plans/agentic-cx-phase38-plan.md);
gap rows G156–G164 in
[`docs/GAPS_AND_AGENTIC_ROADMAP.md`](../GAPS_AND_AGENTIC_ROADMAP.md).

## What each flag changes

| Flag (default off) | When on |
| --- | --- |
| `eq_stakes_escalation` | `app/escalation_policy.assess_escalation` weighs affect, stakes (enforcement, a deadline today, a large amount), effort and confidence. **In the conversation:** "Talk to an officer" is offered — first in the list when urgent, after the answer otherwise. **On every ticket** (auto-escalation or the taxpayer's own button): priority and the response target come from the strongest reading of the last three messages, so the button's empty message does not erase what was said before it. It only offers; the taxpayer decides. |
| `structured_text_handoff` | `app/officer_handoff.enrich_handoff` (called once, in `ChatModel._maybe_create_ticket`) attaches a `HandoffEnvelope` v2.1 to the ticket and prunes the details to ask for against what the taxpayer has already given. The button's placeholder hand-off (`topic: general`, nothing to ask) takes its topic — and so its **team** — and its details from the conversation. |
| `continuous_relationship_memory` (existing) | Web chat opens and moves the taxpayer's resolution case per tax domain (one case per domain; objective is a fixed phrase). Needs the taxpayer's `personalization` consent. |

The flags are independent. `structured_text_handoff` without
`eq_stakes_escalation` briefs the officer but leaves priority as it was.

## Ramp

Flags are addressable (see "Addressable rollout" in
[`RAG_ARCHITECTURE.md`](../RAG_ARCHITECTURE.md)); no deploy is needed:

```bash
FLAG_EQ_STAKES_ESCALATION_PERCENT=10        # a share of signed-in taxpayers
FLAG_STRUCTURED_TEXT_HANDOFF_COHORTS=ura_staff,internal
```

Anonymous taxpayers have no subject to bucket, so a percentage rollout reaches
them only by the flag's default. Pilot with staff cohorts first, then widen.

Tunables (env, read on every call): `ESCALATION_OFFER_TURNS` (6, minimum 2),
`ESCALATION_STAKES_AMOUNT_UGX` (10,000,000), `EQ_HARDSHIP_SLA_TARGET_SECONDS`
(45). The urgent response target is fixed at 30 s, `high` 60 s, `normal` 120 s.

## What to watch

| Signal | Where | Healthy |
| --- | --- | --- |
| `ura_cx_escalation_tier_total{path="offer",tier=…,trigger=…}` | `/metrics` | `priority` is a small share of `offer`; a jump after a release means a lexicon change widened the net |
| `ura_cx_escalation_tier_total{path="ticket",…}` | `/metrics` | tracks `ura_escalation_requested_total{outcome="created"}` |
| `ura_cx_case_events_total{domain=…,event=…}` | `/metrics` | `updated` ≥ `opened`; a large `general` share means domains are not being named |
| Ticket queue, urgent filter | `/admin/tickets` | urgent tickets carry "Prioritised: …" in the **Handoff brief** with a reason an officer agrees with |
| Officer feedback | desk | the "Already given — do not ask again" line matches what they see in the transcript |

A rising share of urgent tickets is the signal to look at: review a sample
of the reasons in the brief, not only the count. If officers disagree with
the reasons, raise `ESCALATION_STAKES_AMOUNT_UGX` or switch the flag off for
that cohort.

## Roll back

Each flag reverts only its own behaviour; to revert all of Phase 38, switch
both off (`=false`, or remove the percentage). New turns and tickets revert at
once:

* `FLAG_EQ_STAKES_ESCALATION=false`: no offer beyond the legacy "sustained
  distress" one, and tickets keep the priority their path gave them
  (`normal` for the button) with no "Prioritised: …" reasons.
* `FLAG_STRUCTURED_TEXT_HANDOFF=false`: no envelope, no "Already given" line,
  nothing pruned, and the button's ticket keeps topic `general` (team
  `general`) with no details to ask for.

Tickets already raised keep what they were given. Resolution cases are
consent-gated state and age out on `RESOLUTION_CASE_TTL_HOURS`.

## Privacy and limits

* The envelope carries slot **names** (`tax type`, `filing period`, `tin`) and
  never their values; the TIN and the answers are reduced to "provided". The
  conversation transcript on the ticket is the redacted one it always was.
* A workflow's answers are read under the owner-scoped state key the chat ran
  under. A guided workflow stored under another owner's key is never read.
* Stakes cues are written for English, Luganda and Kiswahili, plus the
  enforcement phrases the shared lexicon (`text_signals._HARDSHIP_RE`) lists
  for Runyankole and Acholi. "A deadline today" has no `nyn`/`ach` cue: those
  turns are also read through their English rewrite when machine translation
  produced one. A hardship message in a language with no stakes cue is still
  offered a person, but is not put ahead of the queue. Native review of the
  vernacular cues is an open item (plan §8).
* The policy is deterministic and offline (no model call); the same message
  and history give the same tier, which is what makes it testable.
* `POST /v1/escalate` now passes the caller's tenant. Before, a signed-in
  taxpayer in a non-default tenant raised the ticket under `default`.

## Verify after a change

```bash
cd App/backend && pytest tests/test_escalation_policy.py tests/test_turn_cases.py \
  tests/test_taxpayer_escalation.py -q
pytest tests/agents/test_phase38_cx.py -q        # root suite: the service paths
```

Live (isolated api, flags on): drive hardship + enforcement through
`/v1/chat` and `/v1/chat/stream`, press the button after a guided workflow,
then open the ticket in `/admin/tickets` as a staff user and read the
Handoff brief.
