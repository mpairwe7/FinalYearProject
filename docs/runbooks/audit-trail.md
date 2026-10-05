# Runbook — audit trail and the auditor role

What the audit trail records, who can read it, how to prove it has not been
altered, and how to hand evidence to an external auditor. Written 2026-09-29
after a QA audit of the auditor dashboard and a review of the ledger itself
(gaps G66–G72 in
[`docs/GAPS_AND_AGENTIC_ROADMAP.md`](../GAPS_AND_AGENTIC_ROADMAP.md)).

## Separation of duties

| Action | `ura_staff` (officer) | `ura_admin` | `ura_auditor` |
| --- | --- | --- | --- |
| Read tickets, transcripts, calls, analytics | yes | yes | yes |
| Change a ticket, reply to a taxpayer, heartbeat a case | yes | yes | **no (403)** |
| Take, give back, hold, transfer, wrap up or end a call; set desk presence | yes | yes | **no (403)** |
| Rate a call (review) | yes | yes | **no (403)** |
| Listen in to a live call | yes | yes | yes (recorded) |
| Toggle a flag, edit a staff-written answer | no | yes | **no (403)** |
| Read and verify the audit trail (`/admin/audit`) | **no (403)** | yes | yes |
| Seal the audit trail ("Seal now") | **no (403)** | yes | yes |

Every "no" is enforced by the API (`_require_staff_writer`,
`_require_audit_reader`, `_desk_writer`, the admin checks on flags and
overrides), not only hidden in the console. Sealing sits with the audit
readers because it adds a seal and never changes a row. Before 2026-09-29 the ticket
endpoints relied on the console alone: an auditor's direct API call could
resolve a ticket or send a reply to a taxpayer.

The operator key (`INDEX_API_KEY`, break-glass) passes these checks and is
recorded as actor `operator-key`.

`tests/test_auditor_controls.py::test_every_admin_write_route_refuses_an_auditor`
walks every `POST`/`PUT`/`PATCH`/`DELETE` route under `/v1/admin` in the live
app and requires a 403 for an auditor; the only listed exception is sealing. A
new staff write that forgets its role check fails that test.

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
| `staff.call_reviewed` | A call is rated | `call_id`, `rating`, `note_chars` |
| `voice_staff_viewed_call` | A call and its transcript are opened (call detail, or the live call view) | call id (`session_id`) |
| `voice_staff_listened_call` | Someone listens in to a live call, before any audio flows | call id, `transport` (`livekit` / `websocket`) |
| `voice_staff_viewed_brief` / `voice_staff_viewed_caller_history` | A call brief or a caller's history is opened | call id |
| `voice_officer_*` | Call desk: `claimed`, `released`, `joined`, `hold_on` / `hold_off`, `transferred`, `wrapup_saved`, `callback_done`, `ended_call` | call id |
| `voice_*` (other) | Voice consent and recording events (existing) | voice audit id, audio hash |
| `erasure_tombstone` | Personal data erased on request (existing) | hash of the erased user id |
| `audit.trail_viewed` | Someone searches the trail (first page of each search) | the filters used, rows returned |
| `audit.chain_verified` | Someone runs the integrity check | scope, verdict, rows and seals checked |
| `audit.sealed` | Someone seals the trail from the page | sealed range, Merkle root |

Reads and checks of the trail are recorded too, so "who looked at the
audit record, and when" has an answer. The row is written after the read,
so a listing never contains its own read; paging back through older results
is not recorded again.

Every staff row also carries `actor_role`; the row's `user_id` is the actor.
Call events go to the voice audit log first and are chained into the ledger
with the same actor. Before 2026-09-29 that chaining left the ledger's actor
empty — every call-desk action read as done by nobody on `/admin/audit` — and
a failed chain was only a debug line; it is now counted like any other append.
**Content is never copied into the ledger**: a reply or a note is recorded as
its length, so the audit trail adds no second copy of taxpayer data. A failed
append is logged and counted (`audit_append_failed_total` on `/metrics`) —
alert on any increase.

## Read it

`/admin/audit` (nav: Observe → Audit trail). The integrity check runs first
and states whether the record is intact; filters choose the event type, the
person and the period; "Show older events" pages back. APIs:
`GET /v1/admin/audit/events`, `GET /v1/admin/audit/verify` and
`POST /v1/admin/audit/seal`
([`API_REFERENCE.md`](../API_REFERENCE.md)).

## Prove it has not been altered

Each row's fingerprint (`row_hash`) covers the previous row's fingerprint,
the payload **and the envelope**: who acted (`user_id`), the event type, the
time, the tenant and the sequence number. That is hash format v2
(`hash_version = 2`). Rows written before 2026-09-29 are v1 and cover only the
payload, so the actor on those rows is not tamper-evident; they still verify
by their own rule, and a v1 row after a v2 row is itself a break.

The page's verdict comes from `verify_ledger`, which:

1. recomputes every row's fingerprint and checks each row points at the one
   before and that sequence numbers neither skip ("sequence gap: rows 3..3
   are missing") nor repeat ("duplicate sequence number", two writers forked
   the chain);
2. re-checks every **seal** against the rows it covers: their count, the
   Merkle root of their payloads, and the chain head recorded at the seal.

Step 1 alone cannot catch someone with database access who edits a row and
recomputes every fingerprint after it: the chain agrees with itself. Step 2
does, because the seal fixed the old values. The page names that case:
"Events #a to #b no longer match their seal".

