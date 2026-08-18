# agents/

Source of truth for the runtime:

- Supervisor and golden-set routing: `App/backend/app/agents/`
- LangGraph: `App/backend/app/agents/graphs/`
- Tools: `App/backend/app/tools/`
- MCP servers: `App/backend/app/mcp/`

This directory exists so the repo matches a 2026 agent-monorepo map
without breaking `from app.tools` imports used by Docker, HF Space, and CI.
