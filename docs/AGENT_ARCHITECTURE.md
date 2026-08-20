# URA Chatbot — Agent Runtime Architecture

> Companion to `docs/GAPS_AND_AGENTIC_ROADMAP.md`.  The roadmap
> doc describes what was *missing*; this doc describes what the
> `feat/agentic-workflows` branch actually *ships* (Phases A-D of
> the broader Phase 14 work).

Most capabilities below are **gated behind a feature flag**.
`FLAG_TOOL_USE` and `FLAG_TICKET_QUEUE` default OFF.
`FLAG_AGENTIC_MODE` defaults ON after the English routing golden set
holds ≥ 0.95 (`agentic_mode_gate`).

| Flag | Purpose |
|---|---|
| `FLAG_TOOL_USE` | Allow the LLM to call registered tools via Qwen2.5 native function-calling. |
| `FLAG_AGENTIC_MODE` | Route every request through the supervisor classifier before retrieval. |
| `FLAG_TICKET_QUEUE` | Persist supervisor-driven escalations to the `tickets` table. |
| `FLAG_AUDIT_LEDGER` | Write hash-chained audit events on every `service.generate()` return path. |

Flags can be set per-process via env (`FLAG_TOOL_USE=true …`) or
per-request in tests via `flags.set("tool_use", True)`.

---

## 1. 30-second summary

```
┌────────────────────────────────────────────────────────────────┐
│                       User query                              │
└──────────────┬─────────────────────────────────────────────────┘
               ▼
      ┌─────────────────┐
      │  InputGuard     │  OWASP LLM01 — prompt injection check
      └──────┬──────────┘
             ▼
      ┌─────────────────┐
      │ Semantic cache  │  Redis-backed hit-through (Phase 7)
      └──────┬──────────┘
             ▼
      ┌────────────────────────────────────────────────┐
      │             Supervisor (Phase C)               │
      │       rule-based classifier, <1 ms             │
      │                                                │
      │  CLARIFY ───▶ early return (clarify prompt)    │
      │  ESCALATE ──▶ early return + ticket (Phase D)  │
      │  TOOLS ─────▶ tool-call loop w/ whitelist      │
      │  TAX_/CUSTOMS_SPECIALIST ─▶ tool-call loop     │
      │  RAG ───────▶ existing Phase 1-13 pipeline     │
      │  BLOCKED ───▶ unreachable (InputGuard ate it)  │
      └──────┬─────────────────────────────────────────┘
             ▼
      ┌─────────────────────────────────────────────────┐
      │            Hybrid retrieval (Qdrant v1.19.0)      │
      │   dense + BM25 RRF + cross-encoder rerank       │
      └──────┬──────────────────────────────────────────┘
             ▼
      ┌─────────────────────────────────────────────────┐
      │                Qwen2.5-3B on GPU                │
      │  - force_agentic → generate_with_tools() loop   │
      │    • parse <tool_call> blocks                   │
      │    • dispatch via ToolRegistry                  │
      │    • append `tool` message, regenerate          │
      │    • bounded max_iterations=3                   │
      │  - otherwise → plain generate() (Phase 1-13)    │
      └──────┬──────────────────────────────────────────┘
             ▼
      ┌─────────────────────────────────────────────────┐
      │  OutputGuard — PII, sanitise, prompt leakage   │
      │  Grounding check (RAGAS-lite)                   │
      │  Escalation check                               │
      └──────┬──────────────────────────────────────────┘
             ▼
                    ChatResponse
             reply + citations + ticket_id
```

---

## 2. Phase inventory (what's in the branch)

