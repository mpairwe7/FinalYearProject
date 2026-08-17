# Agentic mode default-on + G6 topic persistence — 2026-08-17

Traceability record for turning `FLAG_AGENTIC_MODE` on behind a measured
routing gate, and for shipping conversation topic persistence (G6).

## 1. Intent

1. Serve the supervisor 7-route graph by default once English golden-set
   accuracy holds at the same floor CI already asserts (≥ 0.95).
2. Keep a *current task* across turns so “what documents do I need?”
   after “I’m importing a car” retrieves customs/import, not a generic
   documents FAQ.
3. Never put raw user text into the system prompt (LLM01). Catalog
   labels only.

## 2. Decision log

| Decision | Choice | Why |
|----------|--------|-----|
| Agentic default | `FLAG_AGENTIC_MODE=true` | `.env.example` already said true; the code default was the drift. |
| Gate | `agentic_mode_gate()` on `GOLDEN_SET` / `en` | Same harness as `locale_gate`; a drop is a regression, not a reason to keep the flag off. |
| Floor | 0.95 | Identical to `test_accuracy_does_not_regress`. |
| `FLAG_TOOL_USE` | stays default off | Calculators/workflows still short-circuit. Tool loop only on TOOLS / specialist (`force_agentic`) or an explicit flag flip. |
| Topic store | always on, no new flag | Conversation state, not consent-gated profile memory. |
| Classifier | catalog regex, not an LLM | Deterministic, offline, and the prompt never sees the taxpayer’s words. |
| Follow-up retrieval | `"{label}: {query}"` | Anaphora must hit the task collection, not a bare “documents” query. |
| Reset | “new question / something else / start over / goodbye” | Explicit subject change must drop the stored task. |

## 3. Code surface

| Area | Files |
|------|-------|
| Classifier | `App/backend/app/topics.py` |
| Store | `database.py` / `postgres.py` (`conversation_topics`) |
| Bind | `service.py` `_bind_conversation_topic` (REST + stream) |
| API | `models.py` `ChatResponse.current_topic` |
| Flag | `flags.py` `agentic_mode` default True |
| Gate | `agents/eval_routing.py` `agentic_mode_gate` |
| Tests | `App/backend/tests/test_topics.py`, `tests/agents/test_eval_routing.py`, `tests/agents/test_integration.py` |

## 4. How to re-verify

```bash
cd App/backend
../../.venv/bin/python -c "
from app.agents.eval_routing import run_routing_eval, agentic_mode_gate
r = run_routing_eval()
print(f'en routing: {r.correct}/{r.total} acc={r.accuracy:.3f}')
print(agentic_mode_gate())
"
../../.venv/bin/python -m pytest tests/test_topics.py -q
cd ../..
.venv/bin/python -m pytest tests/agents/test_eval_routing.py tests/agents/test_integration.py -q
```

Cite only numbers printed by that command.

## 5. Still open

- UI topic chip (`TopicChip.tsx`) — API already returns `current_topic`.
- LangGraph per-topic state machine (URA 2026-enhanced recommendation).
- `FLAG_TOOL_USE`, `FLAG_HYDE`, `FLAG_GRAPH_FUSION`, `FLAG_TOOL_RAG` stay off.
