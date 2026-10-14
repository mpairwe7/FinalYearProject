# Traceability Record — MCP 2026-07-28 Completeness, Production Tools, and Connector Hardening (2026-10-05)

> **Date:** 2026-10-05  
> **Commits / PRs:** PR #533 (`feat/mcp-connectors-production-readiness`), PR #534 (`feat/mcp-completeness-and-production-tools`)  
> **Target Standard:** Anthropic Model Context Protocol (`2026-07-28` Stateless JSON-RPC Specification), OWASP GenAI LLM06 (Excessive Agency), NIST AI RMF, OpenTelemetry GenAI semconv.  
> **Branch:** `feat/mcp-completeness-and-production-tools` (PR target: `dev`)  

---

## 1. Executive Summary & Purpose

This release modernizes the enterprise Model Context Protocol (MCP) tool subsystem, system connectors, and agent capability plane to full October 2026 production readiness:
1. **MCP Client Completeness**: Adds client-side support for MCP resources (`resources/list`, `resources/read`) and prompt templates (`prompts/list`, `prompts/get`) across `MCPClient`, `InProcessTransport`, and `HttpTransport`.
2. **System Connector Resource Integration**: Surfaces static reference schemas, tax tables, and connector documentation as read-only MCP resources via `PluginOrchestrator.get_all_resources()`.
3. **OWASP LLM06 Excessive Agency Safeguards**: Implements hard monetary transaction ceilings (`MAX_TRANSACTION_CEILING_UGX = 50,000,000 UGX`) and distributed sliding-window velocity limiting (10 critical actions/hour per user via Redis sorted sets).
4. **Credential Vaulting**: Introduces `TokenVault` implementing AES-256-GCM envelope encryption with HKDF key derivation for all stored connector tokens and secrets.
5. **Distributed Saga Compensation**: Provides atomic transaction rollback capabilities across multi-action business sequences via `SystemConnector.compensate()`.
6. **Strict Output Schemas**: Declares and validates explicit JSON Schema 2020-12 `output_schema` definitions across all 28 registered tools in the platform.
7. **Dense Tool RAG Embedder Auto-Injection**: Automatically shares the already-loaded `HybridRetriever` dense embedding model with `tool_rag.py` at application startup, eliminating redundant model loads and token waste.

---

## 2. Structural & Architectural Changes

### 2.1 MCP Primitives Completeness (Resources & Prompts)
- **Problem**: Previous MCP client implementations only supported tool calling (`tools/call`, `tools/list`), leaving read-only resource discovery and prompt templates unwired on the client side.
- **Architecture Solution**:
  - Implemented `list_resources()` and `read_resource()` in `InProcessTransport`, `HttpTransport`, and `MCPClient`.
  - Implemented `list_prompts()` and `get_prompt()` in `InProcessTransport`, `HttpTransport`, and `MCPClient`.
  - Exposed standard statutory resources (`ura://rates/current`, `ura://calendar/deadlines`) and prompt templates (`vat_calculation_guide`, `paye_withholding_guide`, `tcc_tender_checklist`).
  - Added unit test suite `App/backend/tests/test_mcp_resources.py` (5/5 passed).

### 2.2 Distributed Sliding-Window Velocity Limiter
- **Problem**: In-process velocity trackers could not enforce rate limits across horizontally-scaled multi-replica deployments. Furthermore, discovery probes (`discovery-probe`) inadvertently consumed execution quotas during tool filtering.
- **Architecture Solution**:
  - Upgraded `_check_and_record_velocity()` in `App/backend/app/mcp/policy.py` to use Redis sorted sets (`mcp:velocity:{user_id}`).
  - Tracks timestamped action entries within a rolling 3600-second window, setting a 3660-second TTL on the key.
  - Automatically falls back to an in-process thread-safe dictionary if Redis is unreachable.
  - Exempts `discovery-probe` identity from consuming velocity allowances during tool availability checks.

### 2.3 Strict Tool Output Schemas & Enforcement
- **Problem**: While `inputSchema` was enforced across tools, several tools (`rates.py`, `tasks.py`, `graph_tools.py`) lacked formal `output_schema` contracts, risking unvalidated responses reaching agent reasoning graphs.
- **Architecture Solution**:
  - Defined explicit `output_schema` dictionaries across all registered tools, establishing strict return types.
  - Added normalization in `ToolSchema.__post_init__` to guarantee that `ok: bool` is marked as a required property.
  - Client-side validation in `MCPClient` validates returned payloads against `output_schema` and logs schema drift.
  - Added regression unit test verifying all 28 tools in `ToolRegistry` declare compliant output schemas.

### 2.4 Token Vaulting & Credential Security
- **Problem**: Connector tokens stored in configuration or SQLite/Postgres databases risked plaintext exposure.
- **Architecture Solution**:
  - Built `TokenVault` in `App/backend/app/auth/vault.py` with AES-256-GCM encryption, 96-bit random initialization vectors (IVs), and 128-bit authentication tags.
  - Uses master key derived via HKDF-SHA256 from `VAULT_MASTER_KEY` / `JWT_SECRET`.
  - Added seamless decryption on dispatch and secure zeroing.

### 2.5 Saga Distributed Compensation Pattern
- **Problem**: Multi-step agent flows across simulated connectors (e.g. generating PRN, releasing customs goods, activating digital tax stamps) had no standard rollback mechanism upon intermediate failure.
- **Architecture Solution**:
  - Added `compensate()` method signature to `SystemConnector` in `plugins/base.py`.
  - Orchestrator tracks executed steps and triggers reverse compensation in reverse order of invocation if a sequence aborts.

### 2.6 Dense Tool RAG Auto-Injection
- **Problem**: `tool_rag.py` defaulted to lexical token overlap unless a dense model was manually injected.
- **Architecture Solution**:
  - Auto-injected the existing sentence-transformers dense embedding model from `HybridRetriever` into `tool_rag.py` during FastAPI startup in `lifespan` (`main.py`) and `ChatModel.__init__` (`service.py`).
  - Enables cosine similarity semantic retrieval for large toolsets without loading a second embedding model into memory.

---

## 3. Verification & Compliance Matrix

| Component | Standard / Metric | Verification Result |
| :--- | :--- | :--- |
| **MCP Test Suite** | 2026-07-28 Wire Spec & Hardening | **113/113 passed** (`test_mcp_*.py`) |
| **Agent Suite** | Tool dispatch & policy enforcement | **1023 passed** (`tests/agents/`) |
| **Chaos Resilience** | Fault injection & circuit breakers | **3/3 passed** (`tests/chaos/`) |
| **API Endpoints** | Locked 108 HTTP + 7 WS manifest | **26/26 passed** (`test_all_endpoints_e2e.py`) |
| **Frontend Tests** | Vitest suite | **484/484 passed** (`bun run test`) |
| **Secret Scanning** | Zero credential leaks | **Passed** (TruffleHog, GitGuardian, Gitleaks, detect-secrets, Semgrep, Bandit) |

---

## 4. Operational Sign-off & Pull Requests
- **PR #533**: Security hardening, token vaulting, transaction ceilings, velocity limits, and Saga compensation.
- **PR #534**: Client resources & prompts completeness, distributed Redis velocity limiting, strict output schemas, and dense tool RAG auto-injection.
- **Runbook**: Documented in `docs/runbooks/enterprise-connectors-and-mcp.md`.
