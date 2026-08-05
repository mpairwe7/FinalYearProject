# Deadline reminders

Everything up to now is reactive: the taxpayer asks, the system answers.
A reminder is the opposite — the system reaches out — and that is a
different kind of act.

## Contact is its own consent purpose

Reaching a taxpayer unasked is a **new processing purpose**, not a new
feature on an existing one. Consent to personalisation is consent to a
tailored answer; it is not consent to be messaged. So reminders gate on
`deadline_reminders`, and the gate is the **first thing the selector
does** rather than a check a caller is trusted to remember — the act
being authorised is the contact, so the authorisation belongs where the
contact is decided.

A test asserts that a taxpayer who has granted `personalization` and
nothing else receives nothing.

Withdrawal stops contact on the next selection, with no cache to expire.

## Defaults are the quiet ones

`ReminderPreferences` defaults to `enabled=False`. A profile written by
an older client has no preferences at all, and a missing preference must
never be the thing that turns messaging **on**. Lead time defaults to 3
days and is clamped to 30 — a reminder a month early is noise, and noise
is how a channel gets muted.

| Preference | Default | Notes |
| --- | --- | --- |
| `enabled` | `false` | Opt-in, never inferred |
| `lead_days` | `3` | Clamped to 0–30 |
| `tax_types` | *(empty)* | Empty means "whatever applies to me", from the profile |
| `channels` | `["in_app"]` | The channel that needs no external provider |

## What this does not do

It selects; it does not send. Email and SMS need provider credentials
and a deliverability story, and a selector that is pure and offline can
be tested exhaustively while a sender cannot. `due_reminders()` is the
seam a channel plugs into.

It also returns a **reason** when it selects nothing, rather than an
empty list. A scheduler iterating thousands of taxpayers should be able
to answer "why was nobody reminded?" — and one bad profile must not stop
the run. A consent-store failure returns `consent lookup failed` and
selects nothing: it fails closed, not open.

## A schema note

The allowed consent purposes were written twice — once in the
`ConsentPurpose` enum and once in a SQL `CHECK` constraint — so adding a
purpose to the enum left the database rejecting it. The constraint is
now derived from the enum.

SQLite cannot alter a `CHECK`, so an existing database keeps whichever
list it was created with. `_refresh_consent_purpose_check` rebuilds the
table when the stored DDL is stale, preserving every receipt and keeping
the constraint enforcing — the remedy is not "drop the check". It runs
only when needed; the normal path costs one query against
`sqlite_master`.

Postgres has no such constraint, so the two backends differ in
strictness. The Pydantic enum is the real gate — it rejects an invalid
purpose with a 422 at the API boundary — and the `CHECK` is defence in
depth behind it.
