# ADR-0033: The Statutory Knowledge Kernel

## Status

Accepted — 2026-11-08

## Context and Problem Statement

An architecture review on 2026-11-08 evaluated a proposed hybrid ontology design against the system
as it runs. The proposal had three layers: a hand-built OWL 2 ontology in Neo4j, NVIDIA Auto Ontology
over URA databases, and LangGraph routing between them. The review is published at
<https://claude.ai/artifact/SEZXbBGLwYRm2oVnCiBviQ>.

What the system had, measured on `dev` at `9e4f3e8`:

- **A statutory graph covering a third of the rate tables.** It had 116 nodes and 211 edges, built
  from the effective-dated rate tables (`app/tax/data/FY*.json`). `build_graph` dropped any key whose
  prefix mapped to no known tax. Only **34 of 102** distinct keys reached it, so every deadline,
  penalty rule, fee and procedural timeline was missing. (A coverage count built on
  `tables.known_rate_keys` sees only 98 keys: it lists numeric scalars and omits band lists and rule
  strings.)
- **Figures with no legal basis that looked verified.** Ten FY2026-27 keys and four FY2025-26 keys
  had no `legal_basis` and were not marked `unverified`. Among them are `paye_due_date_monthly` and a
  UGX 6,000,000 EFRIS penalty.
- **No machine-readable schema, no validation and no version** for the graph. Answers, audit rows
  and suspended approvals could not say which knowledge they were computed from.
- **No penalty or interest computation.** `app/tax` held rate tables and money helpers only.
- **No precedence** between calculator, graph, document, FAQ and web figures, and no record when
  they disagree.
- **Shadow agreement of 12 of 12** on the authored multi-hop set. The scorer warns that the set was
  tuned against.
- **No URA operational database reachable.** Every URA connector runs in simulation unless declared
  live, and production offers only live ones.
- **Latency already over budget.** On one RTX A6000, a 14B FP8 model already exceeds the 3 s p95
  target at four concurrent turns.

The proposal's formalism did not fit:

- OWL 2 DL assumes an open world and has no arithmetic or rule priority. It cannot conclude that a
  return was not filed, compute monthly interest, or let an exemption override a general rate.
- Neo4j would add a JVM service to a 16 GB CPU host for a graph that fits in a few hundred
  kilobytes. `app/graph/store.py` already records why an embedded graph database was rejected.
- Auto Ontology targets relational sources, and there was none to describe.

## Decision Outcome

1. **The kernel is the graph, plus a schema, rules, a version and a contract.** It lives in
   `app/kernel/` over the existing `GraphStore`.
2. **Every rate-table key is in the kernel.** Four node kinds are added: `obligation`, `penalty`,
   `fee` and `parameter`. A test fails if any key is unclassified. A fact with no legal basis is
   marked `unverified` and `uncited`, never served as verified.
3. **The schema is one model with three outputs.** `app/kernel/model.py` generates a Neo4j Ontology
   Graph Specification 1.1.0 document (validated against the published JSON Schema), W3C SHACL
   shapes (validated with pySHACL in CI), and the runtime invariant checks. OWL is not the engine.
4. **Releases are versioned and content-addressed.** `app/kernel/releases.json` records each release
   with the SHA-256 of the canonical graph and of the ruleset. CI fails when the tables or rules
   change without a new release entry. A release starts as `engineering_baseline` and becomes
   `approved` only with a named reviewer.
5. **Every turn is pinned.** The kernel version, ruleset version and as-of date are fixed at turn
   start, recorded in the audit row, and carried in the graph state, which the approval HMAC already
   seals. A suspended approval cannot resume under a different kernel release.
6. **The kernel is the agent's action contract.** Kernel functions accept only declared concepts
   and return `NotInKernel` otherwise. The graph's act node dispatches only registered tools.
7. **Precedence is fixed:** computed result, then kernel fact, then official document, then FAQ, then
   live web. A lower source contradicting a higher one is recorded as a conflict and never overrules
   it.
8. **Rules as code.** Late-filing penal tax, late-payment interest, EFRIS penal tax and the digital
   tax stamp penal tax are computed from kernel parameters, with a provision at every step. They are
   marked `pending_tax_team_review` until a reviewer approves them in a release.
9. **Thresholds, not defaults, for the heavier layers.**
   - A Neo4j backend behind `GraphStore` becomes recommended above 50,000 nodes, when traversal p99
     exceeds 5 ms, or on a staff need for ad-hoc Cypher.
   - The data semantic layer (Auto Ontology or equivalent) is staff-only. It requires a
     data-sharing agreement, a DPIA, Vault for credentials, certified metrics and SELECT-only access,
     and its production gate refuses to start without them.
