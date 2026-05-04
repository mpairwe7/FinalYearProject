# URA Chatbot — Production Gap Analysis & Agentic AI Roadmap

> Companion to `App/README.md` (which documents Phases 1–16) and
> `docs/AGENT_ARCHITECTURE.md` (which documents the agent runtime).
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

- **Hybrid retrieval** (Qdrant dense + BM25 RRF + cross-encoder rerank)
- **Grounded generation** (Qwen3-8B, spotlight markers,
token-aware trimming, structured-output option)
- **OWASP LLM Top 10 (2025) coverage** — prompt-injection guards,
PII redaction, system-prompt leakage detection, grounding checks
- **Distributed resilience** — Redis rate limit, Redis semantic cache,
circuit breakers around Qdrant and LLM, hard deadlines
- **Continuous evaluation** — Ragas-compatible harness, SLO alert rules
- **Next.js 16.2.3 + React 19.2 frontend** with glassmorphism UI,
SSE streaming, optimistic feedback, same-origin `/api` proxy
- **Full test pyramid** — backend pytest with a current 35% CI coverage
ratchet, frontend Vitest + Playwright E2E, Flutter mobile CI, k6 load tests
- **Production observability** — Prometheus + Grafana + Jaeger (docker-compose
`--profile monitoring`), 5 SLO alerting rules, pre-built dashboards
- **Security-as-code** — cosign container signing, SLSA v1.2 provenance,
OWASP ZAP DAST, AI red teaming (50 NIST AI 600-1 prompts)
- **Compliance artefacts** — Model Card (EU AI Act Art. 53), PIA (NDPA §28),
bias audit, carbon tracking, incident response simulation
- **Auth system** — JWT (HS256/RS256), RBAC with 5 roles, consent
management (UDPA 2019), user profiles (`auth/` directory)
- **Tool-calling framework** — 6 tool modules, ToolRegistry,
generate_with_tools loop (`tools/` directory)
- **Supervisor routing** — 7 routes with per-specialist tool
whitelists (`agents/` directory)
- **Guided workflows** — 5 YAML-declared workflows with slot filling
(`workflows/` directory)
- **Ticket queue** — CRUD admin endpoints, escalation tool
- **Speech pipeline** — ASR (Whisper), TTS (Piper), MT, Sunbird AI
cloud fallback
- **Memory system** — semantic facts, episodic summaries, working
memory (`memory/` directory)
- **Audit ledger** — hash-chained, Merkle tree proofs (`audit/`
directory)
- **Feature flags** — 18 flags via `flags.py`
- **PostgreSQL backend** option alongside SQLite

**What this is good at:** answering factual questions about URA
policy, performing tax calculations, routing to specialists,
walking users through guided workflows, and escalating to staff.

**What this is not good at yet:** deep multi-step planning (ReAct),
live URA account integration, document ingestion at query time,
proactive notifications, or multi-tenant deployment.

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
| G6 | **No topic persistence.** If a user is working on "importing a car", every reply is standalone — no awareness that they're in the middle of a workflow. | Fragmented UX for multi-step tasks. | Conversation history is fetched, but there's no "current workflow" concept. | Add `conversation_topics` table + a lightweight topic classifier; surface `current_topic` to the LLM. | `service.py`, new `topics.py` module | M |
| G7 🟢 | **No temporal grounding.** ~~The model doesn't know today's date, the current fiscal year…~~ **SHIPPED Phase 14-A** — `get_current_date` and `get_next_deadlines` tools return today's date, day-of-week, fiscal year (`FY2025-26`), days-into-FY, days-remaining, and the next N deadlines.  The LLM calls them explicitly whenever a query mentions "today"/"now"/"this year"/"deadline", per the supervisor's temporal patterns and the `TOOL_USE_PROMPT_SUFFIX` rules. | — | Done. | `backend/app/tools/calendar.py` | Done (S actual) |
| G8 🟢 | **~~Conversation store is not an audit log.~~** **SHIPPED Phase 16** — `audit/` directory with hash-chained, append-only audit ledger and Merkle tree proofs for tamper evidence. Separate from conversation TTL. | — | Done. | `backend/app/audit/` directory | Done |

