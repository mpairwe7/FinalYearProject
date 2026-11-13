# Backend (FastAPI agent host)

Python 3.12. Package: `App/backend/app` (imported as `app`).

## Commands

```bash
cd App/backend
PYTHONPATH=. python3 -m pytest tests/ -q
# from repo root:
PYTHONPATH=App/backend python3 -m pytest App/backend/tests tests/agents -q
PYTHONPATH=App/backend python3 -m app.seed_prototype   # development only
```

## Boundaries

| Module | Role |
| --- | --- |
| `agents/` | Supervisor, golden-set routing, LangGraph |
| `routes/` | Modular domain APIRouters (`connectors.py`, `offline.py`) |
| `tools/` | Registered tools (`ToolRegistry`) |
| `mcp/` | MCP client + tax-calculator server |
| `guardrails.py` | Input/output OWASP LLM01–09 |
| `logging_config.py` | Root JSON logging: request id, trace ids, scrubbed `attributes` and tracebacks |
| `analytics.py` | The one metrics facade (prometheus_client, all workers); `record_chat_turn` |
| `tracing.py` | OTel setup; `llm_call`, `trace_tool_call`, GenAI spans and turn token usage |
| `audit/` | Hash-chained ledger, `turns.append_turn` (one row per answer while `audit_ledger` is on; a failed append is counted and logged, never retried), RFC 3161 seals (`tsa.py`) |
| `mt.py` | Shared translation cache + the figure-fidelity guard |
| `flags.py` | Registry + rollout; do not add a flag here only |

## Rules

- Retrieval flags `hyde`, `graph_fusion`, `tool_rag` stay default off.
- Answers are generated in English and localized in **one** place
  (`localize_reply`). A new exit from `_generate_en` needs no translation call;
  a new branch in `run_chat_turn` does, and must use the locale from the
  retrieval **result**, never the caller's parameter.
- Every translation goes through `mt` — the cache and `figures_survived`. A
  translation that changes a money amount or a percentage is refused and the
  English text is served.
- `ticket_queue` default on; the escalate tool **and** `POST /v1/escalate` both
  return `ok: false` when it is off, rather than promising a handoff. Taxpayer
  escalations route through `_maybe_create_ticket` so one conversation gets one
  officer, with the transcript, the team routing and the live queue event.
