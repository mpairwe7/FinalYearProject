#!/usr/bin/env bash
# Load prototype defaults and print the two commands a demo needs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
set -a
source "$ROOT/configs/prototype.env"
set +a
echo "Prototype env loaded (APP_ENV=$APP_ENV, URA_ACCOUNT_API_MODE=$URA_ACCOUNT_API_MODE)."
echo "API:  cd App/backend && PYTHONPATH=. uvicorn app.main:app --reload --port 8887"
echo "Web:  cd App/frontend && bun dev"
echo "Seed: PYTHONPATH=App/backend python3 -m app.seed_prototype"
echo "Do not export this file into production."
