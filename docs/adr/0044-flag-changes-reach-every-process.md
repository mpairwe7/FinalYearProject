# ADR-0044: A console flag change reaches every API process

## Status

Accepted, 2026-11-12. Phase 6 of the
[11 November call receptionist review](../plans/receptionist-assurance-2026-11-11.md);
it follows [ADR-0043](0043-one-call-process.md), which split the Space's API into a
chat process and a call process and recorded this gap. It closes G612. Approved by
the service owner on 2026-11-12 as a short design in the session that made it.

## Context and Problem Statement

A flag changed from the staff console (`PATCH` or `DELETE /v1/admin/flags/{name}`)
was written to the `flag_overrides` table and applied in the registry of the one
process that answered the request. Every other process read the table only when it
next started. On the Space there are two processes (ADR-0043), so a console change
reached the call process about half the time.

The flag that matters most is `voice_receptionist`, the calls kill switch. Switched
off from the console, it could leave the call process taking calls while the
console said they were off. The runbook's workaround was a Space secret and a Space
restart, plus deleting a stored override that would otherwise beat the secret. That
procedure took minutes and had four steps.

`GET /v1/admin/flags` had the same blind spot: it reported only the registry of the
process that answered.

## Considered Options

1. **Every process re-reads the store on an interval** and makes the overrides that
   came from it match.
2. **The process that takes the change forwards it** to the other process over
   `127.0.0.1`, through a new internal endpoint.
3. **Keep the secret-and-restart procedure** and say so in the console.

## Decision Outcome

Option 1.

Option 2 is instant, but it covers only the two processes in one container: a
second replica, or a process that is restarting, would miss the change. It also
adds an internal write endpoint, which needs its own authentication. Option 3
leaves the fastest, most obvious control (the console switch) working only half
the time, which is the failure this phase exists to remove.

How it works:

- **The sync.** Each process runs a background task
  (`app/main.py`, `_flag_sync_loop`, task `flag-sync`). Every
  `FLAG_SYNC_INTERVAL_SECONDS` (5 by default) it reads `flag_overrides` in a worker
  thread (`_sync_flag_overrides`) and applies the rows with
  `FeatureFlags.apply_stored`. Both roles run it.
- **Only overrides that came from the store follow it.** The registry remembers
  which overrides came from the table (`stored`):
  - the startup replay marks the rows it reads as stored;
  - `PATCH` marks the flag it writes;
  - `apply_stored` marks every row it applies.
  A stored row beats an override set in this process only. An override that came
  from the store and is no longer in it is cleared. An override set in one process
  only (a test, or code that calls `flags.set` directly) is left alone. Unknown
  names are skipped, as at startup.
- **`PATCH` writes the store first**, as `DELETE` already did. It used to set the
  registry first: a failed write left an override in that process alone, which no
  other process would ever see.
- **Protected controls in production.** Startup refuses to start on a stored
  `false` for a protected control (`auth_required`, `multi_tenant`,
  `audit_ledger`, `ticket_queue`, `voice_consent`). A running process does not
  exit. The sync leaves such a row out, so the control stays on; it logs CRITICAL
  once (until the row changes) and counts `flag_sync_refused_total` on every pass.
  The console cannot write such a row (`PATCH` answers 400); it would come from a
  direct database edit or from a process outside production.
- **An unreadable store** keeps the process's flags as they are. The process logs
  ERROR once per run of failures and INFO when the store answers again, and counts
  `flag_sync_failures_total` on every failed pass.
- **The interval.** `FLAG_SYNC_INTERVAL_SECONDS`: 5 by default. 0 turns the sync
  off, and a change then reaches the other processes at their next start. Any other
  value is at least 1. A value that is not a number, or is negative, falls back to
  5 with a warning.