| Phase | Files added / touched | Purpose |
|---|---|---|
| **A** | `backend/app/tools/{__init__,calculators,calendar,rates,rag_tool,escalate}.py` + `flags.py` | Tool framework (ABC + registry + schema) and 11 starter tools |
| **B** | `backend/app/llm.py` + `service.py` | `generate_with_tools()` — bounded tool-call loop wired into the LLM layer, gated by `FLAG_TOOL_USE` |
| **C** | `backend/app/agents/{__init__,state,supervisor}.py` + `service.py` | Supervisor classifier (rule-based), `AgentRoute` enum, `AgentState` TypedDict; routes to CLARIFY/ESCALATE/TOOLS/SPECIALIST/RAG |
| **D** | `backend/app/database.py` (+ schema) + `backend/app/tools/escalate.py` + `service.py` + `main.py` + `models.py` | `tickets` table + CRUD + `escalate_to_human` tool + 4 admin endpoints + `ticket_id` surfacing in ChatResponse |

All code is under the `feat/agentic-workflows` branch.

---

## 3. Tool inventory

Every tool is **auto-registered** on package import via the
`backend/app/tools/__init__.py` auto-import block.  Adding a new
module + one line there makes the tool available to every agent
in the supervisor-specialist graph.

| Tool name | Risk | Side effects | Purpose |
|---|---|---|---|
| `calculate_vat` | low | pure fn | Add or extract 18% VAT on an amount |
| `calculate_paye` | low | pure fn | Monthly PAYE on employment income (progressive bands) |
| `calculate_corporation_tax` | low | pure fn | 30% CIT on chargeable income |
| `calculate_capital_gains` | low | pure fn | Corporate CGT = 30% × (sale − cost) |
| `calculate_customs_duty` | low | pure fn | CIF + duty + VAT landed cost estimator |
| `get_current_date` | low | read-only | Today's date + Ugandan fiscal year + days remaining |
| `get_next_deadlines` | low | read-only | Next N upcoming URA filing deadlines |
| `lookup_rate` | low | read-only | Single tax rate by key (`vat_standard`, `corporation_tax`, …) |
| `list_available_rates` | low | read-only | All known rates in one call |
| `search_ura_knowledge_base` | low | read-only | Existing hybrid retriever exposed as a tool |
| `escalate_to_human` | **medium** | writes to `tickets` | Route the conversation to a URA officer via the ticket queue |

**Risk tiers** drive per-specialist tool scoping:

```python
ToolRegistry.openai_specs(allow_risk=["low"])          # RAG specialist
ToolRegistry.openai_specs(allow_risk=["low","medium"]) # customs specialist
```

Tools with `requires_confirmation=True` on their schema are
**reserved** for a future UI confirmation step — not yet enforced,
but plumbed through so adding `mcp_ura_actions` in Phase 14+ won't
break the contract.

### Adding a new tool — the 3-step recipe

1. Create `backend/app/tools/mytool.py` that inherits `Tool`:

   ```python
   from . import Tool, ToolSchema, ToolRegistry

   class MyTool(Tool):
       @property
       def schema(self) -> ToolSchema:
           return ToolSchema(
               name="my_tool",
               description="Plain-English instruction to the LLM. Explain WHEN to call this and what the result means.",
               parameters={
                   "type": "object",
                   "properties": {"arg1": {"type": "number"}},
                   "required": ["arg1"],
               },
               risk="low",
           )

       def execute(self, arg1: float) -> dict:
           return {"ok": True, "result": arg1 * 2}

   ToolRegistry.register(MyTool())
   ```

2. Add `from . import mytool as _mytool  # noqa: E402, F401` to
   `backend/app/tools/__init__.py` so the module loads on package import.

3. Add a pytest file `tests/agents/test_mytool.py` exercising the tool.

No other code changes are required.  The LLM picks it up the next
request via `ToolRegistry.openai_specs()`.

---

## 4. Supervisor routing table

The supervisor uses **ordered rule chains** (not lookaheads): each
pattern list is checked in order, and the first match wins.  When
no rule matches, the query falls through to `RAG` (the Phase 1-13
pipeline, which is still the safe default).

The routing is handled in
`backend/app/agents/supervisor.py::Supervisor.classify()`.

