# The agentic loop: budgets, thrash suppression, compaction

A bounded *iteration* count is not a bounded *agent*. `max_iterations`
caps how many times the model generates; it says nothing about how many
tools each generation dispatches, whether it dispatches the same one
repeatedly, or how much of the context window the results consume.

`app/agents/loop_control.py` supplies the ceilings that close that gap.
It is used by both agentic execution paths:

- `llm.generate_with_tools` — the live ReAct loop reached when
  `FLAG_AGENTIC_MODE` routes a query to `TOOLS` or a specialist.
- `agents/graphs/main_graph.node_act` — the graph orchestrator.

One implementation, so the two paths cannot drift apart.

## What it stops

### Tool storms and retrieval thrash

Each generation round can emit an unbounded fan-out of tool calls, so
`max_iterations=3` still permits dozens of dispatches per turn.
`ToolCallBudget` applies three ceilings:

| Ceiling | Env var | Default | Stops |
| --- | --- | --- | --- |
| Total dispatches per turn | `TOOL_MAX_CALLS_PER_TURN` | 8 | Runaway cost across rounds |
| Dispatches per round | `TOOL_MAX_CALLS_PER_ITERATION` | 4 | One generation fanning out |
| Dispatches of one tool | `TOOL_MAX_CALLS_PER_TOOL` | 3 | A single retriever being hammered |

A refused call is not silent. The model receives
`{"ok": false, "error": "...", "budget_exhausted": true}` naming which
ceiling it hit and what to do instead ("answer from what you have"), so
it can finish the turn rather than retry blindly.

### Duplicate work

The MCP client's replay cache is keyed on an explicit idempotency key,
so a model re-asking `lookup_rate({"tax_type": "vat"})` in every round
executes it every round. `ToolCallBudget` memoizes on the
`(name, arguments)` fingerprint instead — arguments are canonicalised
with `sort_keys`, so a reordered payload is recognised as the same call.

A repeat is served from the memo with `repeated_call: true` and a note
telling the model it is going in circles. It consumes **no** turn
budget: nothing executed, so charging for it would deny real work later.

### Context bloat

The loop previously fed back `json.dumps(result)[:2000]`. That slice
cuts mid-token — the model receives *invalid JSON* — and discards by
byte position rather than by salience.

`compact_observation` shrinks structurally and always returns something
parseable, in three passes:

1. Truncate long leaf strings and long lists, keeping the most generous
   per-value limit that still fits the budget.
2. Drop whole keys in reverse priority order (`ok`, `error`, `amount`,
   `explanation`, `legal_basis`… are kept longest), recording what went
   into `_omitted` so the model can see what it is not being shown.
3. Fall back to a minimal `{"ok": …, "_truncated": true}` stub.

A turn-wide ledger (`DEFAULT_TURN_OBSERVATION_BUDGET_CHARS`, 12 000)
shrinks later observations as the window fills, floored at
`MIN_OBSERVATION_CHARS` (240) so a late tool result is short rather than
absent.

## Tool RAG on the live path

`ToolRAGSelector` had a single caller — a graph node nothing invokes —
so every agentic turn pasted every registered schema (~4.5 k tokens)
into the prompt regardless of the query. `llm._select_tools_for_query`
now consults it whenever `FLAG_TOOL_RAG` is on, exposing the top
`TOOL_RAG_TOP_K` (default 5) tools plus the mandatory rails
(`search_ura_knowledge_base`, `escalate_to_human`).

The fallbacks are asymmetric on purpose. Flag off or a selector
exception still expose the **full** eligible set (an agent with no
tools cannot act). A scored miss no longer pastes every schema — it
keeps the mandatory rails only. The retriever injects its dense
embedder into Tool RAG when one is already loaded; tests and hosts
without a model stay on token overlap.

Security trimming is unaffected — selection runs over the post-authz
whitelist from `MCPClient.available_for`, never over the raw registry.

## Graph-path specifics

`node_act` additionally refuses to invent arguments. Binding is driven
by each tool's own JSON Schema (`bind_arguments`), and only two bindings
are honest at that layer: a tool with no required parameters is callable
as-is, and a required free-text parameter (`query`, `question`, `text`,
`message`) takes the user's query. Anything else — `amount`,
`tax_type` — is skipped with a reason recorded in `state.skipped_tools`.