- Document analyze: PDF guards, then `malware_scan` (fail-open unless `MALWARE_SCAN_REQUIRED`). `DOCUMENT_PARSE_ISOLATED` runs extractors in a subprocess.
- `URA_ACCOUNT_API_MODE=mock` is sandbox only (`live=false`). Production rejects it.
- Production gap gates: `app/production_readiness.py` (malware isolated parse, https publications, RLS ack, no seed, no live notify). See `docs/PRODUCTION_GATES.md`.
- Publications ingest hashes `URA_PUBLICATIONS_URL` and enqueues reindex. Never auto-`--recreate`.
- `crawl_corpus.review_pages` decides what a crawl may contribute, from the ledger in `Data/crawl/crawl_state.json`, and must stay free of the clock so export and ingest agree. `crawl_gate` is what a crawl must pass before a person sees it. A corpus fetch uses `CORPUS_FETCH_USER_AGENT`, checks each redirect hop and never treats a name or a size as proof of identity (ADR-0023).
- Flag PATCH persists to `flag_overrides` on **this replica**. Cluster-wide still needs `FLAG_*`.
- A new bearer check goes through `_resolve_bearer_context` or `chat_ws_v2._verified_ws_claims`, or calls `auth.revocation.claims_revoked`, so a revoked session is refused (ADR-0053). Tokens without a usable `iat` fail `JWTVerifier.verify`; keep it that way, or they could never be revoked. A socket that outlives its sign-in re-checks `_session_over` (expired or revoked) at least every `_SESSION_POLL_S` (a chat or voice socket: `revocation.SessionCheckedReceiver`); a staff loop whose frame handling can block (a send to a peer that stopped reading) runs under `_while_session_lasts`.
- Receptionist: what an officer says reaches the caller through `room.caller_ws` (or `transport.send_audio` on LiveKit), never through the AI pipeline, whose frames `OfficerOutputGate` and `BrowserCallSerializer` hold back once a call is bridged or ended (ADR-0055). An officer ends a call through `desk.end`, and a server-side end tells the caller `status: ended` before `CallRoom.end()`, which stops the pipeline (`ws._run_pipeline` registers it) and closes the caller's socket.
- Metrics go through `analytics.metrics` only (never `prometheus_client` directly): label values must be bounded (route templates, enums), and a rule or dashboard panel may only name series the code emits — `tests/test_monitoring_config.py` fails otherwise.
- A new chat transport calls `record_chat_turn` once per turn and audits through `ChatModel.record_turn_audit` (or goes through `run_chat_turn`, which does both). Audit the text that was served, after localization.
- A graph approval carries `pending_action.action_digest`; a resume claims its suspend with `CheckpointStore.claim` before acting, and checkpoint SQL stays portable (`ON CONFLICT`, never SQLite-only forms). Keep the `graph_checkpoint_routine` flag off in production (ADR-0030).
- The Gemini branch of the cloud tier answers under `llm.CLOUD_SYSTEM_PROMPT`; the Workers AI branch keeps the full `SYSTEM_PROMPT` through `_build_messages`, a superset. A guarding rule added to `SYSTEM_PROMPT` is added to the cloud prompt too; `tests/agents/test_harness_prompt_contract.py` lists the rules it must keep. Prompt fragments never state a fiscal-year literal or a statutory figure (ADR-0030).
- Turn context (stated identifiers, case facts, tenant, conversation) is read from `turn_context`, never from `service`. Only transports and composition roots import `service`; the graph imports nothing from `service`, and neither do `turn_record`, `live_research_turn`, `english_rendering` or `faq_binding` (`test_agentic_rag_context_sandbox_2026_11.py`, ADR-0031, G486). Stub `answer_in_english` on `english_rendering`, the one patch point.
- A parse child sees only `document_worker.child_environment`; a new extractor setting needs its name allowed there. Keep `DOCUMENT_PARSE_MAX_MEM_BYTES` non-zero (ADR-0031).
- An answer may link only to a trusted host (`guardrails.is_trusted_link_target`): `*.go.ug`, `_DEFAULT_EXTRA_LINK_HOSTS` or `OUTPUT_LINK_ALLOWED_HOSTS`. Never add a redirector. Text a model reads from a third party goes through `scan_retrieved_text` or `scan_external_tool_text`; an upload is wrapped only after `vision.ocr.escape_untrusted_document_markers` (ADR-0029).
- A guard pattern change adds its attack to `Data/eval/redteam_corpus.jsonl` and anything lawful it nearly caught to `Data/eval/guardrail_benign_controls.jsonl`; both gates must pass.
- Live research goes through `live_research_turn.live_research_for_turn`, so REST, SSE and the graph cannot drift. A new search backend implements `web_search_providers.SearchProvider` and stops at `RawResult`: authority, screening and recency are decided once, in `web_research.admit_results`. New words in `_SEARCH_VOCABULARY` must be generic tax terms, never drawn from a query.
- A planned step that does not complete must end in a `PlanRunReport` outcome and show in the block. A market FX rate is an estimate: only `official_bou_rate` may be called a Bank of Uganda rate.
- A new model client wraps its HTTP call in `tracing.llm_call(...)` and reports the provider's `usage`; never estimate tokens from words.
- Every `allow_request()` ends in `record_success()`, `record_failure()` or `release()`. Where a budget also has to agree, use `resilience.admit()`. A cancelled call is `release()`, not a failure.
- Anything slow in an `async def` turn goes through `asyncio.to_thread`: translation and the guards used to run on the event loop and froze every stream on the worker.
- A new generation path calls `turn_record.note_generation` with what answered, so the audit row can name it. A new way to degrade adds its sentence to `DEGRADED_DISCLOSURES`.
- A ticket from a tool goes through `ESCALATION_ROUTE`, never `db.create_ticket`. A tool has no conversation of its own.
- When `multi_tenant` is on, scope every staff ticket detail/write/list/aggregate and live queue event to the authenticated tenant; keep public ticket-capability routes unchanged (ADR-0028).
- When action-level audit is enabled, persist an intent record before high/critical tool effects and fail closed if that required write fails. Share the same `ToolCallBudget` across reasoner fallback, and represent claim-verifier errors as unavailable with a taxpayer disclosure (ADR-0028).
- A calculator slot in `_SLOT_CANDIDATE_KEYS` lists only keys with the same meaning, and an identifier never binds from a tool's output.
- Third-party text that carries an injection is rejected whole, never scrubbed and kept: search results, live documents (cached copies too) and replayed turns. Keep `ephemeral_pdf_sandbox.TAINTED_DOCUMENT_MARKER` in the rejection; the isolated child passes only the message.
- Live document reads go to `web_research._DOCUMENT_POOL` behind `_DOCUMENT_SLOTS`, never to the search pool, and every download checks `SANDBOX_DOWNLOAD_DEADLINE_SECONDS` per chunk, read as it arrives.
- Every rate-table key must reach the kernel: a new key with an unfamiliar shape needs a prefix in `graph.build._KERNEL_TAX_PREFIXES` and, unless it is a rate, a rule in `_KERNEL_KIND_RULES`. Give it a `legal_basis`; one without is served as unverified.
- A change to the rate tables, `graph.build`, `kernel.model` or `kernel.rules` needs a release entry (`python -m app.kernel.release record ...`, docs/runbooks/kernel-release.md); CI fails otherwise. Figures reach answers through kernel functions or calculators, never from a model's own knowledge. Commit the snapshot a release writes; a penalty or interest rule stays `pending_tax_team_review` until a named reviewer approves it in a release. Keep `rules.py` and the `penalty_late_filing` calculator in agreement (parity test). Neo4j statements are module constants with parameters only; semantic-layer metrics are certified templates, never model-written SQL.
- A new or changed rate figure needs a `legal_basis` naming the document and page, and an entry in `app/tax/evidence/rate_evidence.json` (docs/runbooks/kernel-release.md). Answer templates in `calculator_router` read figures from the table (`_table_figures`); never type an amount into one. Read "today" with `tax.tables.kampala_today()`, never the UTC date.
