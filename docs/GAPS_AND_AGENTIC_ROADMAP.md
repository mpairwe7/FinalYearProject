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
| G35 ⚪ | **Situational preamble dilutes the FAQ match.** `_faq_match_score` divides coverage by the terms the *user* supplied, so context lowers the score of the row that answers the question. "I am opening a hardware store in Jinja. Do I have to charge VAT?" scores **0.273** against the 0.58 floor; the bare question scores **0.700**. The answer is in `ura_vat_faqs.csv` throughout. | Reproduced against the 516-row FAQ index. | **Open — the obvious fix was tried and reverted.** Ungating `extract_question_span` from `detect_user_distress` fixed it on a stale 729-doc index, then *cost* 37 points of VAT-journey fact coverage (81.2% → 43.8%) once the index was rebuilt to 7,970 docs: the FAQ scorer only decides the answer when retrieval has fallen back to keyword matching, and against a healthy dense index the preamble is useful context. The narrowing stays distress-gated. Fixing the dilution belongs in the scorer. `tests/test_situational_preamble_retrieval.py` pins both the dilution and the deliberate non-narrowing. |
| G36 🟢 | **An open workflow swallowed every following question.** `_maybe_handle_workflow` returns before retrieval, so while a flow was active the corpus was unreachable and any question was fed to the slot validator. Only exits were completion or six literal cancel words. | Measured PAYE journey: all three turns were non-answers (fact coverage **0.0%**). | **Shipped.** Divert to normal retrieval when the message reads as a question *and* the pending slot cannot accept it, so a mistyped answer still re-asks. PAYE fact coverage 0.0% → **44.4%**. `WorkflowRegistry.pending_step`, `ChatModel._workflow_input_changes_subject`, `tests/test_workflow_topic_change.py`. |
| G37 🟢 | **The accuracy harness could not measure what it claimed.** Journey "coherence" was `len(matched_kw) > 0` — one expected keyword as a bare substring, anywhere in the reply. Abstentions and workflow slot prompts scored as good answers; language fidelity used `"ura"` as both a Luganda and a Kiswahili marker, so plain English passed every time; `"150"` matched inside `"1,500,000"`. | A company-incorporation forms page outscored a real VAT-threshold answer. | **Fixed in `tests/load/tax_education_accuracy_eval.py`**: token-boundary matching, non-answer detection, majority-coverage rule, graded per-turn `keyword_coverage_pct`, and language markers that do not occur in English. Corrected figures: coherence 70.8% → **41.6%**, lg 71.6% → **56.6%**, sw 73.3% → **58.3%**. |

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
| G39 ⚪ | **The workflow router is too eager in local languages.** The 80 probes above entered a flow on their *first* turn — a question was classified as a task. G38 lets them leave; it does not stop them being captured. Luganda/Kiswahili coverage (63.6% each) is ~27 points below English (90.3%) and this is the largest single cause. | **Open.** Needs locale-aware trigger thresholds in `WorkflowRegistry.match_trigger`, gated on a per-locale golden set. |

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
| G41 🔴 | **Jurisdiction is ignored on the rate-table path.** "What is the corporate income tax rate in **Kenya** for 2026?" returns "**The corporation tax rate in Uganda is 30%** … from the official URA FY2026-27 rate table". Reproduces for Rwanda. The answer is labelled Uganda, so it is not a false statement — but it silently substitutes a different question and presents the result with full confidence and a citation. For a government service this is the highest-severity finding here: the same failure mode as G35, on the path users trust most. | **Open.** The deterministic rate path needs a jurisdiction guard: a foreign country named in the query should abstain and say URA covers Uganda only. |
| G42 🟢 | **Calculator flows captured factual questions.** "How much monthly income is exempt from PAYE in Uganda?" entered `calc_paye` and asked for a gross salary; "What will Uganda's VAT rate be in 2031?" entered `calc_vat` and asked for an amount. Both are questions *about* a figure, not requests to compute one, and neither got answered. Not local-language-specific — plain English. The guard belongs in `plan_calculation`, not at the workflow-entry site: the guided flow is opened downstream by `_maybe_handle_calculator` when a plan reports missing params, so a plan that is never formed is a flow that never opens. `_INFO_ONLY_RE` already existed for exactly this and had two gaps — it required `is/are/was/were`, so "what **will** … rate" slipped past, and it had no branch for "how much X **is exempt**". | **Fixed.** `_INFO_ONLY_RE`, `App/backend/tests/test_calculator_router.py::FigureLookupIsNotACalculationTests`. Verified live: both now answer (`hybrid` / rate lookup) while `Calculate PAYE for 3,500,000` and `I want to calculate my VAT` still reach the calculator. |

