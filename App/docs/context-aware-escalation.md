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

## Still open

- **Officer-visible unredaction** — needs a consent and audit design,
  not just code. Deliberately out of scope.
- **No round trip.** Resolving a ticket does not reach the taxpayer.
- **No assignment routing.** `topic` is computed but not used to route;
  `assignee` is still set by hand.
- **Wider backend gap.** Consent, users, profiles, workflow sessions and
  the data-subject export/erasure helpers still have no Postgres mirror
  — the same class of bug as the two above, outside this feature's
  scope.
