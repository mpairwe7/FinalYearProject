# ADR-0063: The analytics database survives restarts as a snapshot

## Status

Accepted, 2026-11-18. Off until `DB_SNAPSHOT_TARGET` is set. Mitigates G616.

## Context and Problem Statement

The HF Space keeps everything in one SQLite file, `analytics.db`, on the container's
own disk: tickets, consent receipts, conversation and working memory, call records
and the hash-chained audit ledger. The Space has no persistent storage, so every
restart and every roll starts on an empty database (G616). The audit ledger and
memory stay in SQLite even when analytics use Postgres, so moving to Postgres does
not solve it.

The service owner declined a paid persistent disk on 2026-11-18 and asked for an
export and restore that reseeds the database on restart.

## Considered Options

1. A gzipped SQL dump in a private HF bucket, exported every few minutes and on
   shutdown, restored before the API starts.
2. Mount a bucket as the database directory (`hf spaces volumes set`).
3. Snapshots in a private dataset repository.

A bucket mount is object storage over FUSE, and SQLite's WAL mode, which the app
uses, is documented not to work on network filesystems. A dataset repository keeps
every snapshot in its git history, so an erased taxpayer would stay in it.

## Decision Outcome

Option 1, in `app/db/snapshot.py`.

- **Export.** SQLite's online backup API copies the live database in one consistent
  read while both API roles keep writing. The copy is dumped with `iterdump`,
  gzipped and stored as `snapshots/<sha256>.sql.gz`. `latest.json` then names it,
  and every other dump is deleted, including one a failed upload or delete left
  behind, except the one `latest.json` names when it is re-read just before the
  prune. A reader never sees half an upload. An export holds the snapshot lock
  throughout, so the loop and a manual export cannot interleave.
- **Restore.** `uvicorn-role.sh` runs `restore` before each role's server starts.
  Under a file lock, the first role decides for the container. It writes `restored`,
  `empty` (no snapshot yet), `kept` (a database was already there) or `failed` to a
  status file next to the database. A damaged `latest.json` (empty, `null` or not
  JSON) is `failed`, not `empty`. `uvicorn-role.sh` stops a restore after 120
  seconds, so a slow bucket cannot hold both roles. The status reads `failed` from
  the moment a download starts until the outcome replaces it, so a killed restore
  stays `failed`. Otherwise the server would create an empty database, and the
  other role would read that as `kept`.
- **Exports stop after a failed restore.** A failed restore starts the API on an
  empty database. An export then would replace the last good snapshot, so exports
  run only after `restored`, `empty` or `kept`, and a program restart cannot change
  that decision.
- **Schedule.** The supervisord program `db-snapshot` exports every
  `DB_SNAPSHOT_INTERVAL_SECONDS` (default 300, minimum 60) and once more on SIGTERM.
  It waits with `sigtimedwait` rather than a signal handler. Its priority stops it
  after both API roles, so the last export sees their last writes, and it logs
  `final export starting` so an export cut short by the platform's stop timeout
  shows in the logs.
- **Privacy.** The HF transport refuses a bucket that is not private.

## Consequences

- A restart loses at most the writes since the last export. A roll that starts the
  new container before the old one's final export loses the same window: the new
  container's next export replaces the old one's final dump. Two databases cannot
  be merged, and refusing to export after such a race would stop backups after
  every roll. Two containers exporting within the same few seconds could still
  delete each other's dump between the re-read and the delete. Their exports are
  normally an interval apart.
- The snapshot holds the same personal data as the database, in HF storage, under
  the account that already hosts the Space. Retention runs on the restored rows at
  startup, and an erasure leaves the target with the next successful export.
- Restored rows keep their hashes, so the audit chain verifies across restarts.
- Enabling it needs a private bucket and an `HF_TOKEN` with write access to it
  ([runbook](../runbooks/database-snapshots.md)).
- shortcut: the dump is built in memory. Stream it through a temp file once the
  database outgrows a few hundred MB.

## Confirmation

- `App/backend/tests/test_db_snapshot.py` covers these cases:
  - A container restarted onto a wiped disk gets back an identical dump.
  - A damaged dump or manifest is never overwritten, even after a program restart.
  - Only the latest dump is kept, stray dumps included, and a public bucket is refused.
  - A local dump is readable by its owner only.
  - The loop exports once more on SIGTERM.

  Removing any of these guards fails its test.
- `App/backend/tests/test_call_process_container.py` checks two things. Each role
  restores before its server starts. The snapshot loop stops after both roles.
