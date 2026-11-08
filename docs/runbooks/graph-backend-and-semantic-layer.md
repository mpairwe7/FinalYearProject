# Runbook: Graph backend and staff semantic layer

Both are ADR-0033 phase 3, and both are off. This runbook says when to turn each on and how.

## Neo4j graph backend (G506)

### When

Only when a threshold in ADR-0033 is met:

- more than 50,000 nodes;
- graph traversal p99 above 5 ms;
- or a staff need for ad-hoc Cypher.

Check:

```bash
cd App/backend && PYTHONPATH=. python -m app.graph.neo4j_store            # add --adhoc-cypher if staff need it
```

`recommended: false` means stay in memory. Today the graph has 322 nodes.

### How

1. Run Neo4j 5 with TLS and a certificate the backend can verify. Create a user that can only write
   `:Kernel` nodes.
2. Install the driver in the backend image: `pip install neo4j`.
3. Set `GRAPH_STORE_BACKEND=neo4j`, `NEO4J_URI=neo4j+s://...` (or `bolt+s://`), `NEO4J_USER`,
   `NEO4J_PASSWORD` and optionally `NEO4J_DATABASE`. Production refuses to start with `bolt://`,
   `neo4j://`, `neo4j+ssc://` or missing credentials.
4. Restart. On first use, the graph is built in memory and synced to Neo4j, which is cleared and
   rewritten. The kernel's own hashed store stays in memory, so releases, pins and replay are unchanged.
5. If Neo4j is unreachable, the log says "Neo4j graph backend unavailable; serving in memory", and
   answers continue from memory.

### On the HF Space

The Space sets `GRAPH_STORE_BACKEND=memory` and `FLAG_SEMANTIC_LAYER=false` explicitly in
`App/deploy/hf-space/Dockerfile`, and its build runs `python -m app.kernel.release check`. Check a
deploy with `GET /ready`: `capabilities.graph_store` and `capabilities.semantic_layer` must read
`memory` and `off`, and `capabilities.kernel` must say `released`.

### Ad-hoc Cypher

Staff query Neo4j directly, with their own read-only database role, not through the agent. The agent
runs only the fixed statements in `app.graph.neo4j_store.QUERIES`.

## Staff semantic layer (G507)

### Preconditions

All of these, before `FLAG_SEMANTIC_LAYER=true` in production:

| Condition | Setting |
| --- | --- |
| A data-sharing agreement covering the analytical data | `SEMANTIC_LAYER_DSA_REF` |
| A data protection impact assessment | `SEMANTIC_LAYER_DPIA_REF` |
| Vault holding a read-only DSN | https `VAULT_ADDR`, `VAULT_TOKEN_FILE` or `VAULT_TOKEN`, `SEMANTIC_LAYER_VAULT_PATH` (KV v2, key `dsn`) |
| A database role that can only SELECT the certified views | in the DSN |
| At least one certified metric | `app/semantic_layer/metrics.json` |

Production refuses to start if any is missing, or if `SEMANTIC_LAYER_DSN` is set. Install `psycopg` in
the backend image.

### Certifying a metric

A metric is a reviewed SQL template, not something the model writes.

1. The data owner writes or reviews the entry: one SELECT over the declared `sources`, named
   `:parameters` with types (`period`, `date`, `code`, `int`), a `group_size` column and a
   `min_group_size` of at least 10.
2. Check it loads: `PYTHONPATH=. python -c "from app.semantic_layer import catalogue; print(catalogue.load())"`.
   The guard refuses comments, multiple statements, writes, DDL, session commands, file and network
   functions, system catalogues and any undeclared source.
3. Set `status` to `certified` and fill `certified_by` and `certified_at`. Commit in a reviewed pull
   request.

### What staff see

`semantic_metric_catalogue` lists certified metrics. `semantic_metric_query` runs one. Results are
capped at 500 rows and 10 s, and say how many rows were suppressed for having fewer than the minimum
group size. Callers who are not `ura_staff` or `ura_admin` are refused by the dispatch policy.

### Development

```bash
FLAG_SEMANTIC_LAYER=true SEMANTIC_LAYER_DSN=sqlite:///analytics-dev.db \
SEMANTIC_LAYER_CATALOGUE=/path/to/dev-metrics.json uvicorn app.main:app
```

The SQLite file opens read-only.
