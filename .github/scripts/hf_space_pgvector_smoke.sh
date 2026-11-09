#!/bin/sh
# Start the Space image's Postgres with supervisord's own command line and check
# pgvector answers, the serving boundary holds, and a base that ships the dense
# leg has it loaded (ADR-0034). Run inside the built image by hf-space-image-check.yml.
set -eu
conf=/etc/supervisor/conf.d/supervisord.conf
cmd=$(sed -n '/^\[program:postgres\]/,/^\[/{s/^command=//p}' "$conf")
test -n "$cmd" || { echo "supervisord does not start Postgres" >&2; exit 1; }
# shellcheck disable=SC2086 # supervisord splits the command line the same way
/usr/sbin/runuser -u postgres -- $cmd &
bin=/usr/lib/postgresql/15/bin
for _ in $(seq 1 30); do
  "$bin/pg_isready" -h /app/pgrun -q && break
  sleep 1
done
# This script runs as root, as uvicorn does: it may be the read-only role and nothing else.
reader() { "$bin/psql" -h /app/pgrun -U ura_dense_reader -d ura_dense -tA -v ON_ERROR_STOP=1 -c "$1"; }
if "$bin/psql" -h /app/pgrun -U postgres -d ura_dense -tAc "SELECT 1" >/dev/null 2>&1; then
  echo "root connected as postgres: the read-only boundary is broken" >&2
  exit 1
fi
echo "pgvector $(reader "SELECT extversion FROM pg_extension WHERE extname = 'vector'")"
# Orthogonal vectors are at cosine distance 1.
test "$(reader "SELECT ('[1,0,0]'::vector <=> '[0,1,0]'::vector)::int")" = "1"
if [ -f /app/Data/dense/manifest.json ] && [ -f /app/backend/app/pgvector_dense.py ]; then
  # This base ships the dense leg: a missing or empty table is a failed load.
  count=$(reader "SELECT value FROM dense_meta WHERE key = 'count'")
  test "${count:-0}" -gt 0
  echo "dense leg loaded: $count vectors"
else
  echo "this base predates the dense leg: pgvector is installed but empty"
fi
