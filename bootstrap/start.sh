#!/usr/bin/env bash
# Start the agent-mesh edge agent detached (no systemd required). Idempotent.
#
# Safe to call at boot and on every login: it returns immediately and does
# nothing if the keepalive supervisor is already running.
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
RUN="$DIR/run"
LOGS="$DIR/logs"
mkdir -p "$RUN" "$LOGS"

# Load runtime config and EXPORT it so the detached agent inherits EDGE_*.
# (sourcing alone does not export; the packaged edge reads env vars with the
# EDGE_ prefix, so without this it would fall back to 127.0.0.1:8000.)
if [ -f "$DIR/etc/edge.env" ]; then
    set -a
    . "$DIR/etc/edge.env"
    set +a
fi

PIDFILE="$RUN/keepalive.pid"
if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
        exit 0
    fi
fi

# Detach fully from the calling (login) shell so the agent survives logout.
setsid nohup "$DIR/keepalive.sh" >>"$LOGS/keepalive.log" 2>&1 </dev/null &
exit 0