G41 and G42 were found by targeted trust probes rather than the coverage bank,
because both return fluent, well-formed, confidently-cited replies — exactly
what a keyword-coverage scorer counts as success. Neither is a regression from
this promotion; both predate it and are recorded here so the promotion does not
imply they were cleared.

**Re-run after fixing G42** — 22 trust probes, 3 failing (was 5 of 14):
calculator-entry 8/8 including the controls that must still reach the
calculator, figures 4/4, grounding 3/3, workflow-escape 3/3 in en/lg/sw. The
three that remain are all abstention, and they share one root: the service
answers a *neighbouring* question with full confidence instead of declining.

| # | Finding | Status |
|---|---|---|
| G43 🔴 | **A non-existent tax was invented, with figures.** "What is the URA Digital Nomad Levy and how do I pay it?" — a tax that does not exist — returned "a tax of **1% of the monthly gross income** for digital nomads … who operate in Uganda for **90 days or more**", plus a registration and monthly-declaration procedure. In `hybrid` mode, citing 2 sources. The same question **earlier in the same session** correctly refused: "the figures in it disagreed with the URA documents I was reading — so I have not shown it … a URA officer has been asked". So the integrity check that should catch this **fires non-deterministically**, which is worse than not having it: it cannot be relied on and it makes the failure hard to reproduce in review. Highest-severity finding in this document. | **Open.** Needs the answer-integrity gate made deterministic for a false-premise question, and a false-premise probe set in the CI gate — every existing probe asks about something real. |
| G41 (restated) 🔴 | The temporal case is the same defect as the geographic one. "What will Uganda's VAT rate be in 2031?" now answers "The standard VAT rate in Uganda is **18%** (FY2026-27)" with no caveat that 2031 is not FY2026-27 — an improvement on the calculator hijack, but still scope substitution. Treat jurisdiction and fiscal period as one guard on the deterministic rate path. | **Open.** |

**Open follow-ups.**

- `Data/eval/coverage_bank.jsonl` (105 taxpayer-voice probes) is entirely
  bare-question, so it cannot detect the G35 class at all. Adding
  preamble-framed variants needs `lg`/`sw` translations from a native speaker —
  `test_every_question_exists_in_every_language` requires all three, and
  inventing them would poison a multilingual gate.
- G37 makes the harness honest, not sufficient. Keyword coverage still cannot
  tell a correct answer from one carrying the right vocabulary; on VAT journey
  turn 2 the incorporation-forms page still clears the majority rule at 0.75
  while the better answer scores 1.00. Grading correctness needs a model judge.
- `tax_education_load_suite.py` counts `workflow` in `grounded_pct`, so a slot
  prompt still inflates that metric the way it used to inflate coherence. Left
  as-is deliberately: redefining a published number is the operator's call.

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

*Document version 2.1 — updated 2026-09-01: added §2.9 (serving-path defects
found by the load harness), corrected the model stack to Sunflower-14B-FP8 /
whisper-large-v3-salt / Spark-TTS-SALT, and re-derived the tool, workflow and
flag counts from the running registries (25 / 14 / 49).*
*Version 2.0 — 2026-04-28 after Phases 14-16 shipped.*
*Previous version (1.0) authored after Phase 1-13.*
*For questions about a specific gap or phase, open an issue
linked to the `roadmap/phase-XX` tag.*
