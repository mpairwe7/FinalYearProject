# URA Chatbot — Production Gap Analysis & Agentic AI Roadmap

> Companion to `App/README.md` (which documents Phases 1–16) and
> `docs/AGENT_ARCHITECTURE.md` (which documents the agent runtime).
>
> **This is the living gap register.** Dated proposals
> (`docs/URA_Chatbot_Roadmap_2026_Enhanced.md`,
> `docs/NEXTGEN_ARCHITECTURE_PROPOSAL_2026.md`) do **not** supersede it.
> Retrieval / agentic serving-path decisions: `App/docs/traceability/retrieval-agentic-upgrade-2026-08-17.md`.
> Document / PDF intake guards: `App/docs/traceability/document-pdf-guards-2026-08-17.md`.
> Prototype gaps + production gates: `App/docs/traceability/prototype-production-gates-2026-08-18.md`.
>
> This document tracks the **remaining gaps** — things that are
> not yet in the codebase and what it would take to close them.
> Gaps closed during Phases 14 A-D, 15, and 16 are marked **SHIPPED**.
>
> Audience: engineers planning the next release cycle + URA
> stakeholders evaluating what a full production deployment looks
> like.
>
> **Status legend:**
> - ⚪ — identified, not yet started
> - 🟡 — partially delivered (see notes on each row)
> - 🟢 — **shipped**

---

## 1. Current state (baseline)

Phases 1–16 are now implemented. Phases 1–13 delivered a hardened,
generic RAG chatbot. Phases 14 A-D, 15, and 16 added identity,
tool-calling, agentic routing, workflows, memory, audit, and speech.

The chatbot now ships with:

- **Hybrid retrieval** (Qdrant dense + BM25 RRF + cross-encoder rerank; optional HyDE on the dense leg via `FLAG_HYDE`)
- **Grounded generation** (`Sunbird/Sunflower-14B-FP8` via vLLM, spotlight markers,
token-aware trimming, structured-output option)
- **OWASP LLM Top 10 (2025) coverage** — prompt-injection guards,
PII redaction, system-prompt leakage detection, grounding checks
- **Distributed resilience** — Redis rate limit, Redis semantic cache,
circuit breakers around Qdrant and LLM, hard deadlines
- **Continuous evaluation** — Ragas-compatible harness, SLO alert rules
- **Next.js 16.2.3 + React 19.2 frontend** with glassmorphism UI,
SSE streaming, optimistic feedback, same-origin `/api` proxy
- **Full test pyramid** — ~2,600 backend/root pytest cases, frontend Vitest +
Playwright E2E, Flutter mobile CI, k6 load tests. The 35% `--cov-fail-under`
ratchet is **not** a quality claim: `pyproject.toml`'s omit list excludes
`main.py`, `llm.py`, `retriever.py` and `speech_service.py`, and the gated
`pytest tests/` run never executes `App/backend/tests/`, so the figure both
under- and mis-reports what is exercised. Treat the retrieval ranking gate
(§2.9) and the corpus coverage gate as the real quality instruments
- **Production observability** — Prometheus + Grafana + Jaeger (docker-compose
`--profile monitoring`), 5 SLO alerting rules, pre-built dashboards
- **Security-as-code** — cosign container signing, SLSA v1.2 provenance,
OWASP ZAP DAST, AI red teaming (50 NIST AI 600-1 prompts)
- **Compliance artefacts** — Model Card (EU AI Act Art. 53), PIA (NDPA §28),
bias audit, carbon tracking, incident response simulation
- **Auth system** — JWT (HS256/RS256), RBAC with 5 roles, consent
management (UDPA 2019), user profiles (`auth/` directory)
- **Tool-calling framework** — 25 registered tools, ToolRegistry,
generate_with_tools loop (`tools/` directory)
- **Supervisor routing** — 7 routes with per-specialist tool
whitelists (`agents/` directory)
- **Guided workflows** — 14 YAML-declared workflows with slot filling
(`workflows/` directory)
- **Ticket queue** — CRUD admin endpoints, escalation tool
- **Speech pipeline** — ASR (`Sunbird/asr-whisper-large-v3-salt`),
TTS (Spark-TTS-SALT), MT, Sunbird AI cloud fallback
- **Memory system** — semantic facts, episodic summaries, working
memory (`memory/` directory)
- **Audit ledger** — hash-chained, Merkle tree proofs (`audit/`
directory)
- **Feature flags** — 49 flags via `flags.py`, with percentage / cohort / allowlist rollout
- **MCP 2026-07-28** — spec `_meta`, `Mcp-Name` check, MRTR `inputRequests`,
  shared idempotency. Decision log:
  `App/docs/traceability/mcp-hardening-2026-08-17.md`.
- **PostgreSQL backend** option alongside SQLite

**What this is good at:** answering factual questions about URA
policy, performing tax calculations, routing to specialists,
walking users through guided workflows, and escalating to staff.

**What this is not good at yet:** unbounded multi-step planning
(the shipped loop is bounded ReAct: one observe hop + one reflect
retry), a **live** URA account API, a real email/SMS sender, or
applied Postgres RLS. Query-time document upload exists
(`POST /v1/documents/analyze`) with optional ClamAV + isolated parse;
production startup requires both (`docs/PRODUCTION_GATES.md`).

---

## 2. Gap analysis

The gaps below are grouped by domain, each with user impact, the
current workaround (if any), the recommended fix, and the code
surface that would need to change. Effort estimates are rough:
**S** = 1–2 days, **M** = 3–5 days, **L** = 1–2 weeks, **XL** = 3+ weeks.

### 2.1 Identity & personalization

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G1 🟢 | **~~No user authentication.~~** **SHIPPED Phase 14** — `auth/` directory with JWT (HS256/RS256), `jwt_auth.py` middleware, `dependencies.py` for FastAPI dependency injection. Bearer token auth on protected endpoints. | — | Done. | `backend/app/auth/jwt_auth.py`, `backend/app/auth/dependencies.py` | Done |
| G2 🟢 | **~~No user profile.~~** **SHIPPED Phase 14** — `user_profiles` table with taxpayer_type, locale, TIN, industry fields. `GET/PUT /v1/me/profile` endpoints. | — | Done. | `backend/app/database.py`, `backend/app/postgres.py`, `backend/app/models.py` | Done |
| G3 🟢 | **~~No consent / data-processing flow.~~** **SHIPPED Phase 14** — `consent_receipts` table with version, purpose, timestamp, withdrawal support. `GET/POST /v1/me/consents` endpoints. Compliant with Uganda Data Protection Act 2019. | — | Done. | `backend/app/database.py`, `backend/app/postgres.py`, `backend/app/models.py` | Done |
| G4 🟢 | **~~No role-based access.~~** **SHIPPED Phase 14** — 5 roles (`public`, `verified_taxpayer`, `ura_staff`, `ura_admin`, `ura_auditor`). `@requires_role` FastAPI dependency. Admin endpoints gated by role. | — | Done. | `backend/app/auth/dependencies.py`, `backend/app/main.py` | Done |

### 2.2 Memory & context

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G5 🟢 | **~~No long-term memory~~ across sessions.** **SHIPPED Phase 16** — `memory/` directory with three tiers: semantic facts, episodic summaries, working memory. Facts extracted with provenance and confidence scores; injected into system prompt at chat time. | — | Done. | `backend/app/memory/` directory | Done |
| G6 🟢 | **~~No topic persistence.~~** **SHIPPED 2026-08-17** — `conversation_topics` table (SQLite + Postgres) + catalog classifier in `topics.py`. Follow-ups inherit the current task; the prompt sees only the catalog label (never raw user text). `FLAG_AGENTIC_MODE` now defaults **on**, gated by `agentic_mode_gate()` (EN golden-set accuracy ≥ 0.95). | — | Done. | `topics.py`, `database.py`, `postgres.py`, `service.py`, `eval_routing.py` | Done |
| G7 🟢 | **No temporal grounding.** ~~The model doesn't know today's date, the current fiscal year…~~ **SHIPPED Phase 14-A** — `get_current_date` and `get_next_deadlines` tools return today's date, day-of-week, and the fiscal year computed from the date (Ugandan FY is 1 July–30 June; on/after 2026-07-01 that is `FY2026-27`), plus days-into-FY, days-remaining, and the next N deadlines. Soft retrieval preference for “this fiscal year” follows the same year via `current_fiscal_year()` / rate tables unless `CURRENT_FISCAL_YEAR` is set. | — | Done. | `backend/app/tools/calendar.py`, `query.py` | Done (S actual) |
| G8 🟢 | **~~Conversation store is not an audit log.~~** **SHIPPED Phase 16** — `audit/` directory with hash-chained, append-only audit ledger and Merkle tree proofs for tamper evidence. Separate from conversation TTL. | — | Done. | `backend/app/audit/` directory | Done |

### 2.3 Capabilities & actions

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G9 🟢 | **No tool use.** ~~The LLM can only generate text from retrieved passages.~~ **SHIPPED Phase 14-B** — `generate_with_tools()` in `llm.py` runs a bounded tool-call loop using Qwen chat-template tool formatting; `ToolRegistry` dispatches via `.call()`; 25 tools auto-registered. Flagged by `FLAG_TOOL_USE`. | — | Done. | Done. | Done (S actual) |
| G10 🟢 | **No calculators.** ~~PAYE, VAT, CGT, customs, income tax, effective rate.~~ **SHIPPED Phase 14-A** — deterministic calculators (`calculate_vat`, `calculate_paye` with progressive bands, `calculate_corporation_tax`, `calculate_capital_gains`, `calculate_customs_duty`) backed by **effective-dated** rate tables (`FY2025-26`, `FY2026-27`, …). A frozen “current year” default is a documented failure mode — see `App/docs/tax-rate-tables.md`. | — | Done. | `backend/app/tools/calculators.py`, `tax/tables.py` | Done (S actual) |
| G11 🟢 | **~~No structured form flows.~~** **SHIPPED Phase 15** — `workflows/` directory with 14 YAML-declared workflows loaded at startup via `loader.py`. Slot-filling state machine (`slots.py`), workflow registry (`registry.py`), keyed on `conversation_id`. | — | Done. | `backend/app/workflows/` directory | Done |
| G12 🟢 | **~~No URA account actions.~~** **PROTOTYPE 2026-08-18** — development defaults to mock (`live=false`). Settings shows the sandbox TIN. Production rejects mock. | Demo account works. | Mock ready. | Wire live `URA_ACCOUNT_API_*`. | `ura_account_mock.py`, Settings | XL |
| G13 🟢 | **~~No document ingestion.~~** **PROTOTYPE 2026-08-18** — upload + guards + optional isolated worker. Dedicated parse pool is post-prototype. | User can attach a receipt. | Demo ready. | Parse pool / gVisor later. | `documents.py`, `document_worker.py` | M |
| G14 🟢 | **~~No scheduled notifications.~~** **PROTOTYPE 2026-08-18** — Settings inbox + staff `/admin/outbox`. Email/SMS mock-queued, not sent. | Taxpayer sees inbox. | Demo ready. | SES / Africa's Talking later. | `notify.py`, `/admin/outbox` | M |
| G15 🟢 | **~~No URA live data.~~** **PROTOTYPE 2026-08-18** — offline fixture ingest when no https URL is set. Nightly workflow. **No auto-recreate.** | Demo ingest works offline. | Fixture ready. | Set a real publications URL. | `Data/eval/publications_fixture.txt` | S |

### 2.4 Knowledge gaps

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G16 🟢 | **~~Unstructured retrieval only.~~** **SHIPPED Phase 30** — `app/graph/` projects the effective-dated rate tables into an 87-node / 153-edge statutory graph. **2026-08-17:** REST and streaming fuse that graph as a third RRF leg (`rrf_fuse_ranked_lists` + calibrated `score_norm`) instead of prepending it. `FLAG_GRAPH_FUSION` / `FLAG_TAX_GRAPH` remain default **off** until the multi-hop golden set is expanded with unseen questions (shadow gate 75%). Fusion is rank-level, not passage-id / entity-linked. | — | Fusion **code** shipped; **production flag stays off**. | Expand the golden set from real traffic; extract prose provisions from the crawl behind review. | `graph/shadow.py`, `retriever.py`, `service.py` | Done (measurement pending) |
| G17 🟢 | **~~No metadata-aware retrieval.~~** **SHIPPED 2026-08-17** — `plan_retrieval()` extracts an explicit FY as a hard Qdrant filter and a mentioned tax type / "this fiscal year" as a soft boost (`current_fiscal_year()`). `search_planned()` is the shared caller (REST, stream, RAG tool, corrective RAG, LangGraph `node_retrieve`, voice speculative prefetch). LangGraph fuses the graph RRF leg when those flags are on and applies the same unbound-FAQ filter + exact-FAQ promote as REST. Hard filters do **not** fire on a bare calendar year (Ugandan FY is July–June). | — | Done. | `query.py`, `retriever.py`, `service.py` | Done |
| G18 🟢 | **~~No multilingual retrieval.~~** **SHIPPED 2026-08-17 (translate-retrieve).** The corpus stays English by design — the model translates the *answer*. `english_retrieval_query()` + `FLAG_TRANSLATE_RETRIEVE` (default on) merge a second hybrid pass on the English translation for non-`en` locales. FAQ keyword path already did this lazily. Routing (Phase 30) is unchanged. A multilingual re-index is **not** required and is not claimed. | — | Done (English index + generate-in-locale). | Optional later: multilingual dense if a Luganda corpus is added. | `query.py`, `retriever.search_planned`, `service.py` | Done |
| G19 🟢 | **~~No citation provenance.~~** **SHIPPED 2026-08-17** — crawl chunks store `url` / `crawled_at`. Hits and `Citation` surface `url`, `effective_date`, `title`. `canonical_source_url()` backfills `https://ura.go.ug` for `ura_*.csv/.pdf` when no deep link was indexed. UI prefers a stored https URL. | — | Done. | Optional per-notice deep links when crawl mapping exists. | `retriever.py`, `models.py`, `ChatMessage.tsx` | Done |

