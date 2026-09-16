#!/usr/bin/env bash
# Keep the edge agent running; restart it if it exits. Single instance via flock.
#
# This is the long-lived process started detached by start.sh. It runs the
# wrapper (bin/agent-mesh-edge), which loads etc/edge.env and execs the real
# binary (bin/agent-mesh-edge.bin).
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
mkdir -p run logs

# Single-instance guard.
exec 9>"$DIR/run/edge.lock"
if command -v flock >/dev/null 2>&1; then
    flock -n 9 || exit 0
fi
echo $$ >"$DIR/run/keepalive.pid"

while true; do
    "$DIR/bin/agent-mesh-edge" >>"$DIR/logs/edge.log" 2>&1
    code=$?
    echo "$(date '+%Y-%m-%dT%H:%M:%S%z') agent-mesh-edge exited (code=$code); restarting in 5s" >>"$DIR/logs/edge.log"
    sleep 5
done
