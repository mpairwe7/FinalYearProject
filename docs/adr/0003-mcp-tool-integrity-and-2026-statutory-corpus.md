# ADR-0003: MCP Tool Descriptor Integrity, Rug-Pull Pinning & 2024–2027 Statutory Corpus Integration

## Status

Accepted — 2026-10-17

## Context and Problem Statement

An October 2026 QA and security audit against the **OWASP MCP Top 10 (2026)**, **OWASP MCP Security Cheat Sheet (2026)**, **CSA Canonical Tool Metadata Schema (CTMS)**, and **OWASP AISVS v1.0 Chapter 10** identified five structural gaps in the Model Context Protocol (`2026-07-28`) setup and tool registry, alongside a statutory knowledge-freshness requirement for the 2024–2027 Uganda legislative cycle (URA, Parliament of Uganda, ULII, URSB, and UNBS):

1. **Misdeclared Mutating/Destructive Memory Tools (`MCP01/MCP02/MCP05`)**: `memory_store` and `memory_discard` mutated persistent semantic facts while declaring `risk="low"`, `read_only=True`, and `required_scopes=()`.
2. **Caller Identity & Tenant Context Drop across `InProcessTransport` (`MCP04/MCP07`)**: `InProcessTransport.call` omitted `_meta` identity fields when invoking `ToolRegistry.call`, and `ToolRegistry.call` did not bind verified `user_id`/`tenant_id` into `tool.execute(...)` when omitted from `inputSchema` to prevent LLM spoofing.
3. **Unpinned Tool Descriptors & Rug-Pull Exposure (`MCP03/MCP08`)**: Tool descriptors lacked a canonical SHA-256 fingerprint over `(name, description, inputSchema, outputSchema, annotations, securityMeta)` and runtime pinning against mid-session remote descriptor mutation or prompt-injection poisoning.
4. **Indirect Prompt Injection & Credential Leak in Tool Outputs (`MCP06 / AISVS 10.4.2`)**: `MCPClient` and `StatelessMCPServer` checked JSON types via `outputSchema` but did not screen tool output strings for indirect prompt-injection directives or leaked bearer/API credentials.
5. **Wire-Level Server Input Validation (`AISVS 10.4.3`)**: `StatelessMCPServer.handle_tools_call` did not enforce `additionalProperties: false` and structural `inputSchema` validation before invoking `ToolRegistry.call`.
6. **2024–2027 Official Statutory Corpus & Rate/Graph Currency**: 23 official 2024–2027 PDFs from URA (`EFRIS Handbook FY 2026-27` in English, Luganda, Swahili, and Runyakole; `Tax Incentives Guide FY 2025-26 Vol. 3`), Parliament of Uganda (`Income Tax`, `VAT`, `Excise Duty`, `Tax Procedures Code`, `Stamp Duty`, `Lotteries and Gaming`, and `External Trade` Amendment Bills/Acts/Reports 2024–2026), URSB (`Business Names Registration Amendment Rules 2024 S.I. No. 43`, `Client Charter 2025-2030`, `Trademarks Fees 2025`), and UNBS (`PVoC Scheme FAQs` and `Imports E-Portal ASYCUDA World & Legal Notice No. 14 of 2025 Compendium`) needed canonical chunking, SHA-256 manifest verification, and rate-table/graph integration.

## Decision Outcome

We implemented a 4-phase defense-in-depth and statutory-integration architecture:

1. **Strict Mutating Memory Tool Declarations**: Updated `memory_store` (`risk="medium"`, `read_only=False`, `idempotent=False`, `required_scopes=("personalization",)`, `scope_exempt_roles=("verified_taxpayer", "ura_staff", "ura_admin")`) and `memory_discard` (`risk="medium"`, `read_only=False`, `destructive=True`, `idempotent=True`, `required_scopes=("personalization",)`, plus tenant-scoped SQL filtering `AND tenant_id = ?`).
2. **End-to-End Verified Identity & Tenant Injection**: `InProcessTransport.call` and `StatelessMCPServer.handle_tools_call` pass verified `user_id` and `tenant_id` from `_meta` to `ToolRegistry.call`, which inspects `tool.execute` via `inspect.signature` and injects verified `user_id`/`tenant_id` without trusting unverified LLM arguments.
3. **Canonical SHA-256 Descriptor Fingerprinting & Rug-Pull Pinning**: Added `compute_descriptor_sha256` and `inspect_descriptor_integrity` in `App/backend/app/mcp/validation.py`, stamped `ug.go.ura.chatbot/descriptorSha256` on all 54 tools in `App/backend/app/tools/__init__.py` and `plugins/base.py`, and enforced descriptor pinning (`_pinned_descriptors`) in `MCPClient`.
4. **Wire-Level Schema Enforcement & Output Screening**: Added `screen_tool_result` in `App/backend/app/mcp/validation.py` and enforced both structural `validate_arguments` and `screen_tool_result` in `MCPClient._call_tool` and `StatelessMCPServer.handle_tools_call` (plus `plugins/server.py`).
5. **2024–2027 Statutory Corpus, Rate Tables & Tax Graph**: Added 23 verified 2024–2027 PDFs to `Data/pdfs/`, exported canonical JSONL chunks (`169` sources, `158` unique, `3,996` verified chunks in `App/Data/pdf_jsonl/pdf_corpus_manifest.json`), updated `App/Model/index_freshness.json`, and integrated 24 statutory rate/fee/penalty keys across `FY2025-26.json`, `FY2026-27.json`, `App/backend/app/tools/rates.py`, `App/backend/app/graph/query.py`, and `plugins/ursb/service.py`.

## Consequences

- **Positive**: Closes OWASP MCP01–MCP08 and AISVS 10.4 gaps across all 54 tools; guarantees cryptographic tool schema pinning and indirect prompt-injection output quarantine; grounds 2024–2027 URA, URSB, and UNBS statutory questions in deterministic rate tables, graph traversals, and hash-verified PDF chunks.
- **Negative / Trade-offs**: Any intentional runtime mutation of a tool's schema requires calling `reset_client()` so `_pinned_descriptors` refreshes cleanly.

## Confirmation

Validated continuously in CI by:
- `App/backend/tests/test_mcp_2026_october_audit_and_corpus.py`
- `tests/agents/test_mcp_2026_october_audit.py`
- `tests/test_monorepo_architecture_governance.py`
