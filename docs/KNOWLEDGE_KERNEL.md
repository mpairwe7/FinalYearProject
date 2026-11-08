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
and `uncited`. Release 1.0.0 has 14 such figures. Among them are `paye_due_date_monthly` and
`efris_penalty_failure_to_issue_invoice`, which need a citation in the rate tables.

## Functions

| Function | Returns | Tool |
| --- | --- | --- |
| `fact(key, as_of)` | The figure in force on a date | `kernel_fact` |
| `obligations(as_of, tax)` | Deadlines and time limits | `kernel_obligations` |
| `penalties(as_of, tax)` | Penal taxes, penalties, interest | `kernel_penalties` |
| `explain(provision)` | Every figure a provision imposes | `kernel_explain` |

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

## Pinning

Each turn is pinned at its start. The kernel version, ruleset version, content hash and as-of date
(Kampala time) are:

- stored in `turn_context.ACTIVE_KERNEL_PIN`;
- written to the audit row (`kernel_pin`, payload schema 4);
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

## Thresholds for heavier layers

| Layer | Status | Becomes appropriate when |
| --- | --- | --- |
| Neo4j backend behind `GraphStore` | Phase 3 | Over 50,000 nodes, traversal p99 over 5 ms, or a staff need for ad-hoc Cypher |
| Data semantic layer (Auto Ontology or equivalent) | Phase 3, staff-only, gated | A data-sharing agreement, a DPIA, Vault, certified metrics |
