# MCP Servers

Out-of-process **Model Context Protocol** servers, speaking spec
`2026-07-28`. Each directory is one server; the namespace it owns is the
`namespace` field on the `ToolSchema` of the tools it serves.

Every server is a `StatelessMCPServer` from [`wire.py`](wire.py): the
envelope, the error codes, the HTTP routes and the authentication of the
caller are written once there. A server module is a name, a namespace
and — where it has them — its resources and prompts.

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

## System connectors (simulators)

| Directory | MCP Server Name | Namespace | Default port |
|---|---|---|:---:|
| `efris/`   | `mcp_efris`   | `efris`              | 8933 |
| `dts/`     | `mcp_dts`     | `digital_tax_stamps` | 8934 |
| `payment/` | `mcp_payment` | `payment_system`     | 8935 |
| `tin/`     | `mcp_tin`     | `tin_registration`   | 8936 |
| `ursb/`    | `mcp_ursb`    | `ursb`               | 8937 |
| `bwims/`   | `mcp_bwims`   | `bwims`              | 8938 |

Each runs alone (`python -m app.mcp.servers.efris --http`), or all six run
in one process, an endpoint each, from [`gateway.py`](gateway.py):

```bash
python -m app.mcp.servers.gateway --port 8930      # POST /efris, /payment_system, …
```

They are simulators over fixture data: under `APP_ENV=production` they serve
no tools.

## URA DMZ namespace (separate deploy target)

See `ura_account/README.md` and `ura_actions/README.md`. These require
SPIRE workload identity, mTLS to URA APIs, and cert-manager rotation.
They cannot run in the core namespace.

## Binding a namespace to a deployed server

The client reads the binding from the environment. Nothing else changes:

```bash
MCP_SERVER_URL_TAX_CALCULATOR=https://mcp-tax.internal/rpc
MCP_SERVER_TOKEN_TAX_CALCULATOR=...      # shared token; the server reads the same variable
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

## What the 2026-07-28 spec requires, and where it is

- **Stateless core.** No `initialize`/`initialized`, no `Mcp-Session-Id`.
  Protocol version, client info and caller identity ride in `params._meta`
  on every request, so any request can hit any replica. `initialize` is
  refused with a message naming the supported versions.
- **`server/discover`.** Every server implements it: supported versions,
  capabilities, and identity under `_meta["io.modelcontextprotocol/serverInfo"]`.
  It is the reachability probe; `ping` left the protocol in this revision
  and is answered only for a caller one release behind.
- **Header-based routing.** `MCP-Protocol-Version`, `Mcp-Method` and
  `Mcp-Name` are required on every POST. A missing header, or one that
  disagrees with the body, is `400` with `HeaderMismatch` (`-32020`).
  A Base64-sentinel `Mcp-Name` is decoded before it is compared.
- **Required `_meta`.** `io.modelcontextprotocol/protocolVersion` and
  `io.modelcontextprotocol/clientCapabilities` on every request. Missing
  fields are `-32602`; a version the server does not serve is `-32022`
  with the supported list.
- **`resultType` on every result**, and `ttlMs` + `cacheScope`
  (`public` | `private`) on `tools/list`, `resources/list`,
  `resources/read`, `prompts/list` and `server/discover`.
- **Errors that mean what they say.** An unknown *method* is `-32601`
  (`404` over HTTP); an unknown tool, resource or prompt is `-32602`.
- **One request per POST**, answered as one JSON object. GET and DELETE on
  the endpoint are `405`. `Origin`, when present, must be allow-listed.
- **Full JSON Schema 2020-12** for `inputSchema` / `outputSchema`, and
  `structuredContent` on results.
- **Multi Round-Trip Requests** replace server-initiated requests: a
  server needing more input returns `resultType: "input_required"` with
  `inputRequests` (a map) and a `requestState` the client replays.

## Authentication

A server reachable on its own address authenticates every HTTP request
(`../auth.py`). With nothing configured it is **locked**: it answers
`/health` and refuses everything else.

| Mode | Configure on the server |
|---|---|
| Shared token | `MCP_SERVER_TOKEN_<NAMESPACE>` — at least 32 characters, the same variable the client reads |
| OAuth 2.1 resource server | `MCP_SERVER_OAUTH_ISSUER`, `MCP_SERVER_OAUTH_JWKS_URL`, `MCP_SERVER_RESOURCE` (the audience), optional `MCP_SERVER_OAUTH_SCOPES` |
| Anonymous | `MCP_SERVER_ALLOW_ANONYMOUS=true` — development only, refused in production |

Under OAuth the server validates signature, issuer, expiry and audience,
serves RFC 9728 metadata at `/.well-known/oauth-protected-resource`, and
challenges with `WWW-Authenticate: Bearer resource_metadata="…"`.

The `ug.go.ura.chatbot/*` fields in `_meta` are the chat backend's
statement about who the call is for. They are honoured only on an
authenticated request; an anonymous caller is always `public`, whatever
the body says. stdio has no HTTP authentication: the launching process is
the trust boundary.

## Shared conventions

`StatelessMCPServer` does all of this; a new server gets it by using the
class rather than by re-implementing it:

1. Serve `server/discover`, `tools/list` and `tools/call`, and reject
   `initialize` with a message pointing at the stateless model.
2. Read `tenant_id` / `user_id` / `user_role` from `params._meta` — and
   only believe them from an authenticated caller.
3. Return `structuredContent` plus a human-readable `content` text block,
   with `isError: true` for tool-level failures — never a JSON-RPC error
   for something the model could correct.
4. Re-run `authorize_tool_call` itself. Client-side authorization is not
   sufficient for a server reachable on its own address.
5. Read only its own resources: a connector must not serve another
   system's data because the caller knew its URI.
6. Ship its own tests (`tests/test_mcp_wire_conformance.py` covers the
   shared rules against every server).

Not done yet: the servers do not emit their own OpenTelemetry spans or
audit-ledger events. Every call is traced and audited once, on the
client side (`trace_tool_call`, `MCPCallResult.to_audit_dict`), and the
HTTP layer propagates `traceparent`; a server-side span and ledger entry
are still to be added.

## Running one

```bash
python -m app.mcp.servers.tax_calculator             # stdio
MCP_SERVER_TOKEN_TAX_CALCULATOR=$(openssl rand -hex 32) \
  python -m app.mcp.servers.tax_calculator --http --port 8931
curl localhost:8931/health                           # {"ok": true, …, "auth": "token"}
```

## Migrating a tool out of process

Set `namespace="<server>"` on its `ToolSchema`, add it to that server's
directory, deploy, then set `MCP_SERVER_URL_<NAMESPACE>`. The arithmetic
stays in `app/tools/` and the server imports it, so the in-process and
remote paths cannot disagree on an answer.