### 2.5 Agentic reasoning

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G20 🟢 | **~~No planning loop.~~** **SHIPPED Phase 15** — `agents/supervisor.py` with 7 routes, per-specialist tool whitelists, and a bounded tool-call loop (up to 3 iterations per request). Flagged by `FLAG_AGENTIC_MODE`. Full planner-executor (JSON plan, tree of thought) remains future work for Phase 17+. | — | Done. | `backend/app/agents/supervisor.py` | Done |
| G21 🟢 | **~~No ReAct / self-correction.~~** **SHIPPED 2026-08-17 (bounded).** Money answers are verified deterministically + one `RevisionBudget` rewrite. LangGraph is `act → observe → synthesize → reflect`. `node_observe` hands off once to `retrieve` when tools produced no usable evidence (`max_handoffs=1`). `node_reflect` re-retrieves once on low faithfulness **or** a reasoning miss (reply shares too few question terms), even if query expand is a no-op. `max_reflections=1`. Industry 2026 practice is MAX_ITER 2–3 — this is **not** unbounded ReAct. | — | Done (bounded). | Do not add unbounded critique-revise. | `service.py`, `evaluator.py`, `graphs/main_graph.py` | Done |
| G22 🟢 | **~~No per-specialty sub-agents.~~** **SHIPPED** — `agents/prompts.py` appends short tax / customs / tool specialist fragments to the shared base prompt (safety rules stay first). Supervisor still narrows tool whitelists per route. Remaining (optional): versioned YAML under `agents/prompts/` with hot-reload. | — | Done (in-code fragments). | Optional YAML split. | `backend/app/agents/prompts.py`, `supervisor.py` | Done |
| G23 🟢 | **~~No delegation between agents.~~** **SHIPPED 2026-08-17 (one hop).** Typed `handoff_*` fields on `AgentGraphState`. When a specialist/tool plan yields no usable observation, the graph hands off once to `retrieve` instead of synthesising an empty answer. Not free-form multi-agent chat — schema-validated, budgeted at one hop (2026 enterprise pattern). | — | Done (bounded). | Extra hops only with a measured quality gate. | `graphs/state.py`, `graphs/main_graph.py` | Done |
| G24 🟢 | **~~No per-user prompt tuning.~~** **SHIPPED 2026-08-17** — profile `detail_level` (`beginner` / `intermediate` / `expert`) appends a short instruction fragment via `detail_level_prompt()`. Intermediate adds nothing (base prompt already matches). Unknown values are ignored so a profile field cannot inject prompt text. Still consent-gated. | — | Done. | Optional industry/language fragments later. | `agents/prompts.py`, `service.py` | Done |

Runtime clarification (2026-09-29): the langgraph flag currently selects a
repository-owned, synchronous, request-scoped dispatcher with LangGraph-style
nodes; it is not the upstream LangGraph package and does not provide
checkpointing, durable resume, interrupts, or retries. It runs only when
`agentic_mode` is enabled, and tool dispatch additionally requires `tool_use`.
Graph fusion and tax graph remain off by default pending stronger held-out evaluation. See
RAG_ARCHITECTURE.md for the runtime boundary and fusion details.

### 2.6 Evaluation & quality

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G25 🟢 | **~~No per-segment quality metrics.~~** **SHIPPED 2026-08-17** — `EvalReport.by_segment` now has `topic`, `locale`, `taxpayer_type`, and `variant`. Groups smaller than 3 are omitted. Prometheus exposition already labels `segment_dim` / `segment`. | — | Done. | — | `evaluation.py` | Done |
| G26 🟢 | **~~No A/B testing.~~** **SHIPPED 2026-08-17** — rollout targeting was already in `flags.py`. Each chat/stream/voice/WS turn now persists `flag_variants` + `locale` on `conversations`. Eval reports `by_segment.variant` (e.g. `hyde:off`). | — | Done. | — | `flags.py`, `database.py`, `postgres.py`, `main.py`, `evaluation.py` | Done |
| G27 🟢 | **~~No drift detection on the index.~~** **Qdrant lifecycle shipped 2026-08-19.** The CPU image build triggers on every shipped corpus source and promotes a staged Qdrant collection through an alias only after validation. Local Compose runs the same lifecycle post-deploy. The freshness probe compares `corpus_hash` with the Qdrant sentinel via `--verify-qdrant`; `--notify` alerts on drift. Managed Vectorize remains a separately credentialed deployment step. | — | Local/embedded Qdrant done; Vectorize pending. | Retain previous versioned collection for rollback. | `index_lifecycle.py`, `freshness.py`, build workflow | In progress |
| G28 🟢 | **~~No red-team fixtures.~~** **SHIPPED 2026-08-18** — `Data/eval/redteam_corpus.jsonl` is a pytest gate (`test_redteam_corpus.py`). PurpleLlama / promptmap remain optional later. | — | CI refuse-all on the corpus. | Optional LLM-vs-LLM weekly. | `guardrails.py`, `test_redteam_corpus.py` | Done |
| G29 🟢 | **~~No human feedback loop into training.~~** **PROTOTYPE 2026-08-18** — preference export + `dpo_job.py` refuse-to-train. Fine-tune is post-prototype. | Pairs can be exported. | Export ready. | Axolotl/DPO behind the eval gate. | `evals/dpo_job.py` | XL |

### 2.7 Operations & multi-tenancy

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G30 🟢 | **~~Single tenant.~~** **PROTOTYPE 2026-08-18** — single-tenant demo. Predicate + RLS template exist; not marketed as multi-tenant. | Capstone is one tenant. | Demo ready. | Apply RLS for a platform later. | `tenancy.py` | L |
| G31 🟢 | **~~No admin UI.~~** **PROTOTYPE 2026-08-18** — flags + exact-match `/admin/overrides` + outbox. Not a full FAQ CMS. | Staff can correct one answer. | Demo ready. | Git-backed prompt editor later. | `cms.py`, `/admin/overrides` | M |
| G32 🟢 | **~~No human-in-the-loop queue.~~** **SHIPPED Phase 15 + staff workbench 2026-08-17** — claim → brief → reply → resolve, live `/v1/admin/tickets/stream`, collision presence, canned replies, first- and next-reply SLA with population breach counts. | — | Done. | — | `ticket_ws.py`, `ticket_presence`, `frontend/src/components/staff/` | Done |
| G33 🟡 | **SLO autoscaling (post-prototype).** Example HPA/KEDA YAML only. **Measured p95 exists 2026-08-19** (`App/docs/traceability/capacity-envelope-2026-08-19.md`, `docs/runbooks/capacity-slo.md`) — one A6000 vLLM, hybrid p95 &gt; 3s from 4 concurrent; FAQ/calculator p95 tens of ms; first API hard fail is `RATE_LIMIT` 429. | Autoscaler still not applied. | Partial (envelope measured; HPA not on). | Apply `infra/k8s/` only after agreeing the hybrid vs blended SLI. | `infra/k8s/`, runbook | M |
| G34 | **Cluster chaos (post-prototype).** In-process fail-closed tests shipped. | Not needed for a laptop demo. | Deferred. | Game day later. | `tests/chaos/` | M |

### 2.8 Production activation (2026-08-18)

Prototype rows above stay **demo-ready**. They become **start blockers**
when `APP_ENV=production`. The gate list and operator checklist live in
`docs/PRODUCTION_GATES.md`. Probe:

```bash
PYTHONPATH=App/backend python3 -m app.production_readiness --as-production
```

`FLAG_HYDE`, `FLAG_GRAPH_FUSION`, `FLAG_TOOL_RAG`, and `FLAG_TOOL_USE`
stay default **off**. Do not treat a green production start as a live
URA account or a delivered SMS channel.

### 2.9 Serving-path defects the eval sets could not see (2026-09-01)

Both were found by running `tests/load/tax_education_*` (outputs in `Results/load/`) against a live
deployment, not by the suite: **77.0%** single-query accuracy and **29.1%**
multi-turn coherence while all ~2,600 tests passed. The common cause is that
every eval set in this repo asks questions in the corpus's own voice — short,
FAQ-shaped, one clause — so neither defect was reachable from any gate.

| # | Defect | Evidence | Outcome |
|---|---|---|---|
| G35 🟢 | **~~Situational preamble dilutes the FAQ match.~~** `_faq_match_score` divided coverage by the terms the *user* supplied, so context lowered the score of the row that answers the question. "I am opening a hardware store in Jinja. Do I have to charge VAT?" scored **0.273** against the 0.58 floor; the bare question scored **0.700**. The answer is in `ura_vat_faqs.csv` throughout. | Reproduced against the 516-row FAQ index. | **Fixed 2026-09-09 (`service.py`), in the scorer as this row concluded it had to be.** Coverage is now computed over the *question span's* terms on both sides of the ratio, so a row is neither charged for situation it cannot cover nor paid for situation it can — and nothing here reaches retrieval, which is what the reverted attempt got wrong. (The first version of this fix narrowed only the denominator, meaning to credit a row that also matched the context; it instead let situation terms substitute for question terms, and a row about hardware-store licences in Jinja scored 0.7955 on "Do I have to charge VAT?" — above the row that answers it. Caught by CodeRabbit on #487 and pinned by `test_a_row_matching_only_the_situation_is_rejected`.) Ungating `extract_question_span` on the *retrieval* query fixed it on a stale 729-doc index and then *cost* 37 points of VAT-journey fact coverage (81.2% → 43.8%) once the index was rebuilt to 7,970 docs: the FAQ scorer only decides the answer when retrieval has fallen back to keyword matching, and against a healthy dense index the preamble is useful context. Subject **focus** therefore keeps the whole query — narrowing that too was tried here and reverted, because it makes `question_recall` trivially 1.0 and trips the focus gate, and five FAQ rows stopped retrieving their own question ("Bona fide changing residence – what is exempt?" narrows to "what is exempt?"). **0.273 → 0.640**; the residual 0.06 against the bare question is the focus term. The distress narrowing stays, but no longer for this reason — it shapes `binding_query` and the distress path. `test_situational_preamble_retrieval.py` and `test_distress_retrieval_query.py` both had a test that *characterised* the defect; each now guards its absence and says so where it stands. |
| G36 🟢 | **An open workflow swallowed every following question.** `_maybe_handle_workflow` returns before retrieval, so while a flow was active the corpus was unreachable and any question was fed to the slot validator. Only exits were completion or six literal cancel words. | Measured PAYE journey: all three turns were non-answers (fact coverage **0.0%**). | **Shipped.** Divert to normal retrieval when the message reads as a question *and* the pending slot cannot accept it, so a mistyped answer still re-asks. PAYE fact coverage 0.0% → **44.4%**. `WorkflowRegistry.pending_step`, `ChatModel._workflow_input_changes_subject`, `tests/test_workflow_topic_change.py`. |
| G37 🟢 | **The accuracy harness could not measure what it claimed.** Journey "coherence" was `len(matched_kw) > 0` — one expected keyword as a bare substring, anywhere in the reply. Abstentions and workflow slot prompts scored as good answers; language fidelity used `"ura"` as both a Luganda and a Kiswahili marker, so plain English passed every time; `"150"` matched inside `"1,500,000"`. | A company-incorporation forms page outscored a real VAT-threshold answer. | **Fixed in `tests/load/tax_education_accuracy_eval.py`**: token-boundary matching, non-answer detection, majority-coverage rule, graded per-turn `keyword_coverage_pct`, and language markers that do not occur in English. Corrected figures: coherence 70.8% → **41.6%**, lg 71.6% → **56.6%**, sw 73.3% → **58.3%**. |

**G37 came back (2026-09-09).** Every correction in that row — token-boundary
matching, non-answer detection, language markers that do not occur in English —
was reintroduced as a defect in `scripts/evaluate_1000_faqs_ngrok.py`, a harness
written after the fix and never told about it. `"ura"` was again a marker for
both Luganda and Kiswahili. See G57. The fixes now live as properties with a
gate on them in `tests/test_benchmark_scoring_integrity.py`, in the tree CI runs,
rather than as corrections inside one harness that the next harness cannot see.

### 2.10 End-to-end evidence on the full GPU stack (2026-09-01)

Measured against the real serving stack — Sunflower-14B-FP8 on vLLM (one
A6000), hybrid Qdrant retrieval over the rebuilt 7,970-document index,
cross-encoder rerank — not the keyword fallback the CI gates use.

| Measure | Keyword fallback | **Sunflower-14B-FP8** |
|---|---|---|
| Single-query accuracy | 67.2% | **75.4%** |
| Multi-turn coherence | 66.7% | **83.3%** |
| Luganda fidelity | 56.6% | **85.0%** |
| Kiswahili fidelity | 58.3% | **100.0%** |
| VAT topic accuracy | 65.4% | **86.2%** |
| p95 latency | 2.7s | **17.0s** |

`corpus_coverage --mode api` over the 105-probe taxpayer-voice bank in all
three languages (`Results/load/` + `/tmp/cov_e2e.json`): **zero abstentions
in 186 probes**, English coverage 90.3%, but **overall 79.25% against an 80%
floor — the gate fails**, and two domains sit well below theirs: `objections`
38.5% and `tin` 46.2% (floor 60% each).

Two findings a government deployment has to weigh:

| # | Finding | Status |
|---|---|---|
| G38 🟢 | **The workflow escape was English-only.** 80 of 186 probes returned a guided-flow slot prompt instead of an answer, *all* of them Luganda or Kiswahili. None opens with an English question word, so G36's escape could not fire for them — the escape stranded exactly the users this system exists to serve. Now language-neutral: an English interrogative opener **or** a trailing `?` on a message of ≥3 words, which is what carries across locales. The word-count floor keeps a hedged `"individual?"` with the validator. No local-language vocabulary was invented — `app.agents.patterns` deliberately refuses that without native-speaker review. | **Fixed.** `_reads_as_question`, `tests/test_workflow_topic_change.py`. |
| G39 🟢 | **~~The workflow router is too eager in local languages.~~** The 80 probes above entered a flow on their *first* turn — a question was classified as a task. G38 lets them leave; it does not stop them being captured. Luganda/Kiswahili coverage (63.6% each) is ~27 points below English (90.3%) and this is the largest single cause. | **Fixed 2026-09-09 (`service._maybe_handle_workflow`).** The cause is a pair of asymmetries, and neither is in `match_trigger`. `match_trigger` is tried against the query's English translation as well as the taxpayer's own words, so a local-language question reaches the English trigger phrases — while `_INFORMATIONAL_WORKFLOW_QUERY_RE`, the escape that keeps an informational question out of a flow, is an English word list ("how do I", "what are the steps", "procedure"). The trigger could fire and the escape could not. G38 had already established the predicate that carries across locales, so the entrance now uses the same one: `_reads_as_question` on the taxpayer's own words (the translation is already covered by the existing regex). Measured before and after — "Can I file a nil return?", "Can I pay my tax today?" and "Must I file an objection?" stop being captured; "File my return" and "Help me file my return" still start a flow. No locale-specific thresholds and no invented vocabulary, so no per-locale golden set is required to gate it. `test_workflow_entry_locale_neutrality.py`. |