| Priority | Route | Trigger | Pre-conditions |
|---|---|---|---|
| 1 | `ESCALATE` | Human-contact phrases (`speak to`, `talk to`, `contact a human/agent/officer`) | None |
| 1 | `ESCALATE` | Dispute / legal vocab (`dispute`, `objection`, `audit`, `appeal`, `court`, `lawyer`, `fraud`) | None |
| 1 | `ESCALATE` | Account-specific (`my TIN`, `my filing`, `my return`, `my account`, `my balance`) | None |
| 2 | `CLARIFY` | Single-word stop-word-only queries | No conversation history |
| 3 | `TOOLS` | Calculation intent: `how much X` / `calculate X` for VAT, PAYE, CIT, CGT, customs duty | None |
| 4 | `TOOLS` | Temporal: `today`, `now`, `current fiscal year`, `next deadline`, `this month` | None |
| 5 | `TOOLS` | Rate lookup: `what's the rate`, `list rates` | None |
| 6 | `CUSTOMS_SPECIALIST` | Customs vocabulary (`import`, `export`, `bill of lading`, `CIF`, `EAC CET`, `tariff`, `clearance`) | None |
| 7 | `RAG` | Default fallback | Always |

Each `RouteDecision` carries:

- `route` — which branch to take
- `reason` — human-readable explanation (logged to OTel spans)
- `confidence` — 0-1 float
- `suggested_tools` — whitelist passed to `generate_with_tools()`
- `clarification_question` — populated only for `CLARIFY`

### Known soft misses (and why they're fine)

The rule patterns deliberately use ordered regexes (not lookaheads)
so they're easy to read and test.  This means a few query shapes
fall through to their second-best route instead of the most
specific match:

Re-measured **2026-08-08** against the live supervisor. Two of the four
rows this table used to carry are no longer true — the amount-first fix
(#219) closed them — which is why the table now records what the router
actually does rather than what it did:

| Query | Ideal | Actual | Note |
|---|---|---|---|
| `"What's my take-home pay on a 2M salary?"` | `TOOLS` (PAYE calc) | ✅ `TOOLS` → `calculate_paye` | **Fixed in #219.** The amount-plus-intent path catches it regardless of word order. |
| `"How much customs duty on a 5m CIF?"` | `TOOLS` (customs calc) | ✅ `TOOLS` → `calculate_customs_duty` | Matches the customs calculation pattern before the specialist check. |
| `"What are the current PAYE percentages?"` | `TOOLS` (rate lookup) | `RAG` | Plural `what are` doesn't match `what(?:'s\| is)`; RAG returns cited passages. |
| `"I sold my shares, what's the CGT?"` | `TOOLS` (CGT calc) | `RAG` | `my shares` is *not* in the account-specific escalation guard (`my tin\|filing\|return\|account\|balance`), and the CGT pattern needs the phrase "capital gains". |

Both remaining rows still produce correct, cited answers — just through
retrieval rather than a calculator.

The LLM classifier that would close them landed in #230 as
`agents/tiebreak.py`, behind `FLAG_SUPERVISOR_LLM_TIEBREAK` (default
off). Its blast radius is measured, not assumed:

- It fires **only below `SUPERVISOR_LLM_THRESHOLD`** (default 0.70).
  Rule confidences run 0.6 (fall-through) → 0.78 (customs) → 0.92
  (calculation) → 0.95 (escalation) → 1.0 (greeting), so at the default
  the model sees the fall-through cases and nothing else. On the English
  golden set that is **5 of 36 cases (14%)**; the other 31 never reach a
  model.
- It **cannot override an escalation, a greeting, or blocked input**, and
  it cannot *choose* `ESCALATE` — raising a ticket on a vague question
  would send routine traffic to staff.
- It **fails open**. No model, a timeout, unparseable output, or an
  invented route name all return the rule decision unchanged; a
  classifier that can break routing is worse than no classifier.
- A changed decision carries `confidence=0.65`, below every matched-rule
  confidence, so an inferred route never outranks a stated one — which
  matters because tier selection reads that number.
- Classifications are cached on the normalized query (case, whitespace,
  trailing punctuation), bounded at 512 entries.

The supervisor stays pure Python on the default path: the module is
imported lazily inside the flag check, so a closed flag costs nothing.

### Adding a new route — the 2-step recipe

1. Add a new value to the `AgentRoute` enum in
   `backend/app/agents/state.py` (the string value is stable and
   logged to analytics — don't rename existing ones).

2. Add a regex + trigger entry to the relevant pattern table in
   `supervisor.py`, or add a whole new `_<name>_PATTERNS` list and
   reference it from `classify()`.  Also handle the route in
   `service.py::generate()` — early-return for short-circuit
   routes, or set `force_agentic` + `force_tool_whitelist` for
   tool-using routes.

---

## 5. LLM tool-calling loop (Phase B)

`backend/app/llm.py::generate_with_tools()` implements a bounded
iterative loop:

```
iteration 1:
  apply_chat_template(messages, tools=[...])    # Qwen2.5 injects tools
  generate once                                 # Qwen2.5-3B on GPU
  parse <tool_call>{...}</tool_call> blocks
  if none → return text (terminal)
  else → dispatch via ToolRegistry.call()
         append assistant message with tool_calls
         append `tool` role message per result

