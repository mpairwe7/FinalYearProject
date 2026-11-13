# Runbook — escalation policy and structured text handoffs

Call records update (2026-11-11, ADR-0040): `update_ticket(officer_touch=False)`
is how the system writes to a ticket without counting as an officer's first
response; the end-of-call summary uses it and no longer replaces an officer's
note (G587). A call wrap-up may resolve a ticket only after the call has ended,
and is accepted only from an officer who handled the call (G305). With
`multi_tenant` on, "is an officer on shift" counts the caller's tenant's
officers only (G584).

Call handoff update (2026-11-11, ADR-0039): a call transfer's ticket is created
in the caller's tenant (it went to `default`, G571), and is written off the
event loop, so a non-English transfer no longer stalls other live calls while
the question is translated (G577). The call's timeout and error lines no longer
say an officer is coming; the transfer does, after it has checked `ticket_queue`
and staffing (G572), and with the queue off the call offers no officer at all.
A waiting caller in crisis is raised to urgent on the officers' screens even
when the row cannot be written (G570).
[Review](../plans/receptionist-assurance-2026-11-11.md).

Call wrap-up update (2026-10-30, ADR-0020): ticket actions honor `ticket_queue`
before saving the outcome (409 when unavailable; 400 for an unknown action).
Created tickets retain the call's tenant, conversation and locale. Reconnect
expiry revokes old media before requeueing; failed revocation terminates the
call. [Assurance review](../plans/receptionist-assurance-2026-10-30.md) records
the remaining ownership, atomic-write and production media gates. Direct local
model calls in `receptionist/brain.py`, `brief.py` and `summary.py` enforce
bounded timeouts and share a fail-fast cooldown (`_VLLM_BRIDGE_DOWN_UNTIL`,
`G365`). The English call path in `brain.py` does not use that cooldown. A
stalled vLLM response on that path can hold the call turn for the full 25 s
before the timeout transfer runs.

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
| `cxp_business_hours` | Phase 40 (`G174`): `app/escalation_policy.evaluate_cxp_schedule` and `business_hours_elapsed_seconds` enforce the timezone-aware URA CXP operating window (`07:00–23:00 EAT` in `Africa/Kampala`, with live-officer presence override). When CXP is closed and no officer is online, `POST /v1/escalate`, `EscalateToHumanTool`, auto-escalated chat turns, and `UraReceptionistBrain._transfer` (`after_hours_callback`) queue the case for priority morning review/callback, set `sla_clock: "cxp_business_hours"`, and return 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) after-hours disclosures (`after_hours_escalation_notice`, `after_hours_escalation_message`). |
| `continuous_relationship_memory` (existing) | Web chat opens and moves the taxpayer's resolution case per tax domain (one case per domain; objective is a fixed phrase). Needs the taxpayer's `personalization` consent. |

The flags are independent. `structured_text_handoff` without
`eq_stakes_escalation` briefs the officer but leaves priority as it was.

## Outside officers' working hours (no flag)

The AI answers all night; officers work the desk's hours — `CXP_TIMEZONE`,
`CXP_OPEN_HOUR`/`CXP_CLOSE_HOUR` (default 07:00–23:00 `Africa/Kampala`),
`CXP_WORKING_DAYS`, `CXP_CLOSED_DATES` (`app/officer_hours.py`; the call
receptionist uses the same settings). Outside them:

* **Every escalation** — the taxpayer's button, auto-escalation, the
  `escalate_to_human` tool — still tries to open (or reuse) the ticket, at any
  hour. When the ticket is saved, the turn carries `officer_hours` (top level,
  and on the handoff), and the chat's escalation banner adds *"Officers work
  {hours}. Your question is queued, and an officer will reply here from
  {next shift}."* — the next shift's weekday, date and time in the reader's
  language and time zone (en, lg, sw, nyn, ach) — and drops the toll-free
  numbers while the desk that answers them is closed. When the ticket cannot be
  saved, the turn carries no officer hours and nothing says the question is
  queued. The banner itself is translated now; it was English in every locale
  (G199).
* **`POST /v1/escalate`** answers with the off-duty message (no toll-free line —
  the same desk answers it) and the `officer_hours` object.
* **SLA** — breaches count only officers' working seconds
  (`officer_hours.working_seconds`): a handoff raised at 23:15 has waited for
  nobody until 07:00. `sla_stats` says so (`sla_clock: "officer_hours"`, with the
  hours). The medians stay wall-clock: they are what the taxpayer waited.

Check: set `CXP_CLOSED_DATES` to today on a test instance and press **Talk to a
URA officer** — the reply names the hours and the next shift.

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
| `ura_ticket_refresh_failed_total` | `/metrics` | 0. A re-escalation that could not update its open ticket: the officer works from the earlier snapshot (G641); the handoff carries `ticket_refresh_failed` |
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

## Routes bind (G309)

