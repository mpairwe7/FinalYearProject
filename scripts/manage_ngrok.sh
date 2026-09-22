#!/usr/bin/env bash
# =============================================================================
# URA Chatbot — Enterprise ngrok Tunnel & Ingress Manager
# =============================================================================
# Usage:
#   ./scripts/manage_ngrok.sh start     # Stop old, start clean tunnel & verify
#   ./scripts/manage_ngrok.sh stop      # Stop any running ngrok tunnels
#   ./scripts/manage_ngrok.sh restart   # Full restart & health check
#   ./scripts/manage_ngrok.sh status    # Inspect live tunnel, metrics, and URLs
#   ./scripts/manage_ngrok.sh health    # Live public endpoint smoke test
#   ./scripts/manage_ngrok.sh rebuild   # Rebuild frontend + sync backend + restart tunnel
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
NGROK_LOG="${LOG_DIR}/ngrok.log"

NGROK_BIN="${HOME}/.config/ngrok/ngrok"
if [[ ! -x "${NGROK_BIN}" ]]; then
  NGROK_BIN="$(which ngrok 2>/dev/null || true)"
fi

TUNNEL_NAME="ura"
LOCAL_PORT="${LOCAL_PORT:-3032}"
EXPECTED_DOMAIN="struttingly-nongeological-briella.ngrok-free.dev"
PUBLIC_URL="https://${EXPECTED_DOMAIN}"
INSPECT_URL="http://127.0.0.1:4040"

mkdir -p "${LOG_DIR}"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

stop_ngrok() {
  log_info "Stopping existing ngrok processes..."
  local pids
  pids=$(pgrep -f "ngrok" || true)
  if [[ -n "${pids}" ]]; then
    for pid in ${pids}; do
      # Avoid killing current script if matched
      if [[ "${pid}" != "$$" ]]; then
        kill -15 "${pid}" 2>/dev/null || true
      fi
    done
    sleep 1
    # Force kill if still running
    pids=$(pgrep -f "ngrok" || true)
    if [[ -n "${pids}" ]]; then
      for pid in ${pids}; do
        if [[ "${pid}" != "$$" ]]; then
          kill -9 "${pid}" 2>/dev/null || true
        fi
      done
    fi
    log_ok "All previous ngrok processes terminated."
  else
    log_info "No existing ngrok processes were running."
  fi
}

check_local_target() {
  log_info "Checking local target service on port ${LOCAL_PORT}..."
  if ! curl -s -f -m 3 "http://127.0.0.1:${LOCAL_PORT}/api/health" >/dev/null 2>&1 && \
     ! curl -s -f -m 3 "http://127.0.0.1:${LOCAL_PORT}/" >/dev/null 2>&1; then
    log_warn "Target port ${LOCAL_PORT} does not appear to be answering on /api/health or /."
    log_warn "Verifying docker containers (ura-app-frontend / ura-app-api)..."
    if command -v docker >/dev/null 2>&1; then
      docker ps --filter "name=ura-app" --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
    fi
  else
    log_ok "Local service on port ${LOCAL_PORT} is healthy and responding."
  fi
}

start_ngrok() {
  stop_ngrok
  check_local_target

  if [[ ! -x "${NGROK_BIN}" ]]; then
    log_error "ngrok binary not found at ${NGROK_BIN} or in PATH."
    exit 1
  fi

  log_info "Launching ngrok tunnel [${TUNNEL_NAME}] -> port ${LOCAL_PORT}..."
  # Start tunnel via configuration or fallback flags
  nohup "${NGROK_BIN}" http "${LOCAL_PORT}" \
    --url="${EXPECTED_DOMAIN}" \
    --log=stdout \
    --log-format=term > "${NGROK_LOG}" 2>&1 &

  local ngrok_pid=$!
  disown "${ngrok_pid}" 2>/dev/null || true
  log_info "ngrok process spawned (PID: ${ngrok_pid}). Log: ${NGROK_LOG}"

  log_info "Waiting for ngrok tunnel readiness on ${INSPECT_URL}..."
  local ready=0
  for _ in {1..20}; do
    if curl -s -m 2 "${INSPECT_URL}/api/tunnels" | grep -q "${EXPECTED_DOMAIN}"; then
      ready=1
      break
    fi
    sleep 0.5
  done

  if [[ ${ready} -eq 1 ]]; then
    log_ok "Tunnel is ACTIVE and published:"
    echo -e "   ${GREEN}► Public URL:${NC}       ${PUBLIC_URL}"
    echo -e "   ${GREEN}► Forwarding to:${NC}    http://localhost:${LOCAL_PORT}"
    echo -e "   ${GREEN}► Web Dashboard:${NC}    ${INSPECT_URL}"
    echo -e "   ${GREEN}► Log file:${NC}         ${NGROK_LOG}"
  else
    log_error "Tunnel failed to start within timeout. Check log below:"
    tail -n 25 "${NGROK_LOG}" || true
    exit 1
  fi

  run_health_check
}