**Scope.** Up to `AUDIT_VERIFY_FULL_MAX_ROWS` rows (default 200 000) the page
walks everything (`scope: full`, about 8 µs per row, so 1.6 s at the
default). Above that it re-checks the newest seal and walks only the rows
written after it, starting from the chain head that seal recorded
(`scope: since_seal`); the page says which it did. Run the full walk offline
on a schedule for a large ledger, and for an external auditor who should not
rely on this server's answer:

```bash
PYTHONPATH=App/backend python3 -m app.audit.verifier --tenant default            # full
PYTHONPATH=App/backend python3 -m app.audit.verifier --tenant default --scope since_seal
```

It exits 1 on any break and writes the report to `audit_report.json`.

## Seals

A seal records, for a range of rows, the Merkle root of their payload hashes
and the `row_hash` of the last row (which commits to everything before it).

- **On a schedule.** Every API replica seals each tenant's new rows every
  `AUDIT_SEAL_INTERVAL_SECONDS` (default 3600; `0` turns the schedule off)
  while `audit_ledger` is on. Seals are unique by their first sequence
  number, so replicas that race produce one seal, not several. Counters:
  `audit_seals_total{trigger="schedule"|"manual"}`, `audit_seal_failed_total`.
- **On demand.** "Seal now" on `/admin/audit` (`POST /v1/admin/audit/seal`).
  Seal before exporting evidence, so the export ends inside a sealed range.
  The seal and the check that follows are themselves recorded, so the page
  shows two events written since, straight after a seal.
- **The witness.** Each seal also writes one log line:
  `audit seal tenant=… seq=a..b merkle_root=… head_hash=…`. Shipped with the
  logs, it puts the seal outside this database, so rewriting the ledger *and*
  its seal table together still disagrees with the log archive. Keep that log
  stream on write-once retention.
- **Cryptographic TSA witness (RFC 3161).** When `AUDIT_TSA_URL` is configured,
  sealing queries an external Time Stamping Authority with the range's Merkle
  root hash. The returned cryptographic timestamp token (`tsa_token`) is stored
  durably in `audit_anchors` and checked during `verify_anchor()`, providing
  independent third-party proof that the range existed prior to the timestamp.

Rows written after the newest seal are protected only by the chain: deleting
the newest rows from the end leaves no break until the next seal. The seal
interval bounds that window.

## When the check fails

Treat any break as a security incident. The page pauses sealing while the
record fails its check, because a new seal would fix the altered record in
place.

1. Do not seal and do not "repair" rows. Snapshot the database (`analytics.db`
   or the Postgres `audit_events` and `audit_anchors` tables) first.
2. Run the offline verifier on the snapshot and keep `audit_report.json`.
3. Compare the seals in `audit_anchors` with the `audit seal` lines in the log
   archive: a seal that disagrees with its log line was rewritten too.
4. `audit_chain_breaks_total{scope}` counts failed checks on `/metrics`, and
   the API logs `audit chain verification failed` at error level.

A ledger written before the unique `(tenant_id, seq)` index existed may
already hold a fork. The index is then not created (logged at error level on
first use), and the verifier reports the duplicate sequence numbers.

## Hand evidence over

"Export … as CSV" saves the events loaded on the page (all filters applied)
with their sequence numbers and row hashes. Cells a spreadsheet would run as a
formula are neutralised. Seal first, then record the verification verdict,
head hash and the seal's Merkle root with the export, so the file can be
matched to the chain and to the log archive later.

## Verify on the local stack

`scripts/probe_audit_trail.py` is the live canary: it checks the trail refuses
unauthenticated reads and seals, that `audit_ledger` cannot be switched by API,
that a chat turn lands on the chain, and it seals twice and verifies in both
scopes. It changes no row. It runs **inside** the api container, so the
break-glass operator key is read from that process's environment and never
leaves it:

```bash
docker exec -i -w /app ura-app-api python - < scripts/probe_audit_trail.py
```

`audit_ledger` is protected — the flags API answers 400 to any change — so turn
it on for the container, not at runtime. With the four-file GPU overlay (see
`local-gpu-salt-ngrok-stack` in the guided-journey probes runbook), add a
throwaway override and recreate only the api, from the main checkout:

```yaml
# /tmp/audit-live.override.yml — do not commit
services:
  api:
    environment:
      FLAG_AUDIT_LEDGER: "true"
      AUDIT_SEAL_INTERVAL_SECONDS: "120"   # watch a scheduled seal within minutes
```

```bash
cd ~/Mpairwe7/FinalYearProject/App
GPU_ID=2 VLLM_GPU_ID=5 SUNFLOWER_GPU_ID=5 docker compose \
  -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  -f docker-compose.local-sunflower.yml -f docker-compose.gpu-salt.yml \
  -f /tmp/audit-live.override.yml up -d --no-deps --no-build api
```

A scheduled seal shows as an `audit seal tenant=… seq=a..b …` log line and as
`audit_seals_total{trigger="schedule"}` on `/metrics`. Recreate the api
without the override afterwards to put the stack back.

**Result, 2026-09-29** (branch build on the local GPU stack): 12/12 probe
checks; a scheduled seal fired on the 120 s interval (`seq=9..10`) with no
`audit_seal_failed_total` or `audit_chain_breaks_total`; through the ngrok
tunnel both audit routes answered 401 unauthenticated and the `/admin/audit`
bundle carried the new seal controls.

## Backends

The ledger runs on SQLite and on Postgres through the same query helpers.
The Postgres path (column upgrade by `ALTER TABLE`, unique-violation retry on
SQLSTATE 23505, the seal race, both verify scopes) was checked against a
Postgres 16 server on 2026-09-29; `tests/agents/test_backend_shim.py` runs the
Postgres cases when `POSTGRES_DSN` is set.
