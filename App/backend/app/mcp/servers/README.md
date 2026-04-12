# MCP Servers

Landing zone for **out-of-process** Model Context Protocol servers
as the Phase 15 full migration progresses.  Phase 15 Lite exposes
tools via the in-process `app.tools` registry; each directory here
will become a FastMCP 0.4+ server when that tool moves out.

## Core namespace (this host)

| Directory | MCP Server Name | Status | Priority |
|---|---|:---:|:---:|
| `tax_calculator/` | `mcp_tax_calculator` | ⚪ scaffolded | P1 |
| `rag/`            | `mcp_rag`            | ⚪ scaffolded | P1 |
| `calendar/`       | `mcp_calendar`       | ⚪ scaffolded | P1 |
| `rates/`          | `mcp_rates`          | ⚪ scaffolded | P2 |
| `document_parser/` | `mcp_document_parser` | ⚪ scaffolded | P2 |
| `forms/`          | `mcp_forms`          | ⚪ scaffolded | P3 |
| `notify/`         | `mcp_notify`         | ⚪ scaffolded | P3 |
| `memory/`         | `mcp_memory`         | ⚪ scaffolded | P2 |

## URA DMZ namespace (Phase 17 — separate deploy target)

See `ura_account/README.md` and `ura_actions/README.md` — these
require SPIRE workload identity, mTLS to URA APIs, and cert-manager
rotation.  They cannot run in the core namespace.

## Shared conventions

Every server MUST:

1. Declare its MCP schema via FastMCP `@server.tool` decorators.
2. Accept a `tenant_id` on every call (passed in `ctx.meta`).
3. Return JSON-serialisable dicts with an `ok: bool` key.
4. Emit OpenTelemetry spans using the stable GenAI semconv 2025
   attribute names (`gen_ai.system`, `gen_ai.operation.name`, etc.).
5. Write a structured audit event via the shared audit ledger.
6. Respect `FLAG_AUTH_REQUIRED` if the tool touches user data.
7. Ship its own `tests/` directory with > 80% coverage.

## How a tool migrates from in-process to MCP

```
# Before (Phase 15 Lite — today):
backend/app/tools/calculators.py   # subclass Tool, register in package __init__

# After (Phase 15 full):
backend/app/mcp/servers/tax_calculator/
├── __init__.py
├── server.py         # FastMCP decorators; same math as calculators.py
├── schemas.py        # Pydantic models for requests / responses
├── transport.py      # stdio + HTTP/SSE
└── tests/
```

The `backend/app/mcp/client.MCPClient` abstraction means the agent
layer doesn't care which backend you use — switch one tool at a
time without touching any caller.
