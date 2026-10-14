#!/bin/sh
set -eu

PORT="${PORT:-8000}"
WORKERS="${WORKERS:-4}"

# Every uvicorn worker writes its Prometheus samples to this directory and
# /metrics adds them up (app/analytics.py). Files left by a previous run would
# be counted again, so the directory is emptied before the workers start.
export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/ura-prometheus}"
mkdir -p "${PROMETHEUS_MULTIPROC_DIR}"
find "${PROMETHEUS_MULTIPROC_DIR}" -maxdepth 1 -type f -name '*.db' -delete

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT}" --workers "${WORKERS}"