### 2.3 Capabilities & actions

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G9 🟢 | **No tool use.** ~~The LLM can only generate text from retrieved passages.~~ **SHIPPED Phase 14-B** — `generate_with_tools()` in `llm.py` runs a bounded tool-call loop using Qwen chat-template tool formatting; `ToolRegistry` dispatches via `.call()`; 11 tools auto-registered. Flagged by `FLAG_TOOL_USE`. | — | Done. | Done. | Done (S actual) |
| G10 🟢 | **No calculators.** ~~PAYE, VAT, CGT, customs, income tax, effective rate.~~ **SHIPPED Phase 14-A** — 5 deterministic calculators (`calculate_vat`, `calculate_paye` with progressive bands, `calculate_corporation_tax`, `calculate_capital_gains`, `calculate_customs_duty`) all backed by FY2025-26 rate tables, unit-tested (29 pytest assertions covering arithmetic + edge cases + error paths). | — | Done. | `backend/app/tools/calculators.py` | Done (S actual) |
| G11 🟢 | **~~No structured form flows.~~** **SHIPPED Phase 15** — `workflows/` directory with 5 YAML-declared workflows loaded at startup via `loader.py`. Slot-filling state machine (`slots.py`), workflow registry (`registry.py`), keyed on `conversation_id`. | — | Done. | `backend/app/workflows/` directory | Done |
| G12 | **No URA account actions.** Can't fetch filing status, balance, registered tax types, next due dates — even for the authenticated user. | Bot only talks *about* URA; doesn't help users actually interact with it. | No integration. | New `mcp_ura_account` server talking to URA's internal API (behind auth); exposes `get_tin_status`, `get_filing_status`, `list_returns_due`, etc. Scoped to the authenticated user. | New `backend/app/tools/ura_account.py`, `auth.py`, secrets mgmt | XL |
| G13 | **No document ingestion.** User can't upload a receipt, invoice, or tax cert to ask "is this correct?" | High-value use case for businesses is blocked. | Index-time PDF parsing exists (`indexer.ingest_pdfs`) but not query-time. | Add `POST /v1/upload` (size-limited, virus-scanned, PII-redacted), an OCR + table-extract pipeline, and a `mcp_document_parser` tool. Vision support should use a pinned vision-capable model. | New `backend/app/uploads.py`, new `DocumentUpload.tsx`, `llm.py` multimodal branch | L |
| G14 | **No scheduled notifications.** Nothing reminds the user "your quarterly VAT return is due in 3 days". | Missed opportunity for value-add engagement. | No scheduler. | Add an APScheduler / Temporal worker that reads user deadlines and dispatches via email / SMS / in-app. | New `backend/app/scheduler.py`, notification channels | L |
| G15 | **No URA live data.** FAQ CSVs were indexed once; new circulars, rate changes, and press releases never reach the bot. | Staleness within weeks of deployment. | Manual re-index via `POST /v1/index`. | Add a nightly ingestion worker: scrape `ura.go.ug/news`, diff against last run, re-embed, upsert to Qdrant. | New `backend/app/workers/news_ingest.py` | M |

