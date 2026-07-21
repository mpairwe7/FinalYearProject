#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x "$ROOT_DIR/../.venv/bin/python" ]]; then
    PYTHON_BIN="$ROOT_DIR/../.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi

cd "$ROOT_DIR"

printf 'Release gate: backend syntax\n'
"$PYTHON_BIN" -m py_compile \
  backend/app/authority.py \
  backend/app/claim_verifier.py \
  backend/app/mcp/policy.py \
  backend/app/mcp/client.py \
  backend/app/tools/rates.py \
  backend/app/tools/ura_account.py \
  backend/app/tools/ura_actions.py \
  backend/app/service.py \
  backend/app/main.py \
  backend/app/chat_ws_v2.py \
  backend/app/confirm_tokens.py \
  backend/app/llm.py

printf 'Release gate: backend tests\n'
(
  cd "$BACKEND_DIR"
  PYTHONPATH=. "$PYTHON_BIN" -m pytest \
    tests/test_authority.py \
    tests/test_claim_verifier.py \
    tests/test_mcp_policy.py \
    tests/test_production_hardening.py \
    tests/test_chat_ws_lifecycle.py \
    tests/test_ws_session_resume.py \
    tests/test_generate_with_tools_events.py \
    tests/test_tool_confirmation.py \
    tests/test_ws_hardening.py \
    -q
)

printf 'Release gate: frontend build\n'
(
  cd "$FRONTEND_DIR"
  bun run build
)

printf 'Release gate: compose production overlay\n'
for attempt in 1 2; do
  if command -v docker-compose >/dev/null 2>&1; then
    compose_ok=false
    docker-compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production.example config --quiet && compose_ok=true
  else
    compose_ok=false
    docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file .env.production.example config --quiet && compose_ok=true
  fi
  if [[ "$compose_ok" == "true" ]]; then
    break
  fi
  if [[ "$attempt" == "2" ]]; then
    exit 1
  fi
  printf 'Compose config check failed once; retrying after transient Docker CLI error\n' >&2
  sleep 1
done

printf 'Release gate passed\n'
