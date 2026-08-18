# MCP hardening — 2026-08-17

Traceability record for the MCP audit follow-through. Pair with
`App/docs/mcp-architecture.md` (protocol) and
`docs/GAPS_AND_AGENTIC_ROADMAP.md` (living register). This file is the
decision log: what changed, why, how to re-verify, and what stays open.

The session audit canvas is a working view. This markdown is the
repo-durable record for later audits.

## 1. Intent

Close the gaps the 2026-08-17 MCP audit named against the official
`2026-07-28` specification, OWASP Agentic Top 10 (ASI, Dec 2025), and
2026 tool-calling practice — without enabling Tool RAG or graph fusion
without a measurement, and without exposing URA account tools (G12).

Shipped in five phases in one change set.

## 2. Decision log

| Decision | Choice | Why |
|----------|--------|-----|
| Spec `_meta` keys | `io.modelcontextprotocol/protocolVersion` + `clientCapabilities` required | Official 2026-07-28: missing fields are `-32602` / HTTP 400. Unprefixed `protocolVersion` is not compliant. |
| Header confused-deputy | Validate `Mcp-Name` against `params.name` | Spec added headers so a gateway cannot authorize one tool while the server runs another. |
| `tools/list` cache | Honour `ttlMs` | Process-lifetime cache served a stale remote schema forever. |
| Unknown-tool error | Name only; no `available_tools` | Enumerating the registry helps prompt-injection (ASI02). |
| MRTR field names | `inputRequests` (legacy `elicitations` still parsed) | Spec name. Agent emits `tool_call.input_required`; retry is a second `call_tool` with `inputResponses`. |
| `requestState` integrity | HMAC-SHA256 when `MCP_REQUEST_STATE_SECRET` is set | Spec: treat `requestState` as attacker-controlled. Unsigned (`alg: none`) only when no secret. |
| Tool RAG default | **off**; dense inject when retriever loads; miss → rails only | 25 schemas ≈ 4.5k tokens. Enabling needs a routing measurement. Fallback-to-all undid the win. |
| Graph Tool RAG flag | `flags.is_enabled("tool_rag")` | `os.getenv` ignored percent rollout. |
| Circuit breaker | Transport exceptions only | `ok: false` from bad taxpayer input is not a sick server (ASI10). |
| Idempotency store | Redis when `REDIS_URL` is set; else in-process 512 | Critical writes must not double-act across replicas. |
| `FLAG_TOOL_RAG` | Stay default off | Same rule as HyDE / graph fusion: mechanism shipped, experiment not. |

## 3. Code surface

| Phase | Files |
|-------|-------|
| 1 Spec | `app/mcp/protocol.py` (new), `transport.py`, `servers/tax_calculator/server.py`, `client.py`, `tools/__init__.py` |
| 2 MRTR | `app/mcp/mrtr.py` (new), `transport.py`, `client.py`, `llm.py` (`tool_call.input_required`) |
| 3 Tool RAG | `tool_rag.py` (`inject_dense_model`, rails-only miss), `retriever.py`, `graphs/main_graph.py` |
| 4 Schemas | `calendar.py`, `escalate.py`, `rag_tool.py`, `ura_account.py`, `ura_actions.py` |
| 5 Ops | `client.py` (Redis idempotency, breaker) |

## 4. Flags and environment

| Name | Default | Rollback |
|------|---------|----------|
| `FLAG_TOOL_RAG` | **off** | leave off; `FLAG_TOOL_RAG_PERCENT` for a canary (leave boolean unset) |
| `TOOL_RAG_TOP_K` | 5 | rails always appended |
| `MCP_SERVER_URL_<NAMESPACE>` | unset → in-process | unset to return local |
| `MCP_SERVER_TOKEN_<NAMESPACE>` | unset | — |
| `MCP_REQUEST_STATE_SECRET` | unset (`alg: none`) | set before `requestState` influences authz |
| `MCP_IDEMPOTENCY_TTL_S` | 86400 | in-process fallback if Redis down |
| `REDIS_URL` | unset | shared replay off |

All of the above are in `.env.example`.

## 5. How to re-verify

```bash
cd App/backend
python3 -m pytest \
  tests/test_mcp_hardening.py \
  tests/test_mcp_transport.py \
  tests/test_mcp_tax_server.py \
  tests/test_mcp_policy.py \
  tests/test_mcp_tasks.py \
  ../../tests/agents/test_mcp.py \
  ../../tests/agents/test_tool_selection.py \
  ../../tests/agents/test_tools_framework.py \
  ../../tests/agents/test_graph.py -q
```

With Tool RAG on (must stay green; default-off suite does not prove the live path):

```bash
FLAG_TOOL_RAG=true python3 -m pytest \
  ../../tests/agents/test_tool_selection.py \
  ../../tests/agents/test_graph.py -q
```

Inventory probe (do not cite from memory):

```bash
python3 -c "from app.tools import ToolRegistry; print(len(ToolRegistry.all()), ToolRegistry.namespaces())"
```

Expected on 2026-08-17: **25 tools**, **11 namespaces**.

## 6. Still open (do not mark shipped)

- `FLAG_TOOL_RAG` measurement on the routing golden set before raising the percent above 0.
- OAuth 2.1 / RFC 9207 `iss` / CIMD — only if a third-party host connects. Internal bearer + SPIRE remains the DMZ plan.
- MCP `resources/` and `prompts/` — optional; rate tables as resources would be a later increment.
- MCP Apps (SEP-1865) — not needed for this product.
- G12 URA account / actions DMZ servers — scaffolds stay fail-closed.
  2026-08-18: sandbox mock + production gates in
  `prototype-production-gates-2026-08-18.md`. Still not a live URA API.
- In-process tool timeout is still a soft deadline (pure Python cannot preempt).
- Schema pin / AIBOM for third-party MCP servers — first-party registry only today.

## 7. Audit verdict (2026-08-17, after hardening)

| Practice | Code | Default | Docs agree |
|----------|------|---------|------------|
| Spec `_meta` reserved keys | Yes | on | Yes |
| `Mcp-Name` body check | Yes | on | Yes |
| `tools/list` TTL | Yes | on | Yes |
| No registry leak on unknown tool | Yes | on | Yes |
| MRTR `inputRequests` + HMAC state | Yes | secret optional | Yes |
| Tool RAG dense + rails-only miss | Yes | **off** | Yes |
| `additionalProperties: false` on all 25 | Yes | on | Yes |
| `outputSchema` on audit-named six | Yes | on | Yes |
| Breaker = transport only | Yes | on | Yes |
| Shared idempotency | Yes | Redis if configured | Yes |
| G12 URA live account | No | fail-closed | Yes (GAPS) |

Doc authority: **GAPS** is the living register. `mcp-architecture.md` is the protocol description. This file is the decision log.
