# prompts/

Runtime prompts live in `App/backend/app/agents/prompts.py` (and
`App/backend/app/llm.py` system prompt). Guardrail signatures in
`guardrails.py` must stay in sync with any new system-prompt line.

There is no hot-reload YAML store yet (remainder of G31).
