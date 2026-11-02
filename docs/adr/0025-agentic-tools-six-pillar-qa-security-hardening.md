# ADR-0025: Six-Pillar AI Agentic Tools QA, Security & Production-Readiness Hardening (G416–G438)

## Status

Accepted — 2026-11-02

## Context and Problem Statement

As a high-stakes government tax administration platform serving the Uganda Revenue Authority (URA), the assistant orchestrates 72 agentic tools across 6 operational categories:
1. **Search & Retrieval Tools** (`rag_tool.py`, `education.py`, `graph_tools.py`, `web_search_providers.py`, `web_research.py`)
2. **API & MCP Connector Tools** (`mcp/transport.py`, `mcp/servers/wire.py`, `mcp/policy.py`, `mcp/client.py`, `mcp/validation.py`, `plugins/*`)
3. **Code Execution & Deterministic Calculation Sandbox Tools** (`tax/schedule_sandbox.py`, `tools/rates.py`, `tools/calendar.py`, `tools/mda_interdependence.py`)
4. **Database, Memory & Account Tools** (`tools/memory_tools.py`, `tools/tasks.py`, `tools/ura_account.py`, `tools/ura_actions.py`, `workflows/registry.py`, `tools/empathy.py`)
5. **File, Document, Sandbox & OCR Tools** (`ephemeral_pdf_sandbox.py`, `tools/document_audit.py`, `ocr_service.py`, `documents.py`)
6. **Web Automation, Portal Co-Pilot & Crawler Tools** (`portal_copilot.py`, `tools/portal_navigator.py`, `web_fx.py`, `ml/scripts/data_aug/crawler.py`)

A comprehensive November 2026 QA and security audit aligned with **OWASP Top 10 for Agentic Applications 2026 (`ASI01`–`ASI10`)**, **OWASP MCP Top 10 (`MCP01`–`MCP10`)**, and **ISO/IEC 42001:2023** surfaced 23 concrete gaps (`G416`–`G438`) across these 6 categories—including unscrubbed direct tool passages, 800-char statutory table truncation in `SearchKnowledgeBaseTool`, `Mcp-Actor-Assertion` key-resolution drift, transaction ceiling (`MAX_TRANSACTION_CEILING_UGX`) bypasses on non-standard amount parameter names, `UserFact` attribute mismatch in `MemoryRecallTool`, missing UDPA 2019 consent and prompt-injection checks in `MemoryStoreTool`, horizontal TIN lookup (`IDOR`) in `UraAccountProfileTool`, missing `.xlsx` zip-bomb checks in `ephemeral_pdf_sandbox.py`, and DTS stamp fee/category drift in `portal_copilot.py`.

## Decision Outcome

We remediate all 23 findings (`G416`–`G438`) in six structured phases:

1. **Phase 1 — Search & Retrieval Tools (`G416`–`G419`)**:
   - `SearchKnowledgeBaseTool` (`rag_tool.py`) scrubs every passage via `strip_hidden_control_and_bidi` + `scan_retrieved_text`, raises the passage window from `800` to `4000` characters (matching `ADR-0024`), uses calibrated relevance scores (`score_calibrated` / `score_norm`), and returns temporal metadata (`fiscal_year`, `valid_from`, `valid_to`, `title`).
   - `TaxEducationTool` (`education.py`) removes the hardcoded superseded `150m` VAT threshold from the EFRIS check answer.
   - `GraphResolveRateTool` and `GraphEffectiveOnTool` (`graph_tools.py`) align `output_schema` with `GraphAnswer.to_dict()`.
   - `SearxngProvider` (`web_search_providers.py`) rejects link-local metadata (`169.254.0.0/16`) and embedded credentials, and `classify_source_authority` (`web_research.py`) matches subdomains across all 28 official Ugandan MDA domains in `TIER1_DOMAINS`.
2. **Phase 2 — API & MCP Connector Tools (`G420`–`G424`)**:
   - `HttpTransport` and `StatelessMCPServer._check_headers` unify `Mcp-Actor-Assertion` HMAC-SHA256 verification across `MCP_OBO_SIGNING_SECRET`, `MCP_SERVER_TOKEN_<NAMESPACE>`, and shared bearer tokens.
   - `_extract_amount_from_args` (`mcp/policy.py`) inspects `amount_paid_ugx`, `adjusted_amount`, `items[]` line-item arrays, `quantity * unit_cost`/`unit_stamp_fee`, and nested `payload` dicts so no transactional write tool bypasses `MAX_TRANSACTION_CEILING_UGX` (`UGX 50,000,000`).
   - `MCPClient._call_tool` defers critical-action velocity recording until after schema validation and idempotency replay lookup (`record_velocity=False` during pre-check).
   - `MCPClient.read_resource` maps `payments://`, `dts://`, and `tin://` URI schemes to their canonical connector namespaces, and `scrub_prompt_overrides_in_result` strips `_UNICODE_CONTROL_RE` and recurses into tuples.
