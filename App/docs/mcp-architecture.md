# MCP architecture

The agent layer never touches `app.tools` directly. Every tool call goes
through `app.mcp.MCPClient`, which routes, authorizes, validates and
guards it. That indirection is what lets a tool move out of the process
without a single caller changing.

Protocol baseline: **MCP `2026-07-28`**.

## The path of one call

```
agent / service fast path
        │
        ▼
  MCPClient.call_tool
        │
        ├─ 1. route      namespace → transport      (transport.py)
        ├─ 2. authorize  tool declarations          (policy.py)
        ├─ 3. validate   arguments vs inputSchema   (validation.py)
        ├─ 4. replay     idempotency key → cached result
        ├─ 5. guard      per-namespace circuit breaker + deadline
        ├─ 6. dispatch   InProcess | HTTP
        └─ 7. account    MCPCallResult → audit ledger
```

Every stage returns a structured `{"ok": false, "error": …}` result
rather than raising. A model that gets a tool call wrong must be able to
read why and retry; an exception gives it nothing to act on.

### 1. Routing

A tool declares which server owns it:

```python
ToolSchema(name="calculate_paye", namespace="tax_calculator", ...)
```

`build_transports()` binds each namespace to `InProcessTransport` unless
`MCP_SERVER_URL_<NAMESPACE>` is set, in which case it binds
`HttpTransport`. A remote binding **wins over** a tool that also happens
to be registered locally, so cutting a namespace over is one env var:

```bash
MCP_SERVER_URL_TAX_CALCULATOR=https://mcp-tax.internal/rpc
MCP_SERVER_TOKEN_TAX_CALCULATOR=…        # optional bearer
```

`MCPClient.health()` shows what a running process actually resolved.

### 2. Authorization

Permissions are **declared by the tool**, not inferred from its name:

```python
ToolSchema(
    name="ura_action_proposal",
    risk="critical",
    required_scopes=("ura_account_access", "ura_actions"),
    allowed_roles=("verified_taxpayer", "ura_staff", "ura_admin"),
    requires_confirmation=True,
    read_only=False, destructive=True,
)
```

The previous rule keyed off a `ura_` name prefix. That is both too broad
(every future `ura_*` tool inherits the grant) and too narrow (a
URA-touching tool named anything else is authorized as needing nothing).
A tool above `low` risk that declares nothing falls back to the risk
tier's defaults and is denied unless they are met.

`scope_exempt_roles` covers the case where a role carries its own
mandate — URA staff opening a ticket are not acting under an end-user
consent grant.

**Discovery and dispatch run the same function.** `available_for()`
calls `authorize_tool_call` with confirmation and idempotency treated as
satisfied, because those are properties of a specific call rather than
of whether the tool should be offered at all. A test asserts the two
agree for every registered tool, so the list the model is shown cannot
drift from the list it is allowed to use.

### 3. Argument validation

Arguments are checked against the tool's JSON Schema 2020-12
`inputSchema` before dispatch, so a bad call becomes
`monthly_gross: 'a lot' is not of type 'number'` instead of a `TypeError`
from inside the tool. Calculator schemas set
`additionalProperties: false`, so a hallucinated argument is caught
rather than silently ignored.

Results are checked against `outputSchema` too, but mismatches are
**logged, not raised** — a server adding a field should not break a
working answer, though the drift must be visible.

### 4. Idempotency

A call carrying an `idempotency_key` stores its result keyed by
`tenant:tool:key`. A retry returns the stored result with
`replayed=True` and never reaches the transport. Keys are tenant-scoped
so they cannot collide across tenants. Critical tools require a key
(`requires_confirmation` implies it), which is what stops a retried
filing submission from acting twice.

### 5. Circuit breaking

Each namespace gets its own `CircuitBreaker` (shared with the retriever
and LLM paths). Three consecutive failures open it, and calls then fail
fast with `retryable: true` without touching the transport. One sick
namespace cannot take out the others.

`timeout_s` is a hard deadline for HTTP transports. In-process calls are
pure Python and cannot be preempted, so it is recorded as a soft
deadline — results carry `deadline_exceeded` for observability rather
than pretending to have cancelled something.

### 6. Audit

`MCPCallResult.to_audit_dict()` **hashes** arguments and results rather
than storing them. A call can carry a TIN or a salary; the ledger's job
is to prove what ran, not to become a second copy of taxpayer data.

## Tool descriptors

`Tool.to_mcp_tool()` emits the spec shape:

```jsonc
{
  "name": "calculate_paye",
  "description": "…",
  "inputSchema":  { "type": "object", … },   // JSON Schema 2020-12
  "outputSchema": { "type": "object", … },
  "annotations": {
    "title": "Calculate paye",
    "readOnlyHint": true, "destructiveHint": false,
    "idempotentHint": true, "openWorldHint": false
  },
  "_meta": {
    "ug.go.ura.chatbot/risk": "low",
    "ug.go.ura.chatbot/namespace": "tax_calculator",
    "ug.go.ura.chatbot/requiredScopes": [],
    "ug.go.ura.chatbot/allowedRoles": []
  }
}
```

