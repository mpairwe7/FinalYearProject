#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8887}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:13000}"
WAIT_TIMEOUT_SECONDS="${PREFLIGHT_WAIT_TIMEOUT_SECONDS:-180}"
POLL_INTERVAL_SECONDS="${PREFLIGHT_POLL_INTERVAL_SECONDS:-2}"
TMP_DIR="$(mktemp -d)"

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

wait_for_url() {
  local label=$1
  local url=$2
  local status_mode=$3
  local outfile="$TMP_DIR/$(printf '%s' "$label" | tr ' /:' '__').out"
  local start_ts end_ts elapsed code
  start_ts=$SECONDS

  while (( SECONDS - start_ts < WAIT_TIMEOUT_SECONDS )); do
    code="$(curl -sS --max-time 10 -o "$outfile" -w '%{http_code}' "$url" || true)"
    case "$status_mode" in
      ok)
        if [[ "$code" == "200" ]]; then
          end_ts=$SECONDS
          elapsed=$(( end_ts - start_ts ))
          printf 'READY %-26s %4ss\n' "$label" "$elapsed"
          return 0
        fi
        ;;
      root)
        if [[ "$code" == "200" || "$code" == "307" ]]; then
          end_ts=$SECONDS
          elapsed=$(( end_ts - start_ts ))
          printf 'READY %-26s %4ss\n' "$label" "$elapsed"
          return 0
        fi
        ;;
      *)
        printf 'Unknown wait mode: %s\n' "$status_mode" >&2
        exit 1
        ;;
    esac
    sleep "$POLL_INTERVAL_SECONDS"
  done

  printf 'Timed out waiting for %s at %s\n' "$label" "$url" >&2
  if [[ -f "$outfile" ]]; then
    printf -- '--- last response (%s) ---\n' "$label" >&2
    tr -d '\r' < "$outfile" | sed -n '1,120p' >&2
    printf -- '--- end response ---\n' >&2
  fi
  exit 1
}

main() {
  require_cmd curl
  require_cmd bash
  if [[ "${APP_ENV:-development}" == "production" || "${REQUIRE_FRESH_AUTHORITY:-false}" == "true" ]]; then
    require_cmd python3
    ROOT_DIR="$ROOT_DIR" python3 -c "import os, sys; sys.path.insert(0, os.path.join(os.environ['ROOT_DIR'], 'backend')); from app.authority import get_authority_status; s=get_authority_status(); print('AUTHORITY', 'ok' if s.get('ok') else 'failed', s.get('manifest_path')); sys.exit(0 if s.get('ok') else 1)" \
      || {
        printf 'Authority manifest preflight failed. Set URA_AUTHORITY_MANIFEST to a fresh hash-checked bundle.\n' >&2
        exit 1
      }
  fi

  printf 'Running deploy preflight\n'
  printf '  backend : %s\n' "$BACKEND_URL"
  printf '  frontend: %s\n' "$FRONTEND_URL"
  printf '  timeout : %ss\n' "$WAIT_TIMEOUT_SECONDS"

  wait_for_url "backend /health" "$BACKEND_URL/health" ok
  wait_for_url "backend /ready" "$BACKEND_URL/ready" ok
  wait_for_url "frontend /" "$FRONTEND_URL/" root
  wait_for_url "frontend /api/v1/speech/health" "$FRONTEND_URL/api/v1/speech/health" ok

  BACKEND_URL="$BACKEND_URL" FRONTEND_URL="$FRONTEND_URL" "$SCRIPT_DIR/live_smoke.sh"
}

main "$@"