This matters because `node_synthesize` used to stitch every observation
into the reply. `calculate_vat({})` fails schema validation, and the
user would have been handed *"amount: required property is missing"* as
their answer. Synthesis now considers only observations with
`ok != false`, and abstains rather than emitting a raw dict.

`node_act` always hands to `node_observe`. Failed or unfillable tools
are not evidence: observe hands off once to `retrieve`
(`max_handoffs=1`) or synthesises an empty plan.

`node_reflect` computes faithfulness and now acts on it: a RAG reply
below `REFLECT_FAITHFULNESS_FLOOR` (0.50, the same scale and threshold
as `CORRECTIVE_RAG_THRESHOLD_NORM`) **or** a reasoning miss (reply
shares fewer than 20% of the question's content terms) goes back
through retrieval once. Query expand is best-effort — identity expand
still retries. `max_reflections` bounds that, so the loop cannot
become the thrash it exists to correct. Tool-only answers are
grounded in their own computation, not in passages, so they never
re-retrieve.

## Observability

`generate_with_tools` returns a `tool_budget` dict, also attached to the
`iteration.final` event and emitted per skip as `tool_call.skipped`:

```json
{"dispatched": 3, "repeats": 2, "denied": 1,
 "distinct_tools": 2, "observation_chars": 4180, "exhausted": false}
```

`dispatched` relative to answered turns is the metric that makes a
thrashing agent visible: a rising ratio means the loop is working
harder for the same result. `repeats > 0` means the model is asking the
same question twice; sustained `denied > 0` means a ceiling is too tight
for real traffic, or the router is planning tools it cannot use.

The graph exposes the same dict under `budget` in
`AgentGraphState.to_summary()`.


## Per-specialist prompts

The supervisor has routed to `tax_specialist`, `customs_specialist` and
`tool_specialist` since Phase 15. Until Phase 19 that changed which
tools were offered and the `agent_role` string in the response — and
nothing about the instructions the model received. A "customs
specialist" that has never been told it is one is a label.

`agents/prompts.py` maps a role to an extra system fragment, appended
**after** the shared prompt so every specialist keeps the base
grounding, abstention and citation rules. That ordering is deliberate: a
specialist must not be able to talk its way out of the safety
instructions by having a longer, more specific prompt. A test asserts
`SYSTEM_PROMPT` survives for every role.

| Role | Adds |
| --- | --- |
| `tax_specialist` | Never state a rate from memory; name the fiscal year; pass on verification warnings |
| `customs_specialist` | Duty is on CIF not invoice; VAT sits on the duty-inclusive value; no single customs rate |
| `tool_specialist` | Use the tools rather than your own arithmetic; ask for one missing input rather than assuming |

Non-specialist roles — greeting, clarification, escalation triage — get
the base prompt unchanged. A greeting does not need a domain persona,
and appending one spends tokens to make the reply worse.

Fragments are kept under ~1 kB and a test enforces it: they cost tokens
on every turn of a tool-calling loop, and a long persona preamble crowds
out the passages and tool results the answer actually depends on.


## Routing eval

Unit tests assert individual cases. They cannot tell you the supervisor
gets 70% of natural calculation phrasings wrong, because a case nobody
wrote a test for is invisible — which is how seven of ten ordinary ways
of asking for a tax figure ended up on the retrieval path, answered from
the model's memory. That was found by hand-probing the router, which
nobody will remember to redo.

`agents/eval_routing.py` scores the supervisor against a labelled set
and reports a **rate plus the cases it got wrong by name**:

```bash
python -m app.agents.eval_routing        # non-zero exit on any miss
```

```
ura_routing_accuracy 1.0000
ura_routing_misses 0
ura_routing_accuracy_by_route{route="tools"} 1.0000
```

A case counts as correct only if the route matches **and** the expected
tool is offered — routing to the right path while omitting the tool that
answers the question is a miss the route alone would hide.

The suite checks the eval can actually *fail* (an eval that cannot
report a miss is decoration), that every route has at least one case (a
route with none is a blind spot by construction), that no golden case
names a tool that does not exist, and that accuracy stays at or above
0.95. The floor rather than an equality is deliberate: adding a hard
case that currently fails should be possible without breaking the
build.

**Add a case here whenever a route is reported wrong.** That is what
turns a one-off bug report into a permanent check.
