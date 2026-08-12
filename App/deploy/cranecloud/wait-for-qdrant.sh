#!/bin/sh
# Block until the in-image Qdrant sidecar is serving, then exec the real command.
#
# supervisord's `priority` controls start ORDER, not readiness: it launches qdrant
# before uvicorn but does not wait for it to answer. Qdrant has to load the baked
# collection's segments first, so uvicorn could win the race, have
# HybridRetriever.initialize() fail to connect, and fall back to keyword search
# over the FAQ CSVs alone. Observed on the live HF Space right after a roll:
# /ready reported {"status":"degraded","retrieval_mode":"keyword"} for the first
# minute or so. It self-corrected — service.py re-initialises the retriever lazily
# on a later request — but every question asked inside that window answered from
# 499 FAQ rows instead of 7,943 documents.
#
# Deliberately NOT fail-closed. If the sidecar is genuinely broken, a pod that
# never starts is worse than one serving degraded answers: the frontend, the
# calculators and the FAQ path all still work without Qdrant. So the wait is
# bounded, and on timeout we start anyway and say so loudly. The lazy re-init
# still picks Qdrant up if it appears later.
set -eu

# Only gate when this deployment actually runs the sidecar. A deployment pointed
# at a managed Qdrant, or one with QDRANT_ENABLED=false, must not pay the wait —
# and in dev there may be nothing on loopback at all.
if [ "${QDRANT_SIDECAR:-false}" != "true" ]; then
    exec "$@"
fi

URL="${QDRANT_URL:-http://127.0.0.1:6333}"
DEADLINE="${QDRANT_WAIT_SECONDS:-90}"
elapsed=0

while [ "$elapsed" -lt "$DEADLINE" ]; do
    if curl -sf "${URL%/}/healthz" >/dev/null 2>&1; then
        echo "wait-for-qdrant: sidecar ready after ${elapsed}s — starting backend"
        exec "$@"
    fi
    elapsed=$((elapsed + 1))
    sleep 1
done

echo "wait-for-qdrant: sidecar did not answer ${URL%/}/healthz within ${DEADLINE}s." >&2
echo "wait-for-qdrant: starting the backend anyway — retrieval will use its fallback" >&2
echo "wait-for-qdrant: chain (Vectorize, then keyword) until Qdrant appears." >&2
exec "$@"