iteration 2: same, with tool results now in context
iteration 3: same (final allowed iteration)
max_iterations hit → return best text + truncated=True
```

Every iteration runs inside the **shared circuit breaker**
(`service._LLM_CIRCUIT`), with a wall-clock deadline that's
**2× the sync deadline** by default (90s for the tool-loop vs
45s for plain `generate()`).

The parser (`_parse_tool_calls`) is **tolerant**:

- Handles parallel calls (multiple `<tool_call>` blocks per turn)
- Re-decodes JSON-encoded-string `arguments` (some model variants)
- Silently skips malformed JSON blocks (no crash)
- Non-dict `arguments` coerced to `{}`
- Non-dict top-level payloads skipped

`_strip_tool_calls` removes raw XML before returning to the user
so operators never see `<tool_call>` tags in chat output.

---

## 6. Feature flag matrix

Every new capability is independently controllable.  The four
most interesting combos:

| `AGENTIC_MODE` | `TOOL_USE` | `TICKET_QUEUE` | Behaviour |
|---|---|---|---|
| ❌ | ❌ | ❌ | **Default (Phase 1-13).** Unchanged hybrid RAG, no tools, no tickets.  Merging this branch ships in this state. |
| ❌ | ✅ | ❌ | **Pure tool mode.** Every request goes through `generate_with_tools()`, retrieval still happens as a seed.  Good for A/B testing tool latency. |
| ✅ | ❌ | ❌ | **Supervisor-only.** Routes classified, but TOOLS/SPECIALIST routes still use plain `generate()`.  Useful to compare supervisor routing quality without the tool-loop latency. |
| ✅ | ✅ | ✅ | **Full agentic.** Supervisor → specialist → tools → ticket queue for escalations.  This is the target production config. |

Other flags (`self_reflect`, `structured_output`, `corrective_rag`,
`semantic_cache`, `query_rewrite`, `reranker`, `eval_auto_run`)
remain independent — they compose with the agentic flags above.

**Phase 23 voice flags** also compose independently:

| `VOICE_STREAMING` | `VOICE_CONSENT` | Behaviour |
|---|---|---|
| ❌ | ❌ | **Default.** Batch-only voice via `POST /v1/voice/chat`. No WebSocket endpoint. |
| ✅ | ❌ | **Streaming, no consent gate.** WebSocket `/v1/voice/chat/stream` active. Voice audit logged but consent not enforced (suitable for internal testing). |
| ✅ | ✅ | **Full production voice.** WebSocket active + `voice_recording` consent required before processing audio. Audit trail chained into AuditLedger. NDPA 2019 compliant. |

Per-request override via `flags.set(name, value)` is available for
A/B tests and notebook experiments.

---

## 7. Ticket queue (Phase D)

### Schema

```sql
CREATE TABLE tickets (
  id              TEXT PRIMARY KEY,           -- UUID
  conversation_id TEXT,
  session_id      TEXT,
  status          TEXT NOT NULL               -- open|assigned|resolved|wontfix
                  CHECK(status IN (...))
                  DEFAULT 'open',
  priority        TEXT NOT NULL               -- low|normal|high|urgent
                  CHECK(priority IN (...))
                  DEFAULT 'normal',
  reason          TEXT DEFAULT '',
  user_query      TEXT DEFAULT '',
  bot_reply       TEXT DEFAULT '',
  assignee        TEXT DEFAULT '',
  staff_note      TEXT DEFAULT '',
  created_at      REAL NOT NULL,
  updated_at      REAL NOT NULL
);
CREATE INDEX idx_tickets_status   ON tickets(status);
CREATE INDEX idx_tickets_priority ON tickets(priority);
CREATE INDEX idx_tickets_created  ON tickets(created_at);
```

Invalid priorities are **coerced to `normal`** with a warning
rather than raising — this is deliberate: the LLM is a fuzzy
upstream caller and we'd rather persist the ticket with a safe
default than drop it.  Invalid statuses on `update_ticket`
**are rejected** (no silent corruption).

### Two sources of tickets

1. **LLM-initiated** — the Qwen model calls the `escalate_to_human`
   tool mid-conversation.  This always persists (deterministic
   side effect the LLM explicitly requested).

2. **Supervisor-initiated** — the supervisor's ESCALATE route
   triggered by escalation rule patterns.  This only persists when
   `FLAG_TICKET_QUEUE` is on; otherwise the escalation marker is
   returned without a DB write (Phase 1-13 behaviour preserved).

### Admin endpoints

All gated by `Authorization: Bearer <INDEX_API_KEY>`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/admin/tickets?status=open&limit=50&offset=0` | List tickets, newest first, paginated, filter by status |
| `GET` | `/v1/admin/tickets/stats?days=30` | Aggregate counts by status and priority |
| `GET` | `/v1/admin/tickets/{id}` | Fetch a single ticket |
| `PATCH` | `/v1/admin/tickets/{id}` | Update status / assignee / staff_note / priority |

