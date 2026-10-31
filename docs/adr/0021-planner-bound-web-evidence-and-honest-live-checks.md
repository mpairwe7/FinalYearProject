# ADR-0021: Planner-bound web evidence and honest live checks

## Status

Accepted for implementation, 2026-10-31; production acceptance pending. Partly
supersedes [ADR-0008](0008-live-web-research-ephemeral-pdf-sandbox-and-self-updating-qdrant.md):
its trigger set, source policy and FX ordering. The sandbox and corpus sync stand.

## Context and Problem Statement

The corpus is rebuilt nightly, so a waiver announced this morning cannot come from
it, and a tax chatbot that sounds current when it is not does more harm than one that
admits the limit. The live research added by ADR-0008 gave web text the same standing
as a tool result: search snippets reached the system prompt unscanned, and their
numbers disabled the claim verifier's contradiction check. A failed search was
silent, so the answer looked current. The planner had the same fault in another
form: a tool that failed left no mark, and the model filled the gap from memory.

The [assurance review](../plans/agentic-planner-web-assurance-2026-10-31.md) reproduced
these and found the supervisor's route ignored on REST since #599.

## Decision Drivers

* A figure the taxpayer relies on must trace to a source that can bear it.
* Text from the web is attacker-reachable, and pattern filters can be optimised around.
* Official sites go down, so failure has to be a designed state.
* Questions contain personal data, and a search provider is a cross-border processor.

## Considered Options

* **A search tool for the model.** Flexible, and the common default. Rejected: a query
  written after reading untrusted text is an exfiltration channel, and the budget and
  vocabulary controls would have to hold against the model itself.
* **Filter personal data out of the query.** Rejected: a lower-case name looks like any
  other word. Reproduced leaking.
* **Keep the verified-facts channel and scan it harder.** Rejected: it would still tell
  the verifier that a web number is deterministic.
* **Refuse to answer when a check fails.** Rejected as the default: the corpus answer is
  usually useful, so it is served with a disclosure.

## Decision Outcome

**Control flow stays with the planner.** Whether to search is decided in code, from the
taxpayer's question and the metadata of local retrieval, such as a passage's edition.
What to search for comes from the question alone. A turn searches once, so nothing from
the web can start or shape a search, and the model has no search tool. This follows
CaMeL's separation of control flow from untrusted data.

**Web text is evidence, never a verified fact.** It reaches the model only as scanned,
spotlighted passages whose headers state authority, evidence and age. The prompt
notice is written wholly by code. Nothing from the web enters `verified_facts`. A
result that reads as an instruction is dropped whole. `live_web_verified` is reserved
for a document actually read this turn.

**Only a closed vocabulary leaves the deployment.** Outbound queries contain listed
generic tax terms and nothing else. Every provider is an external processor under the
G35 cross-border gate once configured, including a self-hosted SearXNG, which passes the
query on to external engines. Hosted keys are refused in sovereign mode.

**A turn cannot change the production corpus by itself.** `auto_qdrant_web_sync` also
needs `WEB_SYNC_PRODUCTION_APPROVED` in production, because a compose overlay defaults
the flag on and a working provider would otherwise activate it unnoticed.

**Failure is an outcome.** Every check ends in one of `confirmed`, `unconfirmed`,
`stale_only`, `no_official_result`, `unavailable` or `ledger_fresh`, and the taxpayer
is told what that means, on REST, SSE and the graph, however the turn exits. A topic
that was left out or whose search failed is disclosed even when another topic was
confirmed. "Found nothing" is never worded as "there is nothing". The status is in the
audit row.

**Search has a documented contract or it does not run in production.** Providers sit
behind one interface with failover and a breaker each. The HTML fallback is
development-only. A vendor's generated answer is never requested.

**A market rate is an estimate.** The rule is the Bank of Uganda mid-rate, so a verified
snapshot outranks any quote. Sources that disagree produce no rate. A sanity bound is
never presented as a rate.

**A plan runs once, within a budget, and says what it could not do.** Steps that fail
or run out of budget are listed as not calculated. One step's failure costs only that
step. The audit row holds the plan digest, and each REST turn is attested against the
behavioural manifest from its real stage timings.

## Confirmation

* `tests/agents/test_web_evidence_boundary.py`: nothing from the web enters the prompt
  notice or the deterministic channel, attribution, egress and age labelling.
* `tests/agents/test_web_research_adversarial_eval.py` over
  `evals/web_research_scenarios/adversarial.jsonl`: each hostile or failed search ends
  in the required status.
* `tests/agents/test_live_research_planning.py`: the precision and recall sets for the
  triggers, and disclosure on REST, SSE and the graph.
* `tests/agents/test_web_search_providers.py`, `tests/agents/test_web_fx.py`,
  `tests/agents/test_plan_execution_contract.py` and
  `tests/agents/test_supervisor_route_contract.py`.
* The live acceptance probes in the
  [runbook](../runbooks/live-web-research-and-qdrant-sync.md#7-acceptance-probes), which
  have not yet been run: production acceptance is pending.

## Consequences

* The model cannot follow up on what it reads, so a multi-hop research question gets
  one round of evidence. This is the price of the first decision and may be revisited
  with a constrained, vocabulary-bound follow-up, never free text.
* With no provider configured, production reports `unavailable` whenever a live check
  is needed. That is safe and visible, but the feature is inert until a provider is
  funded and its transfer assessment approved (G326).
* Dropping the browser identity and forged consent cookie may cost the XE and Google
  sources, leaving FX on a single mirror. No BoU rate is available until a verified
  snapshot is loaded (G327).
* Time-sensitive turns skip the semantic cache and pay full cost.
* Regex injection screening will miss paraphrased attacks. That is why it is not the
  boundary: a missed attack is still a labelled passage with no route to the verifier.
* Streaming turns are not yet attested (G325).
