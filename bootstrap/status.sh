#!/usr/bin/env bash
# Show edge agent status (no systemd).
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
VER="$(cat "$DIR/etc/agent_version" 2>/dev/null || echo '?')"

echo "install dir : $DIR"
echo "version     : $VER"

if [ -f "$DIR/run/keepalive.pid" ] && kill -0 "$(cat "$DIR/run/keepalive.pid" 2>/dev/null)" 2>/dev/null; then
    echo "keepalive   : running (pid $(cat "$DIR/run/keepalive.pid"))"
else
    echo "keepalive   : not running"
fi

if pgrep -f "$DIR/bin/agent-mesh-edge.bin" >/dev/null 2>&1; then
    echo "agent       : running (pid $(pgrep -f "$DIR/bin/agent-mesh-edge.bin" | tr '\n' ' '))"
else
    echo "agent       : not running"
fi

echo "--- last log lines (logs/edge.log) ---"
tail -n 5 "$DIR/logs/edge.log" 2>/dev/null || echo "(no log yet)"
