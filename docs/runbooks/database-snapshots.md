# Runbook: database snapshots across restarts

The HF Space has no persistent disk, so `analytics.db` is empty after every restart
and roll (G616). With `DB_SNAPSHOT_TARGET` set, the database is exported to a
private HF bucket and restored before the API starts (ADR-0063,
`App/backend/app/db/snapshot.py`). It is off until you set the target.

## Turn it on

1. Create a private bucket. It must be private: the export refuses a public one.

   ```bash
   hf buckets create landwind22/ura-chatbot-db --private
   ```

2. Create a fine-grained token with write access to that bucket only, at
   <https://huggingface.co/settings/tokens>. Add it to the Space as the `HF_TOKEN`
   secret.
3. Set the target and, optionally, the interval. The default interval is 300 seconds,
   and the minimum is 60.

   ```bash
   hf spaces variables add landwind22/ura-chatbot \
     --env DB_SNAPSHOT_TARGET=hf://buckets/landwind22/ura-chatbot-db
   ```

   The Space restarts, which wipes the database once more. The first start finds no
   snapshot (`empty`), and the first export follows one interval later.

## Check it

- The Space logs show `db-snapshot` lines. The restore writes `restored`, `empty`,
  `kept` or `failed`, and each export logs `exported analytics database (N bytes,
  M tables)`.
- The bucket holds `latest.json` and exactly one `snapshots/<sha256>.sql.gz`:

  ```bash
  hf buckets list landwind22/ura-chatbot-db --recursive
  ```

- After a restart, a ticket raised before it is still listed in the admin console.

## If the restore failed

The API starts on an empty database. Exports stay off for that container, so the
last good snapshot is not overwritten. The log line names the cause: a missing dump,
a SHA-256 mismatch or a failed `integrity_check`.

1. Download the dump and check it.

   ```bash
   hf buckets cp hf://buckets/landwind22/ura-chatbot-db/latest.json .
   hf buckets cp hf://buckets/landwind22/ura-chatbot-db/snapshots/<sha256>.sql.gz .
   sha256sum <sha256>.sql.gz   # must match latest.json
   gunzip -c <sha256>.sql.gz | sqlite3 /tmp/check.db && sqlite3 /tmp/check.db 'PRAGMA integrity_check'
   ```

2. If the dump is good, restart the Space. The restore runs again on the new
   container.
3. If the dump is damaged, there is no older copy: each export deletes the previous
   dump. Delete `latest.json` to start from an empty database. Exports then resume
   on the next restart.

## Turn it off

Remove the `DB_SNAPSHOT_TARGET` variable. The loop then only waits, and the restore
does nothing. Delete the bucket to remove the last snapshot and the personal data in
it.

```bash
hf spaces variables delete landwind22/ura-chatbot DB_SNAPSHOT_TARGET --yes
hf buckets delete landwind22/ura-chatbot-db --yes
```
