#!/bin/sh
# Prove that this image genuinely carries the two embedded stores it claims:
# a Qdrant collection seeded from the FAQ JSONL corpus, and a working Redis.
#
# Both stores are built or installed at image build time and neither is
# fail-closed at runtime — wait-for-qdrant.sh starts the backend anyway on
# timeout, and cache.py falls back to in-process memory when Redis is
# unreachable. That is the right runtime behaviour (a degraded pod beats a dead
# one) but it means a broken store produces no startup failure to notice: the
# Space served keyword-only answers over 499 FAQ rows for a full minute after a
# roll before anyone spotted it, and the response cache silently returned None
# for weeks. This script moves both failures to BUILD time, where they are loud.
#
# It is deliberately runnable against a live container too:
#
#     docker exec <container> /usr/local/bin/verify-embedded-stores.sh
#
# When Qdrant or Redis is already serving it verifies the running instance and
# leaves it alone; otherwise it starts one, checks it, and stops it again.
#
# Env (all optional, defaults match the image):
#   QDRANT_URL          http://127.0.0.1:6333
#   QDRANT_COLLECTION   ura_knowledge_base_active   (an alias — resolved here)
#   FAQ_JSONL_DIR       /app/Data/faq_jsonl
#   FAQ_INDEX_MIN_ROWS  floor for indexed FAQ points; default 90% of the rows
#                       on disk, which absorbs duplicate-question dedup without
#                       absorbing a missing corpus
#   REDIS_URL           redis://127.0.0.1:6379/0
#   REDIS_MAXMEMORY     192mb
set -eu

# Exported, not just assigned: the Python fragments below read these from
# os.environ, and a plain shell variable is invisible to a child process.
export QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
export QDRANT_COLLECTION="${QDRANT_COLLECTION:-ura_knowledge_base_active}"
export QDRANT_STORAGE="${QDRANT_STORAGE:-/app/qdrant/storage}"
export FAQ_JSONL_DIR="${FAQ_JSONL_DIR:-/app/Data/faq_jsonl}"
export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
export SLOWAPI_STORAGE_URI="${SLOWAPI_STORAGE_URI:-redis://127.0.0.1:6379/1}"
export REDIS_MAXMEMORY="${REDIS_MAXMEMORY:-192mb}"
PYTHON="${PYTHON:-/opt/venv/bin/python}"

QDRANT_PID=""
REDIS_PID=""
SCRATCH=""

cleanup() {
    [ -n "$QDRANT_PID" ] && kill "$QDRANT_PID" 2>/dev/null && wait "$QDRANT_PID" 2>/dev/null
    [ -n "$REDIS_PID" ] && kill "$REDIS_PID" 2>/dev/null && wait "$REDIS_PID" 2>/dev/null
    [ -n "$SCRATCH" ] && rm -rf "$SCRATCH"
    return 0
}
trap cleanup EXIT INT TERM

fail() { echo "verify-embedded-stores: FAIL — $*" >&2; exit 1; }
ok()   { echo "verify-embedded-stores: ok   — $*"; }

# ---------------------------------------------------------------- structure --
# Cheap checks first: if an artefact is missing outright there is no point
# starting a server to discover it.
[ -x /usr/local/bin/qdrant ] || fail "no Qdrant binary at /usr/local/bin/qdrant"
[ -d "$QDRANT_STORAGE" ]     || fail "no Qdrant storage at $QDRANT_STORAGE — the collection was never built"
[ -d "$FAQ_JSONL_DIR" ]      || fail "no FAQ JSONL corpus at $FAQ_JSONL_DIR"
command -v redis-server >/dev/null 2>&1 || fail "redis-server is not installed in this image"
[ -x "$PYTHON" ]             || fail "no Python interpreter at $PYTHON"