Ticket IDs are UUIDs — the path regex is `^[a-f0-9-]{1,64}$`.

---

## 8. Operational runbook

### Enabling the agent tier on a running deployment

1. Set the three env vars:

    ```bash
    FLAG_AGENTIC_MODE=true
    FLAG_TOOL_USE=true
    FLAG_TICKET_QUEUE=true
    ```

2. Set infrastructure env vars for the shared-host port mappings
   (Docker maps Qdrant/Redis to non-default ports to avoid
   conflicts with other tenants):

    ```bash
    QDRANT_URL=http://localhost:16333      # Docker maps 16333→6333
    REDIS_URL=redis://localhost:16379/0    # Docker maps 16379→6379
    DENSE_MODEL=BAAI/bge-m3
    DENSE_DIM=1024                         # Must match the indexed collection
    HF_HOME=/home/developer/hf-cache       # Writable cache dir
    CUDA_VISIBLE_DEVICES=0                 # Pin to a specific GPU
    ```

3. Restart the API (`uvicorn` picks up env on boot).  Qwen weights
   are already cached at `~/hf-cache` — restart takes ~15 s.

4. Verify via `/ready` and exercise one route of each type:

    ```bash
    # TOOLS route — calculator
    curl -X POST $API/v1/chat -H 'Content-Type: application/json' \
        -d '{"message":"How much VAT on UGX 100k?","top_k":3}' | jq .reply

    # ESCALATE route — creates a real ticket
    curl -X POST $API/v1/chat -H 'Content-Type: application/json' \
        -d '{"message":"I want to speak to a human"}' | jq .ticket_id

    # Verify the ticket appears in the admin queue
    curl -H "Authorization: Bearer $INDEX_API_KEY" \
        "$API/v1/admin/tickets?status=open"
    ```

### Clearing a stuck queue

Tickets accumulate forever if staff don't work them.  Use the PATCH
endpoint to bulk-resolve stale ones — for example, close tickets
older than 30 days that are still `open` or `assigned`:

