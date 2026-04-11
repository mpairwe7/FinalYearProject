# URA Chatbot — Production Gap Analysis & Agentic AI Roadmap

> Companion to `App/README.md` (which documents Phases 1–13 already
> shipped). This document covers **what is NOT yet in the codebase**
> and what it would take to move from "grounded FAQ chatbot" to
> "personalized tax assistant with agentic workflows".
>
> Audience: engineers planning the next release cycle + URA
> stakeholders evaluating what a production deployment looks like.

---

## 1. Current state (baseline)

Phases 1–13 give us a hardened, generic RAG chatbot with:

- **Hybrid retrieval** (Qdrant dense + BM25 RRF + cross-encoder rerank)
- **Grounded generation** (Qwen2.5-3B-Instruct, spotlight markers,
  token-aware trimming, structured-output option)
- **OWASP LLM Top 10 (2025) coverage** — prompt-injection guards,
  PII redaction, system-prompt leakage detection, grounding checks
- **Distributed resilience** — Redis rate limit, Redis semantic cache,
  circuit breakers around Qdrant and LLM, hard deadlines
- **Continuous evaluation** — Ragas-compatible harness, SLO alert rules
- **Next.js 16.2.3 + React 19.2 frontend** with glassmorphism UI,
  SSE streaming, optimistic feedback, same-origin `/api` proxy

**What this is good at:** answering *stateless, factual* questions
about URA policy from a static knowledge base.

**What this is not good at yet:** anything that depends on knowing
*who* the user is, *what they've been working on*, or *doing
something* beyond returning text.

---

## 2. Gap analysis

The gaps below are grouped by domain, each with user impact, the
current workaround (if any), the recommended fix, and the code
surface that would need to change. Effort estimates are rough:
**S** = 1–2 days, **M** = 3–5 days, **L** = 1–2 weeks, **XL** = 3+ weeks.

### 2.1 Identity & personalization

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G1 | **No user authentication.** Every session is anonymous (random UUID in `sessionStorage`). | Can't tailor answers to taxpayer type, can't enforce permissions, can't reference user's TIN. | `useAnalyticsStore.ts` generates an anon session id; `X-Session-ID` header only. | OIDC via URA SSO or a lightweight OAuth2 provider (Keycloak / Auth0). Add `X-Auth-Token` (JWT) header; backend verifies and maps to `user_id`. | New `backend/app/auth.py`, middleware in `main.py`, session schema changes in `database.py` | L |
| G2 | **No user profile.** No concept of `taxpayer_type` (individual / company / NGO / non-resident), preferred locale, TIN, industry, tax obligations. | Bot can't personalize — a sole trader and an accounting firm director get the same generic answer. | Frontend stores no profile; backend has no table. | New `users` + `user_profiles` tables; `GET/PUT /v1/me/profile` endpoints; frontend profile panel. | `database.py`, `postgres.py`, new `profiles.py`, `models.py`, `page.tsx` | L |
| G3 | **No consent / data-processing flow.** Uganda Data Protection Act 2019 + GDPR-adjacent frameworks require explicit consent for PII processing. | Hard blocker for any government deployment. | Only a retention TTL (`CONVERSATION_TTL_DAYS=7`). | Add consent banner, `consents` table with version, purpose, timestamp, withdrawal support. Integrate with `OutputGuard.redact_pii` so consented users can opt into richer personalization. | New `consent.py`, `database.py` schema, `ConsentBanner.tsx` | M |
| G4 | **No role-based access.** Staff, admins, and taxpayers need different views. | Can't ship the same app to both consumer and internal URA users. | No RBAC. | Add `role` column, `@requires_role` FastAPI dependency, feature-gate the UI. | `auth.py`, `main.py`, route decorators | M |

### 2.2 Memory & context

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G5 | **No long-term memory** across sessions. Multi-turn memory exists (`db.get_recent_turns`) but only within the 7-day retention window. | User who asks about VAT registration on Monday and returns Friday is treated as a stranger. | `conversations` table with 7-day TTL; no summarization, no facts extraction. | Add a **memory agent** that reads ended conversations and extracts facts into a `user_facts` table (k/v with provenance). Inject relevant facts into the system prompt at next turn. | New `backend/app/memory.py` (batch worker), `user_facts` table, `service.py` prompt builder | L |
| G6 | **No topic persistence.** If a user is working on "importing a car", every reply is standalone — no awareness that they're in the middle of a workflow. | Fragmented UX for multi-step tasks. | Conversation history is fetched, but there's no "current workflow" concept. | Add `conversation_topics` table + a lightweight topic classifier; surface `current_topic` to the LLM. | `service.py`, new `topics.py` module | M |
| G7 | **No temporal grounding.** The model doesn't know today's date, the current fiscal year, or that FY2024-25 rates are superseded by FY2025-26. | Users get stale rates/deadlines silently. | `SYSTEM_PROMPT` is static. | Inject `{"today": "2026-04-12", "fiscal_year": "FY2025-26"}` into the user turn on every call. Add a `temporal_context.py` helper. | `llm.py::_build_messages`, new `temporal.py` | S |
| G8 | **Conversation store is not an audit log.** TTL deletes data; no append-only, tamper-evident trail for regulatory disputes. | Can't answer "what did your bot tell this user on date X?" in a legal context. | SQLite / Postgres with `DELETE` on TTL. | Add an immutable `audit_events` table with hash-chained entries (each row hashes the previous); separate TTL from audit retention. | `database.py`, `postgres.py`, new `audit.py` | M |

