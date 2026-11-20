# Backend (FastAPI agent host)

Python 3.12. Package: `App/backend/app` (imported as `app`).

## Commands

```bash
cd App/backend
PYTHONPATH=. python3 -m pytest tests/ -q
# from repo root:
PYTHONPATH=App/backend python3 -m pytest App/backend/tests App/tests/agents -q
PYTHONPATH=App/backend python3 -m app.seed_prototype   # development only
```

## Boundaries

| Module | Role |
| --- | --- |
| `agents/` | One file per agent role (ADR-0061): `supervisor.py` (routing, with the locale `patterns/`), `tiebreak.py`, `critic.py` (the evaluator-optimizer that checks an answer before the taxpayer reads it), `loop_control.py`; `eval_routing.py` and `eval_multihop.py` measure them |
| `workflows/` | `graph/` (the LangGraph-style turn graph: `main_graph.py`, `runtime.py` with the sealed approvals, `state.py`, `compactor.py`) and the YAML guided journeys (`registry.py`, `loader.py`, `slots.py`, `journey_copilot.py`, `flows/`) (ADR-0061) |
| `api/` | The HTTP routers: `rag.py` (chat over REST, SSE and WebSocket, titles, FAQ), `documents.py` (upload analysis, re-index), `health.py` (`/health`, `/ready`, `/metrics`), `connectors.py`, `offline.py`, `quality.py`; `deps.py` (the shared `get_model` and operator checks) and `models.py` (Pydantic request/response models). `main.py` is the composition root that includes them |
| `rag/` | The RAG flow (ADR-0056): `pipeline.py` (the `ChatModel` orchestrator), `retriever.py`, `generator.py` (answer generation and message assembly), `prompt_builder.py` (message assembly and passage fencing; the prompt text is in `prompts/`), `query.py`, grounding checks, turn context, `tool_budget.py` (keeps tool schemas inside the model's context window) |
| `prompts/` | Every system prompt, one module per area: `answer.py`, `query.py`, `receptionist.py`, `title.py`, `agents.py`, `vision.py`. A module imports its prompt from here under the name it had (ADR-0060) |
| `ingestion/` | Corpus builders (`pdf_corpus`, `faq_corpus`, `crawl_corpus`), document parsing (`docling_intelligence`, `document_normalization`) and `indexer.py` |
| `embeddings/` | `embedder.py`: CPU embeddings for the local dense leg (ADR-0034) |
| `speech/` | Speech and voice (ADR-0067): `speech_service.py` (ASR, translation and TTS behind one service), `speech_normalization.py` (reply text made speakable), `accent_detector.py`, the local voices `orpheus_tts.py` and `spark_tts_salt.py`, `voice_consent.py` (consent receipts and the voice audit log), and the streaming voice transports `voice_stream.py`, `voice_stream_v2.py`, `voice_ws.py`, `voice_ws_v2.py` |
| `research/` | Live web research (ADR-0021, ADR-0035, ADR-0067): `web_research.py` (when to look, what may leave, what an answer may trust), `web_search_providers.py` (the `SearchProvider` backends and failover), `egress.py` (the one outbound HTTP client), `doh_resolver.py`, `web_fx.py` (indicative exchange rates) and `ephemeral_pdf_sandbox.py` (the RAM-backed document sandbox) |
| `tickets/` | Handoff to officers (ADR-0067): `escalation_policy.py` (when a person is offered, and how fast they should answer), `officer_handoff.py` (the one handoff for every path to an officer), `escalation_notify.py`, `officer_chat.py` (live chat with the officer on the case), `officer_hours.py`, `ticket_events.py` and `ticket_ws.py` (live queue events for staff), `hitl_routing.py` (document uncertainty routed to a person) |
| `quality/` | Service quality (ADR-0067): `service_ratings.py` (one table for every stream's satisfaction ratings), `quality_audit.py` (what the 👍/👎 say about the AI's answers), `audit_reviews.py` (the administrator's review queue), `evaluation.py` (the continuous RAG evaluation harness), `discrepancy_detector.py`, `journey_analytics.py`, `artifact_export.py` (the figures and metric tables for the project report) |
| `connectors/` | The URA portal and the services a taxpayer connects (ADR-0067): `connector_intent.py` (whether a turn asks for a lookup in a chosen service), `connector_outcome.py` (what the taxpayer is told once it has been asked), `portal_copilot.py` (portal tasks, confirmation and inline elicitation), `ura_account_mock.py` (sandbox taxpayer profiles, never a live account), `verified_resources.py` (official URA links offered beside an answer) |
| `processing/` | Document processing (ADR-0067): `documents.py` (uploaded files: extraction, classification, analysis), `document_worker.py` (the isolated parse child and its allowed environment), `ocr_service.py` (the Triton OCR sidecar and proxy) |
| `chat/` | The chat turn outside retrieval (ADR-0067): `chat_ws_v2.py` (the streaming text WebSocket), `chat_title.py` (sidebar titles), `conversational.py` (greetings and small talk), `model_welfare.py`, `turn_guidance.py` (the guidance applied to every finished turn), `turn_pipeline.py` (stage contracts and attestation) |
| `tax/` | Fiscal data and the arithmetic that reads it: `tables.py` (the effective-dated rate tables), `money.py`, `calculator_router.py` (deterministic calculator answers and their templates), `scenario_planner.py` (the declarative planner and its verified-facts block), `schedule_sandbox.py`, `glossary.py` (the curated tax terms for each answer language) |
| `vectorstore/` | `vector_db.py` (the pgvector leg), Qdrant `index_lifecycle`, `corpus_sync`, `qdrant_backup`, `point_ids` |
| `db/` | `database.py` (the store and its backend seam), `postgres.py` and `cms.py` (the staff answer overrides, matched on a normalised query) |
| `tools/` | Registered tools (`ToolRegistry`) |
| `mcp/` | MCP client + tax-calculator server |
| `guardrails/` | OWASP LLM01–09 (ADR-0061): `input_checks.py` (`InputGuard`, `scan_*_text`), `fences.py` (spotlight fences), `pii.py` (redaction, streaming DLP), `output_checks.py` (`OutputGuard`, the figure release gate, link provenance), `normalize.py` (shared canonicalisation), `premise.py` (false premises and fact contradictions), `friction.py` (friction against adaptive probing), `pdf.py` (PDF intake guards) |
| `middleware/` | `logger.py` (root JSON logging: request id, trace ids, scrubbed `attributes` and tracebacks), `rate_limiter.py` (the shared slowapi `limiter` and per-route ceilings), `error_handler.py` (the 401 challenge on every HTTP error) |
| `observability/` | `analytics.py` (the one metrics facade: prometheus_client, all workers; `record_chat_turn`), `tracing.py` (OTel setup; `llm_call`, `trace_tool_call`, GenAI spans and turn token usage), `client_telemetry.py` (Web Vitals and client errors from the browser), `security_events.py` (OWASP Logging Vocabulary events) (ADR-0060) |
| `memory/` | Working, episodic, semantic and procedural memory and the resolution ledger; `case_facts.py` (facts the taxpayer stated in this conversation) and `language_state.py` (the conversation's answer language) (ADR-0060); `topics.py` (the task a conversation is about) |
| `audit/` | Hash-chained ledger, `turns.append_turn` (one row per answer while `audit_ledger` is on; a failed append is counted and logged, never retried), RFC 3161 seals (`tsa.py`) |
| `mt.py` | Shared translation cache + the figure-fidelity guard |
| `linear_text.py` | `strip_spans`, `strip_blocks`: one-pass scans that replace a regex exactly (ADR-0057) |
| `config/` | `flags.py` (registry + rollout; do not add a flag here only) and `settings.py` (the API's title and version) |

## Rules

- A regex over chat text gets one way to match each character: no two unbounded runs that
  can match the same text, and no run then a literal on an unanchored search. CodeQL fails the
  PR otherwise, and a lookbehind, a lookahead or a `pos` argument does not satisfy it; use
  `linear_text` or a scan, and keep the old regex as the test oracle (ADR-0057).
- Retrieval flags `hyde`, `graph_fusion`, `tool_rag` stay default off.
- Beside a readable document a fast path may answer but may not ask. Each chat route works out `has_readable_document(attachments)` once and passes it to `_maybe_handle_fast_paths(beside_document=...)`. The declines, the exact calculator answer and the rate table still run. The paths that ask for a missing detail do not, because the detail may be in the document. A document with no extracted text counts as none, so the taxpayer is still asked. Do not make a path's answer depend on words in the message that seem to point at the document: that was tried and measured to cost self-contained questions their instant answer (ADR-0068 part 2).
- Behaviour the HF Space needs goes in this package behind a setting, with tests, never into `HF_CPU_PATCH_B64` in `App/deploy/hf-space/Dockerfile`: no test or review reads that script, and it left two retrieval defects in production (ADR-0068).
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
- Production gap gates: `app/production_readiness.py` (malware isolated parse, https publications, RLS ack, no seed, no live notify). See `App/docs/PRODUCTION_GATES.md`.
- Publications ingest hashes `URA_PUBLICATIONS_URL` and enqueues reindex. Never auto-`--recreate`.
- `crawl_corpus.review_pages` decides what a crawl may contribute, from the ledger in `App/Data/crawl/crawl_state.json`, and must stay free of the clock so export and ingest agree. `crawl_gate` is what a crawl must pass before a person sees it. A corpus fetch uses `CORPUS_FETCH_USER_AGENT`, checks each redirect hop and never treats a name or a size as proof of identity (ADR-0023).
- Flag PATCH persists to `flag_overrides` on **this replica**. Cluster-wide still needs `FLAG_*`.
- A new bearer check goes through `_resolve_bearer_context` or `chat_ws_v2._verified_ws_claims`, or calls `auth.revocation.claims_revoked`, so a revoked session is refused (ADR-0053). Tokens without a usable `iat` fail `JWTVerifier.verify`; keep it that way, or they could never be revoked. A socket that outlives its sign-in re-checks `_session_over` (expired or revoked) at least every `_SESSION_POLL_S` (a chat or voice socket: `revocation.SessionCheckedReceiver`); a staff loop whose frame handling can block (a send to a peer that stopped reading) runs under `_while_session_lasts`.
- Receptionist: what an officer says reaches the caller through `room.caller_ws` (or `transport.send_audio` on LiveKit), never through the AI pipeline, whose frames `OfficerOutputGate` and `BrowserCallSerializer` hold back once a call is bridged or ended (ADR-0055). An officer ends a call through `desk.end`, and a server-side end tells the caller `status: ended` before `CallRoom.end()`, which stops the pipeline (`ws._run_pipeline` registers it) and closes the caller's socket.
- Metrics go through `analytics.metrics` only (never `prometheus_client` directly): label values must be bounded (route templates, enums), and a rule or dashboard panel may only name series the code emits — `App/tests/test_monitoring_config.py` fails otherwise.
- A new chat transport calls `record_chat_turn` once per turn and audits through `ChatModel.record_turn_audit` (or goes through `run_chat_turn`, which does both). Audit the text that was served, after localization.
- A graph approval carries `pending_action.action_digest`; a resume claims its suspend with `CheckpointStore.claim` before acting, and checkpoint SQL stays portable (`ON CONFLICT`, never SQLite-only forms). Keep the `graph_checkpoint_routine` flag off in production (ADR-0030).
- Every generated answer, local and cloud, is built by `generator._build_messages` under `prompt_builder.SYSTEM_PROMPT` (ADR-0036). A guarding rule goes there, and `App/tests/agents/test_harness_prompt_contract.py` holds each one (G667). A judged draft's revision (`pipeline._reflect_llm`) uses its own editor instruction.
- Turn context (stated identifiers, case facts, tenant, conversation) is read from `turn_context`, never from `pipeline`. Only transports (the HTTP routers in `api/` among them) and composition roots import `pipeline`; the graph imports nothing from `pipeline`, and neither do `turn_record`, `live_research_turn`, `english_rendering` or `faq_binding` (`test_agentic_rag_context_sandbox_2026_11.py`, ADR-0031, G486). Stub `answer_in_english` on `english_rendering`, the one patch point.
- A parse child sees only `document_worker.child_environment`; a new extractor setting needs its name allowed there. Keep `DOCUMENT_PARSE_MAX_MEM_BYTES` non-zero (ADR-0031).
- An answer may link only to a trusted host (`guardrails.is_trusted_link_target`): `*.go.ug`, `_DEFAULT_EXTRA_LINK_HOSTS` or `OUTPUT_LINK_ALLOWED_HOSTS`. Never add a redirector. Text a model reads from a third party goes through `scan_retrieved_text` or `scan_external_tool_text`; an upload is wrapped only after `vision.ocr.escape_untrusted_document_markers` (ADR-0029).
- A guard pattern change adds its attack to `App/Data/eval/redteam_corpus.jsonl` and anything lawful it nearly caught to `App/Data/eval/guardrail_benign_controls.jsonl`; both gates must pass.
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
- A change to the rate tables, `graph.build`, `kernel.model` or `kernel.rules` needs a release entry (`python -m app.kernel.release record ...`, App/docs/runbooks/kernel-release.md); CI fails otherwise. Figures reach answers through kernel functions or calculators, never from a model's own knowledge. Commit the snapshot a release writes; a penalty or interest rule stays `pending_tax_team_review` until a named reviewer approves it in a release. Keep `rules.py` and the `penalty_late_filing` calculator in agreement (parity test). Neo4j statements are module constants with parameters only; semantic-layer metrics are certified templates, never model-written SQL.
- A new or changed rate figure needs a `legal_basis` naming the document and page, and an entry in `app/tax/evidence/rate_evidence.json` (App/docs/runbooks/kernel-release.md). Answer templates in `calculator_router` read figures from the table (`_table_figures`); never type an amount into one. Read "today" with `tax.tables.kampala_today()`, never the UTC date.
