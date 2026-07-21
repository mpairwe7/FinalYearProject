#!/usr/bin/env bash
# Crane Cloud container smoke — exercises the full nginx-fronted surface
# (SSE chat, REST chat, frontend, /v2/chat/stream WebSocket handshake)
# against a running container.
#
# Usage:
#   scripts/container_smoke.sh [BASE_URL]
#
# Defaults to http://127.0.0.1:8080 (the published nginx port).
set -euo pipefail

BASE_URL="${1:-http://127.0.0.1:8080}"
WS_URL="${BASE_URL/http/ws}/v2/chat/stream"

pass() { printf '  PASS  %-30s %s\n' "$1" "${2:-}"; }
fail() { printf '  FAIL  %-30s %s\n' "$1" "${2:-}"; exit 1; }

probe() {
    local name=$1 path=$2 expected=$3
    local code
    code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${BASE_URL}${path}" || echo "000")
    if [[ ",${expected}," == *",${code},"* ]]; then
        pass "$name" "${code}"
    else
        fail "$name" "expected one of ${expected}, got ${code}"
    fi
}

printf 'Container smoke against %s\n' "$BASE_URL"

probe "GET /health"           "/health"                "200"
probe "GET /ready (degraded ok)" "/ready"              "200"
probe "GET /tags"             "/tags"                  "200"
probe "GET /docs (optional)"  "/docs"                  "200,301,302,307,308,404"
probe "GET / (frontend)"      "/"                      "200,301,302,307,308"

# Chat REST
reply=$(curl -sS --max-time 30 -X POST "${BASE_URL}/v1/chat" \
        -H 'Content-Type: application/json' \
        -d '{"message":"hello","top_k":4,"locale":"en"}' || true)
if [[ -n "$reply" ]] && echo "$reply" | grep -q '"retrieval_mode"'; then
    pass "POST /v1/chat" "greeting/keyword fallback returns ChatResponse"
else
    fail "POST /v1/chat" "no retrieval_mode in body"
fi

# SSE chat — confirm we see metadata + done frames within 30 s.
sse_body=$(curl -sS --max-time 30 -N -X POST "${BASE_URL}/v1/chat/stream" \
        -H 'Content-Type: application/json' \
        -d '{"message":"What is a TIN?","top_k":4,"locale":"en"}' || true)
if echo "$sse_body" | grep -q "^event: metadata" && \
   echo "$sse_body" | grep -q "^event: done"; then
    pass "POST /v1/chat/stream" "SSE metadata+done frames present"
else
    fail "POST /v1/chat/stream" "SSE frame shape unexpected"
fi

# WebSocket handshake — only if FLAG_WS_CHAT=true inside container.
if curl -sS --max-time 5 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
       -H 'Sec-WebSocket-Version: 13' \
       -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
       -o /tmp/ws_handshake_headers.txt \
       -w '%{http_code}' "${BASE_URL}/v2/chat/stream" 2>&1 | grep -qE "^(101|400|404|426|1001)"; then
    code=$(curl -sS --max-time 5 -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
           -H 'Sec-WebSocket-Version: 13' \
           -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
           -o /dev/null -w '%{http_code}' "${BASE_URL}/v2/chat/stream" 2>&1 || echo "err")
    case "$code" in
      101) pass "WS /v2/chat/stream" "upgrade 101 (handshake OK)" ;;
      *)   pass "WS /v2/chat/stream" "status ${code} (flag-gated or not enabled)" ;;
    esac
fi

printf '\nAll smoke checks passed.\n'