10. **No LangGraph migration now.** The in-house runtime meets the current single-session needs and
    keeps the ADR-0030 guarantees. A migration needs a concrete multi-session durable-workflow
    requirement, and would use "sync" durability on approval paths.
11. **The model is decided by measurement.** `scripts/bench_llm_concurrency.py` measures p50 and p95
    at 1, 4 and 8 concurrent turns. A larger model serves only the turns that need it.

## Consequences

- Positive: all 102 rate-table keys become answerable with a provision and an effective range. The
  14 uncited figures are visible as such.
- Positive: an answer, an audit row and an approval each name the knowledge they rest on, and can
  be replayed against it.
- Positive: a rate-table edit cannot reach production without a release entry that CI checks.
- Negative: every rate-table or rules change now needs a release entry, a deliberate step.
- Negative: the penalty and interest rules stay marked unverified until URA's tax team signs them
  off. The available statute text for interest is the 2021 Income Tax Act s.136; the tables cite the
  revised Tax Procedures Code Act (Cap 343) s.39.
- Neutral: Neo4j and the semantic layer are implemented behind gates that are closed, and stay so
  until their conditions are met.

## Confirmation

Filled in per phase; see "Implementation record" below.

### Implementation record

- Phase 0 (this record and baselines): shadow agreement 12 of 12; kernel coverage 34 of 102 keys; 14
  uncited figures. GPU latency not measurable on the build host (no GPU); the benchmark harness is
  the deliverable.
- Phase 1 (kernel): every one of 102 rate-table keys in the kernel (322 nodes, 593 edges; was 116 and 211);
  0 invariant violations; the Ontology Graph Specification document validates against the published
  1.1.0 schema; the graph conforms to the SHACL shapes under pySHACL 0.30.1; release 1.0.0 recorded as an
  engineering baseline; shadow agreement unchanged at 12 of 12; four kernel tools (76 tools in all).
  `tests/agents/test_statutory_kernel_2026_11.py` (43 tests). Each of nine controls was disabled in turn and the suite failed:
  coverage, uncited-as-unverified, the resume seal, the act contract, the precedence rank filter, table
  parity, the SHACL provenance rule, release matching and the audit pin.
