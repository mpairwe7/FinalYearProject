# ADR-0015: MCP Zero-Trust Context Binding, IETF Idempotency Conflict Detection & ICLR 2026 ToolAthlon Long-Horizon Multi-Server Orchestration

- **Date:** 2026-10-24
- **Owner:** URA Capstone Architecture, QA Audit & Security Engineering Team
- **Builds upon:** [ADR-0003](0003-mcp-tool-integrity-and-2026-statutory-corpus.md), [ADR-0004](0004-mcp-security-threat-model-and-ai-governance-2026.md), and [ADR-0005](0005-mcp-mrtr-transport-hardening-and-csa-aicm-maestro.md)
- **Technical Story:** Gap G250

## Status

Accepted

## Context and Problem Statement

A QA and security audit of the Model Context Protocol (`MCP 2026-07-28`) setup and long-horizon tool orchestration against October 2026 standards (**NSA May 2026 `CSI_MCP_SECURITY.PDF`**, **OWASP MCP Top 10 2026**, **IETF `Idempotency-Key` draft**, and **ICLR 2026 ToolAthlon — `hkust-nlp/Toolathlon`, "The Tool Decathlon: Benchmarking Language Agents for Diverse, Realistic, and Long-Horizon Task Execution"**) identified eight structural gaps across three phases:

1. **Unbound `requestState` Replay & Unsigned State Bypass (`mrtr.py`, `client.py`):**
   `wrap_request_state` signed only the inner `payload` without binding `tool_name`, `namespace`, `tenant_id`, `user_id`, or a TTL (`exp`), allowing a signed `requestState` from one tool or user to be replayed against another tool or user. Furthermore, when `MCP_REQUEST_STATE_SECRET` was unset in development, `MCPClient._call_tool` skipped `unwrap_request_state` whenever the caller omitted `"alg"`/`"sig"` keys.
2. **Cross-User Idempotency Cache Collision & Argument Mutation Replay (`client.py`, `plugins/server.py`):**
   `MCPClient._replay_key` keyed idempotency entries by `f"{tenant_id}:{name}:{idempotency_key}"` without `user_id` or an argument digest (`arguments_sha256`). Within the shared `"default"` tenant, two users supplying the same `idempotency_key` collided, and reusing an `idempotency_key` with different tool arguments silently returned the stale result from the earlier arguments.
3. **Missing `_meta` Confirmation & Consent Propagation to `StatelessMCPServer` (`protocol.py`, `wire.py`):**
   `request_meta()` did not include `ug.go.ura.chatbot/confirmed`, `ug.go.ura.chatbot/idempotencyKey`, or `ug.go.ura.chatbot/grantedPurposes`, and `StatelessMCPServer.handle_tools_call` called `authorize_tool_call` without `confirmed`, `idempotency_key`, or `granted_purposes`, causing confirmed writes and consent-scoped reads to fail over remote HTTP transports.
4. **ToolAthlon LLM Type-Coercion Friction & Missing Self-Correction Hints (`validation.py`):**
   As highlighted by the **ICLR 2026 ToolAthlon** benchmark across 604 MCP tools, LLMs frequently emit formatted numeric strings (`"UGX 4,500,000"`, `"1,800,000"`), boolean strings (`"true"`/`"false"`), or differently-cased enum literals (`"Resident"` vs `"resident"`). Strict JSON Schema validation rejected these unambiguous values without coercion and returned raw schema dumps without actionable `repair_hints`.
5. **Markdown-Image Exfiltration, Extended Secret Leaks & Resource URI Traversal (`validation.py`, `client.py`, `wire.py`):**
   Descriptor and output screens lacked checks for Markdown image exfiltration links (`![...](https://.../?tin=...)`), PEM private keys, database connection strings with embedded passwords, and `ghp_`/`hf_` tokens, and `read_resource` lacked URI scheme allowlisting and path-traversal (`..`, `%2e%2e`) validation.
6. **Single-Namespace Crowding in `ToolRAGSelector` & Missing Cross-Server Chain Execution (`tool_rag.py`, `client.py`):**
   On multi-application ToolAthlon queries (e.g., verify business at URSB $\rightarrow$ convert USD salary $\rightarrow$ calculate PAYE $\rightarrow$ generate payment PRN), `ToolRAGSelector.select` filled top-$k$ slots with `tax_calculator` tools, crowding out `ursb` and `payment_system`, and `MCPClient` lacked a multi-step cross-server chain executor with automatic output-to-input slot binding and fail-closed confirmation halting.

