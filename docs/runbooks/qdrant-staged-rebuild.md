# Qdrant staged rebuild runbook

This runbook covers the local Docker Qdrant service and the embedded sparse
Qdrant in the CPU image. Both use a stable Qdrant alias such as
`ura_knowledge_base_active`; never point the application at a physical
collection that is rebuilt in place.

## Normal operation

The Crane image-build workflow runs automatically when a corpus source changes
under `Data/dataset`, `Data/teacher_qa`, `Data/pdfs`, or `Data/crawl`. It
regenerates the validated JSONL corpus, builds a new embedded Qdrant candidate,
and promotes its alias only after all batches and the source-hash sentinel have
been written. If that build fails, the deployed image and its previous index
remain serving.

For local retrieval, use the overlay; the `qdrant-indexer` post-deploy service
checks the source hash on every `compose up`. It exits without rebuilding when
the active alias already matches.

```bash
cd App
docker compose -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  up -d --build qdrant qdrant-indexer qdrant-backup api
```

The generated `Data/{faq_jsonl,teacher_qa,pdf_jsonl,crawl_jsonl}` inputs must
exist before the local build. Generate them from a full checkout when source
documents changed:

```bash
cd App/backend
PYTHONPATH=. python -m app.indexer --export-faq-jsonl
PYTHONPATH=. python -m app.indexer --export-pdf-jsonl
PYTHONPATH=. python -m app.indexer --export-crawl-jsonl
```

## Full rebuild and verification

Run the same safe lifecycle directly when an operator needs a forced rebuild.
It creates a versioned candidate collection, keeps the active alias unchanged
on any error, and atomically swaps the alias only after the candidate proves it
was built from the current source hash and a deterministic sparse canary query
retrieves every selected source document in the configured top-k. The default
is three corpus-spanning canaries, 100% hit rate, and top-k five; adjust the
three `INDEX_CANARY_*` variables only from a measured evaluation result.

```bash
PYTHONPATH=App/backend \
QDRANT_COLLECTION=ura_knowledge_base_active \
python -m app.index_lifecycle --rebuild --force

PYTHONPATH=App/backend \
QDRANT_COLLECTION=ura_knowledge_base_active \
python -m app.freshness --check --verify-qdrant --write-status
```

`GET /v1/index/freshness` then reports `corpus_hash`, `index_corpus_hash`,
`index_drift`, and `index_snapshot_missing`; any mismatch is explicit and the
freshness command exits non-zero. Run it with `--notify` where
`FRESHNESS_SLACK_WEBHOOK` is configured to alert the owning operations channel.

## Expected duration

- Reusing an already matching alias: seconds (only source hashing and a
  sentinel read).
- Sparse CPU image/index build after JSONL is prepared: budget 5–15 minutes
  for the current corpus; the image pipeline records the actual duration.
- PDF JSONL export is the longest step and is data- and CPU-dependent; budget
  up to 30 minutes on the GitHub-hosted build runner.

Do not use `app.indexer --recreate` for an active local collection: it deletes
the serving collection before replacement. Retain the previous versioned
collection until the deployment and the freshness endpoint have been checked;
it is the rollback target.

## Server upgrade procedure

The Compose profiles use Qdrant v1.19.0. Qdrant storage upgrades must not skip
minor releases: an existing v1.17 volume must first run v1.18, complete a
health and snapshot check, then move to v1.19. Take a verified snapshot before
each step; do not attach a production data volume to a new image until the
preceding minor release has started cleanly.

For a fresh local index, use the v1.19 Compose configuration directly. The
embedded CPU image recreates its Qdrant storage during its immutable build, so
it has no persistent-volume migration path.

## Backup and restore drill

`qdrant-backup` starts once Qdrant is healthy, even if the current candidate
rebuild failed. It snapshots the active physical collection behind the serving
alias daily, downloads it atomically to `qdrant_backups`, records a SHA-256
checksum, retains the seven newest snapshots across all generations of that
alias, and retries failures after five minutes. A monthly drill restores the
newest copy into a disposable collection, validates its source-hash sentinel,
and deletes only that disposable collection; it never alters the serving alias.

Inspect the durable status and trigger a manual drill when an alert fires:

```bash
cd App
docker compose -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  exec qdrant-backup python -m app.qdrant_backup --restore-drill
docker compose -f docker-compose.yml -f docker-compose.local-retrieval.yml \
  logs --tail=100 qdrant-backup
```

`qdrant_backups` is a separate Docker volume from `qdrant_data`, which protects
against collection loss and supports local recovery. It is still on the same
host: production disaster recovery additionally requires an encrypted,
access-controlled copy of that volume in an independently managed object store
and a restore drill using that off-host copy.

Prometheus scrapes the API's durable lifecycle gauges. Alerts cover source/index
drift, failed rebuilds, Qdrant query failures/latency, an unexpectedly small
collection, host disk headroom, stale backups, and stale or failed restore drills.

## Scope boundary

Cloudflare Vectorize is a managed external index. It uses a separate deployment
credential and is not reachable from local Compose or the embedded CPU image.
Its reindex remains an independent release step and must use the same source
hash contract before this issue can be closed for that topology.