- **The API says how far a change reaches.** `GET /v1/admin/flags` adds:
  - `scope`: `every_process_sharing_this_database`, or
    `this_process_until_the_others_restart` when the sync is off;
  - `sync_interval_seconds`;
  - `synced_at`: when the answering process last read the store, in epoch seconds;
  - `stored` on each flag.

  `PATCH` and `DELETE` return the same `scope` and
  `reaches_every_process_within_seconds`. The console's Feature flags page used to
  say "Toggles here change this replica only". It now says a change reaches every
  API process within the interval, or at their restart when the sync is off.

## Consequences

- **The console's calls switch works on the Space.** Switched off, the call
  process reads `off` within one interval. The chat process's `/ready` caches the
  call process's value for 10 s, so the change shows there within about 15 s. New
  call sockets are refused (a caller's, the staff call stream, an officer's audio);
  sockets already open carry on. The Space secret stays as the fallback for when
  the console or the store is unavailable. The runbook's kill switch now leads with
  the console.
- **A stored override now also beats a `FLAG_*` value while a process runs**, not
  only at its start: the sync re-applies it on every pass. So deleting a stored
  override is enough to make every process follow `FLAG_*` again within one
  interval, with no restart.
- **Switching calls on at runtime still needs a restart** when the call process
  started with calls off: `/ready` then says
  `unavailable: switched on after the API started` (ADR-0041). This ADR does not
  change that.
- **Converges, not atomic.** Each process applies a change within one interval of
  the write. A sync pass can read the store just before a `DELETE` in the same
  process and re-apply the row after it; the next pass clears it.
- **Cost:** one `SELECT name, enabled FROM flag_overrides` per process every 5 s.
  On the Space that is two small reads every 5 s of one SQLite table.
- **Replicas with their own database are not reached.** A change reaches every
  process that shares the database. Kubernetes replicas each on their own SQLite
  still need `FLAG_*` on every replica, and the console says so.
- **No alert on a failing sync.** A failing sync is logged and counted. A store
  that cannot be read also fails `PATCH` and `DELETE`, so an operator who tries to
  change a flag sees the failure. Nothing scrapes the Space (G614).

## Confirmation

`App/backend/tests/test_flag_sync.py` runs two `FeatureFlags` registries on one
database, standing in for the two processes. Each test failed before the change, or
was shown to fail when the change it pins was reverted. The tests cover:

- **The registry:**
  - a stored change reaching the process that did not write it, and so does its
    deletion;
  - an override set in this process only being left alone;
  - a stored row beating it and then following the store;
  - an unchanged store changing nothing;
  - unknown names being skipped.
- **One sync pass:**
  - it applies the store and records `synced_at`;
  - `voice_receptionist` stored `false` makes `receptionist.status()` read `off`;
  - an unreadable store keeps the flags, logs once, counts each failure and
    recovers;
  - in production, a stored `false` for `auth_required` is refused without exiting,
    logged CRITICAL once over two passes and counted twice.
- **The interval:** parsing of blank, unset, 2, 0, 0.2, -1 and `abc`.
- **The loop:** it runs on its interval, stops when the app stops and ends when
  cancelled. The real lifespan runs it, and does not run it at interval 0.
- **The API:**
  - a `PATCH` through the app reaching a second registry, and so does the `DELETE`;
  - a `PATCH` whose write fails changes nothing;
  - `GET` reports `scope`, `sync_interval_seconds`, `synced_at` and `stored`.

Each part of the change was also reverted on its own, and a test failed each time:
- `PATCH`'s order;
- the lifespan task;
- the production refusal;
- clearing a deleted row;
- leaving a local override alone;
- logging once;
- `PATCH` marking its row as stored.

`App/frontend/src/__tests__/app/adminFlags.test.tsx` covers the console's note: it
names the interval when the sync is on, gives the restart wording when the sync is
off, and keeps the "replayed whenever a process restarts" sentence.

Not verified here: two real processes on the Space. After the roll, the runbook's
kill-switch steps are the check: switch calls off from the console, read `/ready`
until it says `off`, switch them back on.
