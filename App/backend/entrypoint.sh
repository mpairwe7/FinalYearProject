#!/bin/sh
set -eu

PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers "${WORKERS}"
