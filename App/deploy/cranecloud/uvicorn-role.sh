#!/bin/sh
# Start the API in one of its two roles, under supervisord (see supervisord.conf).
#
#     uvicorn-role.sh chat    the API for everything but calls, 127.0.0.1:8081
#     uvicorn-role.sh calls   the call process, one worker, 127.0.0.1:8082
#
# A live call lives in the memory of the one process that accepted it: its room,
# the officers' sockets and the transfer timers. Several workers behind one port
# would answer "no such call" for most requests, so nginx sends every call path to
# the single-worker `calls` process (nginx.conf) and the rest to the API upstream.
# URA_PROCESS_ROLE tells the app which it is: the chat role refuses a call path
# that reaches it, and reports the call process on /ready.
#
# Reads    UVICORN_WORKERS          the image's total worker budget, default 2
#          LOG_LEVEL                uvicorn's log level, default info
#          PROMETHEUS_MULTIPROC_DIR where every worker's metric samples are written
#          URA_DRY_RUN=1            print the launch instead of exec'ing it
# Exports  URA_PROCESS_ROLE
#          chat:  URA_CALLS_PROCESS_URL=http://127.0.0.1:8082
#          calls: UVICORN_WORKERS=1, which is how the receptionist knows it is the
#                 only call process (VOICE_RECEPTIONIST_REPLICAS=1 is the Space's)
#
# Exits 2 on an unknown role. Like wait-for-qdrant.sh, it execs the server, so
# supervisord's signals and stop handling reach the real process.
set -eu

ROLE="${1:-}"
case "$ROLE" in
    chat)
        PORT=8081
        ;;
    calls)
        PORT=8082
        ;;
    *)
        echo "uvicorn-role: unknown role '$ROLE' (expected chat or calls)" >&2
        exit 2
        ;;
esac

# The chat role gets one worker fewer than the budget so the two roles together
# run what the single program ran: UVICORN_WORKERS=2 is one chat worker plus the
# call process. Blank, non-numeric or zero counts as the image default of 2, and
# the chat role always keeps at least one worker. Leading zeros are dropped first
# because the shell reads a leading 0 as octal.
TOTAL="${UVICORN_WORKERS:-}"
case "$TOTAL" in
    '' | *[!0-9]*) TOTAL=2 ;;
esac
while [ "${TOTAL#0}" != "$TOTAL" ]; do
    TOTAL="${TOTAL#0}"
done
[ -n "$TOTAL" ] || TOTAL=2

if [ "$ROLE" = calls ]; then
    WORKERS=1
    export UVICORN_WORKERS=1
else
    WORKERS=$((TOTAL - 1))
    [ "$WORKERS" -ge 1 ] || WORKERS=1
    export URA_CALLS_PROCESS_URL="http://127.0.0.1:8082"
fi
export URA_PROCESS_ROLE="$ROLE"

# Each worker writes its samples to this directory and /metrics adds them up
# (app/analytics.py), so files left by a previous run would be counted twice.
# They are cleared once per container start, not once per program start: the
# other role's workers write here too, and supervisord restarting this program
# inside a running container must not wipe the samples they have taken since.
# `.reset` records that this container has done it.
#
# One kind of file is dropped on every start. A live gauge (gauge_livemin_<pid>.db,
# gauge_livesum_<pid>.db, ...) is meant to describe the processes that are up, and
# prometheus_client removes a dead process's files only in mark_process_dead(),
# which nothing here calls. So after a crash the dead process's last
# receptionist_available (a min) or connection count (a sum) would still be read as
# current. Counters, histograms and the other gauges stay: they are history.
# `kill -0` is the liveness test; everything in the container runs as root, so
# "cannot signal" means "no such process". The pid is whatever follows the last
# underscore, and a name without one is left alone.
#
# Both roles start together, so the check, the clearing and the sweep run under one
# lock; it is released when flock's child exits, before the server starts, so the
# server never holds it.
DIR="${PROMETHEUS_MULTIPROC_DIR:?PROMETHEUS_MULTIPROC_DIR must be set}"
mkdir -p "$DIR"
flock "$DIR/.lock" sh -c '
    if [ ! -e "$1/.reset" ]; then
        find "$1" -maxdepth 1 -type f -name "*.db" -delete
        : > "$1/.reset"
    fi
    for file in "$1"/gauge_live*_*.db; do
        [ -f "$file" ] || continue
        pid="${file##*_}"
        pid="${pid%.db}"
        case "$pid" in
            "" | *[!0-9]*) continue ;;
        esac
        kill -0 "$pid" 2>/dev/null || rm -f "$file"
    done
' sh "$DIR"

# Gated on the Qdrant sidecar (wait-for-qdrant.sh is bounded and never fail-closed).
set -- /usr/local/bin/wait-for-qdrant.sh /opt/venv/bin/uvicorn app.main:app \
    --host 127.0.0.1 --port "$PORT" --workers "$WORKERS" \
    --log-level "${LOG_LEVEL:-info}" --proxy-headers '--forwarded-allow-ips=*'

if [ "${URA_DRY_RUN:-}" = 1 ]; then
    echo "URA_PROCESS_ROLE=$URA_PROCESS_ROLE"
    case "$ROLE" in
        chat) echo "URA_CALLS_PROCESS_URL=$URA_CALLS_PROCESS_URL" ;;
        calls) echo "UVICORN_WORKERS=$UVICORN_WORKERS" ;;
    esac
    echo "exec $*"
    exit 0
fi

exec "$@"
