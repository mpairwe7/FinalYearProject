# Statutory Knowledge Kernel

Reference and explanation for `App/backend/app/kernel/` (ADR-0033). For the release procedure see
[runbooks/kernel-release.md](runbooks/kernel-release.md).

## What it is

The kernel is the system's single source of truth for Ugandan tax figures. Every rate, threshold,
deadline, penalty, fee and computation parameter in URA's effective-dated rate tables
(`App/backend/app/tax/data/FY*.json`) becomes a node in the statutory graph. Each node is tied to:

- its tax;
- its taxpayer class;
- its fiscal year;
- the provision that imposes it;
- the figure it superseded.

On that graph the kernel adds:

- **a schema**, generated as a Neo4j Ontology Graph Specification 1.1.0 document and as W3C SHACL
  shapes;
- **validation**, by the published JSON Schema, pySHACL in CI, and runtime invariants;
- **releases**: versioned, content-addressed, with a review status;
- **typed functions**, the only way the agent reads figures;
- **a precedence order**, with conflict recording;
- **rules as code** for penalties and interest (phase 2).

The language model never states a figure from its own knowledge. It links a question to declared
concepts, calls kernel functions, and explains what they return.

## Node kinds

| Kind | What it holds | Example key |
| --- | --- | --- |
| `rate` | A rate or band set | `vat_standard`, `paye_bands_resident` |
| `threshold` | A gate on whether a rule applies | `vat_registration_threshold_annual` |
| `obligation` | A deadline or procedural time limit | `paye_due_date_monthly`, `objection_timeline_days` |
| `penalty` | A penal tax, penalty or interest rate | `penal_tax_late_filing_minimum_ugx`, `late_payment_interest_monthly_rate` |
| `fee` | A fixed fee for a service | `ursb_name_reservation_fee_ugx` |
| `parameter` | A number a computation needs | `bik_days_in_year_standard`, `fx_conversion_rule` |

Structure nodes are `tax_type`, `taxpayer_class`, `provision` and `fiscal_year`. Every table key is
classified, and `test_every_rate_table_key_reaches_the_kernel` fails if a new key is not.

**A figure with no recorded legal basis is never served as verified.** It is marked `unverified`
and `uncited`. Release 1.0.0 had 14 such figures. Since release 1.2.0 one remains,
`motor_vehicle_duplicate_logbook_fee`, which no current source confirms (see "Verified figures").

## Functions

| Function | Returns | Tool |
| --- | --- | --- |
| `fact(key, as_of)` | The figure in force on a date | `kernel_fact` |
| `obligations(as_of, tax)` | Deadlines and time limits | `kernel_obligations` |
| `penalties(as_of, tax)` | Penal taxes, penalties, interest | `kernel_penalties` |
| `explain(provision)` | Every figure a provision imposes | `kernel_explain` |
| `rules.*` (below) | A computed penal tax or interest, step by step | `kernel_compute_penalty` |

Each returns a `KernelFact` (value, unit, fiscal year, effective range, provision, unverified, uncited,
kernel version) or a `NotInKernel` with a reason:

- `undeclared`: no such concept;
- `no_fiscal_year`: no release covers the date;
- `not_in_year`: the concept is declared, but not for that year.

The tools are in the `tax_graph` namespace and are gated by `FLAG_TAX_GRAPH`.

## Precedence

A computed result outranks a kernel fact, which outranks an official document, a curated FAQ and the
live web, in that order. A lower source can explain a higher one but never overrules it.

When graph fusion is on (`FLAG_GRAPH_FUSION` with `FLAG_TAX_GRAPH`), a fused graph hit is compared
with the passages beside it. A passage stating a different figure is attached to the hit as
`kernel_conflicts` and sent to the discrepancy review queue as an `evidence_conflict`.

## Verified figures