p95 of 17.0s is a service-design question, not a defect: single A6000, `--max-num-seqs 64`, no batching tier. `docs/runbooks/capacity-slo.md` holds the measured envelope.

### 2.11 Promotion re-verification on the rebuilt index (2026-09-02)

Re-run before promoting `dev` to `main`. Same stack shape as §2.10 — Sunflower-14B-FP8
on vLLM (GPU 5), hybrid Qdrant + cross-encoder rerank (GPU 6) — against an index
rebuilt from scratch after the 2026-08-31 `ura.go.ug` crawl: **7,972 documents**
(509 FAQ, 6 teacher-QA, 6,966 PDF chunks, 491 crawl chunks), alias promoted only
after the canary gate passed 3/3 at hit-rate 1.0.

| Measure | §2.10 | **This run** |
|---|---|---|
| Overall accuracy | 75.4% | **82.0%** |
| Intent precision | — | **91.7%** |
| Multi-turn coherence | 83.3% | **83.3%** |
| English | — | **78.3%** |
| Luganda | 85.0% | **90.0%** |
| Kiswahili | 100.0% | **100.0%** |
| VAT topic | 86.2% | **95.0%** |
| PAYE topic | — | **78.3%** |

The accuracy gain is **not** a model or retrieval improvement. It is the
measurement being corrected: see G40. Cold-run latency was p50 0.360s / p95
11.6s; the re-run's p50 0.053s reflects a warm semantic cache and should not be
quoted as a latency result.

Grounding held: every substantive answer carried sources (`hybrid`, 3–4 each,
faithfulness 1.0). The G38 workflow escape was re-confirmed working in all three
languages against the live stack. Three further findings:

| # | Finding | Status |
|---|---|---|
| G40 🟢 | **The accuracy harness scored the service against superseded law.** It required the FY2025-26 VAT registration threshold (UGX 150,000,000) and PAYE nil-band ceiling (UGX 235,000); the 2026 Finance Acts moved these to 300,000,000 and 335,000. The service answered both correctly from the FY2026-27 table it cites and was scored 45.0% and 35.0% for it, dragging the published overall number down ~6 points. VAT journey turn 2 was worse than a stale figure: at a 300m threshold its 180m scenario stops being over the line, so the turn silently began testing the opposite behaviour. | **Fixed.** Figures refreshed, scenario raised to 350m, and `tests/test_eval_ground_truth_currency.py` re-derives them from the newest `FY*.json` so they cannot rot again. |
| G41 🟢 | **Jurisdiction was ignored on the rate-table path.** "What is the corporate income tax rate in **Kenya** for 2026?" returned "**The corporation tax rate in Uganda is 30%** … from the official URA FY2026-27 rate table"; reproduced for Rwanda and for the demonym ("the Kenyan VAT rate"). The deterministic paths matched on the tax word alone. The answer was labelled Uganda, so it was never a false statement — which is what made it dangerous: a different question answered in the authoritative register, with a citation, on the path taxpayers most trust. | **Fixed.** `detect_foreign_jurisdiction` / `out_of_jurisdiction_reply` in `text_signals.py`, wired as fast path 0 in `_maybe_handle_fast_paths` — ordering *is* the fix, so the test asserts it and was mutation-checked. A message naming Uganda as well still answers, since refusing a comparison would withhold the half URA can speak to. Verified live: `retrieval_mode: out_of_jurisdiction`, no Ugandan figure quoted, and Uganda questions unaffected. |
| G42 🟢 | **Calculator flows captured factual questions.** "How much monthly income is exempt from PAYE in Uganda?" entered `calc_paye` and asked for a gross salary; "What will Uganda's VAT rate be in 2031?" entered `calc_vat` and asked for an amount. Both are questions *about* a figure, not requests to compute one, and neither got answered. Not local-language-specific — plain English. The guard belongs in `plan_calculation`, not at the workflow-entry site: the guided flow is opened downstream by `_maybe_handle_calculator` when a plan reports missing params, so a plan that is never formed is a flow that never opens. `_INFO_ONLY_RE` already existed for exactly this and had two gaps — it required `is/are/was/were`, so "what **will** … rate" slipped past, and it had no branch for "how much X **is exempt**". | **Fixed.** `_INFO_ONLY_RE`, `App/backend/tests/test_calculator_router.py::FigureLookupIsNotACalculationTests`. Verified live: both now answer (`hybrid` / rate lookup) while `Calculate PAYE for 3,500,000` and `I want to calculate my VAT` still reach the calculator. |

G41, G42 and G43 surfaced under targeted trust probes rather than the coverage
bank because all three return fluent, well-formed, confidently-cited replies —
exactly what a keyword-coverage scorer counts as success. None of them is a
regression from this promotion; all three predate it and are recorded here so
the promotion does not imply they were cleared.

**Re-run after fixing G42** — 22 trust probes, 3 failing (was 5 of 14):
calculator-entry 8/8 including the controls that must still reach the
calculator, figures 4/4, grounding 3/3, workflow-escape 3/3 in en/lg/sw. The
three that remain are all abstention, and they share one root: the service
answers a *neighbouring* question with full confidence instead of declining.

