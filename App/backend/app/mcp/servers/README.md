# MCP Servers

Out-of-process **Model Context Protocol** servers, speaking spec
`2026-07-28`. Each directory is one server; the namespace it owns is the
`namespace` field on the `ToolSchema` of the tools it serves.

## Core namespace (this host)

| Directory | MCP Server Name | Status | Transport |
|---|---|:---:|---|
| `tax_calculator/` | `mcp_tax_calculator` | 🟢 live | stdio + streamable HTTP |
| `rates/`          | `mcp_rates`          | ⚪ in-process | — |
| `rag/`            | `mcp_rag`            | ⚪ in-process | — |
| `calendar/`       | `mcp_calendar`       | ⚪ in-process | — |
| `document_parser/` | `mcp_document_parser` | ⚪ planned | — |
| `forms/`          | `mcp_forms`          | ⚪ planned | — |
| `notify/`         | `mcp_notify`         | ⚪ planned | — |
| `memory/`         | `mcp_memory`         | ⚪ planned | — |

"in-process" means the namespace exists and its tools are routed, but
`InProcessTransport` serves them — there is no separate deployment yet.

## URA DMZ namespace (separate deploy target)

See `ura_account/README.md` and `ura_actions/README.md`. These require
SPIRE workload identity, mTLS to URA APIs, and cert-manager rotation.
They cannot run in the core namespace.

## Binding a namespace to a deployed server

The client reads the binding from the environment. Nothing else changes:

```bash
MCP_SERVER_URL_TAX_CALCULATOR=https://mcp-tax.internal/rpc
MCP_SERVER_TOKEN_TAX_CALCULATOR=...      # optional bearer
```

`build_transports()` maps `MCP_SERVER_URL_<NAMESPACE>` onto that
namespace; every other namespace stays in-process. A remote binding
wins over a tool that is also still registered locally, so the cutover
is the env var alone.

Check what a running process resolved with `MCPClient.health()`:

```json
{"protocol_version": "2026-07-28",
 "namespaces": {"tax_calculator": {"transport": "http:tax_calculator",
                                   "circuit": "closed"}}}
```

## What the 2026-07-28 spec changed for us

- **Stateless core.** No `initialize`/`initialized`, no `Mcp-Session-Id`.
  Protocol version, client info and caller identity ride in `params._meta`
  on every request, so any request can hit any replica.
- **Header-based routing.** `Mcp-Method` and `Mcp-Name` duplicate the
  method and tool name into headers so gateways route and authorize
  without parsing the body. Servers must reject a header that disagrees
  with the body.
- **Cacheable lists.** `tools/list` returns `ttlMs` and `cacheScope`.
- **Full JSON Schema 2020-12** for `inputSchema` / `outputSchema`, and
  `structuredContent` on results.
- **Multi Round-Trip Requests** replace server-initiated requests: a
  server needing more input returns `resultType: "input_required"` with
  the elicitations and a `requestState` the client replays.

## Shared conventions

Every server MUST:

1. Serve `tools/list`, `tools/call` and `server/info`, and reject
   `initialize` with a message pointing at the stateless model.
2. Read `tenant_id` / `user_id` / `user_role` from `params._meta`.
3. Return `structuredContent` plus a human-readable `content` text block,
   with `isError: true` for tool-level failures — never a JSON-RPC error
   for something the model could correct.
4. Re-run `authorize_tool_call` itself. Client-side authorization is not
   sufficient for a server reachable on its own address.
5. Emit OpenTelemetry spans using stable GenAI semconv attribute names.
6. Write a structured audit event via the shared audit ledger.
7. Ship its own tests.

## Running one

```bash
python -m app.mcp.servers.tax_calculator             # stdio
python -m app.mcp.servers.tax_calculator --http --port 8931
curl localhost:8931/health
```

## Migrating a tool out of process

Set `namespace="<server>"` on its `ToolSchema`, add it to that server's
directory, deploy, then set `MCP_SERVER_URL_<NAMESPACE>`. The arithmetic
stays in `app/tools/` and the server imports it, so the in-process and
remote paths cannot disagree on an answer.