### 2.3 Capabilities & actions

| # | Gap | User impact | Current state | Recommended fix | Code surface | Effort |
|---|---|---|---|---|---|---|
| G9 | **No tool use.** The LLM can only generate text from retrieved passages. It can't look up an exchange rate, compute a tax owed, check a filing status, or submit a form. | Users have to copy data into Excel and back. | Qwen2.5 supports function-calling but we don't expose any tools. | Introduce an **MCP server registry** (Model Context Protocol) and a tool-calling loop in the LLM layer. See §3. | New `backend/app/tools/`, `backend/app/mcp/`, refactor `llm.py` | XL |
| G10 | **No calculators.** PAYE, VAT, CGT, customs, income tax, effective rate. | Users who need "what would I actually owe?" answers are poorly served. | Not implemented. | Ship a `mcp_tax_calculator` server with one tool per tax type. Deterministic, unit-testable, fast. | New `backend/app/tools/calculators.py` as an MCP server | M |
| G11 | **No structured form flows.** Can't walk a user through "register for a TIN in 5 steps". | Support-centre call volume stays high. | Freeform chat only. | Add a **workflow engine** (simple finite-state-machine per flow) with slot filling. Each flow is a YAML file declaring steps, questions, validators, submission target. | New `backend/app/workflows/`, `models.py`, `page.tsx` | L |
| G12 | **No URA account actions.** Can't fetch filing status, balance, registered tax types, next due dates — even for the authenticated user. | Bot only talks *about* URA; doesn't help users actually interact with it. | No integration. | New `mcp_ura_account` server talking to URA's internal API (behind auth); exposes `get_tin_status`, `get_filing_status`, `list_returns_due`, etc. Scoped to the authenticated user. | New `backend/app/tools/ura_account.py`, `auth.py`, secrets mgmt | XL |
| G13 | **No document ingestion.** User can't upload a receipt, invoice, or tax cert to ask "is this correct?" | High-value use case for businesses is blocked. | Index-time PDF parsing exists (`indexer.ingest_pdfs`) but not query-time. | Add `POST /v1/upload` (size-limited, virus-scanned, PII-redacted), an OCR + table-extract pipeline, and a `mcp_document_parser` tool. Vision support via Qwen2.5-VL. | New `backend/app/uploads.py`, new `DocumentUpload.tsx`, `llm.py` multimodal branch | L |
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
| G20 | **No planning loop.** The pipeline is strictly one-shot: retrieve → generate. No "break this question into subtasks" reasoning. | Complex questions that span 3-4 sources don't get fully answered. | Linear 6-phase RAG pipeline. | Introduce a **planner-executor** agent that produces a JSON plan, then executes each step through the existing retriever + LLM. Feature-flagged via `FLAG_AGENTIC_MODE`. | New `backend/app/agent.py`, `llm.py` tool-use, `flags.py` | L |
| G21 | **No ReAct / self-correction.** Self-reflection exists (`SELF_REFLECT_ENABLED`) but only fires on low faithfulness, not on reasoning mistakes. | The bot happily confabulates when retrieval is insufficient instead of refining. | Single self-reflect pass. | Add a ReAct loop: `Thought → Action → Observation → Thought → …`, bounded by a max step count, with explicit "abort and ask user" action. | `service.py`, new `react.py` | L |
| G22 | **No per-specialty sub-agents.** A tax question should route to a tax-specialist prompt, a customs question to a customs prompt, etc. | One-size-fits-all prompting leaves quality on the table. | Single `SYSTEM_PROMPT` in `llm.py`. | Add a supervisor-specialist pattern (see §3). | New `agents/` tree | L |
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
| G32 | **No human-in-the-loop queue.** Escalation is detected (`escalation_required`) but there's no mechanism to route it to a live URA agent. | Escalation is a dead letter. | `escalation_required: true` in the response; frontend shows a banner. | Add a `tickets` table; escalated conversations push to a queue; staff dashboard lets agents reply, reply lands back in the user's conversation. | New `tickets.py`, staff UI, email/SMS notify | L |
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
                       │    (Qwen2.5-3B, T=0.1)      │
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

- **Supervisor is small + cheap.** Qwen2.5-3B at T=0.1 is fast
  enough to classify in <300 ms.
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

### 3.4 Memory architecture

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

## 4. Recommended next phases (14–20)

Each phase is scoped to be independently shippable. Dependencies
are explicit — don't start G at phase 20 if phase 15 isn't done.

