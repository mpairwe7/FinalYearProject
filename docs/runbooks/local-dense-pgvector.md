# Runbook: the local dense retrieval leg (pgvector)

The HF Space serves a sparse-only Qdrant collection. The local dense leg adds dense search beside
it: multilingual-e5-small embeddings in PostgreSQL 15 + pgvector, fused with BM25 by RRF
(ADR-0034, G523). This runbook covers checking it, turning it off, and fixing a leg that did not
come up.

## How it gets built

1. **CI, base image.** `ura-chatbot-build-push.yml` runs "Embed the corpus for the dense leg".
   - It downloads the pinned model and verifies its SHA-256s.
   - It embeds every corpus document and writes `App/Data/dense/{model/,vectors.npy,manifest.json}`.
   - The step is cached on the corpus and the embedding code, so a build with an unchanged corpus
     skips it.
   - The first build after a corpus change takes about 15–20 minutes longer.
2. **Space build, HF layer.** The roll pushes `App/deploy/hf-space/Dockerfile`, and the Space
   builds it.
   - It installs Debian's PostgreSQL 15, copies the pgvector 0.8.1 extension from the
     digest-pinned image, and runs a SQL selftest.
   - It then runs `python -m app.pgvector_dense load` and `check`.
   - The load refuses vectors whose point ids differ from the baked Qdrant collection's. A refused
     or failed load does not fail the build: it prints
     `WARNING: the dense leg was not loaded`, and the Space serves BM25 alone.
3. **Runtime.**
   - supervisord starts `postgres` (Unix socket `/app/pgrun`, no TCP) beside the other services.
   - The retriever opens the leg at startup when `LOCAL_DENSE_BACKEND=pgvector`. It waits up to
     20 s for the socket.

## Check it after a roll

```bash
curl -s https://landwind22-ura-chatbot.hf.space/ready | python3 -c \
  'import json,sys; c=json.load(sys.stdin)["capabilities"]; print(c["retrieval"], "|", c["local_dense"])'
```

| `retrieval` | `local_dense` | Meaning |
| --- | --- | --- |
| `hybrid` | `pgvector multilingual-e5-small, N vectors` | Working. `N` should equal the corpus size (about 7,200). |
| `sparse` | `unavailable (...)` | The leg failed. The reason says why (table below). BM25 alone is serving. |
| `sparse` | `off` | `LOCAL_DENSE_BACKEND` is empty or `none`. |

Then confirm in the logs (`hf spaces logs landwind22-ura-chatbot --tail 3000`):

- `HybridRetriever ready in SPARSE + LOCAL DENSE mode` appears once per uvicorn worker;
- there is no `Local dense search failed`.

The build log (`hf spaces logs ... --build`) shows `pgvector selftest passed` and
`dense leg loaded`.

## Reasons and fixes

| `local_dense` reason | Cause | Fix |
| --- | --- | --- |
| `UndefinedTable: relation "dense_meta" does not exist` | The load did not run or was refused. | Read the build log. `NOTE: this base predates the dense leg` means the base image has no artifact yet, so roll a base built after ADR-0034 merged. `artifact and Qdrant disagree` means the vectors are for another corpus; see the next row. |
| `artifact and Qdrant disagree` (build log) | The vectors were computed from a different corpus than the collection. This happens when the cache key misses a change that alters point ids, such as `deterministic_point_id` in `retriever.py`, which is not in the key. | Re-run the build workflow with the cache entry deleted (`gh cache delete` on the `dense-e5s-v1-*` key), then roll. |
| `table built for another model` | `dense_cpu.py`'s pins changed but the vectors did not. | Same as above: rebuild the artifact. |
| `OperationalError: ... /app/pgrun ...` | Postgres is not running. | `hf spaces logs` for `postgres` exits. Postgres writes to `/app/pgdata`, which must stay owned by `postgres`. |
| `dense model files rejected` | The model files in the image do not match their pins. | Rebuild the base image; never edit the pins without new vectors. |
| `the Qdrant collection changed since the vectors were loaded` | A new collection was promoted (G292), so the vectors describe the old corpus. The leg turned itself off rather than serve removed guidance. | Rebuild and roll the image so CI embeds the new corpus. |
| `table records no Qdrant corpus binding` | The table was loaded by an older loader. | Rebuild and roll the image. |
| `circuit open: recent dense queries failed or timed out` | Dense searches failed or exceeded `LOCAL_DENSE_TIMEOUT_S` (2 s) three times. | It retries by itself. If it persists, check the `postgres` program and `local_dense_query_errors_total`. |

## Turn it off

Add the Space secret `LOCAL_DENSE_BACKEND=none`. A secret overrides the Dockerfile's `ENV`, and a
secret write restarts the Space in about two minutes. Retrieval returns to BM25 alone; nothing
else changes.

Do not add it as a variable: a name that exists as both a secret and a variable takes the Space
down with `CONFIG_ERROR`.

To remove the leg entirely, revert the HF layer block in `App/deploy/hf-space/Dockerfile` in a PR.
Never edit the Space directly, because the next roll overwrites it.

## What changes when it is on

With the leg in a result, abstention reads the CPU reranker's own word-overlap score (bounded in
[0, 1]) instead of a sigmoid of it that never fell below the threshold. The Space therefore
abstains on weak lexical matches it used to answer from. After a roll, compare
`retrieval_abstention_decisions_total{signal="rerank",abstained="true"}` with the day before.

## What it does not do

- Dense similarity does not decide abstention or context pruning (G524). A passage found only by
  the dense leg still needs the reranker's (on CPU, word overlap's) support to be served.
- e5 is not trained on Luganda, Runyankole or Acholi. Those questions reach retrieval through
  translate-then-retrieve, as before.