| # | Finding | Status |
|---|---|---|
| G43 🟢 | **A non-existent tax was invented, with figures.** "What is the URA Digital Nomad Levy and how do I pay it?" — a tax that does not exist — returned "a tax of **1% of the monthly gross income** for digital nomads … who operate in Uganda for **90 days or more**", plus a registration and monthly-declaration procedure. In `hybrid` mode, citing 2 sources. The same question **earlier in the same session** correctly refused: "the figures in it disagreed with the URA documents I was reading — so I have not shown it … a URA officer has been asked". So the integrity check that should catch this **fires non-deterministically**, which is worse than not having it: it cannot be relied on and it makes the failure hard to reproduce in review. Highest-severity finding in this document. | **Fixed** (`premise_guard.py`, `service.py`, Issue #441). Deterministic epistemic false-premise validation (`check_false_premise`) checks asserted statutory tax/levy concepts against legitimate Ugandan tax heads and the retrieved knowledge base. Fictitious instruments (e.g. "URA Digital Nomad Levy", "Space Exploration Tax", "Moon Mining Duty") are affirmatively refuted before generation, directing taxpayers to legitimate tax heads and URA offices, while preserving natural conversational queries ("how do I pay tax"). |
| G45 🟠 | **The temporal half of G41 is still open.** "What will Uganda's VAT rate be in 2031?" answers "The standard VAT rate in Uganda is **18%** (FY2026-27)" with no caveat that 2031 is not FY2026-27. Same shape as the geographic case — scope substitution — and the jurisdiction guard does not cover it because the country named *is* Uganda. | **Open.** The rate path should treat a fiscal period outside the loaded table the way it now treats a foreign country. |

**Deployed-Space verification.** The local GPU stack is not the shipping shape —
the HF Space runs a Qdrant sidecar over a sparse-only collection with no torch —
so the FAQ battery was re-run against `https://landwind22-ura-chatbot.hf.space`
directly. **11 of 12 pass**, including both local-language VAT probes and the
G42 fix, which is live there. The one failure:

| # | Finding | Status |
|---|---|---|
| G44 🟢 | **PAYE threshold questions asked in taxpayer language returned the *previous* year's figure.** Originally logged as "the bands question returns a passage instead of the bands". Re-probed on the Space 2026-09-02, the defect is worse than that: it is a wrong number, not a missing one. `plan_rate_lookup` gated on a "rate"/"threshold" word **and** a named tax, so *"How much of my salary is tax free?"* and *"At what monthly salary do I start paying PAYE?"* failed the gate, fell through to `hybrid` retrieval, and were answered **UGX 235,000** from superseded handbooks (`TAXATION-HANDBOOK-FY-2024-25`, `FY-2025-26`, a Pharmaceutical Manufacturers PDF). `FY2026-27.json` says **335,000** — the tax-free line was quoted 100,000 UGX/month too low. *"What are the PAYE rates?"* was correct throughout, which is exactly what hid it. | **Fixed** (`calculator_router.py`). Three changes: (1) `_PAYE_THRESHOLD_ASK_RE` accepts the tax-free/start-paying phrasings, every alternative anchored on employment-income vocabulary so no turnover or rental question can reach the PAYE bands; (2) the ask gate now accepts `bands` alongside `rates`/`thresholds` — the type table already carried an `income tax band` alternative that the ask gate never let through; (3) that alternative was singular, so the plural nobody writes as "band" never matched. Guarded by `SalaryThresholdIsARateLookupTests`, mutation-checked. |

| G46 🟢 | **A comparison question was answered half-way, silently.** Raised by CodeRabbit on PR #428 and reproduced: `detect_foreign_jurisdiction` returns `''` when Uganda is named alongside another country, deliberately, so *"What is Uganda's VAT rate compared with Kenya's?"* is answered rather than refused — the half URA can speak to is worth more than a blanket decline. Its docstring said the caller adds a scope caveat in that case, and no caller did. The reply gave Uganda's 18%, cited the URA rate table, and never mentioned Kenya, which reads as an answered comparison. The G41 guard made this possible by making the decline path exist at all; it is the seam between refusing and answering. | **Fixed** (`text_signals.py`, `service.py`). `detect_comparison_jurisdiction` is the exact complement of `detect_foreign_jurisdiction` — one fires or the other, never both — and `_add_comparison_scope_caveat` appends the caveat at the fast-path dispatcher rather than inside each handler, so the rate, calculator and TIN paths cannot drift apart on it. `ComparisonScopeCaveatTests` includes a wiring test that reads the dispatcher source: the other five tests call the helper directly and all stayed green when the dispatcher call was deleted. **The first version over-fired** — keyed on "Uganda AND another country" alone, it caveated *"I import goods from Kenya to Uganda — what customs duty do I pay?"*, a legitimate and fully answerable URA question, with a line about not being able to give Kenya's side of a comparison nobody asked for. Cross-border trade questions name other countries constantly and outnumber real comparisons, so the detector now also requires an explicit comparison marker (comparative adjectives only — "I import **more than** 100 units" is a quantity). |

| G47 🔴 | **A customs question is answered with the contact footer and nothing else.** Found while verifying G46 on the deployed Space (`sha-bc4d715`, 2026-09-02). *"I import goods from Kenya to Uganda — what customs duty do I pay?"* returns, in full: *"If you get stuck at any step, URA is happy to help: visit https://ura.go.ug, call toll-free 0800 117 000 / 0800 217 000, or WhatsApp 0772 140 000."* Mode is `hybrid`, so retrieval ran and produced no usable passage, but the reply is not an abstention — it carries no "I couldn't find" marker, so `_is_non_answer` does not classify it and the accuracy harness would score it as an answer that simply missed its keywords. Predates the G46 work: the caveat was being appended to this same footer before the over-fire was narrowed. | **Open.** Two separable problems: retrieval finding nothing for a common cross-border question (the corpus has customs material), and a footer-only reply being emitted at all instead of an abstention. The second is the more dangerous — a reply with no content and no abstention marker is invisible to every gate that keys on abstention. |
| G48 🟢 | **FAQ retrieval evaluated to low faithfulness (0.00) with unwarranted human escalation.** Stored vector hits carried the passage as \`text\` without top-level \`question\` and \`answer\` fields. \`_faq_match_score\` evaluated to 0.0, causing \`_filter_unbound_faq_hits\` to drop the exact FAQ match from candidate hits, leaving only handbook PDF chunks. The model generated answers unsupported by those PDF chunks alone, dropping faithfulness to 0.00 and triggering \`! Human review recommended — low_faithfulness=0.00\`. | **Fixed** (\`retriever.py\`, \`service.py\`, \`providers/vectorize.py\`, \`reindex_vectorize.py\`). Extracted QA pairs from \`text\` when missing, promoted exact FAQ matches (\`faq_question_equivalence >= 1.0\`) to index 0, backfilled missing fields in step 3c FAQ blend, and added \`_short_circuit: True\` to cached turns in \`generate_retrieval_only\` so cache hits stream properly. |
| G49 🟢 | **Official URA contact emails were masked by the PII sanitizer.** Taxpayers asking for URA contact details received \`Email:[REDACTED_EMAIL]; [REDACTED_EMAIL] | https://ura.go.ug\` because \`redact_pii_text\` matched every email address without domain awareness. | **Fixed** (\`guardrails.py\`, \`text_signals.py\`). Added \`is_official_ura_email\` exempting \`@ura.go.ug\`, \`@*.ura.go.ug\`, and \`@go.ug\` from PII redaction, restored legacy \`[REDACTED_EMAIL]\` markers to \`services@ura.go.ug; info@ura.go.ug\` in \`OutputGuard.sanitize\`, and added official email to \`CONTACT_FOOTER\`. |
| G50 🟢 | **Multi-turn coreference misbinding and false contradiction withholding.** Multi-turn queries suffered three failure modes: (1) \`rewrite_with_history\` replaced pronouns with the previous turn's topic even when the query introduced its own active subject (*"What is EFRIS and would I be required to use it?"* became *"What is EFRIS and would I be required to use Value Added Tax (VAT)?"*, triggering false-premise rejection on "use Value Added Tax"); (2) \`numeric_contradiction\` compared user-supplied scenario figures (*"turnover of 80m"*) against statutory limits (*"150m"*) and falsely withheld answers with \`CONTRADICTED_CLAIM_REPLY\`; (3) texting shorthand \`wht is\` expanded to Withholding Tax. | **Fixed** (\`query.py\`, \`premise_guard.py\`, \`entailment.py\`, \`claim_verifier.py\`). Bound pronouns to intra-sentential subjects first, added action verbs to \`_STOP_AND_ACTION_WORDS\`, excluded user query amounts from model-introduced contradiction checks, and disambiguated \`wht is\` to \`what is\`. |
| G51 🟢 | **Jurisdiction guard refused questions containing the pronoun "us" as United States questions (#434).** \`text_signals.py\` matched \`\\busa?\\b\`, causing ordinary taxpayer questions like *"can you help us with vat registration"* or *"tell us about paye rates"* to trigger a foreign jurisdiction refusal naming the United States. | **Fixed** (\`text_signals.py\`). Required positive evidence for the country reading (unambiguous spelling, determiner *"the US"*, or specific nouns like *"federal"*, *"irs"*, *"citizens"*); ensured \`_PURE_FOREIGN_TAX_RE\` matches *"the us"* rather than bare *"us"*; guarded by \`UsPronounIsNotTheUnitedStatesTests\`. |
| G52 🟢 | **FAQ retrieval lexical brittleness on natural paraphrases & VAT workflow hijacking (#430).** Natural taxpayer paraphrases (*"how do i work out how much my lorry is allowed to carry"*, *"what papers does a foreign business need..."*, *"can i keep my business books in french..."*, *"what happens if i move goods out of a free zone without permission"*) yielded 0 hits and abstained, while verbatim twins succeeded. Additionally, questions containing declarative premises (*"my business is registered for vat, do i have to use efris"*) were hijacked by the turnover-elicitation workflow. | **Fixed** (\`calculator_router.py\`, \`service.py\`). Added declarative premise check \`_ALREADY_REGISTERED_RE\` in \`calculator_router.py\`; added English functional prepositions, quantifiers, and conversational verbs to \`_STOP_WORDS\` / \`_FAQ_QUERY_STOP_WORDS\`; implemented domain phrase normalizations \`_FAQ_PHRASE_SYNONYMS\` and \`_FAQ_TERM_ALIASES\`; added automatic synonym-expansion retry in \`_simple_search\`; guarded by \`test_natural_user_paraphrases_match_indexed_faqs\`. |
| G53 🟢 | **PDF corpus chunks leaked page furniture, running headers, and scrambled marginal text into extractive answers (#436).** Extractive fallback answers displayed raw page furniture (e.g. running headers *"East African Community Customs Management"*, revision markers *"[Rev. 2009"*, page numbers, blockquote marginal notes, and list emphasis artifacts *( _a_ )*). Moreover, concise curated FAQ answers (<120 chars) were diluted by appending raw statutory PDF chunks. | **Fixed** (\`service.py\`, \`pdf_corpus.py\`). Added extraction and runtime cleaning for marginal blockquotes, revision tags, legal act headers, isolated page numbers, all-caps titles, and list item markdown italics; tightened FAQ excerpt threshold from 120 chars to 40 chars in \`_build_grounded_revision\` so concise FAQ answers are never diluted; guarded by \`test_pdf_page_furniture_and_marginal_notes_cleaned\` and \`test_concise_faq_answer_is_not_diluted_by_second_chunk\`. |
| G54 🟢 | **Bantu decoder agglutinative loops and repetition in vLLM / local generation.** Autoregressive decoders on agglutinative Bantu genitive chains (*"ogw'omusolo ogw'okubonereza..."*) fell into low-perplexity repetitive sequences; measured 2026-09-04, a Luganda penalties question returned 1,330 characters of one repeated n-gram. | **Fixed** (`llm.py`). Graded penalties on both paths (`LLM_REPETITION_PENALTY = 1.1`), plus `LLM_MIN_P = 0.08` and `LLM_PRESENCE_PENALTY = 0.05` on the vLLM path. **Corrected 2026-09-09:** the original entry claimed `LLM_NO_REPEAT_NGRAM_SIZE = 6` as part of the served fix. It is not — vLLM's `SamplingParams` has no such field, so it reached only the local Transformers fallback, and its code default, `.env.example` and the docs disagreed three ways. It now defaults to 0 everywhere: a hard block on every repeated 6-gram forbids the phrase and citation repetition that correct statutory answers contain. Note also that at `LLM_TEMPERATURE = 0.2` min-p rarely binds; the loop-breaking on the served path is carried by the penalties. |
| G55 🟢 | **Machine translation rewrites statutory figures, and the guard against it costs the taxpayer their language.** MT is paraphrastic: a reply saying "UGX 235,000" comes back saying "UGX 253,000". `figures_survived` caught that and served English instead — never a wrong number, but a vernacular speaker got an English wall of text. | **Fixed** (`mt.py`, `service.py`). Figures are now masked behind opaque sentinels *before* translation (`protect_figures`) and restored after (`restore_figures`), so the translator is never shown a digit it could paraphrase. Only the digits are masked; the currency code and percent sign stay visible because they are the cue the target language needs ("ebitundu 18 ku buli kikumi"). A tier that cannot carry the sentinels is retried unprotected, so protection can only add vernacular coverage, never remove it; `figures_survived` remains as the assertion and English remains the fallback when a figure is dropped outright. **Supersedes the 2026-09-09 `heal_vernacular_figures` entry:** repairing MT output after the fact meant guessing where a number belonged, and guessing wrong wrote a figure into a sentence that never had one (found by CodeRabbit on #481, narrowed in #482). Narrowing it until it could not guess left it unable to fire at all — a figure only counted as missing once its digits were absent, and every insertion path it had left required those digits to be present, so the function was unreachable in all nine of its realistic input shapes. Masking before the fact needs no guess. |
| G56 🟡 | **Unstructured prompt prose, and a statutory projection that sat behind its own prompt defences.** Complex legislative paragraphs degraded translation precision on non-English queries. `extract_statutory_context` was added to project statutory figures into the prompt, and re-read `p["text"]` raw — after `_build_messages` had already scrubbed the same passages with `scan_retrieved_text` and trimmed them to the token budget. A figure planted inside an injected span was therefore mined for the prompt after the passage body carrying it had been redacted, and a figure trimmed away for budget was projected with no passage left to support it. Its slots also carried no entity label and no passage marker, so several rates in context arrived as an unattributed menu, and `slots[:5]` truncated silently. | **Fixed** (`llm.py`). The projection now takes the *prepared* `(citation index, text)` pairs — the scrubbed, trimmed text the model actually sees — so it can no longer outrun either defence. The block became a `## Figure cross-check`: every figure carries the citation index of each passage that states it, and the model is told not to state a figure absent from the list or attribute one to a passage it is not listed against. Only the figure and its indices are emitted — never a quotation of the surrounding prose, which would move attacker-controlled text outside the `<passage>` spotlight isolation for attribution the index already gives. Ordering follows retrieval rank and truncation is declared rather than silent. Guarded by `test_statutory_projection.py`. **Resolved by G57 (2026-09-09):** the suspicion recorded here — that the cross-lingual eval stemmer in `scripts/evaluate_1000_faqs_ngrok.py` was loosened in the same series that reports the accuracy gains it scores — was correct and understated. The stemmer was one of four defects, and the locale scores it produced were capped near 30% by construction. No number that harness published before 2026-09-09 is comparable to one it publishes after; a re-run under the new scorer is the only baseline. |
| G57 🟢 | **The 1,000-FAQ benchmark could not measure the locales it reported on.** `scripts/evaluate_1000_faqs_ngrok.py` published "English 73.14%, Luganda 29.70%, Kiswahili 28.06%" and the 43-point gap was read as a model defect. Luganda and Kiswahili answers were scored against **English prose keywords** scraped off the English source answer — the first four words of four or more characters — plus the anchors `["omusolo", "ura"]`, matched as bare substrings. `"ura"` is therefore true of `"accurate"`, `"natural"` and `"insurance"`, and `"150"` matches inside `"1,500,000"`. Running the harness's own corpus builder, the highest score a **perfectly translated** Luganda answer set could reach was **30.4%** against a 100% ceiling for English; the reported 29.70% was 98% of that maximum. The 200 multi-turn locale turns carried English-only ground truth and had a ceiling of **0.4%**. A reply that failed to translate scored better than one that succeeded. Three further faults: non-answers were unscored and a `"how much"` elicitation floor awarded them 0.75; the accuracy mean excluded every non-200, while p90 sat at the 60s client timeout; and all 1,000 queries were English text with only the `locale` field varying, so `FLAG_TRANSLATE_RETRIEVE`, the query rewriter and `WorkflowRegistry.match_trigger` never saw a vernacular question and **G39 was unreachable from this harness**. Every defect except the last is one **G37 had already fixed** in `tests/load/tax_education_accuracy_eval.py`, reintroduced in a harness written afterwards — that file's own comment records `"ura"` being a marker for both languages "and every reply names URA". | Ceiling re-derived from the builder, 2026-09-09. | **Fixed 2026-09-09.** A non-English locale is now scored only on what a correct answer in that language contains: vernacular terms matched allowing noun-class inflection, English terms that have a vernacular rendering in `CROSS_LINGUAL_CONCEPT_MAP` scored on that rendering, locale-invariant acronyms (`VAT`, `EFRIS`, `TIN`) that reviewed Luganda answers keep in English, and the figures and citations that do not translate. An English prose term with no vernacular rendering **leaves the denominator** rather than counting as a miss — its absence is what a correct translation looks like. Ceiling is now 100% in all three locales and an English fallback scores under 7% and is flagged. Non-answers score 0.0 with language-neutral slot-prompt detection (G38's lesson: an English-only test strands the locales it serves); non-200s enter a second reported denominator; an item the harness cannot weigh is declared `scorable=False` rather than recorded as a model failure. Twelve Luganda and four Kiswahili probes now ask **in-language**, read from `Data/eval/rag_eval_lg.jsonl` and the reviewed probes already in the repo — no vocabulary is invented, per `app.agents.patterns`. `tests/test_benchmark_scoring_integrity.py`. |
| G58 🟢 | **Three properties claim verification relied on were never re-checked after translation.** Answers are generated and claim-verified in English and translated on the way out (`service.localize_reply`), so every conclusion the verifier reached describes the English draft. `mt.protect_figures` prevents a digit being paraphrased and `figures_survived` compares the digits — and nothing looked at the **units**, the **length**, or the **citation markers**. `protect_figures` masks digits and deliberately leaves the percent sign and currency code visible (they are the cue the target language needs for "ebitundu 18 ku buli kikumi"), so "18%" arriving as a bare "18" kept every digit and passed. The collapse guard was `len(candidate) < max(12, len(text) // 10)` — a floor at one tenth, which passes a translation that dropped nine tenths of the answer; it catches a collapsed response and not a truncated one, and a truncated answer reads as complete while omitting the taxpayer's obligations. And `claim_verifier` keys off the `[n]` markers to decide whether a claim was supported at all, so a translation that drops or renumbers one ships an answer whose provenance no longer matches the report that approved it. | — | **Fixed 2026-09-09 (`mt.py`, `service.py`).** `units_survived` fires only on total loss of a marker kind, because `figures()` pools categories precisely to tolerate one rate rendering as a word. `length_plausible` uses a floor **measured, not chosen**: across the 23,838 aligned human pairs in `Data/online_corpora/salt/`, one in a thousand falls below 0.449 (en→lg) and 0.434 (en→sw), and below 0.4 lies 0.04% — `MT_MIN_LENGTH_RATIO=0.35` sits under both, and a whole answer concentrates harder around the median than any single pair. `citations_survived` is set equality, not order: a marker that moved with its clause is still attached to the right claim. Each failure carries its own metric and log line rather than sharing `figures_changed` — right digits with a lost unit is a different fault needing a different fix. `test_localization_integrity.py`. **Still open:** semantic drift inside the translated prose — a flipped negation, a dropped condition — is not covered. Detecting it needs entailment over the localized text and a per-locale golden set to gate it; the guards above cover the mechanical harms only. |

| G59 🟢 | **The 100-FAQ multimodal benchmark had 4 English misses on the hybrid tail, with leaky gold labels, missing rate-table heads, unhandled municipal tax jurisdiction, and brittle FAQ priority routing.** Identified on the 100-FAQ battery (88.2% EN, 73% LG/SW): FAQ-EN-15 (mobile money withdrawal excise) retrieved stale 2021 PDF chunks; FAQ-EN-16 (late-filing penalty) used gold "TIN" rather than statutory TPCA figures (UGX 200,000 / 2%) and withheld on draft disagreement; FAQ-EN-24 (local service tax) is a municipal council levy outside URA mandate that withheld; FAQ-EN-28 (presumptive turnover threshold) missed `_priority_faq_hits` because only TIN and return filing had regex triggers. | — | **Fixed 2026-09-16 (`FY2026-27.json`, `calculator_router.py`, `service.py`, `text_signals.py`, `retriever.py`, `run_100_faqs_multimodal_ngrok.py`).** (1) Added `excise_duty_mobile_money_withdrawal` (0.5%), `presumptive_tax_lower_threshold` (10m), `presumptive_tax_upper_threshold` (150m), `penal_tax_late_filing_monthly_rate` (2%), and `penal_tax_late_filing_minimum_ugx` (200k) to `FY2026-27.json`, `FY2025-26.json`, and `calculator_router.py` fast rate lookups; (2) added local government / municipal tax jurisdiction handler (`detect_local_government_tax` / `local_government_tax_reply`) clarifying LST is collected by local councils/KCCA under the Local Governments Act; (3) expanded `_priority_faq_hits` for small business and presumptive tax queries; (4) added recency discount for stale 2020–2022 PDF chunks in `retriever.py`; (5) corrected gold labels in `scripts/run_100_faqs_multimodal_ngrok.py` and scored legitimate abstentions/withholds separately. Guarded by `TestStatutoryGapFixes` and `NewRateLookupsTests`. |

| G60 🟢 | **A how-to question about one's own return opened an officer ticket.** Found by the journey probes on the local GPU stack (2026-09-29): "How do I file my return?" was escalated in 0.5 s with a ticket. The supervisor's account rule (`agents/patterns/en.py`) and the answer judge (`service._evaluate_response_judge`) fired on any `my return` / `my TIN` / `my account`, so a procedural question with a published answer went into the officer queue. | — | **Fixed 2026-09-29 (`agents/patterns/en.py`, `service.py`).** `HOW_TO_QUESTION_RE` ("how do I", "how to", "where can I", "guide me", "walk me through", "steps to", "procedure") exempts a how-to question in both places. "What is my balance?", "Has my return been received?", "My TIN is blocked" and "Please help me, my account is locked" still escalate; code review removed "help me" from the exemption for that last reason. `AccountEscalationTests`, `tests/test_guided_journeys_integration.py`. |

| G61 🟢 | **Guided journeys existed but could not be reached.** The live stack started a flow only for exact trigger phrasings: "Walk me through filing my VAT return" opened a VAT explainer (triggers were base verbs, "file a return"); no answer to "How do I …" ever mentioned that a step-by-step guide existed; and there was no journey for a Tax Clearance Certificate or vehicle registration (on the Space, "Guide me through registering my imported car" abstained). | — | **Fixed 2026-09-29 (`workflows/registry.py`, `turn_guidance.py`, two flows).** `match_trigger` normalises verb forms and the tax type before "return" and matches a flow's own name. `apply_turn_guidance` adds `Guide me step by step through <flow>` to an answered question whose task a flow covers (also to an abstention); a click starts it. New `tax_clearance` and `motor_vehicle_registration` flows, from URA's TCC page and the Ministry of Works digital-plates PDF (May 2026); two live-verified links. The G39 entrance rule is unchanged: "I need a tax clearance certificate" is answered and carries the offer. Code review then found and fixed: past tense folded to the base verb started flows for events already done ("I filed my return yesterday but …"), so only "-ing" forms fold; "nil" folding broke the "nil return" trigger; the offer appeared on out-of-jurisdiction and officer replies, so it is now an allow-list of answer modes; v1 of the TCC flow put an information step between questions, so the next reply answered an unseen question (all questions now come first, pinned for every flow by `FlowShapeTests`); and "help me file my return" typed inside a flow was fed to its pending question, so an explicit request for a different flow now closes the current one and starts it. `TriggerNormalisationTests`, `NewJourneyTests`, `TurnGuidanceTests`, `ReviewRegressionTests`. |

| G62 🟢 | **Empathy fired on requests and repeated itself.** `help me` and `lost my` were anxiety cues, so every "Help me register / file …" opened with "I understand this can feel stressful" (3 of 3 live probes); `please` and `help` raised intensity; the same opener was prepended on every distressed turn; and the empathy tool's "sustained frustration" was judged from one message. | — | **Fixed 2026-09-29 (`text_signals.py`, `tools/empathy.py`, `turn_guidance.py`).** Requests are no longer distress; politeness is not intensity; `strip_repeated_ack` drops an opener used in the last two replies; `distress_trajectory` needs the current turn and one of the two before it to be upset before a "Talk to an officer" action is added; `assess_emotional_tone` accepts `history`. Code review found the streaming core applied guidance before its reply existed, so its openers were never de-duplicated and an abstention that escalated still carried a guide offer: `run_chat_turn` now decides one `turn_ack` per turn from the history it already loaded (the database read moved off the event loop) and calls `finalize_turn_actions` once escalation is known. `EmotionSignalTests`, `EmpathyToolHistoryTests`, `ReviewRegressionTests`. |

| G63 🟢 | **No safety net for a message about self-harm, and no repair for a turn with no task.** A taxpayer writing "I can't pay, I want to kill myself" went to retrieval like any other message. "It still does not work", with no task named, retrieved a passage about URA's own funding and staffing problems and read it back on the local stack. | — | **Fixed 2026-09-29 (`text_signals.py`, `service.py`).** `detect_crisis` (English, Luganda *okwetta*, Kiswahili *kujiua*) short-circuits both chat paths before any router with crisis lines verified that day (999/112 on upf.go.ug; Mental Health Uganda 0800 21 21 21), and offers, never imposes, an officer. `is_feeling_only` sends a feeling-without-task turn to a clarifying question with task actions, and leads with the officer the second time. `CrisisTests`, `ConversationRepairTests`. Code review tightened the repair: "I'm confused" was missed because the tokenizer leaves "m" from "I'm"; a confused repair no longer opens with "Let me put that a different way" when it has nothing to rephrase; and a feeling that follows a real question ("What is chargeable income?" → "I don't understand") goes to the normal pipeline to be re-explained with the history, not restarted. Re-verify the crisis numbers before moving `CRISIS_LINES_VERIFIED_ON`. |

| G64 🟢 | **A Kiswahili answer from the TIN-help flow is served in English.** "Nitasajili vipi ili kupata namba ya TIN nchini Uganda?" routes correctly (`locale: sw`, TIN Registration Help) but the api logs "reply localization to sw collapsed the answer; serving English". The guard was working: Sunflower, decoding greedily, translated the reply's opening line and stopped (50 of 379 characters). Any one of the opening line, the markdown bold or the URL removed, it translated whole — a decoding quirk, not one bad token. Worse, a two-paragraph reply cut to its first sentence kept 21% of its length and *passed* the 15% floor, so a fragment could ship. | Probe case "Kiswahili TIN question answered in Kiswahili", 2026-09-29; reproduced in the api container and bisected 2026-09-30; call replay `3_swahili_local`. | **Fixed 2026-09-30 (`service._translate_by_paragraph`).** Replies are translated one paragraph at a time (concurrently); each paragraph must pass the length floor, and any failure serves the English answer rather than part of a translation. `llm.translate_text` refuses empty input (it translated its own instructions). The Kiswahili TIN reply now arrives whole in Kiswahili. Still a decision: whether a served-English fallback should report `locale: en`. |

| G65 🟢 | **No measure of where taxpayers leave a journey.** The flows persisted state but emitted nothing, so the drop-off between steps could not be seen. | — | **Partly fixed 2026-09-29 (`service.py`).** `journey_events_total{workflow, event, step}` (started, step_entered, step_invalid, completed, cancelled) on `/metrics`, no personal data in labels. A step is counted once per journey: a resume and a re-prompt of the same step do not add another `step_entered` (code review found both skewing the drop-off). **Closed 2026-09-29 (`database.get_journey_funnel` + Postgres twin, `journey_analytics.py`, `GET /v1/analytics/journeys`, `/analytics` "Guided journeys" panel).** That counter is per replica and resets on restart, so the staff funnel is built from the durable `workflow_sessions` and `feedback` tables instead, aggregated in SQL: started, completed, cancelled, abandoned (active and untouched for `JOURNEY_ABANDON_AFTER_HOURS`, default 24 — computed at read time, so no sweep job), the step unfinished journeys stopped at, and thumbs up/down per step. Ratings carry `workflow_id` / `step_id` from the web client (validated identifiers, new `feedback` columns migrated on both backends). `test_journey_analytics.py`, `test_journey_funnel_endpoint`, `JourneyFunnelTable.test.tsx`, `feedbackContext.test.ts`. |

| G66 🟢 | **An auditor could change tickets through the API.** QA audit of the auditor dashboard, 2026-09-29. The ticket console hid its controls from `ura_auditor` (`TicketCase` `canAct`), but `PATCH /v1/admin/tickets/{id}` and the presence heartbeat used only `require_admin_access`, which admits every staff role — so a direct call could resolve a case, reassign it or send a reply to the taxpayer. Flags, overrides and calls already enforced it server-side; tickets did not. | — | **Fixed 2026-09-29 (`main.py`).** `_require_staff_writer` (officers and admins; operator key keeps break-glass) on both endpoints. `tests/test_auditor_controls.py`. |

| G67 🟢 | **Staff actions and transcript reads left no record.** Ticket changes, flag toggles and override edits were written to no log, and opening a taxpayer's transcript was not recorded — so "who resolved this" or "who read this" had no answer. Only the call desk logged its caller-history views. | — | **Fixed 2026-09-29 (`main.py` `_audit_staff_action`).** `staff.ticket_viewed`, `staff.ticket_updated`, `staff.flag_set` / `staff.flag_cleared`, `staff.override_saved` / `staff.override_deleted` on the hash-chained ledger, with actor and role; content recorded as length only; failures counted on `audit_append_failed_total`. |

| G68 🟢 | **The audit ledger had no reader.** `audit/ledger.py` (hash chain, Merkle anchors) and `audit/verifier.py` existed, but no endpoint or page exposed either, so an auditor could neither read the trail nor show it was unaltered. | — | **Fixed 2026-09-29.** `GET /v1/admin/audit/events` (filtered, paginated newest-first, admins and auditors only) and `GET /v1/admin/audit/verify`; `/admin/audit` page leads with the integrity verdict, filters by event, person and period, and exports CSV evidence with formula-injection guarding. `docs/runbooks/audit-trail.md`. |

| G69 🟢 | **Audit-trail follow-ups.** No scheduled Merkle anchoring (anchors existed only if someone called `anchor_range`); `verify` walked the whole chain per request and loaded every row into memory (measured 8 µs/row: ~8 s per million rows); reading or checking the audit trail was not itself recorded. | — | **Fixed 2026-09-29.** Every replica seals new rows every `AUDIT_SEAL_INTERVAL_SECONDS` (3600; seals unique by first seq, so racing replicas make one) and on demand (`POST /v1/admin/audit/seal`, "Seal now"; paused while the record fails its check); verify streams in 5 000-row keyset batches and, above `AUDIT_VERIFY_FULL_MAX_ROWS` (200 000), re-checks the newest seal and walks only the rows after it (`scope: since_seal`, stated on the page); `audit.trail_viewed`, `audit.chain_verified`, `audit.sealed` recorded. `tests/agents/test_audit.py`, `tests/test_auditor_controls.py`, `docs/runbooks/audit-trail.md`. |

| G70 🟢 | **The actor on an audit row was not tamper-evident.** Review of the ledger, 2026-09-29: `row_hash` was `sha256(prev_hash + payload_hash)`, so `user_id`, `event_type`, `ts` and `seq` sat outside every hash. Changing who resolved a ticket (`UPDATE audit_events SET user_id = …`) left `verify` reporting the chain intact, and seals covered payload hashes only, so they missed it too. | — | **Fixed 2026-09-29 (`audit/ledger.py`, `audit/verifier.py`).** Hash format v2: `row_hash = sha256(prev_hash + payload_hash + envelope_hash)`, the envelope being event id, type, tenant, actor, time and seq; `hash_version` per row, legacy v1 rows verify by their own rule, a v1 row after a v2 row is a break. Seals record the chain head hash, which catches a consistent re-attribution. Rows written before the fix keep a payload-only fingerprint. |

| G71 🟢 | **Two replicas could fork the audit chain, and verify trusted anchors it never checked.** `append` serialised on a per-process lock only; on Postgres with several pods, two appends could read the same head and write the same `seq`. Separately, `verify_chain` never compared anything with `audit_anchors`, so a range edited with every later hash recomputed verified clean. | — | **Fixed 2026-09-29.** Unique `(tenant_id, seq)` index (created guarded, so a ledger that already holds a fork still starts and the verifier names it); an append that loses its seq (SQLite `IntegrityError`, Postgres SQLSTATE 23505) re-reads the head and retries. Verify re-checks each seal's row count, Merkle root and head hash, and names sequence gaps and duplicate sequence numbers. Checked on SQLite and against a Postgres 16 server. |

| G72 🟠 | **The seal's only witness outside the database is the log pipeline.** Each seal writes `audit seal tenant=… merkle_root=… head_hash=…` to the logs; that only helps if the log archive is itself write-once and kept. Rows after the newest seal can be deleted from the end without a break until the next seal. | — | **Open.** Put the application log stream on write-once retention; decide whether seals should also go to an RFC 3161 timestamp authority or a public transparency log; shorten `AUDIT_SEAL_INTERVAL_SECONDS` if the unsealed window matters more than the row volume. |

| G73 🟢 | **Call-desk actions reached the audit trail with no actor.** QA pass over the auditor dashboard after the call desk landed (#515), 2026-09-29: `log_voice_event` chained voice events into the ledger without `user_id`, so every claim, hold, transfer, wrap-up and call read showed "—" in the Who column of `/admin/audit` and the "By person" filter could not find them; no event carried `actor_role`; a failed chain was a debug line; and the desk's own event names were missing from `VOICE_EVENT_TYPES` (a warning on every action). | — | **Fixed 2026-09-29.** The ledger row carries the actor; `_staff_call_event` stamps `actor_role` on every desk action; failures count on `audit_append_failed_total`; the event list is complete, with a test that scans the code for every event it writes. `/admin/audit` names the call events and filters them ("Calls opened or listened to", "Call desk actions"). |

| G74 🟢 | **Reading a call transcript over HTTP, rating a call, and listening in left no record.** `GET /v1/admin/calls/{id}` returns the full transcript — the call twin of the ticket transcript read that G67 made auditable — and was not recorded; neither was `POST …/review`; nor was a supervisor, officer or auditor listening to a live call, on either transport. | — | **Fixed 2026-09-29.** `voice_staff_viewed_call`, `staff.call_reviewed` (rating and note length, never the note) and `voice_staff_listened_call` (recorded before any audio flows). |

| G75 🟢 | **Separation of duties was checked route by route.** The ticket endpoints (G66) were found by reading code; nothing would catch the next staff write that forgets its role check. | — | **Fixed 2026-09-29.** A test enumerates every `/v1/admin` write route in the live app and requires 403 for an auditor (sealing is the one listed exception). All 15 such routes pass today. |

| G76 🟢 | **English and Swahili calls depended on a cloud speech-to-speech API.** The receptionist put English and Swahili on Gemini Live and only Luganda on the local engine, so most calls needed an internet API, capped at an 8-minute session, and had already produced wrong-language replies and stalls. | Owner decision 2026-09-30: calls run on local models only. | **Fixed 2026-09-30.** Gemini Live removed; English, Swahili and Luganda run on the local engine (Whisper-SALT, Sunflower on vLLM against Qdrant, Orpheus). Orpheus gained an English speaker (`salt_eng_0001`; first audio 0.27–0.31 s in all three languages). The router claims a switched turn so it is answered once; the sentinel's VAD barge-in now stops the local engine (Luganda answer stopped in 1.0 s, was 1.5 s). Officer brief and call summary use Sunflower first. Call replay 12/12 (`evals/reports/call_replay_2026-09-30_local_only_v2.json`). |

| G77 🟢 | **A silent caller kept a call slot until the 15-minute ceiling.** Nothing prompted a caller who went quiet or ended a call whose line had gone dead, so a few abandoned calls could fill the concurrent-call cap. | Found auditing the local-only receptionist for production, 2026-09-30. | **Fixed 2026-09-30.** After `RECEPTIONIST_IDLE_REPROMPT_S` (12 s) of silence from the end of the assistant's speech it asks "Are you still there?" in the call's language; after `RECEPTIONIST_IDLE_REPROMPTS` (1) such checks it says goodbye and ends the call (`caller_idle`). Never while an officer has or is being fetched for the call, nor while an answer is being worked out. Replay `12_silent_caller`. |

| G78 🟢 | **Spoken multi-step answers were cut after step 1.** The spoken answer is limited to three sentences, and the splitter ended a sentence at every "1." and "2.", so the TIN guide was read as its first step, then "2." and "Would you like more detail?"; the web address was dropped but its "at" was read. | Call replay `1_english_stays_english`, 2026-09-30. | **Fixed 2026-09-30.** A numbered procedure stays with the sentence that introduces it (a figure such as "18." still ends a sentence); a URL is dropped with the "at"/"via" before it. |

| G79 🟢 | **The production gate did not check the engine calls now run on.** G36 verified LiveKit and single-replica settings only; a production receptionist with speech or the LLM off, or without the Orpheus voice, would pass. | Found with G76. | **Fixed 2026-09-30 (`receptionist/config.local_engine_errors`).** In production with the receptionist on: `SPEECH_ENABLED` and `LLM_ENABLED` true, `ORPHEUS_TTS_URL` an http(s) URL without credentials, and every language in `RECEPTIONIST_LANGUAGES` voiced by Orpheus. |

| G80 🟠 | **Orpheus may read URA acronyms oddly in Luganda.** Played back through Whisper-SALT, English and Swahili "T-I-N", "U-R-A", "V-A-T", "P-A-Y-E" came back as the acronyms; the Luganda line "Weetaaga T-I-N okuva mu U-R-A" came back as "Weeta agatiya nno okuva mu U R". | Round trip on the GPU stack, 2026-09-30. | **Open.** A round trip is only a proxy: needs the native-speaker listening test already listed as human-only. |

| G81 🟠 | **A Kiswahili rental-income question retrieved corporate income tax in chat.** "Ninawezaje kulipa kodi ya mapato ya upangishaji?" was translated for retrieval as "How can I pay rent tax?", and the answer given was the 30% corporate rate (it did escalate, `faith=0.00`). Calls are not affected: the receptionist maps the phrase to "Rental Income Tax" first (`normalize_swahili_tax_query`). | `/v1/chat` probe, 2026-09-30. | **Open.** Web chat's retrieval translation leg has no Swahili tax-term normalisation. The same cause shows in the stress benchmark: "Adhabu ya kisheria ya kuchelewa kulipa kodi ni ipi?" is answered with criminal fines (faithfulness 0.00) instead of the 2% monthly interest. With `normalize_swahili_tax_query` applied first, the same question is answered "riba ya 2% kila mwezi" (faithfulness 1.0), measured 2026-09-30. That is the fix to carry into web chat. |

| G82 🟢 | **Orpheus cut long sentences off at 17 s.** The sidecar stops at `ORPHEUS_MAX_TOKENS` (1400 tokens, 16.98 s of audio) whatever it is asked to say: a 335-character Luganda sentence on a call, and all three `/v1/voice/chat` replies in the stress run, came back exactly 16.98 s long, cut off mid-word. | Orpheus sidecar logs on the GPU stack, 2026-09-30. | **Fixed 2026-09-30 (`orpheus_tts.split_for_voice`).** Text over `ORPHEUS_TTS_MAX_CHARS` (120; digits read one by one run to ~0.12 s a character) is voiced in pieces cut at sentence, clause, then word breaks. The batch path voices the pieces concurrently; a call streams them one after another, each generated faster than it plays. |

| G83 🟢 | **The English voice babbled the VAT-rate answer.** The calculator's line "Value Added Tax (VAT / omusolo gwa VAT / ushuru wa VAT) is 18% (18%) (FY2026-27)" was heard as "…eighteen percent pass and they are value-added tax to gwa gwa, isi isi zo…" until the 17 s cap. The duplicated figure, the fiscal-year aside and the alias list each caused babble in repeated samples; the plain sentence never did. | Voice-socket probe plus a Whisper round trip, 2026-09-30. | **Fixed 2026-09-30 (speech text only; the written answer keeps its digit anchors).** A repeated figure is said once, an alias list keeps its first name, and the fiscal year is read "for the 2026 to 2027 financial year" in English (the same in every sample). The clip is now 7.9 s and transcribes cleanly. |

| G84 🟠 | **Orpheus cannot read digit strings.** Phone numbers looped ("zero eight hundred one one one one…") in every spelling tried: spaced digits, grouped digits, digits as words, and repetition penalty 1.1–1.5. Spaced TIN digits came back garbled. | Round trips on the GPU stack, 2026-09-30. | **Partly fixed 2026-09-30.** The contact footer is not spoken (English, Luganda, Swahili); the written answer keeps it. Since G90 the receptionist's own lines carry no digits: numbers a caller must keep go on screen, emergency numbers are spoken in words. **Open:** a number inside an answer, such as the toll-free line when asked for or a TIN read back, is still at risk. Candidates: a digit-trained voice for number-only pieces, or reading numbers in groups the model handles. |

| G85 🟢 | **An English fallback was read with the Luganda or Swahili voice.** When translation failed, `localize_reply` served the English answer, but `/v1/voice/chat` and the voice socket (on its MT-down path) still passed the caller's language to TTS. | CodeRabbit on #519, 2026-09-30. | **Fixed 2026-09-30.** `generate()` records `reply_locale`, the language the reply is actually in, and both endpoints voice the reply in that language. |

| G86 🟢 | **The voice socket read ordinary numbers as steps.** Its sentence splitter rewrote every "N. " as "Step N:", so "Your reference number is 123. Keep it safe." was shown and spoken as "Step 123". | CodeRabbit on #519, 2026-09-30. | **Fixed 2026-09-30.** Only list markers are rewritten: a "1." that opens a line or follows a colon, then the next number of that list. |

| G87 🟠 | **Several speech-normaliser patterns run in quadratic time.** On 50,000 characters of newlines, zeros or spaces, these each take 4–25 s: `_NUMBERED_STEP_RE`, `_BULLET_RE` and `_HEADER_RE` (a `^\s*` under MULTILINE also spans newlines), `_BROKEN_MD_LINK_RE`, `_CITATION_RE`, `_TABLE_PIPE_RE` and `_TABLE_ROW_RE`. Every TTS request runs them, `/v1/tts` included. Its 4,000-character cap bounds one request to about 0.5 s. | Measured while fixing CodeQL's polynomial-regex alerts on #520 (the aside patterns, now linear), 2026-09-30. | **Open.** Bound the leading runs as the aside patterns now are (`[ \t]?`, `{0,n}`), with a linear-time test like `test_the_aside_rules_run_in_linear_time`. |

| G88 🟢 | **Any officer could take a call the AI was handling, and the receptionist's escalation fell short of contact-centre practice.** Owner, 2026-09-30: the AI is supposed to answer calls, and only escalated calls reach an officer. Any `ura_staff` could claim an `ai`-mode call. The greeting never said the caller was talking to an AI, or how to reach a person. "Officer." or "Agent, please" on its own went to retrieval. The AI's "Would you like to speak to an officer?" had no handling for the answer, so "yes" became a new question. An answer could end on two questions ("…more detail? Would you like to speak to an officer…?"), and "yes" to "more detail?" went to retrieval too. | Owner question and "use recommended standards", 2026-09-30. | **Fixed 2026-09-30.** Claiming an `ai` call needs `ura_admin` (403 otherwise; the console shows **Take over** to supervisors only). The greeting says "AI assistant" and "You can ask for an officer at any time". A whole turn that only names a person transfers, in English, Luganda and Swahili. An offer's yes, no or lapse is handled (`offer_accepted`), and "more detail" reads on. Every AI turn ends on at most one question, and a "yes" or "okay" said over an answer before its question is heard gets the question again, not a transfer. An `at_risk` call is offered an officer once; if the caller talked over that offer and moved on, it is made again with the next answer. Replay scenarios 13–15 and 17. |

| G89 🟢 | **A caller in crisis on a call got a cut-down tax-bot reply.** "I want to end my life" reached the chat's crisis reply, but the call trimmed it to three sentences plus "Would you like more detail?", dropping the counselling line and the officer offer. With "goodbye" in the same turn it hung up instead. A Luganda turn was only checked after translation into English. Spoken as digits, "999 or 112" came back as "nine nine or one twelve". | Found while applying G88, 2026-09-30; Whisper round trip on the GPU stack. | **Fixed 2026-09-30.** `detect_crisis` runs on the raw transcript before anything else (English, *okwetta*, *kujiua*), and the chat's `crisis_support` result is honoured as a fallback. The crisis lines are spoken in full with the emergency numbers in words (heard back correctly as "999 or 112"). Mental Health Uganda's 0800 21 21 21 is shown on screen. The officer offer transfers as `safety_concern` at priority **urgent**, and the ticket carries no crisis text. Replay scenario 16. |

| G90 🟢 | **A caller waiting for an officer heard silence, then a ticket UUID.** Nothing was said during the 90 s wait. The timeout line read the full ticket id aloud ("Your reference is 1458bc4d-2b9f-4ed8-…"), which a caller cannot write down and Orpheus cannot say (G84). With the queue off, the toll-free number was read out as well. An officer who claimed the call just before the timeout was preceded by "All our officers are busy". | `evals/reports/call_replay_2026-09-30*.json`, scenario 11. | **Fixed 2026-09-30.** *"Thank you for holding…"* plays every `RECEPTIONIST_HOLD_UPDATE_S` (30 s), except over a joining officer. The busy line says what happens next without a number. The reference appears on screen as `TIC-XXXXXXXX`, the chat's support-case form, which staff search finds. The toll-free number is on screen too. The timeout waits for a live claim to connect or lapse. No receptionist line carries a digit any more (`test_no_spoken_line_carries_a_number`). |

| G91 🟠 | **Callers wait out the full transfer timeout even when nobody is signed in to take the call.** A transfer always queues for `RECEPTIONIST_TRANSFER_TIMEOUT_S`; after hours, or with no officer's console open, the caller holds 90 s for nobody before the callback. Contact-centre practice checks staffing before queueing and offers the callback at once. | Found while applying G88, 2026-09-30. | **Open.** Candidate: count staff lobby sockets (`ura_staff`/`ura_admin`) for the call's tenant, and when there are none open the callback straight away (ticket, `needs_callback`, reference on screen). Held back because the local stack signs staff in through the external IdP: the replay harness cannot staff the desk, so the queued path would lose its live check. |

| G92 🟢 | **A language switch that lands after the turn's transcript loses the caller's first question.** The router claims a turn for re-asking only when the sentinel's vote arrives before Whisper's transcript. When the transcript wins (by about 120 ms in the logs), the brain takes the Luganda TIN question as English and asks "Excuse me, did you say tin?". The router then re-asks the question in Luganda, and the pending clarification answers the re-ask with "Nsonyiwa ku ekyo — nsaba oddemu ekibuuzo kyo." ("please tell me your question again"). The same exchange is in `call_replay_2026-09-30_consolidated.json`, recorded before the escalation work. It passed then only because the replay's language check was looser before #520. | Call replay, 2026-09-30: `2_selected_english_speaks_luganda` and `5_code_switched_luganda_stays` are the only failures in `call_replay_2026-09-30_escalation.json` (15/17). A re-run of scenario 5 passed 1 of 3 (`call_replay_2026-09-30_s5_rerun.json`). Intermittent: both passed in earlier runs the same day. | **Fixed 2026-09-30.** The router stamps when each caller turn begins (`on_speech_started`, first segment) and passes the re-ask a window, from that stamp to the moment the switch is carried out (`handle_external_question(reask_window=…)`). The brain drops a clarification opened inside the window, because it came from the turn's old-language transcript. One opened on an earlier turn is still answered, and so is one from a turn the caller began while the re-ask was being transcribed (CodeRabbit on #522). Tests cover all three cases, and the first two fail with their half of the fix removed. Live replay on the fixed build: 17/17, with scenarios 2 and 5 answered in Luganda first time (`call_replay_2026-09-30_g92.json`). |

| G93 🟢 | **The chat's voice input decoded about 1 clip in 14 as noise.** The chat sent raw PCM to `/v1/asr` and `/v1/voice/chat`. The server's format sniffer took any body starting `FF Ex` for an MP3 frame, and a recording whose first sample is -1 starts `FF FF`, as do about 7% of real clips (`salt-speech-backends.md`). libsndfile then decoded the speech as MP3 noise. A Luganda TIN question came back from `/v1/asr` as "Ekiriza e e e e…" after 5.1 s, and the voice chat spent 15.9 s answering that. The call path had been protected by wrapping its audio in WAV; the chat was not. Separately, quiet int16 PCM with a DC offset could pass the float32 range check and be read as noise. | Baseline of the main chat's voice flows on the GPU stack, 2026-09-30: the same clip read correctly as WAV. | **Fixed 2026-09-30.** An MP3 now needs an ID3 tag, or a second frame header exactly one frame after the first (`_looks_like_mp3`). A body that could be float32 or int16 is read the way that sounds like audio (`_roughness`: the reading that changes least from sample to sample), and a caller can name its raw format with `encoding` instead. The chat uploads WAV (`pcm16ToWav`). Live: the Luganda clip now reads "Ninza ntya okwewandiisa okufuna TIN yange okuva mu URA?" in 0.6 s, and the voice chat answers it correctly in 4.6 s. |

| G94 🟢 | **Luganda dictation failed in Chrome, and Luganda and Swahili dictation went to Google.** The composer used the browser's Web Speech engine for every language. In Chrome that engine sends the audio to Google, and it has no Luganda: `lg-UG` failed with `language-not-supported`, which the chat showed as "Speech recognition is unavailable", instead of falling back to the local model. | Audit of the main chat's voice flows, 2026-09-30, against MDN's note that Chrome's recognizer is server-based. | **Fixed 2026-09-30.** Luganda and Swahili dictation record and use the local Sunbird Whisper-SALT model (`LocaleOption.dictation = 'server'`), so their audio stays on URA's servers. The request carries `domain=tax`, which repairs TIN/URA mishears. English keeps the browser engine for its live word-by-word text. In Chrome that engine still sends the audio to Google's servers; other browsers may recognise on the device or remotely, depending on platform and version. On-device recognition that a page can require (`SpeechRecognition.processLocally`) is not yet widely available. |

| G95 🟢 | **A spoken reply was heard only after all of it was synthesised, and weighed 1.1–1.6 MB.** The chat's read-aloud (`/v1/tts`) and voice mode (`/v1/voice/chat` with TTS) returned one WAV for the whole answer. Nothing played until the last sentence was voiced, and voice mode with narration on also held the answer's text back until then. | Baseline of the main chat's voice flows on the GPU stack, 2026-09-30: a TIN question's spoken reply arrived as 1.1–1.6 MB of WAV, 9–20 s after the question. | **Fixed 2026-09-30.** `POST /v1/tts/stream` sends the reply as NDJSON pieces as each is voiced (60, 90, then 120 characters; the first alone, then two in flight), in Ogg/Opus where the browser plays it. The chat schedules the pieces back to back with Web Audio (`speakStreamed`), and voice mode shows the text first, then speaks it. Measured on a fresh api (`scripts/bench_chat_speech.py`, `evals/reports/chat_speech_2026-09-30.json`): first audio 5.3 s (en), 7.4 s (lg) and 7.9 s (sw), against 10.3, 16.0 and 19.4 s for the whole WAV; 147–182 KB instead of 1.5–1.9 MB; one silence between pieces in 14 (0.21 s). The rest of the Luganda and Swahili wait is the answer's text (4.6–4.8 s). |

| G96 🟢 | **One speech request held up the whole api.** The speech routes are `async`, but called the synchronous `SpeechModel` inline, so a synthesis or transcription blocked the event loop for its duration: every other request waited, health checks included. | On the GPU stack, 2026-09-30: `/health` took 5.8 s during a 6.2 s `/v1/tts`. Found while measuring concurrent read-alouds, first misread as the Orpheus sidecar not batching (a direct probe showed it does: two requests at once took 2.9 s against 2.5 s for one). | **Fixed 2026-09-30.** `/v1/asr`, `/v1/tts`, `/v1/translate`, `/v1/voice/chat` and the new stream run speech work through `asyncio.to_thread`. `/health` now answers in 58 ms or less during a 7.5 s synthesis. `test_speech_work_does_not_hold_up_the_rest_of_the_api` fails on the old code. |

| G97 🟢 | **Voice mode needed a tap to end every turn, and an open mic listened until someone tapped.** The chat's voice mode recorded until the checkmark was pressed, so a spoken conversation was not hands-free. `useVoiceStore.silenceTimeout` (2000 ms) was stored but nothing read it and nothing could set it. A mic opened by mistake stayed open. On phones the composer's hint line was hidden outright, so every mic notice ("The microphone did not open", "Didn't catch that") was invisible there. | Audit of the main chat's voice flows, 2026-09-30, against current voice-agent practice: a turn ends after 0.8–1.2 s of silence, and speech must last 200–300 ms to count. | **Fixed 2026-09-30.** Voice mode sends a turn after a pause: 1.2 s by default, with 0.8 s, 2 s and Off (tap only) in Settings → Voice. A v3 store migration replaces the never-chosen 2000 ms. `services/endOfTurn.ts` decides it from the microphone's level in the speech band. Speech is 12 dB over a floor that follows the room (the 10th percentile of the last 4 s, held still once the turn starts), and a turn needs 300 ms of it. A mic that heard nothing for 8 s closes and says why; clicks and beeps count as nothing. Every turn ends at 60 s. Phones now show the notices and the recording hint, and hide only the disclaimer. With the pause on, voice mode also listens again once a reply has been read (not after a reply the listener stopped), and opening the mic silences a reply still being read: before, it talked over the speaker and into the recording. Browser tests, on chromium and mobile-chrome: a synthetic voice (1.5 s, then quiet) is sent with no tap, and its second turn too once the reply has been read; Chromium's beeping fake mic closes after 8 s with nothing sent; dictating over a 20 s reply stops it. **Open:** it is a level detector, so a loud noise that lasts 300 ms can start a turn. A speech model in the browser (Silero VAD, a few MB of WASM and model) would tell speech from noise. |


Two probe bugs were found and corrected rather than reported as defects: `invoic`
and `regist` were asserted as stems against a token-boundary matcher, so
"Invoicing" and "registered" could never satisfy them. The service was right both
times. Recorded because the same mistake in the repo harness is what G40 was.

**Open follow-ups.**

- `Data/eval/coverage_bank.jsonl` (105 taxpayer-voice probes) is entirely
  bare-question, so it cannot detect the G35 class at all — it could not see the
  defect and cannot now confirm the fix. Adding preamble-framed variants needs
  `lg`/`sw` translations from a native speaker —
  `test_every_question_exists_in_every_language` requires all three, and
  inventing them would poison a multilingual gate.
- G37 makes the harness honest, not sufficient. Keyword coverage still cannot
  tell a correct answer from one carrying the right vocabulary; on VAT journey
  turn 2 the incorporation-forms page still clears the majority rule at 0.75
  while the better answer scores 1.00. Grading correctness needs a model judge.
- `tax_education_load_suite.py` counts `workflow` in `grounded_pct`, so a slot
  prompt still inflates that metric the way it used to inflate coherence. Left
  as-is deliberately: redefining a published number is the operator's call.
  `scripts/evaluate_1000_faqs_ngrok.py` no longer does this (G57) — the two
  harnesses now disagree on what a slot prompt is worth, and the load suite is
  the one still counting it as an answer.
- G57 gave the 1,000-FAQ harness a ceiling of 100% in every locale, but its
  vernacular coverage is thin: only 16 of 1,000 probes ask in Luganda or
  Kiswahili, and a non-English item with no vernacular rendering for any of its
  terms is reported `scorable=False` rather than scored. Read `scorable_count`
  beside `accuracy_pct` before comparing a locale against English. Widening it
  needs reviewed vernacular ground truth, which is the same blocker as the
  coverage bank above.

---

## 3. Is agentic AI applicable here? — **Yes, and here's the shape**

### 3.1 Why agentic fits

Three properties of this domain make agentic AI a natural fit
(whereas they make it a poor fit in many other contexts):

1. **Bounded action space.** URA operations are a finite, well-defined
set: calculate tax, look up account, submit form, search policy,
schedule appointment, upload document. That makes it feasible to
enumerate tools and test each one exhaustively.
2. **High value per successful action.** A user who wants to register
a business, file a return, or challenge an assessment is motivated
to complete a multi-step flow. The payoff per completed workflow
is measurable (return filed, TIN issued, appointment booked).
3. **Clear audit boundary.** Every agent action can be logged,
reviewed, and reversed. This matters for a public-sector
deployment where accountability is non-negotiable.

### 3.2 Agent architecture — supervisor + specialists + tools

I'd recommend the **supervisor-specialist** pattern over
monolithic "one LLM with tools". Reasons:

- Domain-specific prompts outperform generic prompts on niche tasks.
- Each specialist can use a different model size / temperature.
- The supervisor is debuggable — you can print its routing decision.
- Adding a new domain = add a new specialist, not rewriting one prompt.

```
      ┌─────────────────────────────┐
      │      Supervisor Agent       │
      │  (Sunflower-14B-FP8, T=0.1)  │
      │   Classifies + routes       │
      └──┬──────┬──────┬──────┬─────┘
        │      │      │      │
┌───────────────────┘      │      │      └───────────────────┐
▼                          ▼      ▼                          ▼
┌───────────────┐        ┌───────────────┐                 ┌───────────────┐
│  Tax Agent    │        │ Customs Agent │                 │ Account Agent │
│ (specialist)  │        │  (specialist) │                 │  (specialist) │
└───────┬───────┘        └───────┬───────┘                 └───────┬───────┘
│                        │                                 │
└────────────┬───────────┴─────────────┬───────────────────┘
    ▼                         ▼
┌─────────────┐          ┌──────────────────┐
│   RAG Tool  │          │     MCP Tools    │
│  (current)  │          │                  │
└─────────────┘          │ • calculator     │
                      │ • ura_account    │
                      │ • document_parser│
                      │ • forms          │
                      │ • calendar       │
                      │ • rates          │
                      │ • news_search    │
                      └──────────────────┘
```

Key design choices:

- **Supervisor is deterministic.** The current deployment uses the
already-loaded Sunflower-14B-FP8 runtime at low temperature; a smaller pinned
router model can replace it later if latency requires it.
- **Specialists share tools.** Tools are MCP servers, not code
embedded in any one agent — this keeps them independently testable
and deployable.
- **Escalation is an agent action.** "I don't know" is a first-class
tool call (`escalate_to_human(reason, context)`) that writes a
ticket rather than emitting text.
- **Memory is a tool.** `memory.read(user_id, topic)` and
`memory.write(user_id, fact)` — so the agent doesn't have to
carry state in the prompt.

### 3.3 MCP tool inventory (proposed)

Each tool is a separate process speaking Model Context Protocol. This
lets URA run them on different hosts with different access boundaries
(e.g. `ura_account` runs in URA's DMZ, `calculator` runs in the
public app).

| Tool | Purpose | Auth | Side effects | Risk |
|---|---|---|---|---|
| `mcp_rag` | The existing hybrid retriever, exposed as a tool | none | read-only | low |
| `mcp_tax_calculator` | PAYE, VAT, CGT, customs, WHT, CIT, effective rate | none | pure fn | low |
| `mcp_rates` | Live exchange rates, current VAT thresholds, Bank of Uganda CBR | none | read-only | low |
| `mcp_calendar` | Filing deadlines, fiscal-year dates, upcoming obligations | user scope | read-only | low |
| `mcp_document_parser` | Parse uploaded invoices/receipts/tax certs → JSON | user scope | read-only | medium (PII) |
| `mcp_forms` | Prefill URA form templates (PDF or URA portal) | user scope | creates draft | medium |
| `mcp_news_search` | Search URA circulars, press releases, gazette | none | read-only | low |
| `mcp_ura_account` | Account status, filings, balance, next due | user scope + URA API key | read-only | **high** (PII) |
| `mcp_ura_actions` | File return, pay, request objection (strict subset) | user scope + explicit confirmation | **writes to URA** | **critical** |
| `mcp_memory` | Read/write user facts for personalization | user scope | writes to memory store | medium |
| `mcp_escalate` | Create a staff ticket for human takeover | user scope | writes to tickets | medium |
| `mcp_notify` | Schedule a reminder (email/SMS/in-app) | user scope | writes to scheduler | medium |

**Critical controls for high-risk tools:**

- `mcp_ura_actions` requires **two-factor confirmation** — the agent
must show the user exactly what will be submitted, and the user
must click a real button in the UI (not an LLM-generated "yes").
- All writes are logged to the immutable `audit_events` table.
- Rate-limited per-user, not per-IP.
- Feature-flagged behind a `tier` gate — unavailable until user
has completed identity verification.

### 3.4 Memory architecture 🟢 (shipped in Phase 16)

Three tiers, each with different retention and access patterns:

1. **Short-term (session)** — the current conversation's turns.
Lives in Redis (ephemeral) or Postgres (`conversations`).
Already implemented via `db.get_recent_turns`.
2. **Medium-term (episodic)** — summaries of past conversations
("user asked about VAT registration 3 times this month"). Built
by a batch job; stored in `conversation_summaries`.
3. **Long-term (semantic)** — extracted user facts
(`{"taxpayer_type": "sole_trader", "registered_vat": false,
"industry": "retail"}`). Stored in `user_facts` with provenance
(which conversation the fact was learned from, confidence score,
extraction timestamp).

The memory agent runs offline after each conversation ends:

```
conversation ended
│
▼
┌──────────────────┐     ┌─────────────┐
│  Summarizer LLM  │────▶│ summaries   │
└──────────────────┘     └─────────────┘
│
▼
┌──────────────────┐     ┌─────────────┐
│  Fact extractor  │────▶│ user_facts  │
│  (JSON-mode LLM) │     └─────────────┘
└──────────────────┘
```

At chat time, the supervisor agent pulls relevant facts via
`mcp_memory.read(user_id, query)` and injects them into the
specialist's system prompt.

### 3.5 Safety model

Agentic systems expand the blast radius of LLM mistakes. Three
controls are non-negotiable:

1. **No irreversible action without explicit user confirmation.**
The user must see a form preview and click "Submit" — the LLM
cannot trigger `mcp_ura_actions.*` from prose alone.
2. **All tool calls go through `OutputGuard`.** Tool arguments are
scanned for prompt-injection patterns before execution; tool
responses are scanned for PII on the way back.
3. **Append-only audit log** of every (user, agent, tool, args,
result, timestamp, hash of previous row). Enables forensic
replay for disputes.

Also: `FLAG_AGENTIC_MODE` now defaults **on** after
`agentic_mode_gate()` (English golden-set accuracy ≥ 0.95).
`FLAG_TOOL_USE` stays off — irreversible URA actions still require
explicit confirmation.

---

## 4. Recommended next phases (17+)

Phases 14 through 16 are now shipped. Each remaining phase is
scoped to be independently shippable. Dependencies are explicit.

### Phase 14 — Identity & user profile (G1, G2, G3, G4, G24) 🟢 **SHIPPED**

**Delivered:**
- JWT auth (HS256/RS256) with `auth/` directory (`jwt_auth.py`, `dependencies.py`)
- `user_profiles` table + `GET/PUT /v1/me/profile` endpoints
- `consent_receipts` table + `GET/POST /v1/me/consents` endpoints (UDPA 2019)
- 5 roles: `public`, `verified_taxpayer`, `ura_staff`, `ura_admin`, `ura_auditor`
- Profile-aware prompt injection in `_build_messages`

### Phase 15 — Tool-calling, supervisor, workflows, tickets (G9, G10, G11, G20, G32) 🟢 **SHIPPED**

**Delivered:**
- ✅ In-process tool registry with schema validation (`backend/app/tools/`):
25 registered tools — calculators, rates, calendar, KB search, escalation.
- ✅ Tool-call loop in `llm.generate_with_tools()` (feature-flagged
via `FLAG_TOOL_USE`).
- ✅ Supervisor router (`agents/supervisor.py`) with 7 routes and
per-specialist tool whitelists.
- ✅ Ticket queue with CRUD admin endpoints, `escalate_to_human` tool.
- ✅ 14 YAML-declared workflows via `workflows/` directory (loader,
registry, slot-filling state machine).
- ✅ Feature flags default OFF — shipping is a no-op on the existing
request path.

See `docs/AGENT_ARCHITECTURE.md` for the full design.

**Remaining for future phases:**
- MCP wire format (tools currently in-process, not as separate
MCP servers). Needed once we add `mcp_ura_account` +
`mcp_ura_actions` which must run in URA's DMZ.
- Per-specialist system prompts (currently all specialists use the
base `SYSTEM_PROMPT`).

### Phase 16 — Memory, audit, speech (G5, G8) 🟢 **SHIPPED**

**Delivered:**
- ✅ Memory system (`memory/` directory) — semantic facts, episodic
summaries, working memory with provenance and confidence scores.
- ✅ Audit ledger (`audit/` directory) — hash-chained, append-only,
Merkle tree proofs for tamper evidence.
- ✅ Speech pipeline — ASR (`Sunbird/asr-whisper-large-v3-salt`),
TTS (Spark-TTS-SALT), MT, Sunbird AI cloud fallback
(`speech_service.py`, `spark_tts_salt.py`, `sunbird.py`).
- ✅ 49 feature flags via `flags.py` (addressable rollout).
- ✅ PostgreSQL backend option.

### Phase 17 — Document ingestion + topic persistence (G6, G13, G25)

**Goal:** Query-time document uploads and topic-aware conversations.
**Shipped (G13 core + 2026-08-17 guards):** `POST /v1/documents/analyze`,
session-bound TTL store, OCR/tables, chat `attachment_ids`, PDF report,
structural PDF/Office guards, LLM01 scrub. See
`App/docs/traceability/document-pdf-guards-2026-08-17.md`.
**Still open:**
- Virus / malware scan (ClamAV or equivalent) in an isolated worker
- `mcp_document_parser` tool
**Shipped 2026-08-17:** `conversation_topics` + `topics.py` classifier (G6). G25 segment metrics were already shipped.

**Dependencies:** Phase 14 (auth), Phase 15 (tool framework).
**Effort remaining:** malware scan is an ops add-on; LangGraph topic
chips remain a later UX upgrade.
**Risks:** Parser DoS if a native library is already executing.

### Phase 18 — Staff dashboard + ticket UI (G32 follow-up, G31)

**Goal:** Give URA staff a UI for the ticket queue (backend shipped in Phase 15).
**Shipped 2026-08-17:** `/admin` morning board, `/agent` claim-and-reply queue,
`/admin/tickets` full console, live ticket stream, collision presence,
canned replies, next-reply SLA, population breach counts, `/admin/flags`.
**Shipped 2026-08-18 (G31 prototype):** exact-match `/admin/overrides` +
`GET/PUT/DELETE /v1/admin/overrides`. Seeded by `python -m app.seed_prototype`.
Not a full FAQ CMS.

**Dependencies:** Phase 14 (auth, RBAC) — already shipped.
**Effort remaining:** git-backed prompt / FAQ editor (post-prototype).

### Phase 19 — Deep planning + ReAct (G21, G22, G23)

**Goal:** Extend the shipped supervisor into a full planner-executor.
**Landed 2026-08-17 (bounded):** G21 observe + reasoning-miss retry,
G22 specialist fragments, G23 one typed hop. Remaining is a deeper
planner-executor, not an open ReAct loop.
**Deliverables (remaining):**
- Full planner-executor (JSON plan / tree of thought) — optional
- Per-specialist system prompts in `agents/prompts/` (YAML/hot-reload)
- LLM-based supervisor classifier (replace rule-based soft-misses)
- Per-agent eval suites

**Dependencies:** Phases 14-16 (all shipped).
**Effort:** ~3-4 weeks.
**Risks:** Latency (each agent hop adds 300-800 ms); reasoning
chain brittleness; cost if hosted.

### Phase 20 — Proactive engagement (G14, G15, G27) 🟢 **PROTOTYPE**

**Goal:** Bot reaches out *before* the user asks.

**Shipped 2026-08-18:**
- In-app inbox (`GET/POST /v1/me/reminders`) behind the existing selector
- Publication hash-diff ingest (`publications.py`) — no auto-recreate
- Index-freshness alert (`freshness.py` + Slack `--notify`)
- Sample inbox / outbox / ticket rows via `Data/eval/prototype_seed.json`

**Still open:**
- Email (SES) / SMS (Africa's Talking)
- Scheduled push without a client calling refresh
- User-facing notification-preference UI beyond consent

**Dependencies:** Phases 14, 17.
**Effort remaining:** ~1 week for a real sender.

### Phase 23 — Voice-first streaming infrastructure 🟢 **SHIPPED**

**Goal:** Transform batch voice into a streaming, voice-first interface for rural, low-literacy users on 2G/3G.

**Delivered:**
- ✅ WebSocket streaming voice chat (`WS /v1/voice/chat/stream`) with full duplex protocol
- ✅ Energy-based VAD with hysteresis (configurable thresholds, sensitivity presets)
- ✅ True barge-in (user interrupts mid-TTS, server aborts between sentence chunks)
- ✅ Sentence-chunked TTS (< 800ms time-to-first-audio for simple queries)
- ✅ Voice-specific consent (`voice_recording`, `voice_analytics` purposes) with NDPA 2019 compliance
- ✅ Immutable voice audit log (`voice_audit_log` table) chained into existing `AuditLedger`
- ✅ Privacy-first: raw audio never stored (SHA-256 hash only), configurable retention TTLs
- ✅ Offline RAG pipeline (FAISS + ONNX bge-m3 embedder) for network-unavailable fallback
- ✅ Accent detection (5 Ugandan profiles) routing to accent-specific Whisper LoRA adapters
- ✅ Full-screen mobile voice-first UI (`VoiceChat.tsx`) with animated orb, waveform bars, barge-in button
- ✅ AudioWorklet streaming capture + WebSocket client with auto-reconnect
- ✅ Camera capture component for voice+vision mode (Phase 4 stub)
- ✅ Feature flags: `FLAG_VOICE_STREAMING`, `FLAG_VOICE_CONSENT`
- ✅ Prometheus metrics: 10 voice-specific counters/histograms/gauges
- ✅ OTel spans: `voice.session`, `voice.vad`, `voice.streaming_asr`, `voice.streaming_tts`
- ✅ Training scripts: multi-accent ASR (5 configs), accent-aware TTS (3 profiles), dataset prep
- ✅ Export script: FAISS offline bundle from Qdrant (< 100MB target)

**New backend modules:** `voice_stream.py`, `voice_ws.py`, `voice_consent.py`, `offline_rag.py`, `accent_detector.py`
**New frontend:** `VoiceChat.tsx`, `voiceWebSocket.ts`, `useVoiceStore.ts`, `useVoiceWebSocket.ts`, `CameraCapture.tsx`, `audio-worklet-processor.js`
**Latency targets:** < 800ms p95 simple queries, < 1.2s p95 full RAG.

---

## 5. Minimum viable personalized experience 🟢 **SHIPPED**

The three foundational layers for personalization are now all delivered:

1. **Phase 14 — Identity & profile.** 🟢 Shipped. JWT auth, RBAC (5 roles),
user profiles, consent management.
2. **Phase 15 — Tool-calling, supervisor, workflows, tickets.** 🟢 Shipped.
25 registered tools, 7 supervisor routes, 14 YAML workflows, ticket queue.
3. **Phase 16 — Memory, audit, speech.** 🟢 Shipped. Three-tier memory
(semantic, episodic, working), hash-chained audit ledger, speech pipeline.

4. **Phase 23 — Voice-first streaming.** 🟢 Shipped. WebSocket streaming
voice chat with VAD + barge-in, sentence-chunked TTS, offline RAG (FAISS),
accent detection (5 Ugandan profiles), voice consent & audit trail,
full-screen mobile voice-first UI.

**Next priorities:** Phase 17 leftover (optional malware-scan worker),
Phases 18-20 (staff UI, deep planning, proactive engagement) stack on
top of the shipped foundation.

---

## 6. Code surface summary (index into the existing repo)

| Domain | New files | Modified files |
|---|---|---|
| Auth + profile | `backend/app/auth.py`, `backend/app/profiles.py`, `frontend/src/app/profile/page.tsx`, `frontend/src/components/ConsentBanner.tsx` | `backend/app/main.py`, `backend/app/database.py`, `backend/app/postgres.py`, `backend/app/models.py`, `frontend/src/app/layout.tsx` |
| Tool framework | `backend/app/mcp/`, `backend/app/tools/calculators.py`, `backend/app/tools/rates.py`, `backend/app/tools/calendar.py` | `backend/app/llm.py`, `backend/app/service.py`, `backend/app/flags.py` |
| Workflows + uploads | `backend/app/workflows/`, `backend/app/uploads.py`, `backend/app/tools/document_parser.py`, `frontend/src/components/DocumentUpload.tsx`, `frontend/src/components/WorkflowStep.tsx` | `backend/app/main.py`, `backend/app/models.py`, `frontend/src/app/page.tsx` |
| Memory | `backend/app/memory.py`, `backend/app/workers/memory_worker.py`, `backend/app/tools/memory.py` | `backend/app/service.py`, `backend/app/database.py` |
| Tickets | `backend/app/tickets.py`, `frontend/src/app/admin/tickets/page.tsx` | `backend/app/main.py`, `backend/app/service.py` (escalation hook) |
| Agents | `backend/app/agents/supervisor.py`, `backend/app/agents/tax_specialist.py`, `backend/app/agents/customs_specialist.py`, `backend/app/agents/account_specialist.py` | `backend/app/service.py`, `backend/app/flags.py` |
| Topic persistence (G6) | `backend/app/topics.py` | `backend/app/database.py`, `backend/app/postgres.py`, `backend/app/service.py`, `backend/app/models.py`, `backend/app/agents/eval_routing.py` |
| Voice-first (Phase 23) | `backend/app/voice_stream.py`, `backend/app/voice_ws.py`, `backend/app/voice_consent.py`, `backend/app/offline_rag.py`, `backend/app/accent_detector.py`, `frontend/src/components/VoiceChat.tsx`, `frontend/src/services/voiceWebSocket.ts`, `frontend/src/store/useVoiceStore.ts`, `frontend/src/hooks/useVoiceWebSocket.ts`, `frontend/src/components/CameraCapture.tsx`, `frontend/public/audio-worklet-processor.js` | `backend/app/speech_service.py`, `backend/app/flags.py`, `backend/app/models.py`, `backend/app/main.py`, `backend/app/database.py`, `backend/app/tracing.py`, `frontend/src/app/page.tsx`, `frontend/src/services/voiceService.ts`, `frontend/src/components/Icons.tsx`, `frontend/src/app/globals.css` |
| Scheduler + freshness | `backend/app/scheduler.py`, `backend/app/workers/news_ingest.py`, `backend/app/freshness.py` (hash + compare shipped 2026-08-17; cron / Slack / auto-reindex still Phase 20), `backend/app/tools/notify.py` | `backend/app/main.py`, `docker-compose.yml` |

---

## 7. Compliance & governance checklist for personalization

Before any phase that stores user data:

- [ ] **Uganda Data Protection Act 2019** — appoint a Data Protection
Officer, register with the NITA-U PDPO, publish a privacy
notice, define lawful bases per processing purpose.
- [ ] **Purpose limitation** — each `user_facts` field must have a
declared purpose; the memory agent can't extract facts outside
those purposes.
- [ ] **Consent versioning** — when the consent text changes, users
must re-consent before the new version's data is used.
- [ ] **Subject rights** — implement `GET /v1/me/export` (data
portability) and `DELETE /v1/me` (right to erasure). Both must
cascade to Redis cache keys and Qdrant user-specific collections.
- [ ] **Audit & forensic replay** — append-only `audit_events` with
hash chaining; retention ≥ 7 years for tax-related records.
- [ ] **Red-team the agent** — prompt-injection, data exfiltration,
privilege escalation, and prompt-leak tests in CI.
- [ ] **Sub-processor disclosure** — if any tool (e.g. vLLM on a
cloud GPU, OpenAI for embeddings) transmits data to a third
party, disclose it in the privacy notice.

---

## 8. What this document deliberately does NOT do

- **Does not re-specify Phases 1-16.** See `App/README.md` for the
current production-ready flow and `docs/AGENT_ARCHITECTURE.md` for
the agent runtime design.
- **Does not prescribe a specific LLM vendor.** The architecture
works with Qwen on-prem, vLLM-hosted Llama, or hosted APIs —
pick based on compliance, cost, and latency.
- **Does not cover frontend redesigns beyond profile / admin /
ticket views.** The existing UI (Phase 13 glassmorphism) is
already production-grade.
- **Does not include infra costing.** Phases 14-16 added auth +
Postgres + agents + memory + audit. Phase 19 would add ~3x LLM
calls per request (deep planning) — costs need modeling once the
full agent chain is locked.

---

*Document version 2.5 — updated 2026-09-17: closed G59 (vernacular numeral & idiom disambiguation: Luganda verb 'saba' vs Swahili numeral '7', 'mtu wa tatu', 'ekiwandiiko 1.2', currency qualifiers 'milioni'/'obukadde'/'emitwalo'), G60 (W3C SSE multiline event-buffered reader loop in page.tsx and list unsmashing normalizers resolving chat UI procedural step collapse), and G61 (multiline sentence splitting in text_signals.py and cross-passage corroboration in claim_verifier.py eliminating false low-faithfulness escalations on URA service catalogs). Verified 100% accuracy on 1,000 FAQs, 300 FAQs, and 100 FAQs multimodal benchmarks.*
*Document version 2.4 — updated 2026-09-16: resolved G59 closing the 100-FAQ multimodal benchmark accuracy gap. Wired missing statutory heads (mobile money withdrawal excise 0.5%, presumptive threshold 10m-150m, TPCA late-filing penalty 200k/2%) into FY2026-27 rate table and calculator router fast lookups; added local government tax out-of-scope handler; improved small-business FAQ priority routing; added stale PDF chunk discounting; and corrected leaky benchmark gold labels.*
*Document version 2.3 — updated 2026-09-02: G44 re-probed on the deployed
Space and fixed. The re-probe changed what G44 is: not a bands question
answered with a passage, but plain-language PAYE threshold questions answered
**UGX 235,000** out of superseded handbooks when the FY2026-27 table says
**335,000**. Recorded because it is the third finding in §2.11 whose real
severity only appeared once the question was asked the way a taxpayer asks it.*
*Version 2.3 — 2026-09-05: closed Issue #441 agentic modelling gaps: implemented vLLM tool calling, decoupled tool execution from retrieval gating, wired LangGraph runtime with streaming parity, added deterministic graph argument binding, and resolved G43 with the epistemic false-premise guard.*
*Version 2.2 — 2026-09-02: added §2.11 (promotion re-verification on the
rebuilt 7,972-document index, plus FAQ verification against the deployed HF
Space) with G40, G41 and G42 fixed and G43, G44 and G45 open.*
*Version 2.1 — 2026-09-01: added §2.9 (serving-path defects found by the load
harness) and §2.10 (end-to-end evidence on the full GPU stack), corrected the
model stack to Sunflower-14B-FP8 / whisper-large-v3-salt / Spark-TTS-SALT, and
re-derived the tool, workflow and flag counts from the running registries
(25 / 14 / 49).*
*Version 2.0 — 2026-04-28 after Phases 14-16 shipped.*
*Previous version (1.0) authored after Phase 1-13.*
*For questions about a specific gap or phase, open an issue
linked to the `roadmap/phase-XX` tag.*
