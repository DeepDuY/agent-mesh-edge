#!/usr/bin/env bash
# Install the agent-mesh edge agent on a host WITHOUT systemd / cron / an
# entrypoint you control (e.g. a Firecracker microVM or a plain container).
#
# Autostart: it writes /etc/profile.d/agent-mesh-edge.sh. That file is sourced
# by /etc/profile, which is read by the login shell the Firecracker init starts
# at boot AND by every SSH login shell -- so the agent starts at boot and on
# login. The script detaches via setsid, so it survives logout.
#
# Usage:
#   ORCHESTRATOR_URL=http://host:8000 TOKEN=<user-token> EDGE_ALIAS=name \
#     bash install-nosystemd.sh
#
#   FORCE_REINSTALL=1 ...   # replace an existing installation in INSTALL_DIR
#
# Env (optional): INSTALL_DIR, WORK_DIR, EDGE_LLM_API_KEY, EDGE_LLM_BASE_URL,
#                 EDGE_LLM_MODEL, EDGE_LLM_MODELS, EDGE_SYSTEM_PROMPT
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="${INSTALL_DIR:-/opt/agent-mesh-agent}"
ORCHESTRATOR_URL="${ORCHESTRATOR_URL:-}"
TOKEN="${TOKEN:-}"
AGENT_ID="${EDGE_ALIAS:-$(hostname -s 2>/dev/null || echo agent)}"
WORK_DIR="${WORK_DIR:-${INSTALL_DIR}/work}"

if [ -z "$ORCHESTRATOR_URL" ]; then
    echo "ERROR: ORCHESTRATOR_URL is required (e.g. http://10.0.0.1:8000)" >&2
    exit 1
fi
if [ -z "$TOKEN" ]; then
    echo "ERROR: TOKEN is required (your user API token)" >&2
    exit 1
fi

if [ "$(id -u)" -ne 0 ]; then
    echo "WARNING: not running as root; writing ${INSTALL_DIR} or /etc/profile.d may fail" >&2
fi

# ---- stop an existing install so bin/ files can be replaced --------------
if [ -x "${INSTALL_DIR}/stop.sh" ]; then
    "${INSTALL_DIR}/stop.sh" >/dev/null 2>&1 || true
else
    pkill -f "${INSTALL_DIR}/keepalive.sh" 2>/dev/null || true
    pkill -TERM -f "${INSTALL_DIR}/bin/agent-mesh-edge.bin" 2>/dev/null || true
    if command -v pgrep >/dev/null 2>&1; then
        i=0
        while [ "$i" -lt 20 ] && pgrep -f "${INSTALL_DIR}/bin/agent-mesh-edge.bin" >/dev/null 2>&1; do
            sleep 0.5
            i=$((i + 1))
        done
        pkill -KILL -f "${INSTALL_DIR}/bin/agent-mesh-edge.bin" 2>/dev/null || true
    fi
fi

if { [ -f "${INSTALL_DIR}/bin/agent-mesh-edge.bin" ] || [ -f "${INSTALL_DIR}/bin/agent-mesh-edge" ]; } \
   && [ -z "${FORCE_REINSTALL:-}" ]; then
    echo "ERROR: ${INSTALL_DIR} already has an installation." >&2
    echo "       Re-run with FORCE_REINSTALL=1 to replace it." >&2
    exit 1
fi

echo "==> Installing agent-mesh edge (nosystemd) to ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}/bin" "${INSTALL_DIR}/etc" "${INSTALL_DIR}/run" \
         "${INSTALL_DIR}/logs" "${WORK_DIR}"

# Drop stale sync/upgrade state.
rm -f "${INSTALL_DIR}/etc/config_version" "${INSTALL_DIR}/etc/upgrading" \
      "${INSTALL_DIR}/etc/upgrade-started" "${INSTALL_DIR}/bin/agent-mesh-edge.bin.old" \
      "${INSTALL_DIR}/etc/agent_version.bak"

# Binaries: real binary as .bin, rollback-aware wrapper as agent-mesh-edge.
cp -f "${SCRIPT_DIR}/bin/agent-mesh-edge.bin" "${INSTALL_DIR}/bin/agent-mesh-edge.bin"
cp -f "${SCRIPT_DIR}/bin/agent-mesh-edge" "${INSTALL_DIR}/bin/agent-mesh-edge"
cp -f "${SCRIPT_DIR}/bin/opencode" "${INSTALL_DIR}/bin/opencode"
chmod +x "${INSTALL_DIR}/bin/agent-mesh-edge.bin" \
         "${INSTALL_DIR}/bin/agent-mesh-edge" \
         "${INSTALL_DIR}/bin/opencode"

# Helper scripts.
for f in start.sh stop.sh status.sh keepalive.sh; do
    cp -f "${SCRIPT_DIR}/${f}" "${INSTALL_DIR}/${f}"
    chmod +x "${INSTALL_DIR}/${f}"
done

# Runtime config.
cat > "${INSTALL_DIR}/etc/edge.env" <<EOF
EDGE_AGENT_ID=${AGENT_ID}
EDGE_ORCHESTRATOR_URL=${ORCHESTRATOR_URL}
EDGE_TOKEN=${TOKEN}
EDGE_HEARTBEAT_S=3
EDGE_RUNTIME=opencode
EDGE_WORKDIR=${WORK_DIR}
EDGE_INSTALL_DIR=${INSTALL_DIR}
EDGE_LLM_API_KEY=${EDGE_LLM_API_KEY:-}
EDGE_LLM_BASE_URL=${EDGE_LLM_BASE_URL:-}
EDGE_LLM_MODEL=${EDGE_LLM_MODEL:-}
EDGE_LLM_MODELS=${EDGE_LLM_MODELS:-}
EDGE_SYSTEM_PROMPT=${EDGE_SYSTEM_PROMPT:-}
LOG_LEVEL=INFO
EOF
chmod 600 "${INSTALL_DIR}/etc/edge.env"

# Version marker reported to the orchestrator.
if [ -f "${SCRIPT_DIR}/VERSION" ]; then
    cp -f "${SCRIPT_DIR}/VERSION" "${INSTALL_DIR}/etc/agent_version"
fi

# ---- boot + login autostart ----------------------------------------------
PROFILE_HOOK="/etc/profile.d/agent-mesh-edge.sh"
cat > "$PROFILE_HOOK" <<EOF
# agent-mesh edge agent autostart (boot + login).
# Sourced by /etc/profile, which the Firecracker init's login shell reads at
# boot and which every SSH login shell reads. MUST NOT exit or return non-zero
# (the boot shell runs with 'set -e'). Keep it bulletproof and non-blocking.
if [ -x "${INSTALL_DIR}/start.sh" ]; then
    "${INSTALL_DIR}/start.sh" >/dev/null 2>&1 || true
fi
:
EOF
chmod 644 "$PROFILE_HOOK"

# ---- start now -----------------------------------------------------------
"${INSTALL_DIR}/start.sh"

echo "==> Installed agent-mesh edge (nosystemd special build)"
echo "    version   : $(cat "${INSTALL_DIR}/etc/agent_version" 2>/dev/null || echo '?')"
echo "    autostart : ${PROFILE_HOOK}  (boot + login)"
echo "    status    : ${INSTALL_DIR}/status.sh"
echo "    control   : ${INSTALL_DIR}/start.sh | ${INSTALL_DIR}/stop.sh"
echo "    log       : ${INSTALL_DIR}/logs/edge.log"
