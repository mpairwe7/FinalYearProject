# Memory, context, and session review — 2026-10-03

## Scope

This review covers the English (`en`), Luganda (`lg`), and Swahili (`sw`)
assistant paths: durable personalization memory, rolling conversation context,
topic and workflow state, language continuity, and WebSocket resume. The changes
were applied in phases to the existing SQLite/PostgreSQL persistence boundary.

## Findings and applied phases

### Phase 1 — ownership and session boundaries

Before this pass, conversation history and some topic/workflow state could be
looked up by a conversation or session identifier without consistently checking
the tenant and subject. WebSocket resume also lacked a durable, server-owned
binding between a response and its owner.

The implementation now:

- Scopes authenticated history reads by tenant, subject, and conversation. An
  anonymous read needs its session identifier; a bare conversation identifier
  cannot retrieve history.
- Uses opaque tenant/owner-scoped keys for topic and workflow rows. Existing
  rows are migrated only when one owner can be established; ambiguous legacy
  rows are left untouched rather than assigned to a guessed subject.
- Checks the tenant on workflow reads, updates, completion, export, and erasure.
- Issues WebSocket response IDs on the server and allows resume only when the
  exact unexpired response belongs to the same authenticated tenant, subject,
  and conversation. Anonymous clients do not receive cross-connection resume.
- Carries the stored locale into context and lets an explicit supported locale,
  including `en`, override automatic continuity. Inferred locale follows the
  taxpayer's prior turns rather than assistant output. *(Superseded 2026-10-06:
  the web client sends `en` by default, so a bare `en` is now a hint unless the
  client sets `locale_explicit`, and continuity reads the stored locale, which
  `normalize_history_turns` had been dropping — G113/G116.)*

### Phase 2 — memory lifecycle and privacy

Before this pass, short-term working state lived in process memory, retries could
duplicate persistent memory, and export/forget did not cover all three memory
tiers consistently.

The implementation now:

- Stores working state in the selected shared analytics backend, keyed by
  `(tenant_id, user_id)`, with a 30-minute expiry. Reads and writes require
  active personalization consent.
- Tenant-scopes semantic facts and episodic summaries. Exact repeats are
  idempotent; a changed taxpayer type supersedes the previous active value.
- Stores one coarse episode per conversation. It keeps a tax-topic label rather
  than raw inquiry text or inferred distress labels.
- Recognizes targeted English, Luganda, and Swahili fact cues, and avoids
  treating a mention of a language as proof of the user's language preference.
- Includes semantic facts, episodic summaries, and working state in subject
  export; personalization withdrawal and erasure clear all three tiers.

### Phase 3 — context quality and prompt integrity

Rolling context is bounded and summarized. Summary inputs use taxpayer messages
rather than assistant claims; the summary omits raw inquiry text and reference
values before it is added to the prompt. Retrieved context is framed as
untrusted input. Locale and targeted tax-topic cues cover English, Luganda, and
Swahili. These changes reduce context poisoning and avoid carrying unsupported
assistant claims forward as facts.

### Phase 4 — backend parity and verification

SQLite and PostgreSQL schemas and operations now include tenant-scoped
conversation response mappings and shared working memory. Tests cover backend
column parity, memory lifecycle, consent, rolling context, locale handling,
workflow ownership, transcript access, and export/erasure behavior.

## Remaining work, ordered by priority

1. **~~Finish tenant isolation for adjacent analytics and support records.~~** **SHIPPED 2026-10-05.**
   Added `tenant_id` columns, defaults, indexes, and queries across `tickets`,
   `feedback`, `analytics_events`, and `sessions` on both SQLite (`database.py`)
   and PostgreSQL (`postgres.py`) backends, with full signature and column
   parity verified.
2. **Run the live PostgreSQL migration and lifecycle suite.** The local
   verification skipped PostgreSQL cases because `POSTGRES_DSN` is not
   configured. SQLite and static backend-parity checks passed, but they do not
   replace a migration run against PostgreSQL.
3. **Make WebSocket resume an end-to-end CI check.** The resume unit tests pass.
   The existing `TestClient` WebSocket integration tests did not reach
   `session_ready` in this environment, so the handshake path still needs a
   functioning integration runner and a full test against it.
