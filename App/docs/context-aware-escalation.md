# Context-aware escalation

When the bot hands a taxpayer to a human, the human should not have to
ask them to start again. That is the whole feature, and the 2026
handoff literature is blunt about the cost of getting it wrong: most
bot-to-human transfers lose context, and the ones that do measurably
raise handle time and drop satisfaction.

The pipeline existed before this work — triggers, a handoff packet, a
tickets table, admin endpoints. What it did not do was carry the
conversation.

## What an officer receives

`GET /v1/admin/tickets/{id}` returns the ticket plus:

| Field | What it is |
| --- | --- |
| `transcript` | The whole conversation, both sides, oldest first, with timestamps |
| `handoff.summary` | One-line statement of what the taxpayer needs |
| `handoff.topic` | Classified category — drives `required_details` |
| `handoff.sentiment` | Their state at the point of transfer |
| `handoff.transfer_style` | `warm` (brief the officer first) or `cold` |
| `handoff.turns_before_handoff` | How long they had been going |
| `handoff.required_details` | What to have ready before making contact |
| `response_judge` | Why the bot's own answer was not trusted |

`GET /v1/admin/tickets` is the queue view and deliberately omits the
transcript — fifty tickets should not ship fifty conversations.

## Why the transcript is copied, not joined

The obvious design is a join: the ticket has `conversation_id`, the
`conversations` table has the turns. It does not work here.

`conversations` is purged after `CONVERSATION_TTL_DAYS` (default 7). A
ticket has no TTL and can sit in a queue far longer. A live join would
therefore return an empty transcript for precisely the tickets that
have been waiting longest — the taxpayer who has been ignored for two
weeks is the one who least wants to start over.

So the transcript is snapshotted onto the ticket at escalation time.
The turn that *caused* the escalation is appended explicitly, because
the caller logs it to `conversations` only after `generate()` returns —
without that the officer would be missing the message that triggered
the handoff.

## Privacy

Three constraints, all deliberate:

- **Storage is redacted.** `redact_for_storage` runs on both sides of
  every turn before it is persisted, and `STORE_RAW_PROMPTS=true` is a
  hard startup failure in production (NDPA §19 data minimisation). The
  transcript an officer sees is therefore the redacted one — email,
  phone, TIN, NID, card and passport numbers appear as
  `[REDACTED_*]`. The *substance* of the conversation survives intact,
  which is what stops the repetition; the identifiers do not.
  Officer-visible unredaction would need a consent and audit story of
  its own and is not part of this work.
- **Erasure reaches the ticket.** Because the ticket now holds the
  taxpayer's words, `delete_user_cascade` deletes by `tickets.user_id`
  as well as by `conversation_id`. The old path resolved tickets only
  through `conversations`, so once those were purged an erasure request
  silently left the ticket — and its transcript — in place.
- **The notification carries no transcript.** See below.

## Delivery

The pipeline used to end at a database row: an officer had to poll and
notice. `app/escalation_notify.py` posts a webhook on ticket creation.

```
ESCALATION_WEBHOOK_URL            POST target; unset disables delivery
ESCALATION_WEBHOOK_TOKEN          bearer token, sent as a header
ESCALATION_WEBHOOK_TIMEOUT        seconds (default 5)
ESCALATION_WEBHOOK_MIN_PRIORITY   lowest priority to send (default normal)
```

The payload is triage metadata — id, priority, topic, sentiment,
transfer style, summary — on a field **allowlist**, so a column added to
the ticket later has to be opted in rather than leaking by default. It
never contains the transcript: a webhook is an external system, and
erasure cannot reach a third party's copy.

Delivery is best-effort by construction. The ticket is committed first,
dispatch runs on a daemon thread so a slow endpoint cannot delay the
taxpayer's reply, and the thread target cannot raise. A dead webhook
degrades to "the ticket is queued, nobody was paged" — the behaviour
before this change — never to a lost escalation.

## Queue behaviour

- **One conversation, one ticket.** `find_open_ticket` is consulted
  before creating; an open or assigned ticket for the same conversation
  is reused. Asking for a human three times used to open three tickets
  for three officers to each start cold. Resolved tickets do not block
  a genuinely new problem later in the same conversation.
- **Ordered by priority, then by waiting time.** Newest-first meant a
  computed priority changed nothing about what an officer saw. Within a
  priority the oldest now comes first, so a waiting taxpayer moves up
  instead of being buried by newer arrivals.
- **Failure is loud.** If persistence fails the taxpayer is still being
  told a human will follow up, so the handoff carries
  `ticket_persisted: false` and a `delivery_warning`, and the log line
  says `ESCALATION LOST`. It used to fail silently.

## Two production bugs this uncovered

Both pre-existed and both would have made the feature a no-op where it
matters. Production mandates `ANALYTICS_BACKEND=postgres`.

**Tickets were never dispatched to Postgres.** The dispatch block in
`database.py` re-bound conversation functions to the Postgres module but
not ticket functions, and `postgres.py` had no tickets table at all. So
every escalation was written to a per-replica SQLite file: invisible to
an officer whose request reached a different pod, lost on restart, and
pointing at a `conversation_id` in a different database.

**Postgres logged no conversations.** `postgres.log_conversation` was
missing the `contexts` and `user_id` parameters that all three call
sites pass. It raised `TypeError`, and every caller wraps the call in
`except Exception: logger.warning(...)`. The result was silent: no
multi-turn memory, no transcript to attach to an escalation, and nothing
for erasure to find.

`TestBackendParity` now asserts that every name in the dispatch block
exists in `postgres.py` and takes the same arguments, so neither can
recur quietly.