show_status() {
  log_info "Querying ngrok status from ${INSPECT_URL}..."
  if ! curl -s -m 2 "${INSPECT_URL}/api/tunnels" >/dev/null 2>&1; then
    log_warn "ngrok local inspection API (${INSPECT_URL}) is unreachable. ngrok is not running."
    return 1
  fi

  local tunnels_json
  tunnels_json=$(curl -s -m 2 "${INSPECT_URL}/api/tunnels")
  local pub_url
  pub_url=$(echo "${tunnels_json}" | jq -r '.tunnels[0].public_url // empty' 2>/dev/null || true)
  local target_addr
  target_addr=$(echo "${tunnels_json}" | jq -r '.tunnels[0].config.addr // empty' 2>/dev/null || true)
  local http_count
  http_count=$(echo "${tunnels_json}" | jq -r '.tunnels[0].metrics.http.count // 0' 2>/dev/null || true)
  local conn_count
  conn_count=$(echo "${tunnels_json}" | jq -r '.tunnels[0].metrics.conns.count // 0' 2>/dev/null || true)

  log_ok "ngrok Daemon Status: RUNNING"
  echo "--------------------------------------------------------"
  echo -e "  Public Endpoint:    ${GREEN}${pub_url}${NC}"
  echo -e "  Forward Target:     ${target_addr}"
  echo -e "  Total Connections:  ${conn_count}"
  echo -e "  Total HTTP Hits:    ${http_count}"
  echo -e "  Inspect Dashboard:  ${INSPECT_URL}"
  echo "--------------------------------------------------------"
}

run_health_check() {
  log_info "Executing public end-to-end health probe against ${PUBLIC_URL}..."
  local health_code
  health_code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
    -H "ngrok-skip-browser-warning: 1" \
    "${PUBLIC_URL}/api/health" || echo "000")

  if [[ "${health_code}" == "200" ]]; then
    log_ok "Public API probe: HTTP 200 OK (${PUBLIC_URL}/api/health)"
  else
    log_warn "Public API probe returned HTTP ${health_code} on /api/health"
  fi

  local root_code
  root_code=$(curl -s -o /dev/null -w "%{http_code}" -m 10 \
    -H "ngrok-skip-browser-warning: 1" \
    "${PUBLIC_URL}/" || echo "000")

  if [[ "${root_code}" == "200" ]]; then
    log_ok "Public Web UI probe: HTTP 200 OK (${PUBLIC_URL}/)"
  else
    log_warn "Public Web UI probe returned HTTP ${root_code} on /"
  fi
}

rebuild_stack_and_tunnel() {
  log_info "Initiating complete rebuild of URA stack and ngrok tunnel..."

  log_info "1. Syncing latest backend code to container..."
  if docker ps | grep -q "ura-app-api"; then
    docker cp "${ROOT_DIR}/App/backend/app/." ura-app-api:/app/app/
    log_ok "Backend code synchronized into ura-app-api."
    docker restart ura-app-api >/dev/null 2>&1 || true
    log_ok "ura-app-api restarted."
  fi

  log_info "2. Rebuilding frontend Docker container..."
  (cd "${ROOT_DIR}/App" && docker compose build frontend)
  (cd "${ROOT_DIR}/App" && docker compose up -d frontend)
  log_ok "Frontend container rebuilt and listening on port ${LOCAL_PORT}."

  log_info "3. Restarting ngrok tunnel..."
  start_ngrok
  log_ok "Complete stack rebuild and tunnel ingress established successfully."
}

case "${1:-status}" in
  start)
    start_ngrok
    ;;
  stop)
    stop_ngrok
    ;;
  restart)
    start_ngrok
    ;;
  status)
    show_status
    ;;
  health)
    run_health_check
    ;;
  rebuild)
    rebuild_stack_and_tunnel
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|health|rebuild}"
    exit 1
    ;;
esac
