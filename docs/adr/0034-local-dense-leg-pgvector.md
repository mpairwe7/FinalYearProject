# ADR-0034: A local dense retrieval leg in pgvector for the CPU deployments

## Status

Accepted — 2026-11-09

## Context and Problem Statement

The HF Space and Crane Cloud images run without a GPU. Their Qdrant collection is baked at image
build as **sparse-only** (`SPARSE_ONLY_INDEX=true`), because a dense vector needs `bge-m3` and
torch, which the CPU image does not ship. On the live Space on 2026-11-08 the retriever logged
`HybridRetriever ready in SPARSE-ONLY mode`, and `/ready` reported `retrieval: sparse`.

So production retrieval was BM25 alone, reranked by the CPU layer's word-overlap heuristic
(`_CpuLexicalReranker`). Two failures follow:

- **A question that shares no word with the right passage finds nothing.** `search()` returned
  `[]` when no query term was in the BM25 vocabulary.
- **The wrong passage can win on rare terms.** "How long can EFRIS work offline?" was answered
  from a generic "How does EFRIS work?" FAQ instead of the one stating the offline window.

The Cloudflare Vectorize fallback exists, but it is remote and dense-only, and it is off on the
Space (`DENSE_FALLBACK_BACKEND=none`). The question was how to give the CPU deployments real dense
retrieval: on CPU, inside the container, without egress, and without slowing every deploy.

## Considered Options

- **Dense vectors in the existing Qdrant collection.** That needs the corpus embedded during the
  image's Qdrant bake, which reruns on every backend change (about 35 minutes of CPU embedding each
  time). It also changes the collection that every other path depends on.
- **pgvector beside the sparse collection (chosen).** Postgres + pgvector inside the Space
  container, loaded with vectors computed once per corpus in CI.
- **bge-m3 on CPU.** Its official ONNX export is 2.27 GB in fp32. The index build would take far
  longer, and so would every query.

## Decision Outcome

Chosen: **a pgvector dense leg, fused with BM25 by reciprocal-rank fusion (RRF).**

- **Model.** `intfloat/multilingual-e5-small` (MIT, 384 dimensions, about 100 languages) runs
  through ONNX Runtime.
  - **Pins.** Revision `614241f622f53c4eeff9890bdc4f31cfecc418b3`; `onnx/model.onnx` and
    `tokenizer.json` are pinned by SHA-256 in `app/dense_cpu.py`. The fp32 export is used, not
    int8, because integer kernels can differ between the CPU that builds the index and the one
    that serves queries.
  - **Measured** on a 2-core i5-7200U: queries take 10 ms. Passages embed at about 8 per second
    (FAQ length) or 3 per second (512 tokens). On the FAQ corpus, the int8 export's vectors
    matched fp32 at cosine 0.996.
- **Embedding.** The build workflow's "Embed the corpus for the dense leg" step runs
  `python -m app.pgvector_dense build-artifact`.
  - It embeds every document from `app.indexer.load_documents()`, using the indexer's own
    `_embedding_text`.
  - It records each vector against the document's Qdrant point id
    (`retriever.deterministic_point_id`).
  - The step is cached on the corpus JSONL, the CSVs, `dense_cpu.py`, `pgvector_dense.py` and
    `indexer.py`, so an unchanged corpus is not embedded again.
  - The model and vectors ship in the base image as `App/Data/dense`: 470 MB of model plus about
    5.5 MB of float16 vectors.
- **Load.** The Space layer installs Debian bookworm's PostgreSQL 15. The pgvector 0.8.1 extension
  files are copied from `pgvector/pgvector:0.8.1-pg15-bookworm`, pinned by digest. A SQL selftest
  proves they load and answer an HNSW query.
  - `python -m app.pgvector_dense load` scrolls the baked Qdrant collection and requires its
    point-id set to equal the artifact's exactly. Ids derive from source, page, section, chunk id
    and the text's hash, but not from the title or metadata that the embedded text's contextual
    prefix is built from. So the manifest also carries a fingerprint of every document as Qdrant
    stores it, and one changed since it was embedded refuses the load.
  - It then copies Qdrant's own payloads and the vectors into `dense_passages` and builds an HNSW
    index (`m=16`, `ef_construction=64`, cosine).
  - `check` then queries as the serving role.
  - A refused or failed load never fails the build: the tables are dropped, retrieval serves BM25
    alone as before, and `/ready` names the reason.