- Phase 2 (core): rules as code in `app/kernel/rules.py` (ruleset 1.0.0, every rule
  `pending_tax_team_review`, every result unverified until approved), reached through the
  `kernel_compute_penalty` tool (77 tools in all); release 1.1.0 recorded for the ruleset, graph
  content unchanged; a hash-verified snapshot per released content hash, so a past turn's facts replay
  exactly; the legal-change pipeline (`app/kernel/legal_change.py`: diff, corroboration against the
  Act's text, a review queue needing a named reviewer); eight golden scenarios with asserted traces,
  including both sides of the 1 July boundary, a contradicting passage and replay;
  PROV-O and AgentO export of the audit ledger (`app/audit/provenance.py`); the concurrency benchmark
  harness (`scripts/bench_llm_concurrency.py`), not yet run on the A6000. Building replay showed that
  serialising the graph returned live property dictionaries, so a caller editing the copy changed the
  cached kernel; `Node`/`Edge` now copy them. Rule results are verified only under an approved
  release, so marking a rule approved in code is not enough. `tests/agents/test_kernel_core_2026_11.py`
  (47 tests). Each of 19 controls was disabled in turn and the suite failed.
- Phase 3 (advanced), built behind closed gates:
  - **Neo4j backend** (`app/graph/neo4j_store.py`, `GRAPH_STORE_BACKEND=neo4j`, off). It matches the
    in-memory store on every node kind, neighbour and walk. Every statement is a fixed, parameterised
    constant; labels and relationship types come from the enums; walks keep the in-memory bounds.
    Production refuses it without verified TLS and credentials (G506). If Neo4j is unreachable, the
    in-memory graph is served. The kernel's hashed store stays in memory, and the Neo4j copy is
    synced from it. The advisor reports 322 nodes; the thresholds are not met, so it stays off.
  - **Staff-only semantic layer** (`app/semantic_layer/`, `FLAG_SEMANTIC_LAYER`, off). Two tools in a
    new `semantic_layer` namespace (79 tools, 19 namespaces), declared `high` risk for `ura_staff` and
    `ura_admin` only. Certified aggregate metrics only, never free SQL. A SELECT-only guard checks
    each template against its declared sources. Execution is read-only, with 500 rows and 10 s at
    most, and groups below 10 are suppressed. Production refuses it without https Vault, a Vault path,
    a data-sharing agreement and a DPIA reference, and a certified metric, and with any plaintext DSN
    (G507). The catalogue ships two drafts and certifies nothing.
  - **Offline refinement** (`app/kernel/refine.py`). Each turn records the concepts the kernel lacked
    and the uncited figures it served, by key only, in the audit row (payload schema 5). Recurring
    ones, and recurring evidence conflicts, become proposals in the review queue. Nothing is applied.
  - **Consistency:** the new rules match the existing `penalty_late_filing` calculator on penal tax and
    capped interest, and a parity test holds them together. The ruleset now cites the interest cap
    as the calculator does, which is release 1.1.1.
  - `tests/agents/test_kernel_advanced_2026_11.py` (58 tests). Each of 30 controls was disabled in
    turn and the suite failed.
- Audit, 2026-11-08 (QA review of phases 0-3, three phases):
  - **A, figures verified against original documents** (release 1.2.0). Of the 14 figures with no
    legal basis, 13 now cite a document and page. Six of the 14 were wrong or stale and are corrected:
    baggage USD 500 to 2,000, land transfer stamp duty 1% to 1.5%, the FY2026-27 EFRIS floor UGX
    6,000,000 to double the tax or 200,000, the EFRIS offline window 24 hours to 5 days, the vehicle
    transfer fee, and the quarterly VAT test, UGX 37,500,000 to 75,000,000 (not one of the 14, but
    stale against the new annual threshold). The 2026 vehicle stamp duty was missing and is added. The
    duplicate logbook fee cannot be confirmed and says so. `app/tax/evidence/rate_evidence.json` quotes
    every page, and `tests/agents/test_rate_table_verification_2026_11.py` re-reads the PDFs. Answer
    templates had their own copies of these figures, so a table fix did not reach answers; they now
    read the table, and a test forbids a literal amount in a template for a table figure. The kernel
    checks figures the law derives from each other (`validate.DERIVED`).
  - **B, freshness** (release 1.2.1). One clock: the tables read the Kampala date, as the kernel pin
    does (they read UTC, three hours behind at the 1 July turn). After the last published table
    ends, answers say no newer table exists rather than rolling over silently. An unconfirmed figure
    says so in the answer. The frozen `query.CURRENT_FISCAL_YEAR` constant is gone. Precedence is
    edition-aware: a passage contradicting the figure in force ranks below the others; an older
    edition is *superseded* and not sent to reviewers, while an unlabelled or current one is a
    *conflict*. USD amounts and UGX penalties are now compared, and values print as reviewers read
    them (`6000000`, not `6e+06`). `tests/agents/test_kernel_freshness_2026_11.py`; 17 controls
    disabled in turn, each failed it.
  - **C, the HF Space CPU image.** The Space build is gated on a recorded kernel release
    (`python -m app.kernel.release check`; a base older than the kernel skips it). The Phase 3
    settings are explicit there (`GRAPH_STORE_BACKEND=memory`, `FLAG_SEMANTIC_LAYER=false`), and
    `/ready` reports the kernel release, graph store, semantic layer and tables in force.
- CodeRabbit review of PR 706 (41 findings, all verified and fixed). Three had disabled earlier work
  in production:
  - The discrepancy queue's CHECK rejected `evidence_conflict`, so no conflict was ever queued. Both
    schemas now allow it, and existing SQLite and Postgres databases are migrated at start-up.
  - The kernel pin and trace were reset before the audit row was written, so every row recorded
    `null`. They now travel on the turn's result, as the plan does.
  - The REST loop's tool calls never reached the ledger. They are now passed as records.

  Also fixed:
  - **Precedence:** it is re-applied after FAQ promotion, a passage stating the kernel's figure
    beside another rate is not a conflict, and "usd" is a unit word.
  - **Neo4j:** publishes by release, keeps the first write, falls back to memory on a failed read,
    closes its drivers, and keeps walks within their limits.
  - **Rules and tools:** duration-dependent penalties need a stated number of months, and
    non-finite amounts are input errors (ruleset release 1.2.2).
  - **Approval:** an unchanged baseline can be approved, and legal-change decisions are locked, keep
    any earlier decision, and allow rejecting a stale draft.
  - **Semantic layer:** literals in parameters, calendar dates, null certifiers, comma-joined
    relations and quoted function names are now handled.
  - **Unverified figures** carry their warning in lookups and workflows.
  - **Graph tools** bind kernel-tool arguments from the question when it states them.
  - **Provenance export:** per-turn dates.
  - **Table reload** refreshes the kernel.
  - **Python 3.10:** clocks no longer use `datetime.UTC`.

  `tests/agents/test_kernel_review_fixes_2026_11.py`; 29 fixes disabled in turn, each failed it.