Risk and scopes travel in `_meta` under a reverse-DNS prefix — where the
spec puts implementation-defined metadata — so a gateway can authorize
on them without understanding our tool names.

`to_openai_spec()` is unchanged and still feeds Qwen's chat template.

## Namespaces

| Namespace | Tools | Risk | Deployment |
|---|---|---|---|
| `tax_calculator` | 8 calculators | low | 🟢 standalone server available |
| `education` | `explain_tax_concept` | low | in-process |
| `rates` | `lookup_rate`, `list_available_rates`, `compare_tax_years` | low | in-process |
| `rag` | `search_ura_knowledge_base` | low | in-process |
| `calendar` | `get_current_date`, `get_next_deadlines` | low | in-process |
| `empathy` | `assess_emotional_tone` | low | in-process |
| `tax_graph` | `graph_resolve_rate`, `graph_rate_history`, `graph_effective_on` | low | in-process (embedded) |
| `core` | `escalate_to_human` | medium | in-process |
| `tasks` | `task_create`, `task_get`, `task_cancel` | medium | in-process, Postgres/SQLite-backed |
| `ura_account` | `ura_account_profile` | high | DMZ |
| `ura_actions` | `ura_action_proposal` | critical | DMZ |

## The `mcp_tax_calculator` server

```bash
python -m app.mcp.servers.tax_calculator                  # stdio
python -m app.mcp.servers.tax_calculator --http --port 8931
curl localhost:8931/health
```

It imports the same calculators the in-process path uses — it is
transport, not arithmetic — so an answer cannot differ between the two.
A test asserts that parity directly.

`handle_request(body, headers)` is a pure function, which is why the
protocol is tested without a socket. No MCP SDK dependency: JSON-RPC 2.0
over HTTP or newline-delimited stdio is small enough to implement
exactly, and keeps the server deployable in the same slim image.

The server re-runs `authorize_tool_call` itself. Client-side
authorization is not sufficient for a server reachable on its own
address.

## What `2026-07-28` changed here

- **Stateless core.** No `initialize`/`initialized`, no `Mcp-Session-Id`.
  Protocol version, client info and caller identity ride in `params._meta`
  on every request, so any request can land on any replica. The server
  rejects `initialize` with a message saying so rather than a bare
  "method not found".
- **Header routing.** `Mcp-Method` / `Mcp-Name` duplicate method and
  tool name into headers. A header that disagrees with the body is
  rejected — otherwise a gateway could authorize one method while the
  server runs another.
- **Cacheable lists.** `tools/list` returns `ttlMs` (1h) and
  `cacheScope: "server"`; these calculators are public and identical for
  every caller.
- **Full JSON Schema 2020-12** on input and output, and
  `structuredContent` on results alongside a human-readable `content`
  text block.
- **Multi Round-Trip Requests** replace server-initiated requests.
  `HttpTransport` surfaces `resultType: "input_required"` verbatim with
  its `elicitations` and `requestState`, so a caller can elicit and
  replay instead of treating it as a failure.

## Long-running work (`tasks`)

The stateless core has a consequence: work that outlives one request
cannot hold a connection, and cannot be remembered in a worker's memory
either, because the poll that asks about it may reach a different
replica. So a long call returns a **task id** and the caller polls it.

```
task_create(kind, args, idempotency_key) → {task_id, status: "pending"}
task_get(task_id)                        → {status, progress, result?, error?}
task_cancel(task_id)                     → {…, cancelled: bool}
```

State lives in `mcp_tasks`, through the same backend-agnostic helpers
the audit ledger uses, so it is one table whichever backend is live.

Three properties the table enforces rather than hopes for:

- **Idempotency on creation.** `UNIQUE(tenant_id, idempotency_key)`, and
  `create_task` both checks first *and* recovers from the constraint
  violation — two replicas can pass the check at the same instant, so
  the index is what actually stops a retried filing acting twice.
- **Unkeyed tasks do not collide.** The key defaults to `NULL`, not
  `''`: both SQLite and Postgres treat NULLs as distinct in a UNIQUE
  index, whereas `''` would allow exactly one keyless task per tenant.
- **Terminal states are final.** `succeeded`, `failed` and `cancelled`
  cannot be moved out of, so a late worker cannot overwrite a
  cancellation the taxpayer has already been told about.

Reads are scoped by tenant in the `WHERE` clause rather than checked
afterwards, and a task belonging to another tenant returns the same
`no such task` as one that does not exist — distinguishing them would
confirm the id exists elsewhere.

`kind` is a closed set (`document_ocr`, `filing_submission`,
`graph_rebuild`, `bundle_export`). An open one would let a model invent
work nothing knows how to run, leaving the row at `pending` for ever
with no worker and no error.

## Extending

- **New tool:** subclass `Tool`, declare `namespace`, `risk`,
  `required_scopes`, `allowed_roles` and the annotation hints, register
  it, and add it to the auto-import list in `app/tools/__init__.py`.
- **New server:** add `app/mcp/servers/<name>/` following
  `tax_calculator`, set `namespace="<name>"` on its tools, then bind
  `MCP_SERVER_URL_<NAME>`. See `app/mcp/servers/README.md`.