On 2026-11-08 the figures with no legal basis were checked against URA's published law and guidance
(`Data/pdfs`, URA's site). Six were wrong or stale and were corrected, and 13 of 14 now cite a
document and page. The rest of the year's figures were checked against URA's FY2026-27
amendments booklet, which also corrected the quarterly VAT test.

- **Evidence:** `app/tax/evidence/rate_evidence.json` holds, for each verified figure, the value,
  document, page and a verbatim quote, plus web sources and open items.
- **Re-check:** `tests/agents/test_rate_table_verification_2026_11.py` re-opens each PDF page in CI
  and checks the quote and the table value. An FAQ web page is not a gazetted schedule, so a figure
  that rests on one stays unverified.
- **Templates:** answer templates read the year's table (`calculator_router._table_figures`). A
  template for a table figure may not write its own amount.
- **Derived figures:** `validate.DERIVED` checks figures the law defines from others, for example
  the quarterly VAT test as a quarter of the annual threshold (VAT Act s.7(1)(a)).

## Freshness

- **One clock.** The tables, the kernel pin and the calculators read the date in Kampala
  (`tables.kampala_today()`).
- **No silent rollover.** `tables.coverage()` returns `in_force`, `before_first` or `after_latest`.
  After the last table ends, answers use it and say that no newer table has been published.
- **Unconfirmed figures say so** in the answer itself.
- **Editions.** `precedence.apply_precedence` ranks a passage that contradicts the figure in force
  below the others:
  - from an older edition, it is *superseded*: expected, and not reported;
  - unlabelled or current, it is a *conflict*, and goes to the discrepancy queue.

  It judges by the figure the passage states, not only by labels, because about two thirds of URA
  filenames carry no fiscal year. Retrieval separately demotes editions four or more years old.

## Readiness

`GET /ready` includes `capabilities.kernel` (release, review status, ruleset),
`capabilities.graph_store`, `capabilities.semantic_layer` and `capabilities.rate_tables` (years held,
year in force, coverage), so a deploy can be checked from outside.

## Pinning

Each turn is pinned at its start. The kernel version, ruleset version, content hash and as-of date
(Kampala time) are:

- stored in `turn_context.ACTIVE_KERNEL_PIN`;
- written to the audit row (`kernel_pin`, since payload schema 4);
- stamped on the graph state.

Because the approval HMAC (ADR-0030) covers the whole state, a suspended approval cannot resume
under a different kernel release.

## Schema artefacts

`python -m app.kernel.schema_export --out DIR` writes:

- `kernel.ontology-graph-spec.json`, validated against the vendored
  `app/kernel/vendor/ontology-graph-spec-1.1.0.schema.json`;
- `kernel.shapes.ttl` (SHACL);
- `kernel.data.ttl` (the instances, as RDF).

RDF names use the `urn:ura-kernel:` scheme, because the kernel names no web resource it controls.

## Invariants

`app.kernel.validate.check(store)` runs without an RDF stack. It checks:

- the model's required properties, patterns and cardinalities;
- provenance (a figure cites a provision or is unverified);
- non-overlapping fiscal years;
- a same-key, acyclic supersession chain;
- completeness and value parity against the rate tables.

## Rules as code

`app/kernel/rules.py` (ruleset 1.0.0) computes from kernel parameters for the as-of date, and names
the provision at every step.

| Rule | Arithmetic | Source |
| --- | --- | --- |
| `late_filing_penal_tax(tax_payable, months_late)` | greater of UGX 200,000 and 2% × tax × months; a part-month counts (`late_filing_months`) | TPCA (Cap 343) s.49(1) |
| `late_payment_interest(unpaid, months, penal_tax)` | 2% per month, simple, capped at principal plus penal tax | TPCA s.39; ITA s.136(5), (7) (2021 text) |
| `efris_penal_tax(tax_due)` | greater of double the tax and the minimum | TPC (Amendment) Act 2026 s.4 (s.93) |
| `dts_unstamped_penal_tax(tax_due)` | greater of double the tax and the minimum | TPC (Amendment) Act 2026 s.2 (s.21(3)) |

Every rule is `pending_tax_team_review`, and every result is `unverified` until a release approves the
ruleset. Interest takes whole months from the caller: the available text does not say how a
part-month counts, and the kernel does not invent a convention. Changing `rules.py` changes the
ruleset hash, so it needs a release.

## Releases and replay

Each release in `releases.json` has a snapshot, `snapshots/<content hash prefix>.json.gz`: the
canonical graph, gzipped byte-stably. `release.load_snapshot(version)` rebuilds it and refuses it if
its hash differs from the recorded one. `fact` and `facts` take `store=` and `version=` to answer from
it, so an audited turn's figures can be reproduced from its `kernel_pin`. Rules replay only against
the running ruleset.

## Legal change

`python -m app.kernel.legal_change` takes a drafted rate table and the Act's text (PDF, text or
Markdown):