The supervisor's ESCALATE and specialist routes apply on REST and SSE alike, whichever
branch produced the decision. Handoff phrasing ("connect me to a supervisor", "transfer
me to a person") is matched before every other intent, so a stray "now" cannot route an
upset taxpayer to today's date. To confirm after a change, ask for a person **on an index
that returns passages**: an empty index abstains and opens a ticket by another path,
which is how G309 stayed hidden.

## Tools and held actions (ADR-0022)

`escalate_to_human` goes through `_maybe_create_ticket` by way of `ESCALATION_ROUTE`, so a
ticket from the model has the conversation, transcript and team, dedupes against an open
ticket, and is announced. Outside a turn, such as an MCP server, it records the owner and
tenant but has no conversation. If the route fails the tool reports failure, and the model
cannot promise a handoff.

No tier, throttle or guardrail can hold this tool. With `risk_tiered_autonomy_gates` on, a
`high` action is held and opens a ticket whose reason names the action. There is no approval
endpoint: the officer completes the action in their own system or declines it, and
`critical` actions are refused outright.

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
* The taxpayer's case routes (`GET /v1/escalate/{id}`, `/reply`, `/messages`,
  `/csat`) take no login, so they answer to the **exact ticket UUID** only
  (G243). Until 2026-10-23 the status and reply routes fell back to the staff
  ticket *search*: any word from someone's case, or a bare wildcard, opened its
  reason, query and transcript, and a reply landed on it as that taxpayer's
  words. A taxpayer who lost the id asks in the chat that raised the case: the
  chat answers "status of `TIC-…`" from the requester's own tickets only
  (signed-in subject, or the session the request presents) and, like every
  fast path, only after the input guard and the crisis check.
* A ticket search (`list_tickets(q=…)`, the staff queue's search box) escapes
  `%` and `_`, so a search for either matches that character, not every row.
* Voice call transfer and hold notices spoken through `UraSpeechTTS` (`receptionist/tts.py`)
  share `clean_text_for_speech` and `apply_tts_engine_phonetics` (`Tin`, `Nin`, `Vat`, `Payee`,
  `Efris`, `U R A`, `P R N`, `Runyankole`, `Acholi`) with Main Chat (`G439`, `G440`).

## In the taxpayer's chat (ADR-0049, 2026-11-13)

- **Any turn that opens a ticket is followed.** The chat reads `ticket_id` from
  `/v1/chat` and from the stream's frames, as it does from the HumanHandoff button,
  so the waiting banner shows and the officer's messages arrive in that chat. A
  crisis or well-being reply shows the HumanHandoff button (G625).
- **No ticket, no promise.** The supervisor's ESCALATE route says
  `ESCALATION_NOT_QUEUED_REPLY` ("This assistant cannot pass your question to an
  officer right now…" and the toll-free lines) when `_maybe_create_ticket` returns
  nothing, on REST and the stream, exactly as `POST /v1/escalate` does (G627). If
  taxpayers report that sentence, check the `ticket_queue` flag and the logs for
  `ESCALATION LOST` before anything else.
- **A lost case hands back.** A 404 or 422 on the thread or a reply ends the officer
  chat in the browser with "This support case is no longer available…" and the
  assistant answers again (G626). On the Space this follows every restart that
  loses the database (G616): expect it there until storage persists.
- **The case room** speaks to the real status: open (waiting for an officer),
  assigned (the team that has it), won't fix (closed, call 0800 117 000),
  resolved, or could not be loaded (G628).
- **The rail** marks a conversation holding a case "Case open" or "Officer
  chatting" (G629). The hand-back notice tells the taxpayer the assistant has not
  seen the officer chat (G630). An officer's translated message is labelled with
  what they wrote one click away (G631).

## On the officer's desk (ADR-0050, 2026-11-13)

- **Translations keep the figures.** The reply and End-chat routes drop a supplied
  translation whose figures differ from the English and translate again;
  `officer_reply_translation_rejected_total` counts it. A rising count means
  officers are sending previews of text they then changed, or a translation
  backend is shifting numbers: read the logs for "translation dropped".
- **Canned results are templates.** "EFRIS invoice check", "WHT credit check",
  "Customs documents check", "Objection (Sec 24)" and "TIN status" insert text
  with `[[…]]` parts; the composer will not send until each is replaced with what
  the officer actually found.
- **What the AI did** heads every case: whether the claim check ran, a withheld
  figure, a judge revision, the judge's confidence (all labelled as a model's)
  and the sources reviewed. "No record … read its answer as unchecked" means the
  ticket was raised without a judge verdict (ADR-0051).
- **A re-escalation updates the ticket**: answers given since the last snapshot
  are appended and the priority rises if the new one is higher (G641).
- **Hotfixes from discrepancy reports** need a statute reference for a precedence
  rule and show what they change before they apply; each write is on the audit
  trail (`staff.precedence_created`, `staff.tombstone_created`,
  `staff.override_saved`, `staff.discrepancy_verified`).

## Verify after a change

Run from the repository root; each directory change stays in its subshell.

```bash
(cd App/backend && pytest tests/test_escalation_policy.py tests/test_turn_cases.py \
  tests/test_taxpayer_escalation.py tests/test_officer_chat.py -q)
pytest tests/agents/test_phase38_cx.py -q        # root suite: the service paths
pytest tests/agents/test_supervisor_route_contract.py -q   # G627: no ticket, no promise
pytest tests/test_answer_integrity_integration.py -k AnswersOnlyToItsOwner -q   # G243 BOLA
(cd App/frontend && bunx vitest run src/__tests__/hooks/useOfficerChat.test.ts \
  src/__tests__/components/SupportCaseModal.test.tsx \
  && CI=1 bunx playwright test e2e/officer-chat.spec.ts --project=chromium)   # G625
```

Live (isolated api, flags on): drive hardship + enforcement through
`/v1/chat` and `/v1/chat/stream`, press the button after a guided workflow,
then open the ticket in `/admin/tickets` as a staff user and read the
Handoff brief.
