# ADR-0040: Call records and accountability — one audit row per call turn, a wrap-up owned by the officers who handled the call, presence inside its tenant

## Status

Accepted, 2026-11-11. Phase 2 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md);
it follows [ADR-0039](0039-call-handoff-and-caller-safety.md). It closes the
wrap-up gate ADR-0020 left open (G305), most of the staff-socket gate (G304)
and the retention finding G555.

## Context and Problem Statement

Phase 1 made the handoff reach an officer. This phase is about what a call
leaves behind and who may write it.

- **Call turns the AI handled itself wrote no audit row** (G585): a crisis
  statement, a request for an officer, a repeat, a goodbye. The repository's
  rule is one row per turn on every transport. Where a row was written it could
  be wrong: a Luganda turn hashed the English pivot the caller never heard, and
  a turn the caller talked over or that timed out was recorded `completed`.
- **Anyone could write a call's wrap-up** (G305): any officer of the tenant, on
  any call, a live one included, and a second officer's wrap-up replaced the
  first's. A ticket opened at wrap-up was opened again when the save was retried.
- **Presence had no tenant** (G584): the board, the `officer.presence` lobby
  event and the "is anyone on shift" count crossed tenants, and the update body
  was stored as sent.
- **The end-of-call summary acted as an officer** (G587): it wrote the ticket's
  staff note, which stamps the SLA's first response and replaced an officer's note.
- **Staff sockets had no lifetime and no cap** (G304): the officer's audio socket
  and a listener's ignored token expiry, nothing limited how many a user held,
  and each officer utterance started its own transcription task.
- **Two streams lost or leaked** (G589, G590): a listen socket never woke when
  its call ended; a turn written while the live transcript was opening never
  reached the officer.
- **A missing table undid a purge** (G555): on Postgres the resolved-ticket
  purge shared a transaction with the voice tables, which did not exist where
  calls were off.

## Decision Outcome

**The brain writes a call turn's one audit row.** Each caller turn carries a
record of the lines said in reply. When the turn is over, one row is written
through `ChatModel.record_turn_audit` with `channel="call"`, the reply being
what the caller was told, in the call's language. `generate(defer_audit=True)`
hands its audit fields back under `_pending_audit` instead of writing the row,
so the model's grounding and usage go on the same row. The disposition is
`completed`, `cancelled` (talked over, superseded, or the call left the AI),
`deadline_exceeded` or `failed`. A turn nothing was said to (an officer has the
call) is not an AI turn and writes nothing. A failed write is logged and never
costs the caller their answer.

**A wrap-up belongs to the officers who handled the call.** `bridge` records
each officer in `handled_by`. A wrap-up is accepted from one of them once they
are off the call: 409 for the officer still on it, 403 for an officer who never
was, and for any officer on a call still with the AI or the queue. A call no
officer reached can be wrapped up by any officer of the tenant. Each officer's
outcome and note are kept under their id in `wrapups`; the call's own `outcome`
and `wrapup_note` are the last handling officer's, whichever saves first. A
ticket opened at wrap-up is linked to the call in its own write before anything
else, and withdrawn if the link fails. `resolve` is refused while the caller is
still on the line. A callback is completed by one conditional update.

**Presence is a tenant's.** `officer_presence` gains `tenant_id`; the board, the
staffing count and the lobby event are scoped by it when tenancy is on. A lobby
event reaches a tenant-scoped console only when it names that tenant, by call id
or explicitly; one that names neither reaches none. The update body is a
validated model, and a heartbeat cannot mark an officer free while this server
has them on a live call.

**The summary is the system's.** `update_ticket(officer_touch=False)` writes
without stamping a first response, and the summary writes a ticket's note only
while it has none. The template summary calls a call answered only when a tax
answer was given, not the crisis lines.

**Staff sockets end with the session and are admitted under a cap.** The audio
and listen loops close with 4401 when the token expires; an officer on a call
then goes into the reconnect grace, so the caller is kept. Staff sockets take a
slot by address before sign-in and by user after it; a call has at most five
listeners. Officer speech is transcribed by one worker per leg with a backlog of
four.

**Streams.** The live transcript subscribes before it reads its snapshot and
drops queued turns the snapshot already holds. A listen socket polls for the
end of the bridge and no longer returns from a `finally`.

**Retention.** Each purge is its own transaction, and the Postgres bootstrap
creates the call tables as SQLite's does.

**Corrected in review, before merge.** Review of this change found the
following, each fixed here with a test that failed first:

