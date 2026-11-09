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
- Second review round (26 more findings, all fixed):
  - **Queue migration:** the SQLite rebuild is one transaction, and a leftover copy from an
    interrupted rebuild is dropped. The Postgres lookup is scoped with `to_regclass`.
  - **Streamed turns:** they hold one kernel trace for the whole turn, so agentic kernel calls reach
    the audit row.
  - **Precedence:** a stated change ("is now 20%") is a conflict even beside the old figure;
    conflicts first found after FAQ blending are queued, once per turn; the LangGraph FAQ gate also
    re-orders by precedence.
  - **Graph tool binding:** exact sections only, the requested fiscal year, and an amount stated
    as the tax due.
  - **Semantic layer:** the production gate checks the effective flag (overrides, rollouts) and runs
    again at every use; the SQL guard refuses parenthesised FROM items; quoted identifiers are not
    rewritten.
  - **Rate answers:**
    - a question naming a fiscal year is answered from that year's table, where the FY2025-26 VAT
      threshold had been answered with FY2026-27's;
    - a named year, or an explicit calculation period, gets no rollover caveat;
    - no literal 6% stands in for a missing import WHT entry;
    - the rate schedule names its unverified figures;
    - a missing stamp-duty rate is an error, not UGX 0.
  - **Load evaluations:** figure concepts are mandatory and matched as whole words, with
    equivalent forms accepted.
  - **Docs:** tool counts give the production inventory (56 tools, 13 namespaces).
- Third review round (13 findings, all fixed):
  - **Precedence:**
    - a change cue counts only when the fact is named before it in the same statement, with no
      other figure in between ("the export rate increased to 5%" is not about the standard rate);
    - a past mention ("previously it was 18%", "from 18% to") no longer hides the figure the
      passage now gives;
    - a conflict is marked queued only once its write succeeds, including a Postgres write that
      reports `failed`.
  - **Streamed turns:** each turn gets a fresh kernel pin and trace, and the outer context is
    restored after the audit, so a WebSocket session no longer carries one turn's misses or date
    into the next.
  - **Rate answers:**
    - a named fiscal year with no table is declined, vehicle questions included;
    - summaries, aliased entries and template-quoted figures name every unverified figure they show;
    - `lookup_rate` and `list_available_rates` warn when no period was asked and today is past
      the latest table;
    - the schedule names figures with no statutory source.
  - **SQL guard:** every FROM list is read by parenthesis depth, in CTE bodies and subqueries too.
  - **MCP errors:** the two land-transfer tools' output schemas require every field only on
    success, so a missing-rate error reaches the host intact.
  - **Load evaluations:** the matcher normalises whitespace and requires whole numbers
    (`2,000` is not in `2,000,000`), shared by all three runners and tested in
    `tests/test_load_runner_concept_matching.py`.
  - Each of 17 fixes was disabled in turn, and a test failed every time.

### Amendment (2026-11-09): superseded figures stay out of answers (G534)

After the dense leg (ADR-0034) went live, the Space answered "What is the passenger baggage
duty-free allowance?" with **USD 500** instead of the verified USD 2,000. Two failures combined:
- **The match was by name.** Precedence matched a passage to a fact only when the fact's name
  appeared near the figure. Dense retrieval surfaced an EACCMA Fifth Schedule paraphrase
  ("personal effects … not exceeding USD 500") that never names the allowance.
- **Detection only reordered.** A detected contradiction was moved down, but nothing told the model
  so, and the model could still quote it.

The dense leg was switched off on the Space (`LOCAL_DENSE_BACKEND=none`) until the following:
- **Former values are contradictions on the same subject.** A fact's legal basis can record a
  replaced value ("raised from USD 500"; also "previously", "formerly", "replacing", "replaced",
  "was"). A currency or percent sign is required, so a date never counts, and the current value
  never counts. A passage retrieved with the fact that states such a value is a conflict
  (`reason="former_value"`) when two of the fact's distinctive words (five letters or more, from
  its name or legal basis) stand near the value. A paraphrase still shares its Act and schedule;
  the UGX 150,000,000 presumptive-tax ceiling does not share the VAT threshold's words. Facts now
  carry the full legal basis, not only the claim's citation.
  - This does not apply to a passage that also states the current figure, or that frames the old
    value as history ("formerly USD 500").