### Phase 14 — Identity & user profile (G1, G2, G3, G4, G24)

**Goal:** Know who the user is and what they care about.
**Deliverables:**
- OIDC login (URA SSO if available, else Keycloak for demo)
- `users`, `user_profiles`, `consents` tables
- `GET/PUT /v1/me/profile` + profile UI route
- `consent banner` with versioned consents
- Profile-aware prompt injection in `_build_messages`

**Unlocks:** Every subsequent phase depends on having a user_id.
**Effort:** ~2 weeks.
**Risks:** Auth integration delays; privacy review latency.

### Phase 15 — Tool-calling foundation (G9, G10)

**Goal:** Let the LLM call deterministic tools for things it
shouldn't be asked to generate (numbers, dates, lookups).
**Deliverables:**
- MCP client in `backend/app/mcp/`
- Tool registry with schema validation
- `mcp_rag`, `mcp_tax_calculator`, `mcp_rates`, `mcp_calendar`
- Tool-call loop in `llm.generate()` (feature-flagged via
  `FLAG_TOOL_USE`)
- OTel spans around tool calls
- Unit tests per tool

**Unlocks:** Phases 16–20.
**Effort:** ~3 weeks.
**Risks:** Qwen2.5-3B's tool-calling is reliable but not perfect;
expect to fall back to text responses sometimes.

### Phase 16 — Workflow engine + document ingestion (G11, G13)

**Goal:** Multi-step flows ("register for TIN") and uploads.
**Deliverables:**
- YAML-declared workflows loaded at startup
- Slot-filling state machine keyed on `conversation_id`
- `POST /v1/upload` with size/virus scanning
- `mcp_document_parser` tool (Qwen2.5-VL for vision)
- UI: upload button, step progress indicator

**Dependencies:** Phase 14 (auth), Phase 15 (tool framework).
**Effort:** ~2-3 weeks.
**Risks:** Workflow engine complexity; document parser quality.

### Phase 17 — Long-term memory agent (G5, G6, G24, G25)

**Goal:** Remember the user across sessions.
**Deliverables:**
- `user_facts` + `conversation_summaries` tables
- Offline memory worker (APScheduler or Celery)
- Fact-extraction JSON-mode prompt
- `mcp_memory` tool
- Per-segment metrics in `evaluation.py`

**Dependencies:** Phase 14 (user_id), Phase 15 (tools).
**Effort:** ~2 weeks.
**Risks:** Fact extraction hallucinations (mitigated by
provenance + confidence score); privacy concerns (mitigated by
consent and user-facing "memory" controls).

### Phase 18 — Human-in-the-loop queue (G32)

**Goal:** Actually route escalated conversations to URA staff.
**Deliverables:**
- `tickets` table with status, assignee, priority
- Staff dashboard route in `/admin/tickets`
- Real-time push via WebSocket when tickets arrive
- Reply-back mechanism surfaces in the user's chat
- SLA tracking (time to first response, time to resolution)

**Dependencies:** Phase 14 (auth, RBAC).
**Effort:** ~2 weeks.

### Phase 19 — Agentic supervisor + specialists (G20, G21, G22, G23)

**Goal:** Move from linear RAG to supervised planning.
**Deliverables:**
- Supervisor agent in `backend/app/agents/supervisor.py`
- Specialist agents: tax, customs, account, forms
- LangGraph-style state machine
- ReAct loop with max-step bound
- Per-agent prompts + eval suites
- `FLAG_AGENTIC_MODE` feature flag, off by default

**Dependencies:** Phases 14, 15, 17.
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

---

## 5. Minimum viable personalized experience

If you could only ship three things next quarter, do these in order:

1. **Phase 14 — Identity & profile.** Without a `user_id`, nothing
   else personalization-related works.
2. **Phase 15 — Tool-calling foundation.** Unlocks calculators and
   live data, which is the fastest way to move users from "asked a
   question" to "solved my problem".
3. **Phase 17 — Long-term memory.** Turns the bot from a search box
   into an assistant that knows the user.

Phases 16, 18, 19, 20 are high-value but stack on top of those three.

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

- **Does not re-specify Phases 1-13.** See `App/README.md` for the
  current production-ready flow.
- **Does not prescribe a specific LLM vendor.** The architecture
  works with Qwen on-prem, vLLM-hosted Llama, or hosted APIs —
  pick based on compliance, cost, and latency.
- **Does not cover frontend redesigns beyond profile / admin /
  ticket views.** The existing UI (Phase 13 glassmorphism) is
  already production-grade.
- **Does not include infra costing.** Phase 14 adds SSO + Postgres,
  Phase 19 adds ~3x LLM calls per request (supervisor +
  specialist + tool) — costs need modeling once tool and agent
  selection is locked.

---

*Document version 1.0 — authored after Phase 1-13 shipped.*
*For questions about a specific gap or phase, open an issue
linked to the `roadmap/phase-XX` tag.*
