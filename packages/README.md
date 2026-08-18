# packages/

This checkout is a **single-app agent host**, not a split JS/Python package
monorepo. Shared contracts live in `App/backend/app` (Pydantic models, tool
schemas, flags) and are consumed by `App/frontend` over HTTP/WebSocket.

Do not extract `packages/agent-core` until Docker, HF Space, and
`.github/workflows` can import it without a path rewrite.