- `propose` diffs the draft against the latest fiscal year, and extracts every percentage, UGX
  amount, currency-point figure and day count from the Act;
- a change is `corroborated` only when one Act sentence states its value and names it (generic words
  such as "days" or "notice" do not count);
- the proposal waits in the review queue (`app/kernel/review/`, or `KERNEL_REVIEW_DIR`);
- `approve` or `reject` needs a reviewer's name and role, a rejection needs a reason, a decided
  proposal cannot be decided again, and a draft edited after proposal cannot be approved.

Nothing reaches the kernel from the Act directly. The draft table stays the source, and the Act only
checks it.

## Scenarios

`app/kernel/scenarios.py` holds golden multi-step scenarios. Each fixes its inputs, and states the
values and cited provisions its trace must contain. They cover both sides of the 1 July boundary, the
penalty and interest rules, and deadlines. The tests add a contradicting passage, which must be
recorded as a conflict while the figure stands, and replay from every recorded release. Luganda and
Swahili questions reach the kernel through the English retrieval pivot, which its own suites test.
The kernel takes declared keys, not text.

## Provenance export

`python -m app.audit.provenance --tenant default --out turns.prov.jsonld` exports `generate` rows as
W3C PROV-O JSON-LD, typed with AgentO. Each turn is a `prov:Activity` and an `agento:ExecutionStep`:

- it used its kernel release (an `agento:KnowledgeBase` with its content hash), its model, its tools
  and its index corpus;
- it generated the answer, identified by its digest.

User ids are replaced by an unsalted SHA-256 prefix. That is a pseudonym, so the export is an
internal record.

## Thresholds for heavier layers

| Layer | Status | Becomes appropriate when |
| --- | --- | --- |
| Neo4j backend behind `GraphStore` | Built, off (`GRAPH_STORE_BACKEND=neo4j`) | Over 50,000 nodes, traversal p99 over 5 ms, or a staff need for ad-hoc Cypher |
| Data semantic layer (Auto Ontology or equivalent) | Built, off, staff-only (`FLAG_SEMANTIC_LAYER`) | A data-sharing agreement, a DPIA, Vault, certified metrics |

See `docs/runbooks/graph-backend-and-semantic-layer.md`.

### Neo4j backend

`app/graph/neo4j_store.py` implements `GraphStore` over a Neo4j driver, for the graph that tools and
fusion read.

- **Release integrity:** the kernel's own store, whose hash is the release, stays in memory. Neo4j
  holds a copy synced from it.
- **Fixed statements:** every statement is a constant in `QUERIES`, with values passed as parameters.
  Labels and relationship types come from the `NodeKind` and `EdgeKind` enums.
- **Bounded walks:** walks use the in-memory bounds (`MAX_HOPS` 3, `MAX_NODES` 200).
- **Round-trip:** properties are stored as JSON, so a tuple comes back as a list.
- **Production gate:** a connection needs `neo4j+s://` or `bolt+s://` and credentials. If Neo4j is
  unreachable, the in-memory graph is served.

### Semantic layer

`app/semantic_layer/` answers what URA's data shows, for staff only. It runs certified aggregate
metrics and never free SQL.

- **Templates:** each metric's SQL is a reviewed template that passes the SELECT-only guard over its
  declared sources.
- **Execution:** read-only, at most 500 rows and 10 s.
- **Small groups:** groups under the metric's minimum size, at least 10, are suppressed.
- **Access:** the tools are `high` risk for `ura_staff` and `ura_admin`.

## Refinement

Kernel functions record two things in the turn's trace (`app/kernel/trace.py`), by key only:

- concepts the agent asked for that the kernel lacked;
- uncited figures it served.

Text the taxpayer typed is dropped. The audit row carries the trace (payload schema 5), and replays
are not traced. `python -m app.kernel.refine` queues what recurs in at least 3 turns, with recurring
evidence conflicts, as one review proposal. Nothing is applied without a reviewer and a release.

## Agreement with the calculator

`rules.late_filing_penal_tax` and `rules.late_payment_interest` agree with the `penalty_late_filing`
calculator, and a parity test enforces it. Both cite the interest cap as TPCA s.39(5). With penal tax
and interest for the same months at the same 2%, the cap cannot bind. It binds only when interest
runs longer than the late-filing period, for example a return filed on time and left unpaid.