- **Runtime.** Postgres listens on a Unix socket in `/app/pgrun` only (`listen_addresses=` empty,
  `--auth-host=reject`, no `host` lines in `pg_hba.conf`). It runs as `postgres` under
  supervisord. Peer authentication with an ident map lets the serving process (OS user root)
  connect only as `ura_dense_reader`, which can only `SELECT`, and the Space build fails if root
  can still connect as `postgres`. Superuser steps (selftest, load) run as the `postgres` OS user.
  - The retriever opens the leg when `LOCAL_DENSE_BACKEND=pgvector` and the collection is
    sparse-only, preferring it to Vectorize. It waits up to 20 s for the socket, never for a table
    built for another model.
  - Each search embeds the query and runs a filtered HNSW scan (`hnsw.iterative_scan =
    relaxed_order`, `ef_search` 80) under Qdrant's filter semantics. The result is fused with the
    BM25 list by `rrf_fuse_ranked_lists`, the combinator the statutory graph leg already uses.
  - A question with no BM25 term still gets candidates from the dense leg. Whether any of them
    can be answered from is the lexical gate's decision (below).
  - Behind the CPU lexical reranker, the reranked order is blended with the fused order, so word
    overlap alone does not undo the dense ranking.
  - The leg has its own circuit breaker; its failures never count against Qdrant's. Each search
    runs on a two-worker pool and is abandoned after `LOCAL_DENSE_TIMEOUT_S` (2 s). Postgres
    enforces `statement_timeout` (1.5 s), and a search waits at most 0.5 s for the store's
    connection, so a stalled Postgres costs a query its dense half, never its BM25 half. While
    the breaker is open, `/ready` reports the leg unavailable and the mode as `sparse`.
  - **Corpus binding.** The load records the collection's BM25 binding hash in `dense_meta`, and a
    table without one is refused. When the serving collection's hash differs (a promoted corpus,
    G292), the leg turns itself off, so removed guidance cannot be served from stale vectors.
  - **Verified against the serving collection.** The table is a snapshot, and a live replacement
    (corpus sync deletes a superseded guide's points without a tombstone) or a promotion between
    binding refreshes could leave it holding passages the collection no longer serves. Every
    dense hit is confirmed with one `retrieve` call and built from the collection's current
    payload; unconfirmed hits are dropped (`local_dense_stale_hits_total`). When Qdrant is down
    nothing can be confirmed, so the leg is not served alone and retrieval falls back as before.
  - **Bounded concurrency.** At most one dense search per pool worker is outstanding; a stalled
    search keeps its slot until it returns, so expired work never queues ahead of a recovery
    probe, and the leg counts as unavailable while its breaker is open or half-open.
  - **Artifact integrity.** The manifest carries a SHA-256 over the ordered ids and the vector
    bytes, so a replaced or reordered `vectors.npy` is refused.
- **Gates read word overlap, never dense similarity.** e5's cosine similarities are compressed
  into roughly 0.7–0.9 and are not calibrated, so they decide nothing. The PR 707 review found
  that the CPU layer's lexical reranker score, which is already bounded in [0, 1], reached the gates
  through a sigmoid (`normalize_rerank_score`). Every hit then gated at 0.5–0.73, above the 0.30
  abstention threshold, so a dense-only passage with no word in common could carry an answer.
  - With the dense leg in a result, each hit's `score_calibrated` (what abstention and pruning
    read) is now the CPU reranker's own bounded score.
  - `score_norm` (what ordering and preference boosts read) is that same set of scores reassigned
    by blended rank, so boosts no longer sort the list back into word-overlap order.
  - The effect is that abstention on the Space fires again for weak lexical matches whenever the
    leg is on. Calibrating dense similarity so it can take part in gating is G524.

## Consequences

- **Good:** the CPU deployments get dense + BM25 hybrid retrieval with no egress. `/ready` reports
  `retrieval: hybrid` and `capabilities.local_dense: "pgvector multilingual-e5-small, N vectors"`.
- **Good:** an unchanged corpus costs nothing at build, and the Space build only loads vectors and
  builds the index, which takes seconds.
- **Good:** a corpus or model mismatch cannot serve wrong vectors. It is refused at load (point
  ids), at open (model, revision, dimension) and at the model files (SHA-256).
- **Bad:** the image grows by about 470 MB of model and about 150 MB of PostgreSQL packages. Each
  uvicorn worker holds an ONNX session of roughly 500 MB.
- **Bad:** the first build after a corpus change embeds the whole corpus, about 15–20 minutes on a
  4-core runner. The cache key covers `point_ids.py` (moved out of `retriever.py`), so a change to
  how point ids are derived re-embeds, and other retriever edits do not.
- **Behaviour change:** with the leg on, the Space abstains on weak lexical matches it used to
  answer from (see the gates bullet). That is the gate working as documented, but the abstention
  rate will rise; watch `retrieval_abstention_decisions_total{signal="rerank",abstained="true"}` after the roll.
- **Neutral:** Crane Cloud's image carries the artifact but never sets `LOCAL_DENSE_BACKEND`; its
  retrieval is unchanged.
- **Open:** dense similarity does not take part in abstention (G524). Luganda, Runyankole and
  Acholi still rely on translate-then-retrieve; e5 is not trained on them.
- **Open (G525):** the serving stack runs as root under supervisord, as every program in the base
  image does. Peer authentication stops the app's own database connection from writing, but a
  compromised root process could still switch to the `postgres` OS user. Running uvicorn as an
  unprivileged user is a change to the whole image, tracked separately.

- **Incident (2026-11-09):** within an hour of going live, the leg put an outdated EACCMA passage
  (baggage allowance USD 500) in front of the model, and the Space quoted it. The leg was switched
  off with the Space secret `LOCAL_DENSE_BACKEND=none` (all eight live probes passed again) until
  precedence caught paraphrased and former-value contradictions and stopped sending them to the model
  (G534, ADR-0033 amendment). Re-enabling is in the runbook.

## Confirmation

- `App/backend/tests/test_local_dense_leg.py` (unit; Postgres and the model faked) covers:
  - pinning, pooling and prefixes;
  - artifact ids and refusals;
  - the load's id-set check and grants;
  - filter semantics;
  - fusion and the no-term path;
  - the separate breaker, the blend wiring and readiness;
  - backend selection order and the startup wait;
  - the Space layer's pins, socket-only Postgres and matching tokenizer pins.

  18 controls were each disabled in turn, and a test failed every time.
- `App/backend/tests/test_local_dense_pgvector_live.py` runs against real pgvector in
  `local-dense-pgvector.yml` (service container, same digest). It covers the load, the read-only
  serving role, filters and reloads.
- `hf-space-image-check.yml` builds the Space Dockerfile before merge. It then starts Postgres
  with supervisord's command line and queries pgvector (`.github/scripts/hf_space_pgvector_smoke.sh`).
- After a roll, `/ready` must show `retrieval: hybrid` and a `local_dense` vector count equal to
  the corpus size. The Space build log must show `dense leg loaded`.