### 2.4 Knowledge gaps

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G16 | **Unstructured retrieval only.** Everything lives in one Qdrant collection as flat text passages. There's no knowledge graph of {tax_type → rules → rates → exemptions → filing_cycle → forms}. | Queries that require joining across facts ("VAT rate for non-resident importing a car") fall back to keyword luck. | 487 passages in `ura_knowledge_base`. | Add a **GraphRAG** layer: extract entities + relations into a Neo4j/Kùzu graph; use graph traversal for compositional queries, vector retrieval for leaf facts. | New `backend/app/graph.py`, ingestion extension, new retriever branch | XL |
| G17 | **No metadata-aware retrieval.** Can't filter to "only FY2025-26 sources" or "only VAT passages" at query time, even though the payload has the fields. | Stale-document answers when multiple fiscal years share the collection. | `HybridRetriever.search` supports filters but no caller uses them. | Route queries through a tiny classifier → extract `filters={doc_type, fiscal_year, tax_type}` → pass to `search()`. | `service.py`, `query.py` | S |
| G18 | **No multilingual retrieval.** Users writing in Luganda get routed through the same English index; the prompt just says "respond in Luganda". | Poor recall on non-English queries. | Single language index. | Swap `DENSE_MODEL` to a multilingual embedder (BGE-M3 or multilingual-e5-large) and re-index. Already planned as Phase 11 default — blocked on NVIDIA driver ≥550 on the current host (see `App/README.md` → "Host-level gotchas"). | `retriever.py`, `indexer.py`, ops | S (code) / L (ops) |
| G19 | **No citation provenance.** Citations show file + section, but not the URA URL the document came from. | Users can't click through to the original notice. | Payload has `source` filename only. | Extend indexer to store a canonical URL + effective date per passage; surface those in the UI. | `indexer.py`, `retriever.py`, `models.py`, `page.tsx` | S |

### 2.5 Agentic reasoning

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G20 🟢 | **~~No planning loop.~~** **SHIPPED Phase 15** — `agents/supervisor.py` with 7 routes, per-specialist tool whitelists, and a bounded tool-call loop (up to 3 iterations per request). Flagged by `FLAG_AGENTIC_MODE`. Full planner-executor (JSON plan, tree of thought) remains future work for Phase 17+. | — | Done. | `backend/app/agents/supervisor.py` | Done |
| G21 | **No ReAct / self-correction.** Self-reflection exists (`SELF_REFLECT_ENABLED`) but only fires on low faithfulness, not on reasoning mistakes. | The bot happily confabulates when retrieval is insufficient instead of refining. | Single self-reflect pass. | Add a ReAct loop: `Thought → Action → Observation → Thought → …`, bounded by a max step count, with explicit "abort and ask user" action. | `service.py`, new `react.py` | L |
| G22 🟡 | **No per-specialty sub-agents.** ~~A tax question should route to a tax-specialist prompt…~~ **PARTIAL Phase 14-C** — the supervisor now routes customs vocabulary to `CUSTOMS_SPECIALIST` with a narrowed tool whitelist (`calculate_customs_duty`, `search_ura_knowledge_base`, `get_current_date`).  `TAX_SPECIALIST` is reserved but uses the base prompt today. | Per-specialist system prompts not yet written. | Partial — next step: add `agents/prompts/` with per-route system prompts. | `backend/app/agents/supervisor.py` | Done (partial) |
| G23 | **No delegation between agents.** When the planner calls a specialist, the specialist can't call back to the planner for more context. | Limits the depth of reasoning chains. | N/A. | Use LangGraph-style message passing with explicit state (`AgentState` typed dict). | `agent.py` | M |
| G24 | **No per-user prompt tuning.** The system prompt is one constant, regardless of who asks. | Beginners vs accountants get the same jargon level. | Static prompt. | Parameterize `SYSTEM_PROMPT` on user profile fields (`detail_level: beginner | intermediate | expert`). | `llm.py::_build_messages`, `service.py` | S |

### 2.6 Evaluation & quality

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G25 | **No per-segment quality metrics.** The eval harness gives one faithfulness score for everyone. | Can't see that "individual taxpayer" queries are well served but "customs agent" queries aren't. | `evaluation.run_evaluation()` computes global means. | Extend `EvalReport` with `by_segment` dimensions (taxpayer_type, topic, locale). | `evaluation.py`, `main.py::run_eval` | S |
| G26 | **No A/B testing.** Can't ship a new prompt, new embedder, or new personalization strategy to 10% of traffic and compare. | Risk-averse, slow iteration. | No flag-based routing to experiments. | Add experiment flags (`experiment:prompt_v2:percent=10`) and log the experiment variant on each conversation. | `flags.py`, `analytics.py`, `database.py` | M |
| G27 | **No drift detection on the index.** When URA updates a policy, Qdrant still returns the old passage — nothing signals staleness. | Silent failure mode. | Manual re-index. | Hash each source file at index time; nightly compare; alert on delta; auto-enqueue re-index. | `indexer.py`, new `freshness.py` worker | M |
| G28 | **No red-team fixtures.** Prompt-injection patterns are hand-written regex; no automated adversarial testing. | Coverage gaps. | `_INJECTION_PATTERNS` in `guardrails.py`. | Integrate PurpleLlama / promptmap test suite into CI. | `tests/security/`, `.github/workflows/` | S |
| G29 | **No human feedback loop into training.** Thumbs-down feedback accumulates but doesn't fine-tune anything. | Improvement requires manual prompt engineering. | `export_review_feedback` exists, no training pipeline consumes it. | Build a weekly DPO/KTO fine-tuning job from the negative feedback set; promote via the model registry. | `ml/` pipeline, model registry | XL |

