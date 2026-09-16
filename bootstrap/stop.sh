#!/usr/bin/env bash
# Stop the agent-mesh edge agent (keepalive supervisor + running binary).
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_PAT="$DIR/bin/agent-mesh-edge.bin"

# 1) keepalive supervisor (so it cannot restart the agent while we stop it).
if [ -f "$DIR/run/keepalive.pid" ]; then
    PID="$(cat "$DIR/run/keepalive.pid" 2>/dev/null || true)"
    if [ -n "${PID:-}" ]; then
        kill "$PID" 2>/dev/null || true
    fi
    rm -f "$DIR/run/keepalive.pid"
fi
pkill -f "$DIR/keepalive.sh" 2>/dev/null || true

# 2) the edge binary (PyInstaller onefile parent + child).
pkill -TERM -f "$AGENT_PAT" 2>/dev/null || true

# 3) wait for a clean exit, then escalate to SIGKILL if it lingers.
if command -v pgrep >/dev/null 2>&1; then
    i=0
    while [ "$i" -lt 20 ] && pgrep -f "$AGENT_PAT" >/dev/null 2>&1; do
        sleep 0.5
        i=$((i + 1))
    done
    if pgrep -f "$AGENT_PAT" >/dev/null 2>&1; then
        pkill -KILL -f "$AGENT_PAT" 2>/dev/null || true
    fi
fi

rm -f "$DIR/run/edge.lock"
echo "agent-mesh edge stopped"