4. **Add longitudinal multilingual memory evaluations.** Rule-based extraction
   now covers targeted patterns, but it needs a curated English/Luganda/Swahili
   suite for code-switching, corrections, forget requests, consent transitions,
   false preference extraction, and whether stored facts improve later task
   completion. Score update and deletion behavior, not only recall.
5. **Add durable graph checkpoints and idempotent tool effects.** Chat history
   and working memory now survive worker changes, but the graph runtime still
   lacks a durable checkpoint/replay contract for an in-flight workflow. Add
   checkpoint versioning, resumable execution, and idempotency keys around
   external side effects before promising crash-safe agent continuation.
6. **~~Normalize locale tags at the API boundary.~~** **SHIPPED 2026-10-06**
   (`language_state.normalize_locale_tag`: `sw-UG` → `sw`, `lug` → `lg`). See
   the [2026-10-06 follow-up](MEMORY_CONTEXT_SESSION_REVIEW_2026-10-06.md),
   which also found that "an explicit supported locale, including `en`" above
   switched auto-detection off for the web client (G113). The base language
   (`en`, `lg`, `sw`) now drives routing and memory cues; a regioned or
   case-varied tag such as `sw-UG` or `SW` is reduced to it at the boundary.

## Verification evidence

Commands run from the repository root:

```text
PYTHONPATH=App/backend python3 -m pytest tests/agents/test_memory.py App/backend/tests/test_rolling_context.py App/backend/tests/test_conversational_frame.py tests/agents/test_escalation_context.py App/backend/tests/test_privacy_compliance_306.py App/backend/tests/test_postgres_workflow_sessions.py tests/agents/test_identity_consent_parity.py tests/agents/test_backend_column_parity.py -q --timeout=30
164 passed, 3 skipped

PYTHONPATH=App/backend python3 -m pytest tests/agents/test_backend_shim.py -q -k 'not live' --timeout=30
19 passed, 4 skipped

PYTHONPATH=App/backend python3 -m pytest App/backend/tests/test_ws_session_resume.py -q -k 'not IntegrationTest' --timeout=10
2 passed, 2 deselected

ruff check App/backend/app/service.py --select F821 --output-format concise
All checks passed

python3 -m compileall -q App/backend/app
Passed

git diff --check
Passed
```

The seven skipped backend tests require a live `POSTGRES_DSN`. The two WebSocket
integration cases were excluded from the passing unit command because their
TestClient handshake did not complete in this environment. A broad Ruff pass
still reports style/security findings across the large touched modules; those
findings were not all classified against the base revision. The targeted
undefined-name check and compilation passed.

## Current standards and research informing the changes

- OWASP's 2026 Agentic Applications Top 10 identifies memory and context
  poisoning as an agent risk. The associated guidance supports scoped,
  consented memory, bounded retention, provenance, and treating retrieved
  context as untrusted input: [OWASP Top 10 for Agentic Applications 2026](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/),
  [ASI06: Memory and Context Poisoning](https://genai.owasp.org/2026/05/13/memory-is-a-feature-it-is-also-an-attack-surface/).
- The OWASP Agent Control Standard emphasizes runtime hooks, traceability,
  observability, and control around agent actions. This supports keeping
  ownership, resume, and lifecycle decisions in explicit server-side state:
  [OWASP Agent Control Standard](https://genai.owasp.org/resource/agent-control-standard-acs/).
- NIST's Generative AI Profile frames these as lifecycle risk-management tasks:
  define governance, measure behavior, monitor it after deployment, and retain
  human oversight for consequential outcomes: [NIST AI 600-1](https://doi.org/10.6028/NIST.AI.600-1).
- BCP 47 defines interoperable language tags. Normalization at the boundary is
  the next locale improvement for regioned and case-varied tags:
  [RFC 5646](https://www.rfc-editor.org/info/rfc5646/).
- Recent memory research argues for evaluating remember/update/forget behavior,
  evidence, scope, and multi-session task completion—not retrieval alone:
  [MemOps](https://arxiv.org/abs/2607.12893),
  [MemoryArena](https://arxiv.org/abs/2602.16313).
- The MCP project's 2026 direction moves protocol sessions toward stateless
  transport. That reinforces keeping taxpayer conversation identity and memory
  in application-owned, access-controlled state rather than relying on a
  transport session identifier: [MCP changelog](https://modelcontextprotocol.io/specification/draft/changelog),
  [2026 protocol update](https://blog.modelcontextprotocol.io/posts/2026-07-28/).