```bash
curl -H "Authorization: Bearer $INDEX_API_KEY" \
    "$API/v1/admin/tickets/stats?days=30"
# Then PATCH each offender individually.
```

A future Phase 15 task will add a dedicated `/v1/admin/tickets/bulk`
endpoint for batch updates.

### Triaging incoming tickets

Recommended SLOs (not yet enforced, to be added in Phase 20):

| Priority | Time-to-first-response | Time-to-resolution |
|---|---|---|
| `urgent` | 15 min | 4 h |
| `high` | 1 h | 24 h |
| `normal` | 4 h | 72 h |
| `low` | 24 h | 1 week |

---

## 9. Observability hooks

Every agentic request sets the following OTel span attributes in
`trace_ctx` (visible in `/v1/analytics/dashboard` and any OTLP-
exported trace):

| Attribute | When set | Example |
|---|---|---|
| `agent_route` | `FLAG_AGENTIC_MODE=true` | `tools`, `rag`, `escalate` |
| `agent_route_confidence` | same | `0.92` |
| `specialist` | route is `TOOLS` / `*_SPECIALIST` | `customs_specialist` |
| `tool_calls` | any tool-loop invocation | `["calculate_vat", "lookup_rate"]` |
| `tool_iterations` | same | `2` |
| `ticket_id` | escalation path fired | `b3d1631e-...` |
| `prompt_leakage` | OutputGuard detected system-prompt regurgitation | `true` |

These stack with the existing Phase 1-13 attributes
(`gen_ai.usage.input_tokens`, `rag.retrieval.num_results`,
`gen_ai.faithfulness_score`, etc.).

---

## 10. Safety model

Three non-negotiable controls:

1. **Every tool call is sandboxed.**  `ToolRegistry.call()` wraps
   every `execute()` in try/except and returns a structured
   `{"ok": false, "error": "..."}` on any raise.  The LLM never
   sees a Python traceback; a misbehaving tool can't crash the
   request.

2. **Risk tiers gate tool scope.**  Specialists declare
   `allow_risk=["low"]` or `["low","medium"]`; tools with
   `risk="high"` or `"critical"` (none in this branch) require
   explicit per-tool whitelisting by the caller.

3. **No irreversible writes in Phase A-D.**  The only tool that
   writes anything is `escalate_to_human`, and it writes to a
   local `tickets` table — nothing is sent to URA systems.
   Future high-risk tools (`mcp_ura_actions` from the roadmap)
   will additionally require a UI confirmation step
   (`requires_confirmation=True` on the schema) before execution.

4. **Bounded loops.**  `generate_with_tools()` caps at 3
   iterations.  Parser silently skips malformed blocks.  Tool
   whitelist is validated against the registry (unknown tool
   names return a structured error, not a crash).

5. **Feature flags default OFF.**  Shipping this branch changes
   zero runtime behaviour until an operator explicitly enables
   the flags.

---

## 11. Test suite

**153 pytest tests in `tests/agents/`, running in 2.6 seconds.**

**304 pytest tests in `tests/agents/`, running in ~7 seconds.**