## Decision Outcome

We implemented a 3-phase hardening and optimization architecture across `App/backend/app/mcp/` and `plugins/server.py`:

1. **Phase 1 — Zero-Trust `requestState` Binding, User-Scoped Idempotency & `_meta` Parity (`mrtr.py`, `protocol.py`, `client.py`, `wire.py`, `plugins/server.py`):**
   - `wrap_request_state` and `unwrap_request_state` sign and verify optional `_binding` metadata (`tool_name`, `namespace`, `tenant_id`, `user_id`, `iat`, `exp`) inside the canonical HMAC-SHA256 digest, and `MCPClient._call_tool` always validates non-`None` `request_state`.
   - `MCPClient._replay_key` scopes idempotency entries by `f"{tenant_id}:{user_id or 'anon'}:{name}:{idempotency_key}"` and stores `_mcp_args_sha256` (also enforced in `plugins/server.py`), returning `error="idempotency_key_conflict"` when a key is reused with different arguments.
   - `request_meta()` propagates `ug.go.ura.chatbot/{confirmed,idempotencyKey,grantedPurposes}`, which `StatelessMCPServer.handle_tools_call` honours only when `trusted_identity=True`.
2. **Phase 2 — ToolAthlon Schema-Guided Coercion, Repair Hints & Output/Resource Security Guards (`validation.py`, `client.py`, `wire.py`):**
   - `coerce_arguments_to_schema` losslessly coerces unambiguous numeric strings, boolean strings, and case-insensitive enum strings prior to `validate_arguments`, while `build_validation_repair_hints` emits `missing_required`, `unexpected_fields`, `expected_types`, and `allowed_enums` when validation fails.
   - Extended `_TOOL_POISONING_PATTERNS` and `_CREDENTIAL_LEAK_PATTERNS` with `markdown_exfiltration_link`, `private_key_leak`, `db_connection_secret_leak`, and `vcs_or_hf_token_leak`, and added `validate_resource_uri` (`ALLOWED_RESOURCE_SCHEMES` + path-traversal blocking) and output screening on `MCPClient.read_resource`.
3. **Phase 3 — Namespace-Diversified `ToolRAGSelector` & Long-Horizon `MCPClient.execute_tool_chain` (`tool_rag.py`, `client.py`):**
   - `ToolRAGSelector` indexes `schema.namespace` and parameter property names, adds 5-locale (`en`, `lg`, `sw`, `nyn`, `ach`) connector synonyms (`risiti`, `stakabadhi`, `sitampu`, `stempu`, `kampuni`, `ebitongole`, `boondi`), and applies 2-pass namespace diversity (`diversify_namespaces=True`) so every strongly matched MCP namespace is represented in top-$k$.
   - `MCPClient.execute_tool_chain` executes multi-step cross-server tool chains with automatic output-to-input slot binding (`_CHAIN_SLOT_ALIASES` + explicit `bind_from`), halting fail-closed at confirmation (`status="awaiting_confirmation"`) or elicitation (`status="input_required"`) boundaries and emitting SHA-256 `audit_trail` records for every step.

## Consequences

- **Positive:**
  - Eliminates cross-user/cross-tool `requestState` and `idempotency_key` replay vulnerabilities (`OWASP MCP01`, `MCP02`, `ASI02`, `ASI03`).
  - Eliminates LLM numeric/boolean/enum formatting failures on single-turn and multi-turn tool calls while keeping strict JSON Schema enforcement for ambiguous inputs.
  - Multi-application ToolAthlon workflows across `tax_calculator`, `ursb`, `payment_system`, `efris`, `digital_tax_stamps`, `tin_registration`, and `bwims` retrieve balanced tool sets and chain outputs deterministically.
- **Negative / Trade-offs:**
  - Callers that intentionally reuse an `idempotency_key` with mutated parameters now receive `ok=False, error="idempotency_key_conflict"` and must mint a new `idempotency_key` for new parameters (matching the IETF `Idempotency-Key` specification).

## Confirmation

Validated by:
- `PYTHONPATH=App/backend python3 -m pytest tests/agents/test_mcp_toolathlon_2026_october.py App/backend/tests/test_mcp*.py tests/agents/test_mcp*.py -q`
- `PYTHONPATH=App/backend python3 -m pytest tests/test_monorepo_architecture_governance.py -q`
- `python3 governance/compliance_check.py`
