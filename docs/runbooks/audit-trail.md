# Runbook — audit trail and the auditor role

What the audit trail records, who can read it, how to prove it has not been
altered, and how to hand evidence to an external auditor. Written 2026-09-29
after a QA audit of the auditor dashboard (gaps G66–G69 in
[`docs/GAPS_AND_AGENTIC_ROADMAP.md`](../GAPS_AND_AGENTIC_ROADMAP.md)).

## Separation of duties

| Action | `ura_staff` (officer) | `ura_admin` | `ura_auditor` |
| --- | --- | --- | --- |
| Read tickets, transcripts, calls, analytics | yes | yes | yes |
| Change a ticket, reply to a taxpayer, heartbeat a case | yes | yes | **no (403)** |
| Take, hold, transfer or end a call | yes | yes | **no (403)** |
| Toggle a flag, edit a staff-written answer | no | yes | **no (403)** |
| Read and verify the audit trail (`/admin/audit`) | **no (403)** | yes | yes |

Every "no" is enforced by the API (`_require_staff_writer`,
`_require_audit_reader`, `_desk_writer`, the admin checks on flags and
overrides), not only hidden in the console. Before 2026-09-29 the ticket
endpoints relied on the console alone: an auditor's direct API call could
resolve a ticket or send a reply to a taxpayer.

The operator key (`INDEX_API_KEY`, break-glass) passes these checks and is
recorded as actor `operator-key`.

## What is recorded

With `audit_ledger` on (production refuses to start without it), each of
these appends one hash-chained row to `audit_events`:

| Event type | When | Payload |
| --- | --- | --- |
| `staff.ticket_viewed` | A ticket and its transcript are opened | `ticket_id` |
| `staff.ticket_updated` | A ticket change is accepted | `ticket_id`, changed `status` / `assignee` / `priority` / `locale`, `staff_note_chars`, `officer_reply_chars` |
| `staff.flag_set` / `staff.flag_cleared` | A flag is toggled or reset | `flag`, `enabled` |
| `staff.override_saved` / `staff.override_deleted` | A staff-written answer changes | `override_id`, `enabled` |
| `generate` | The assistant answers (existing) | hashes of question and reply, route, scores |
| `tool_confirm` | A taxpayer confirms or refuses an action (existing) | tool, decision |
| `voice_*` | Voice consent and recording events (existing) | voice audit id, audio hash |
| `erasure_tombstone` | Personal data erased on request (existing) | hash of the erased user id |

Every staff row also carries `actor_role`; the row's `user_id` is the actor.
**Content is never copied into the ledger**: a reply or a note is recorded as
its length, so the audit trail adds no second copy of taxpayer data. A failed
append is logged and counted (`audit_append_failed_total` on `/metrics`) —
alert on any increase.

## Read it

`/admin/audit` (nav: Observe → Audit trail). The integrity check runs first
and states whether the record is intact; filters choose the event type, the
person and the period; "Show older events" pages back. APIs:
`GET /v1/admin/audit/events` and `GET /v1/admin/audit/verify`
([`API_REFERENCE.md`](../API_REFERENCE.md)).

## Prove it has not been altered

The page's verdict comes from `verify_chain`, which recomputes every row's
fingerprint from its stored payload and checks each row points at the one
before. An edited, deleted or reordered row appears as a break at its
sequence number, and everything after it stops being evidence until the
cause is found. Treat a break as a security incident.

For a large ledger, or for an external auditor who should not rely on this
server's answer, verify offline against a database copy:

```bash
PYTHONPATH=App/backend python3 -m app.audit.verifier --tenant default
```

Compare the head hash with the latest Merkle anchor (`audit_anchors`, shown on
the page): an anchor published outside the system is what makes a rewrite of
the whole chain detectable too. No job anchors automatically yet; run
`AuditLedger.anchor_range` on a schedule and publish the root.

## Hand evidence over

"Export … as CSV" saves the events loaded on the page (all filters applied)
with their sequence numbers and row hashes. Cells a spreadsheet would run as a
formula are neutralised. Record the verification verdict and head hash with
the export, so the file can be matched to the chain later.