FAQ_ROWS_ON_DISK=$(cat "$FAQ_JSONL_DIR"/*.jsonl 2>/dev/null | wc -l | tr -d ' ')
[ "${FAQ_ROWS_ON_DISK:-0}" -gt 0 ] || fail "$FAQ_JSONL_DIR contains no JSONL rows"
ok "$FAQ_ROWS_ON_DISK FAQ rows on disk in $FAQ_JSONL_DIR"

# --------------------------------------------------------------- qdrant up ---
if ! curl -sf "${QDRANT_URL%/}/healthz" >/dev/null 2>&1; then
    # Serve from a throwaway copy, never the baked storage. Starting Qdrant
    # materialises its sparse segment files — measured at ~1 MB on disk growing
    # to ~36 MB — and when this runs as a Docker build gate every one of those
    # touched files lands in the image layer. Verifying a copy keeps the baked
    # bytes identical and the layer delta at zero. (When Qdrant is ALREADY
    # serving, as it is inside a running pod, this branch is skipped entirely
    # and the live instance is checked in place.)
    SCRATCH="${TMPDIR:-/tmp}/verify-embedded-stores.$$"
    cp -a "$QDRANT_STORAGE" "$SCRATCH" \
        || fail "could not copy $QDRANT_STORAGE for verification"
    QDRANT_STORAGE="$SCRATCH"

    QDRANT__STORAGE__STORAGE_PATH="$QDRANT_STORAGE" \
    QDRANT__SERVICE__HTTP_PORT=6333 \
    QDRANT__SERVICE__HOST=127.0.0.1 \
    QDRANT__TELEMETRY_DISABLED=true \
    QDRANT__LOG_LEVEL=WARN \
    /usr/local/bin/qdrant >/dev/null 2>&1 &
    QDRANT_PID=$!
    for _ in $(seq 1 90); do
        curl -sf "${QDRANT_URL%/}/healthz" >/dev/null 2>&1 && break
        sleep 1
    done
    curl -sf "${QDRANT_URL%/}/healthz" >/dev/null 2>&1 \
        || fail "Qdrant did not become healthy — it usually failed to exec; check libunwind8"
fi
ok "Qdrant serving at $QDRANT_URL"

# ------------------------------------------------------- collection + seed ---
# QDRANT_COLLECTION is an alias in this image: index_lifecycle builds a
# versioned candidate and promotes the alias only after its retrieval gate
# passes, so a failed build leaves the previous collection serving. Resolve the
# alias explicitly rather than trusting the name to be a collection, because a
# dangling alias and a missing collection are different faults.
ALIAS_JSON=$(curl -sf "${QDRANT_URL%/}/aliases") || fail "Qdrant is serving but /aliases did not answer"
RESOLVED=$(printf '%s' "$ALIAS_JSON" | "$PYTHON" -c '
import json, os, sys
alias = os.environ["QDRANT_COLLECTION"]
for entry in json.load(sys.stdin).get("result", {}).get("aliases", []):
    if entry.get("alias_name") == alias:
        print(entry.get("collection_name", ""))
        break
')

if [ -z "$RESOLVED" ]; then
    # No alias — accept a plain collection of that name, which is what a
    # non-staged build or a hand-seeded dev store produces.
    curl -sf "${QDRANT_URL%/}/collections/${QDRANT_COLLECTION}" >/dev/null 2>&1 \
        || fail "neither an alias nor a collection named '$QDRANT_COLLECTION' exists"
    RESOLVED="$QDRANT_COLLECTION"
    echo "verify-embedded-stores: note — '$QDRANT_COLLECTION' is a plain collection, not an alias"
fi
ok "alias '$QDRANT_COLLECTION' resolves to collection '$RESOLVED'"

count_doc_type() {
    curl -sf -X POST "${QDRANT_URL%/}/collections/${RESOLVED}/points/count" \
        -H 'Content-Type: application/json' \
        -d "{\"filter\":{\"must\":[{\"key\":\"doc_type\",\"match\":{\"value\":\"$1\"}}]},\"exact\":true}" \
        | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["result"]["count"])' 2>/dev/null
}

FAQ_INDEXED=$(count_doc_type faq_jsonl || echo 0)
TOTAL_INDEXED=$(curl -sf -X POST "${QDRANT_URL%/}/collections/${RESOLVED}/points/count" \
    -H 'Content-Type: application/json' -d '{"exact":true}' \
    | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["result"]["count"])' 2>/dev/null || echo 0)

[ "${FAQ_INDEXED:-0}" -gt 0 ] \
    || fail "the collection holds ZERO faq_jsonl points — the FAQ corpus was not seeded. \
This is the regression that leaves retrieval answering from keyword fallback only."

# Ingest drops exact-duplicate questions, so indexed < on-disk is normal and
# small. A floor catches a corpus that half-loaded; it is not an equality check.
MIN_ROWS="${FAQ_INDEX_MIN_ROWS:-$(( FAQ_ROWS_ON_DISK * 90 / 100 ))}"
[ "$FAQ_INDEXED" -ge "$MIN_ROWS" ] \
    || fail "only $FAQ_INDEXED faq_jsonl points indexed, below the floor of $MIN_ROWS \
(${FAQ_ROWS_ON_DISK} rows on disk) — the FAQ corpus loaded only partially"

ok "$FAQ_INDEXED faq_jsonl points indexed (floor $MIN_ROWS, $FAQ_ROWS_ON_DISK on disk)"
ok "$TOTAL_INDEXED points in the collection across all doc types"

for dt in teacher_qa_jsonl pdf_chunk crawl_chunk; do
    n=$(count_doc_type "$dt" || echo 0)
    echo "verify-embedded-stores: info — $dt: ${n:-0}"
done

# ---------------------------------------------------------------- redis up ---
# Started with the EXACT command line supervisord uses, so a bad REDIS_MAXMEMORY
# or a flag this redis-server build rejects fails here rather than at pod start.
REDIS_STARTED_HERE=false
if ! "$PYTHON" - <<'PY' >/dev/null 2>&1
import os, redis
redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=1).ping()
PY
then
    /usr/bin/redis-server --bind 127.0.0.1 --port 6379 --save "" --appendonly no \
        --maxmemory "$REDIS_MAXMEMORY" --maxmemory-policy allkeys-lru \
        --daemonize no --loglevel warning >/dev/null 2>&1 &
    REDIS_PID=$!
    REDIS_STARTED_HERE=true
    redis_up=false
    for _ in $(seq 1 30); do
        if "$PYTHON" -c '
import os, redis, sys
try:
    redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=1).ping()
except Exception:
    sys.exit(1)
' >/dev/null 2>&1; then
            redis_up=true
            break
        fi
        sleep 1
    done
    # Reported here rather than letting the round-trip below raise: the usual
    # cause is a flag redis-server rejected, and "it never started" is a more
    # useful message than a connection-refused traceback.
    [ "$redis_up" = true ] || fail "redis-server did not start with the flags supervisord uses \
(--maxmemory '$REDIS_MAXMEMORY' --maxmemory-policy allkeys-lru); check REDIS_MAXMEMORY"
fi

"$PYTHON" - <<'PY' || fail "Redis did not pass its round-trip check"
import os, sys

import redis

url = os.environ["REDIS_URL"]
cache = redis.Redis.from_url(url, socket_connect_timeout=3)
cache.ping()

# The two databases must be distinct: db 0 is the response cache and db 1 the
# rate-limit store, split so that flushing the cache cannot reset someone's
# rate-limit window. A single shared db would make that flush a bypass.
limits_url = os.environ.get("SLOWAPI_STORAGE_URI", "redis://127.0.0.1:6379/1")
limits = redis.Redis.from_url(limits_url, socket_connect_timeout=3)
limits.ping()

key = "ura:verify:embedded-stores"
cache.set(key, "1", ex=30)
if cache.get(key) != b"1":
    sys.exit("cache db did not return the value it just stored")
if limits.get(key) is not None:
    sys.exit(f"{url} and {limits_url} share a keyspace; they must be separate databases")
cache.delete(key)

policy = cache.config_get("maxmemory-policy").get("maxmemory-policy")
maxmem = int(cache.config_get("maxmemory").get("maxmemory", 0))
if policy != "allkeys-lru":
    sys.exit(f"maxmemory-policy is {policy!r}, expected 'allkeys-lru'")
if maxmem <= 0:
    sys.exit("maxmemory is unset; Redis would compete with Qdrant for the pod's RAM")
print(f"verify-embedded-stores: ok   — Redis round-trip on {url}, "
      f"rate limits isolated on {limits_url}")
print(f"verify-embedded-stores: ok   — maxmemory {maxmem} bytes, policy {policy}")
PY

if [ "$REDIS_STARTED_HERE" = true ]; then
    ok "Redis verified (started for the check, stopping again)"
else
    ok "Redis verified (an instance was already serving; left running)"
fi

echo "verify-embedded-stores: PASS — Qdrant seeded with the FAQ corpus, Redis embedded and serving"