```
tests/agents/
├── conftest.py              — shared fixtures (tmp_db in-memory, fresh_registry, clean_flags)
├── test_calculators.py      —  29 tests: VAT add/extract, PAYE bands, CIT/CGT arithmetic, customs landed-cost
├── test_calendar_rates.py   —  17 tests: get_current_date, fiscal year boundary, deadlines horizon, rate lookup
├── test_tool_parser.py      —  17 tests: parallel tool_calls, string-encoded args, malformed, strip
├── test_supervisor.py       —  50 tests: every route, priority ordering, RouteDecision immutability
├── test_tools_framework.py  —  11 tests: registry dispatch, risk-tier filter, OpenAI spec envelope, error paths
├── test_tickets.py          —  21 tests: ticket CRUD, pagination, validation, escalate tool round-trip
├── test_integration.py      —   8 tests: supervisor→tool whitelist resolution, escalation→ticket flow
├── test_auth.py             —  37 tests: JWT roundtrip + temporal claims, AuthUser claims-to-user mapping,
│                                         Pydantic model validation, user / profile / consent CRUD, subject rights
├── test_mcp.py              —  23 tests: MCPClient singleton + list/describe/call, security trimming by
│                                         risk tier + consent, audit-dict SHA256 determinism, Tool RAG scorer,
│                                         mandatory rails, fallback path
├── test_graph.py            —  14 tests: AgentGraphState, GraphRuntime bounded dispatch, error capture,
│                                         node exception handling, main graph CLARIFY/ESCALATED outcomes
├── test_memory.py           —  35 tests: decay math (half-life, clamping), working memory TTL,
│                                         episodic + semantic CRUD with user isolation, supersede, forget,
│                                         fact extractor patterns, MemoryService consent-gated reads
├── test_audit.py            —  25 tests: Merkle root (empty/single/odd), hash-chained append with
│                                         tenant isolation, monotonic seq, tamper detection,
│                                         erasure tombstone, anchor range
└── test_me_endpoints.py     —  17 tests: FastAPI TestClient end-to-end for /v1/me/{whoami,profile,
                                          consents,export,delete} including full onboard→grant→
                                          export→withdraw→erase flow
```

Run locally:

```bash
.venv/bin/python -m pytest tests/agents/ -q
```

The suite is **fully offline** — no GPU, no Qdrant, no Redis, no
Qwen.  Live-LLM integration tests belong in a separate
`tests/integration/` suite gated by `PYTEST_INTEGRATION=1`
(not included in this branch).

### Fixture design notes

- `tmp_db` — **in-memory SQLite** (`sqlite3.connect(":memory:")`)
  monkey-patched into `app.database._get_connection`.  This
  avoids all file-locking issues that pytest's `tmp_path_factory`
  hit on hosts where `/tmp/pytest-of-<user>` lives on an NFS
  mount, and avoids cross-test WAL state contamination.
- `fresh_registry` — clears and re-imports the tool modules so
  tests that manipulate the registry don't leak state into the
  next test.
- `clean_flags` — clears the in-memory flag override dict before
  and after each test.

---

## 12. What this branch does NOT include

These items from `docs/GAPS_AND_AGENTIC_ROADMAP.md` are **still
gaps**.  The agent runtime is capable of handling them, but the
integrations haven't been built yet:

| Gap | What's needed |
|---|---|
| G1 — Auth | 🟢 **Landed in Phase 14** — OIDC-ready JWT verifier + FastAPI dependencies.  HS256 dev path + RS256/JWKS stubs for Keycloak. |
| G2 — User profile | 🟢 **Landed in Phase 14** — `users` + `user_profiles` + `consent_receipts` tables + /v1/me/* endpoints. |
| G5 — Long-term memory | 🟢 **Landed in Phase 16** — three-tier (working + episodic + semantic) with consent-gated retrieval + temporal decay. |
| G8 — Audit ledger | 🟢 **Landed in Phase 21 subset** — hash-chained `audit_events` + Merkle anchoring + `verify_chain` CLI. |
| G13 — Document uploads | 🟢 **Landed (#222)** — `POST /v1/documents/analyze` + `GET /v1/documents/{id}/report`, backed by `documents.py`, `ocr_service.py` and `vision/`. **2026-08-17:** `pdf_guards.py` fail-closed intake (header, encryption, JS/Launch/embedded files, xref/page caps), Office zip-slip/macro reject, LLM01 scrub + `<untrusted_user_document>` wrap. No ClamAV; no `mcp_document_parser`. |
| G14 — Notifications | ⚪ Scheduler for deadline reminders via email / SMS / in-app.  Phase 20 (scaffolded). |
| G16 — Knowledge graph | 🟡 **Graph + RRF fusion code shipped 2026-08-17** — `app/graph/` projects the rate tables; REST/stream fuse it as a third RRF leg. `FLAG_TAX_GRAPH` / `FLAG_GRAPH_FUSION` stay default off until unseen multi-hop ≥ 75%. Fusion is rank-level, not passage-id linked. Golden set: `agents/eval_multihop.py`. |
| G18 — Multilingual | 🟢 **Translate-retrieve 2026-08-17** — corpus stays English; `FLAG_TRANSLATE_RETRIEVE` merges an English hybrid pass. Generation stays in the user locale. Routing (#224) unchanged. |
| G21 — Self-correction | 🟢 **Bounded ReAct 2026-08-17** — money path (#226, #229) plus LangGraph `act → observe → synthesize → reflect`. One retrieve handoff when tools produce no evidence; one re-retrieve on low faithfulness or a reasoning miss. Not unbounded ReAct. |
| G22 — Specialist prompts | 🟢 **Landed (#218)** — `agents/prompts.py` gives `tax_specialist`, `customs_specialist` and `tool_specialist` their own instructions, appended to the shared base prompt so safety rules stay first. |
| G26 — A/B testing | 🟢 **Variant logging 2026-08-17** — rollout targeting (#223) plus `flag_variants` / `locale` on each conversation turn. Eval reports `by_segment.variant`. |
| G32 — HITL staff UI | 🟢 **Landed (#215)** — `App/frontend/src/app/admin/tickets/page.tsx`, with live escalation events pushed to staff (#217). |

The remaining gaps (G12 URA DMZ, G14 notifications, and G16 *measurement*
— expand unseen multi-hop before turning fusion on) are scaffolded with
README files under `backend/app/mcp/servers/`, `backend/app/workflows/`,
and `backend/app/scheduler/` so follow-up PRs have clear landing spots.

---

## 13. Change log (feat/agentic-workflows branch)

**Phase A-D (original in-process agent runtime):**

| Commit | Phase | Summary |
|---|---|---|
| `2784fa0` | A | Tool framework + 10 starter tools |
| `810b88d` | B | Qwen2.5 tool-calling loop wired into LLM layer |
| `0069dcd` | C | Supervisor router + agent state machine |
| `858eda0` | D | Ticket queue for escalation handoff |
| `55c0f38` | D hotfix | Expose `ticket_id` in `ChatResponse` schema |
| `4066eb0` | docs | pytest suite (153 tests) + docs/AGENT_ARCHITECTURE.md v1 |

**Phase 14-21 (2026 roadmap — identity, MCP, memory, audit, scaffolds):**

| Commit | Phase | Summary |
|---|---|---|
| `15b7bc6` | 14 | Zero-trust identity, tenancy, consent, subject rights |
| `510e634` | 15 Lite | MCP abstraction + Tool RAG + LangGraph-style orchestration |
| `1ed7589` | 16 | Three-tier personal memory + consent gating + temporal decay |
| `6330f1d` | 21 subset | Hash-chained audit ledger + per-segment eval |
| `ec224c4` | 17-20 | Directory scaffolds + READMEs for DMZ MCP, workflows, scheduler |
| `79c7239` | 21 wire | Audit ledger wired into `service.generate` on every return path |
| `95236ae` | tests + docs | +151 pytest tests (154 → 304), TestClient integration for /v1/me/*, AGENT_ARCHITECTURE v2 |
| `<current>` | infra | Qdrant v1.19.0, staged aliases, retained snapshots, restore drills, and lifecycle metrics |

See `git log --oneline feat/agentic-workflows ^main` for the
authoritative list.

**Test growth across the project:**

| Milestone | Tests | Runtime |
|---|:---:|:---:|
| Phase A-D initial | 153 | 2.57 s |
| + Phase 14-21 (this doc) | **304** | **~7 s** |

---

*Document version 2.1 — updated after the Qdrant v1.19 lifecycle hardening and
infrastructure connectivity fixes on `feat/agentic-workflows`.
Keep this file in sync with any subsequent Phase 14+ changes;
if you add a new tool, route, or flag, update Sections 3, 4, and 6.*