- **Stale passages are left out.** A former-value or older-edition (`superseded`) passage is not
  sent to the model. It is listed once on the graph hit under `kernel_precedence_dropped` (key,
  kernel and stated values, edition, reason, source, passage identity), however often the order is
  rebuilt.
- **Unresolved disagreements are disclosed, not hidden.** An unlabelled or current passage stating
  a different figure may be right: after a new Finance Act the kernel can be the stale side. It is
  kept, ranked last and queued for review as before. Its header, written by code outside the
  spotlight markers, now says that it conflicts with the official rate table, gives both figures,
  and tells the model to report both and advise confirming with URA.

`tests/agents/test_kernel_precedence_superseded_2026_11.py` uses the live passage verbatim. Fifteen
controls were each disabled in turn, and a test failed every time.

### Amendment (2026-11-09, later): replaced figures are screened on every path (G535)

The G534 fix was deployed, and the dense leg was switched back on. A paraphrase probe, "Under the
EACCMA Fifth Schedule, what value of accompanied goods is exempt for a traveller?", was still
answered **USD 500**. The kill switch went back on within two minutes. With the leg off, the same
question still answered USD 500, so the cause was not dense retrieval:
- **Precedence needs a graph hit.** The graph leg returns a fact only when the question names it,
  and this one never did.
- **The corpus itself was stale.** Eleven FAQ rows stated a replaced figure as current: the USD 500
  baggage allowance, and the UGX 150m annual / 37.5m quarterly VAT threshold. The portal navigator
  gave the USD 500 allowance as well.

Decision:
- **A former-value screen on every path.**
  - `precedence.screen_former_values` runs on everything `HybridRetriever.search_planned` returns
    (REST, streaming, the RAG tool, LangGraph and voice). It also runs in
    `reorder_after_promotion`, so FAQ rows blended in later are screened too. Neither needs a graph
    hit.
  - It checks only the facts in force whose legal basis records a replaced value, so it is cheap.
- **Explicit subject cues.** A number alone cannot say which figure it is: UGX 150,000,000 is the
  replaced VAT threshold and also the presumptive-tax ceiling in force. Each fact with a replaced
  value has subject cues in `_SUBJECT_CUES`:
  - baggage: a passenger or traveller word;
  - VAT threshold: VAT and registration or threshold, and no "presumptive" within 150 characters;
  - EFRIS refund: refund and a receipt or invoice word.

  A test fails when a rate table records a replaced value that has no cues. UGX amounts written in
  words ("UGX 150 million", "UGX 37.5M") are read.
- **The data corrected at source.** Each figure was checked against the PDFs in `Data/pdfs`:
  - USD 2,000: Taxation Handbook FY2025-26, pp.64 and 80, and the Passenger Baggage leaflet
    FY2024-25, p.2.
  - UGX 300m: Taxes Amendments FY2026-27, pp.4 and 13.
  - UGX 75m in three months: VAT Act s.7(1)(a), one quarter of the annual threshold.

  The 11 FAQ rows and the portal navigator's baggage strings (two pages and the point-of-entry list) were corrected. Rows from edition-labelled files now
  give the current figure and say what it replaced. Three gold ids in
  `evals/retrieval_multilingual/gold.jsonl` follow their rows' new content hashes.

- **Limitation.** A question about a past year is answered from that year's rate table. Passages
  giving last year's figure are left out of retrieval even then.
- **Tests.** `tests/agents/test_kernel_former_value_screen_2026_11.py` includes a corpus test: no
  FAQ row may state a replaced figure as current. Nine controls were each disabled in turn,
  including putting one stale row back, and a test failed every time.