### 2.7 Operations & multi-tenancy

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G30 | **Single tenant.** One knowledge base, one prompt, one model for everyone. | Can't offer this to KCCA, NSSF, or private firms under the same codebase. | Implicit single-tenancy. | Add `tenant_id` everywhere (users, conversations, Qdrant collections, rate limits). | Backend-wide, DB schema changes | L |
| G31 | **No admin UI.** Ops staff can't curate content, approve uploads, override bot answers, manage flags without SSH'ing to the server. | Non-technical staff can't operate the system. | CLI + curl only. | Small admin Next.js route with RBAC gating `/admin/*`. | New routes, `auth.py`, UI | L |
| G32 🟢 | **~~No human-in-the-loop queue.~~** **SHIPPED Phase 15** — `tickets` table with CRUD, `escalate_to_human` tool, 4 admin REST endpoints (`GET/PATCH /v1/admin/tickets[/{id}][/stats]`). Supervisor `ESCALATE` route persists tickets when `FLAG_TICKET_QUEUE=true`. `ticket_id` surfaces in `ChatResponse`. | Staff UI (Next.js `/admin/tickets` page) remains future work. | Done (backend). | `backend/app/database.py`, `backend/app/tools/`, `backend/app/main.py` | Done |
| G33 | **No SLO-driven autoscaling.** Prometheus alerts exist, but no action is taken — no HPA, no Kubernetes operator. | Manual intervention during load spikes. | Alert rules only. | Kubernetes HPA on `chat_response_time_ms` p95, plus a KEDA scaler on Redis queue depth. | Infra, new `k8s/` manifests | M |
| G34 | **No chaos / failure drills.** We've hardened against failure modes but never exercised them end-to-end. | Unknown unknowns in prod. | Unit tests only. | Add Litmus/ChaosMesh experiments: kill Redis, spike Qdrant latency, kill LLM worker, measure recovery. | `tests/chaos/`, CI schedule | M |

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
      │    (Qwen3-8B, T=0.1)        │
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
already-loaded Qwen3-8B runtime at low temperature; a smaller pinned
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

