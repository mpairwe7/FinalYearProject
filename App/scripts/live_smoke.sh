#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8887}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:13000}"
TIMEOUT_SECONDS="${SMOKE_TIMEOUT_SECONDS:-60}"
# What LLM_MODEL the deployment under test is expected to report. Was a bare
# hardcoded "Qwen/Qwen3-8B" in six places below; drifted the moment the
# default model changed (Sunbird/Sunflower-14B-FP8 via LLM_BACKEND=vllm — see
# docs/MODEL_SWAP_GUIDE.md) and would have failed every one of those
# assertions on a correctly-running deployment. Override for any other model.
EXPECTED_MODEL="${EXPECTED_MODEL:-Sunbird/Sunflower-14B-FP8}"
RUN_ID="${SMOKE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
TMP_DIR="$(mktemp -d)"

HTTP_CODE=""
CURL_TIME=""

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    printf 'Missing required command: %s\n' "$1" >&2
    exit 1
  }
}

fail() {
  printf 'FAIL %s\n' "$1" >&2
  exit 1
}

pass() {
  printf 'PASS %-28s %8ss\n' "$1" "$2"
}

show_response() {
  local file=$1
  printf -- '--- response body (%s) ---\n' "$file" >&2
  tr -d '\r' < "$file" | sed -n '1,160p' >&2
  printf -- '--- end response ---\n' >&2
}

assert_status() {
  local expected=$1
  local actual=$2
  local label=$3
  [[ "$actual" == "$expected" ]] || fail "$label returned HTTP $actual (expected $expected)"
}

assert_root_status() {
  local actual=$1
  local label=$2
  [[ "$actual" == "200" || "$actual" == "307" ]] || fail "$label returned HTTP $actual (expected 200 or 307)"
}

assert_json() {
  local file=$1
  local expr=$2
  local label=$3
  if ! jq -e "$expr" "$file" >/dev/null; then
    show_response "$file"
    fail "$label failed JSON assertion: $expr"
  fi
}

assert_file_contains() {
  local file=$1
  local pattern=$2
  local label=$3
  grep -Eq "$pattern" < <(tr -d '\r' < "$file") || {
    show_response "$file"
    fail "$label missing pattern: $pattern"
  }
}

assert_file_not_contains() {
  local file=$1
  local pattern=$2
  local label=$3
  if grep -Eqi "$pattern" < <(tr -d '\r' < "$file"); then
    show_response "$file"
    fail "$label matched forbidden pattern: $pattern"
  fi
}

perform_request() {
  local method=$1
  local url=$2
  local outfile=$3
  local body=${4-}
  local result
  local -a args=(
    curl
    -sS
    --max-time
    "$TIMEOUT_SECONDS"
    -o
    "$outfile"
    -w
    "%{http_code} %{time_total}"
    -X
    "$method"
    "$url"
  )
  if [[ -n "$body" ]]; then
    args+=(-H "Content-Type: application/json" -d "$body")
  fi
  if ! result="$("${args[@]}")"; then
    fail "request failed: $method $url"
  fi
  HTTP_CODE="${result%% *}"
  CURL_TIME="${result##* }"
}

perform_stream_request() {
  local url=$1
  local outfile=$2
  local body=$3
  local result
  if ! result="$(
    curl -sS -N \
      --max-time "$TIMEOUT_SECONDS" \
      -H "Content-Type: application/json" \
      -o "$outfile" \
      -w "%{http_code} %{time_total}" \
      -X POST \
      -d "$body" \
      "$url"
  )"; then
    fail "stream request failed: POST $url"
  fi
  HTTP_CODE="${result%% *}"
  CURL_TIME="${result##* }"
}

check_health() {
  local outfile="$TMP_DIR/backend-health.json"
  perform_request GET "$BACKEND_URL/health" "$outfile"
  assert_status 200 "$HTTP_CODE" "backend /health"
  assert_json "$outfile" '.status == "alive"' "backend /health"
  pass "backend /health" "$CURL_TIME"
}