3. **Phase 3 — Code Execution & Deterministic Calculation Sandbox Tools (`G425`–`G428`)**:
   - `evaluate_multi_period_tax_schedule` (`tax/schedule_sandbox.py`) restricts execution to unprivileged read-only calculator namespaces (`tax_calculator`, `rates`) with `risk == "low"` and `not required_scopes`, and excludes thresholds, caps, and unit rates from multi-period summation.
   - `CompareFiscalYearsTool` (`rates.py`) aligns its output schema and formats progressive band changes cleanly.
   - `CurrentDateTool` and `NextDeadlineTool` (`calendar.py`) evaluate today's date in `Africa/Kampala` (`EAT`, `UTC+3`).
   - `AssessLifeEventTaxOnboardingTool` (`mda_interdependence.py`) accepts `CR` refugee NINs and hyphenated NINs.
4. **Phase 4 — Database, Memory & Account Tools (`G429`–`G433`)**:
   - `MemoryRecallTool` reads `predicate`, `object_value`, and `extractor_model` on `UserFact`; `MemoryStoreTool` enforces UDPA 2019 `personalization` consent and rejects prompt-injection payloads (`OWASP ASI06`).
   - `TaskCreateTool`, `TaskGetTool`, and `TaskCancelTool` bind `user_id` and `tenant_id` to prevent cross-tenant/cross-user task polling or cancellation.
   - `UraAccountProfileTool` enforces `TPCA Cap. 343 s. 75` (Official Secrecy) subject-TIN ownership (`IDOR` guard), and both `ura_account.py` and `ura_actions.py` refuse HTTP redirects and bound response bytes to `64 KiB`.
   - `resolve_workflow_side_question` (`workflows/registry.py`) invokes `get_next_deadlines` and `explain_tax_concept(topic=...)`, and the slot calculator bridge matches parameterized `number[min=...]` validators.
5. **Phase 5 — File, Document, Sandbox & OCR Tools (`G434`–`G436`)**:
   - `ephemeral_pdf_sandbox.py` validates `.xlsx` ZIP archives against zip-bombs, path traversal, and macros before `openpyxl.load_workbook`, and bounds `_L1_CHUNK_CACHE` to 64 entries.
   - `AuditTaxDocumentTool` (`document_audit.py`) scrubs input text, supports `document_id` table lookup, and preserves `currency` and `wht_ugx`.
   - `ocr_service._decode_image` normalizes EXIF orientation (`ImageOps.exif_transpose`), and `documents._extract_csv` bounds row materialization.
6. **Phase 6 — Web Automation, Portal Co-Pilot & Crawler Tools (`G437`–`G438`)**:
   - `portal_copilot.py` unifies `_DTS_PRODUCT_CATALOG` with `plugins/digital_tax_stamps/service.py` (`STAMP_UNIT_FEES` and `"WINE"`/`"WINES"`/`"JUICES"`), scrubs all DOM telemetry strings through `strip_hidden_control_and_bidi` + `scan_retrieved_text`, and wraps `grounding_block` in `<untrusted_portal_dom_telemetry>` spotlighting tags (`OWASP ASI01`).
   - `web_fx._default_http_fetcher` enforces a `2 MiB` response cap and blocks embedded credentials, and `crawler._strip_hidden` strips `_INVISIBLE_CHARS` before quarantine screening.

## Consequences

- **Positive**: Closes 23 concrete security, schema-conformance, financial-ceiling, memory-safety, and statutory-accuracy gaps across all 6 tool categories without altering public API contracts or latency budgets.
- **Negative / Trade-offs**: Unprivileged schedule batches in `schedule_sandbox.py` can only invoke `tax_calculator` and `rates` tools (`risk == "low"`, `read_only=True`), which is the intended least-privilege boundary (`OWASP ASI02`).

## Confirmation

Verified by `tests/agents/test_phase43_agentic_tools_hardening.py`, `governance/compliance_check.py`, and `threat-model/validate_threats.py`.