Signature parity turned out not to be enough. `postgres.get_ticket`
selects an explicit column list, and that list had drifted from the
table it queries — the `tickets` DDL grew `user_id` and the SELECT did
not, so on the backend production mandates the field silently vanished
from every ticket dict. A signature check cannot see inside a SQL
string. `TestTicketColumnParity` compares the declared columns against
the declared SELECT lists and needs no database to do it;
`TestLiveBackendRoundTrip` executes the real SQL when `POSTGRES_DSN` is
set.

### Verifying against a real Postgres

The unit suites use SQLite. To exercise the backend production actually
runs:

```bash
docker run -d --name ura-pg -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=ura \
  -p 55432:5432 postgres:16.6-alpine
POSTGRES_DSN="postgresql://postgres:pw@localhost:55432/ura" \
  python -m pytest tests/agents/test_backend_column_parity.py
```

Worth doing for any change touching `postgres.py`: the `user_id` defect
above passed every SQLite test and every signature check.

## The round trip (Phase 18)

Escalation used to be one-way: the officer resolved the ticket and
nothing went back. The taxpayer had been told a human would follow up,
and the answer sat in a queue only staff could see.

`PATCH /v1/admin/tickets/{id}` now accepts `officer_reply`. It is a
**separate field from `staff_note`** on purpose — `staff_note` stays
internal, and an officer's candid note ("caller obstructive, refer to
audit") reaching the taxpayer would be a serious incident. A test
asserts the note cannot leak into the delivered payload.

When the taxpayer next opens the conversation, `generate()` delivers the
reply before anything else — ahead of greeting detection and retrieval,
because a human's answer outranks anything the bot would say. Delivery is
marked *after* the text is composed, so a failure re-delivers rather than
silently dropping it: being told twice is a far smaller harm than never
being told.

### SLA

`first_response_at` and `resolved_at` are stamped on the ticket when they
happen, not computed on read, so they survive later edits. First response
means the first real officer touch — assignment, a note, a reply, or
moving the ticket off `open`; leaving it open is not a response.

`GET /v1/admin/tickets/sla?days=30` reports **medians**, not means: one
ticket left over a holiday weekend would otherwise make the whole queue
look broken. Medians stay period-scoped. `breaching`,
`breaching_first_response`, `breaching_next_reply`, and
`awaiting_next_response` use every `open`/`assigned` row so a 20-row
queue sample cannot under-count. After the first officer touch the
next-reply clock is `reply_at` or `first_response_at`.

## Routing

`_handoff_topic` has classified every escalation since the handoff
packet existed, and nothing acted on it — customs disputes sat next to
TIN registrations and officers triaged by reading each row.

Tickets now carry a `team`, mapped from the topic at escalation time:

| Topic | Team |
| --- | --- |
| `objection_or_dispute` | `disputes` |
| `account_specific` | `taxpayer_accounts` |
| `customs` | `customs` |
| `registration` | `registration` |
| `general_tax_support` | `general` |

Override any of them with `ESCALATION_TEAM_<TOPIC>` so a deployment
matches its own org chart without a code change. A topic the map has not
caught up with goes to `general` rather than nowhere.

`GET /v1/admin/tickets?team=customs` filters the queue, and the response
carries the team list in effect so the UI does not hardcode an org
chart.

## The staff queue

`/admin/tickets` (Next.js) renders the queue the backend orders —
urgent first, then longest-waiting — with the waiting time on every row
and the SLA strip showing first-response and next-reply clocks plus
population breach counts. Canned replies insert text only; they do not
send. If the case is assigned to someone else, Send is disabled until
Assign to me. `POST /v1/admin/tickets/{id}/presence` heartbeats the
officer so the case can show who else has it open.

Selecting a ticket shows **the whole transcript, unedited**. Summarising
it in the UI would put back exactly the problem the feature removes.

Two inputs, deliberately separate:

| Field | Goes to | Seen by |
| --- | --- | --- |
| Reply to the taxpayer | `officer_reply` | The taxpayer, on their next turn |
| Internal note | `staff_note` | Staff only |

Merging them into one "notes" box would be a privacy incident waiting
to happen, so the tests assert each control writes to its own field.

A warm transfer is flagged with its opening guidance, so an officer sees
the taxpayer's state before they make contact rather than discovering it
mid-call.

## Live events

`WS /v1/admin/tickets/stream` pushes escalations to staff as they
arrive. Staff roles only, read-only, and `?team=customs` narrows it to
one queue. The client sends nothing — a socket that only ever receives
cannot be used to reach the ticket store. The staff UI
(`TicketLiveBanner` on every `/admin` and `/agent` page) opens
`/api/v1/admin/tickets/stream` and invalidates the queue, SLA, and
stats queries on `escalation.created`.

The event carries the same triage metadata as the webhook and **no
transcript**: it fans out to every connected staff socket, which is the
wrong shape for a taxpayer's tax affairs. The officer fetches the
conversation through the authenticated admin API.

**It works across replicas.** A WebSocket lives on one pod while tickets
are created on whichever pod served the taxpayer, so an in-process hub
alone would show an officer only the tickets that happened to land
beside them — the per-replica failure the rest of this work removed.
Each pod publishes to a Postgres `LISTEN`/`NOTIFY` channel and fans out
to its own sockets. On SQLite, single-node by definition, the local hub
is the whole story and is correct.

Per-socket queues are bounded: a slow client drops its oldest events
rather than growing memory, and a reconnect re-reads the queue view
anyway.

## Still open

- **Officer-visible unredaction** — needs a consent and audit design,
  not just code. Deliberately out of scope.

- **No skills-based assignment within a team.** Routing puts a ticket
  in front of the right team; picking the individual officer is still
  manual.
- **Wider backend gap.** Consent, users, profiles, workflow sessions and
  the data-subject export/erasure helpers still have no Postgres mirror
  — the same class of bug as the two above, outside this feature's
  scope.