check_ready() {
  local outfile="$TMP_DIR/backend-ready.json"
  perform_request GET "$BACKEND_URL/ready" "$outfile"
  assert_status 200 "$HTTP_CODE" "backend /ready"
  assert_json "$outfile" '.status == "ready" and .model_loaded == true' "backend /ready"
  pass "backend /ready" "$CURL_TIME"
}

check_backend_chat() {
  local workflow_body
  local workflow_out="$TMP_DIR/backend-chat-workflow.json"
  workflow_body="$(
    jq -nc \
      --arg msg "I need a TIN" \
      --arg cid "smoke-backend-workflow-$RUN_ID" \
      '{message:$msg,conversation_id:$cid}'
  )"
  perform_request POST "$BACKEND_URL/v1/chat" "$workflow_out" "$workflow_body"
  assert_status 200 "$HTTP_CODE" "backend /v1/chat workflow"
  assert_json \
    "$workflow_out" \
    '.model == "'"${EXPECTED_MODEL}"'"
      and .retrieval_mode == "workflow"
      and .agent_role == "workflow_guide"
      and (.workflow.id == "tin_registration")
      and (.reply | test("individual|company|ngo"; "i"))' \
    "backend /v1/chat workflow"
  pass "backend /v1/chat workflow" "$CURL_TIME"

  local rag_body
  local rag_out="$TMP_DIR/backend-chat-rag.json"
  rag_body="$(
    jq -nc \
      --arg msg "What is a TIN?" \
      --arg cid "smoke-backend-rag-$RUN_ID" \
      '{message:$msg,conversation_id:$cid}'
  )"
  perform_request POST "$BACKEND_URL/v1/chat" "$rag_out" "$rag_body"
  assert_status 200 "$HTTP_CODE" "backend /v1/chat rag"
  assert_json \
    "$rag_out" \
    '.model == "'"${EXPECTED_MODEL}"'"
      and (.retrieval_mode == "hybrid" or .retrieval_mode == "hybrid_corrected")
      and .agent_role == "rag_answerer"
      and (.citations | length > 0)
      and (.response_judge.final_decision == "approve")
      and (.reply | test("Taxpayer Identification Number|10-digit"; "i"))
      and ((.reply | test("(^okay, the user|let me check|looking at passage|the user is asking|the key detail here is)"; "i")) | not)' \
    "backend /v1/chat rag"
  pass "backend /v1/chat rag" "$CURL_TIME"
}

check_backend_stream() {
  local body
  local outfile="$TMP_DIR/backend-chat-stream.sse"
  body="$(
    jq -nc \
      --arg msg "What is a TIN?" \
      --arg cid "smoke-backend-stream-$RUN_ID" \
      '{message:$msg,conversation_id:$cid}'
  )"
  perform_stream_request "$BACKEND_URL/v1/chat/stream" "$outfile" "$body"
  assert_status 200 "$HTTP_CODE" "backend /v1/chat/stream"
  assert_file_contains "$outfile" '^event: metadata$' "backend /v1/chat/stream"
  assert_file_contains "$outfile" "\"model\": \"${EXPECTED_MODEL}\"" "backend /v1/chat/stream"
  assert_file_contains "$outfile" '^event: token$' "backend /v1/chat/stream"
  assert_file_contains "$outfile" 'Taxpayer Identification Number|10-digit' "backend /v1/chat/stream"
  assert_file_not_contains \
    "$outfile" \
    '(^|data: )(Okay, the user|Let me check|Looking at passage|The user is asking|The key detail here is|Passage \[[0-9]+\]|So the definition is consistent)' \
    "backend /v1/chat/stream"
  assert_file_contains "$outfile" '^event: done$' "backend /v1/chat/stream"
  pass "backend /v1/chat/stream" "$CURL_TIME"
}

check_frontend_root() {
  local outfile="$TMP_DIR/frontend-root.html"
  perform_request GET "$FRONTEND_URL/" "$outfile"
  assert_root_status "$HTTP_CODE" "frontend /"
  pass "frontend /" "$CURL_TIME"
}