- *Session expiry was never read.* The staff endpoints took the token's expiry
  from the fourth value of `_resolve_ws_principal`, which is the token's
  consent purposes. No staff socket had ever expired, on the lobby stream
  either, where the same line predates this change. The expiry now comes from
  the verified claims (`chat_ws_v2.resolve_ws_session`), and the lobby's wait
  is bounded by it.
- *A wrap-up trusted "no room here".* With the call on another worker, or its
  process gone, the call was read as ended. The stored status now decides.
  The desk's stale-row "end" had the same fault from the other side: it marked
  a live-looking row ended whenever this process had no room for it. It now
  does so only where nothing else can be holding the call (one worker and one
  replica declared, or a row older than a call may last) and answers 409
  otherwise.
- *Two officers saving together lost one wrap-up.* The save is a
  compare-and-write on the stored list (`store.merge_wrapup`), retried on a
  miss, so it holds across processes.
- *A model-run slot was given back too early, or never.* It is now held until
  the thread that took it has returned, and freed at once when a step was
  cancelled before its thread started (`_GenerationSlot`). A thread that
  starts after the slot was given back does no work.
- *The audit row named the call's language, not the reply's.* `reply_locale`
  is English when a translation failed and the English answer was served.
- Smaller: the brain switches only into the deployment's call languages; a
  model run reads its language before it is scheduled; the ticket withdrawn
  after a failed link is a system write and a failed withdrawal is logged as
  an orphan, and any ticket write that reports failure stops the wrap-up
  before it is saved; an outcome that replaces `callback` drops the callback
  it asked for; an officer's last utterances are transcribed after the leg
  closes, within 20 s, and the call's summary and wrap-up draft wait for them; the schedule's "an officer is online" counts the caller's
  tenant only; the summary's note is conditional in the UPDATE itself; a
  listener's place is taken before anything is awaited and given up when
  their socket closes during a silence.

Also in this phase: a Luganda calculator answer goes through the guarded
translation (G586); a Luganda synthesis that states a figure the checked answer
does not is replaced by the guarded translation (G588); a call runs at most two
model generations at once (G591); the language policy adopts a language the
brain moved the call to, and the brain honours an on-screen choice when no
router is attached (G592); a transfer's team and note are validated.

## Consequences

- One more write per call turn, off the event loop. Rows for call turns now
  digest the spoken form (with the assistant's spoken renderings of figures and
  acronyms), not `generate`'s written reply.
- A deployment that does not declare one worker and one replica can no longer
  end a live-looking row from a process that has no room for it until the row
  is older than a call may last (about 17 minutes at the default ceiling). An
  officer there sees 409 and tries again. Declaring the single process, which
  production must do anyway, removes the wait.
- An officer who was never bridged to a call can no longer wrap it up, which
  the console allowed. A reloaded console that has lost its active call still
  cannot submit a wrap-up (G611 family, phase 4).
- `handled_by` exists only for calls bridged after this change. Earlier rows
  fall back to the row's last `officer_id`.
- Session expiry is enforced; logout or revocation before expiry is not, because
  the service keeps no revocation list. G304 stays open for that and for a rate
  limit on officer audio frames.
- A superseded model run still runs to completion; only the number in flight is
  bounded. Stopping one needs a cancel signal inside `generate`.
- `notification.arrived`, which carries a taxpayer's id and deadline, no longer
  reaches tenant-scoped staff consoles. Nothing in the console consumed it.
- Three columns are added by the existing startup migration
  (`voice_calls.handled_by_json`, `voice_calls.wrapups_json`,
  `officer_presence.tenant_id`). No flag, route or model changes.

## Confirmation

`App/backend/tests/test_receptionist_records_accountability.py`: each test
failed before its fix, and `DeferredAuditTests` runs the real `ChatModel` so the
deferred row is exercised end to end rather than through a double. The wrap-up,
presence and callback tests use the real SQLite store; the Postgres purge is
tested against a recording double, not a live database, and the staff-socket
tests against fake sockets.

The session-expiry defect above passed this file's first version because the
tests handed the endpoint an expiry through a double of sign-in. Two tests now
sign a real token and patch nothing about sign-in
(`test_the_expiry_comes_from_the_token_the_verifier_checked`,
`test_the_lobby_closes_when_a_real_token_expires`). The conditional ticket
note and the wrap-up merge are tested on SQLite only; their SQL is the same
text on Postgres and has not been run against one here.