Also: keep `FLAG_AGENTIC_MODE=false` as the default. Ship as an
opt-in beta, gated on a specific user tier, for the first 90 days.

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
6 tool modules — calculators, rates, calendar, KB search, escalation.
- ✅ Tool-call loop in `llm.generate_with_tools()` (feature-flagged
via `FLAG_TOOL_USE`).
- ✅ Supervisor router (`agents/supervisor.py`) with 7 routes and
per-specialist tool whitelists.
- ✅ Ticket queue with CRUD admin endpoints, `escalate_to_human` tool.
- ✅ 5 YAML-declared workflows via `workflows/` directory (loader,
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
- ✅ Speech pipeline — ASR (Whisper), TTS (Piper), MT, Sunbird AI
cloud fallback (`speech_service.py`, `sunbird.py`).
- ✅ 18 feature flags via `flags.py`.
- ✅ PostgreSQL backend option.

### Phase 17 — Document ingestion + topic persistence (G6, G13, G25)

**Goal:** Query-time document uploads and topic-aware conversations.
**Deliverables:**
- `POST /v1/upload` with size/virus scanning
- `mcp_document_parser` tool (pinned vision model, OCR + table extract)
- `conversation_topics` table + lightweight topic classifier
- Per-segment quality metrics in `evaluation.py`
- UI: upload button, step progress indicator

**Dependencies:** Phase 14 (auth), Phase 15 (tool framework).
**Effort:** ~2-3 weeks.
**Risks:** Document parser quality; topic classifier accuracy.

### Phase 18 — Staff dashboard + ticket UI (G32 follow-up, G31)

**Goal:** Give URA staff a UI for the ticket queue (backend shipped in Phase 15).
**Deliverables:**
- Staff dashboard route in `/admin/tickets` (Next.js)
- Real-time push via WebSocket when tickets arrive
- Reply-back mechanism surfaces in the user's chat
- SLA tracking (time to first response, time to resolution)
- Admin UI for flag management, content curation

**Dependencies:** Phase 14 (auth, RBAC) — already shipped.
**Effort:** ~2 weeks.

### Phase 19 — Deep planning + ReAct (G21, G22, G23)

**Goal:** Extend the shipped supervisor into a full planner-executor.
**Deliverables:**
- ReAct loop with max-step bound (`Thought -> Action -> Observation`)
- Per-specialist system prompts in `agents/prompts/`
- LangGraph-style state machine with delegation between agents
- LLM-based supervisor classifier (replace rule-based soft-misses)
- Per-agent eval suites

**Dependencies:** Phases 14-16 (all shipped).
**Effort:** ~3-4 weeks.
**Risks:** Latency (each agent hop adds 300-800 ms); reasoning
chain brittleness; cost if hosted.

### Phase 20 — Proactive engagement (G14, G15, G27)

**Goal:** Bot reaches out *before* the user asks.
**Deliverables:**
- Scheduler (APScheduler) with cron-like deadlines
- Notification channels (email via Resend/SES, SMS via
Africa's Talking, in-app)
- Fresh-data ingestion worker (URA news scraper + nightly reindex)
- Index-freshness alert
- User-facing notification preferences

**Dependencies:** Phases 14, 17.
**Effort:** ~2 weeks.

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
6 tool modules, 7 supervisor routes, 5 YAML workflows, ticket queue.
3. **Phase 16 — Memory, audit, speech.** 🟢 Shipped. Three-tier memory
(semantic, episodic, working), hash-chained audit ledger, speech pipeline.

4. **Phase 23 — Voice-first streaming.** 🟢 Shipped. WebSocket streaming
voice chat with VAD + barge-in, sentence-chunked TTS, offline RAG (FAISS),
accent detection (5 Ugandan profiles), voice consent & audit trail,
full-screen mobile voice-first UI.

**Next priorities:** Phases 17-20 (document ingestion, staff UI, deep
planning, proactive engagement) stack on top of the shipped foundation.

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
| Voice-first (Phase 23) | `backend/app/voice_stream.py`, `backend/app/voice_ws.py`, `backend/app/voice_consent.py`, `backend/app/offline_rag.py`, `backend/app/accent_detector.py`, `frontend/src/components/VoiceChat.tsx`, `frontend/src/services/voiceWebSocket.ts`, `frontend/src/store/useVoiceStore.ts`, `frontend/src/hooks/useVoiceWebSocket.ts`, `frontend/src/components/CameraCapture.tsx`, `frontend/public/audio-worklet-processor.js` | `backend/app/speech_service.py`, `backend/app/flags.py`, `backend/app/models.py`, `backend/app/main.py`, `backend/app/database.py`, `backend/app/tracing.py`, `frontend/src/app/page.tsx`, `frontend/src/services/voiceService.ts`, `frontend/src/components/Icons.tsx`, `frontend/src/app/globals.css` |
| Scheduler + freshness | `backend/app/scheduler.py`, `backend/app/workers/news_ingest.py`, `backend/app/workers/freshness.py`, `backend/app/tools/notify.py` | `backend/app/main.py`, `docker-compose.yml` |

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

*Document version 2.0 — updated 2026-04-28 after Phases 14-16 shipped.*
*Previous version (1.0) authored after Phase 1-13.*
*For questions about a specific gap or phase, open an issue
linked to the `roadmap/phase-XX` tag.*