check_frontend_proxy() {
  local speech_out="$TMP_DIR/frontend-speech-health.json"
  perform_request GET "$FRONTEND_URL/api/v1/speech/health" "$speech_out"
  assert_status 200 "$HTTP_CODE" "frontend /api/v1/speech/health"
  assert_json "$speech_out" '.status == "ready" and .enabled == true' "frontend /api/v1/speech/health"
  pass "frontend /api/v1/speech/health" "$CURL_TIME"

  local workflow_body
  local workflow_out="$TMP_DIR/frontend-chat-workflow.json"
  workflow_body="$(
    jq -nc \
      --arg msg "I need a TIN" \
      --arg cid "smoke-frontend-workflow-$RUN_ID" \
      '{message:$msg,conversation_id:$cid}'
  )"
  perform_request POST "$FRONTEND_URL/api/v1/chat" "$workflow_out" "$workflow_body"
  assert_status 200 "$HTTP_CODE" "frontend /api/v1/chat workflow"
  assert_json \
    "$workflow_out" \
    '.model == "'"${EXPECTED_MODEL}"'"
      and .retrieval_mode == "workflow"
      and .agent_role == "workflow_guide"
      and (.workflow.id == "tin_registration")' \
    "frontend /api/v1/chat workflow"
  pass "frontend /api/v1/chat workflow" "$CURL_TIME"

  local rag_body
  local rag_out="$TMP_DIR/frontend-chat-rag.json"
  rag_body="$(
    jq -nc \
      --arg msg "What is a TIN?" \
      --arg cid "smoke-frontend-rag-$RUN_ID" \
      '{message:$msg,conversation_id:$cid}'
  )"
  perform_request POST "$FRONTEND_URL/api/v1/chat" "$rag_out" "$rag_body"
  assert_status 200 "$HTTP_CODE" "frontend /api/v1/chat rag"
  assert_json \
    "$rag_out" \
    '.model == "'"${EXPECTED_MODEL}"'"
      and (.retrieval_mode == "hybrid" or .retrieval_mode == "hybrid_corrected")
      and .agent_role == "rag_answerer"
      and (.citations | length > 0)
      and (.response_judge.final_decision == "approve")
      and ((.reply | test("(^okay, the user|let me check|looking at passage|the user is asking|the key detail here is)"; "i")) | not)' \
    "frontend /api/v1/chat rag"
  pass "frontend /api/v1/chat rag" "$CURL_TIME"
}

check_frontend_stream() {
  local body
  local outfile="$TMP_DIR/frontend-chat-stream.sse"
  body="$(
    jq -nc \
      --arg msg "What is a TIN?" \
      --arg cid "smoke-frontend-stream-$RUN_ID" \
      '{message:$msg,conversation_id:$cid}'
  )"
  perform_stream_request "$FRONTEND_URL/api/v1/chat/stream" "$outfile" "$body"
  assert_status 200 "$HTTP_CODE" "frontend /api/v1/chat/stream"
  assert_file_contains "$outfile" '^event: metadata$' "frontend /api/v1/chat/stream"
  assert_file_contains "$outfile" "\"model\": \"${EXPECTED_MODEL}\"" "frontend /api/v1/chat/stream"
  assert_file_contains "$outfile" '^event: token$' "frontend /api/v1/chat/stream"
  assert_file_contains "$outfile" 'Taxpayer Identification Number|10-digit' "frontend /api/v1/chat/stream"
  assert_file_not_contains \
    "$outfile" \
    '(^|data: )(Okay, the user|Let me check|Looking at passage|The user is asking|The key detail here is|Passage \[[0-9]+\]|So the definition is consistent)' \
    "frontend /api/v1/chat/stream"
  assert_file_contains "$outfile" '^event: done$' "frontend /api/v1/chat/stream"
  pass "frontend /api/v1/chat/stream" "$CURL_TIME"
}

main() {
  require_cmd curl
  require_cmd jq

  printf 'Running live smoke checks\n'
  printf '  backend : %s\n' "$BACKEND_URL"
  printf '  frontend: %s\n' "$FRONTEND_URL"
  printf '  run id  : %s\n' "$RUN_ID"

  check_health
  check_ready
  check_backend_chat
  check_backend_stream
  check_frontend_root
  check_frontend_proxy
  check_frontend_stream

  printf 'PASS all live smoke checks completed successfully\n'
}

main "$@"
