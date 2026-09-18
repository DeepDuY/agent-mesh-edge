# agent-mesh-edge

Self-contained source for the **agent-mesh edge probe** (the agent that runs on
each remote machine, polls the orchestrator for tasks and reports results).

This repository is the single source of truth for the probe. The orchestrator
repository (`agent-mesh`) only consumes the built bootstrap package.

## Branches

| Branch | Purpose | Version |
| --- | --- | --- |
| `main` | Standard probe, installed as a **systemd** service (Linux) / LaunchDaemon (macOS) / Scheduled Task (Windows). | `1.6.4` |
| `nosystemd` | Same probe plus a **no-systemd** installer (Firecracker microVMs, plain containers): autostart via `/etc/profile.d` + `setsid`. | `1.6.4-docker` |

## Layout

```
src/agent_mesh/edge/     probe source (agent, runner, execution, upgrade, config)
src/agent_mesh/shared/   vendored protocol subset (constants, schemas, permissions)
scripts/                 PyInstaller spec + bootstrap package builder
bootstrap/install.sh     canonical installer shipped inside the package (Linux/macOS)
bootstrap/install.ps1    Windows installer (Scheduled Task, SYSTEM)
bootstrap/agent-mesh-edge.cmd  Windows keepalive launcher
VERSION                  probe version (also embedded in the package)
```

## Build

```sh
python scripts/build-agent-bootstrap.py            # -> dist/agent-mesh-agent-<os>-<arch>.tar.gz
python scripts/build-agent-bootstrap.py --opencode /path/to/opencode
python scripts/build-agent-bootstrap.py --os win32 # on a Windows host -> agent-mesh-edge.exe
```

The script downloads `opencode` automatically when it is not installed locally
(including the official `opencode-windows-*.zip` for `win32`). **PyInstaller
cannot cross-compile**: the Windows package must be built on Windows.

The resulting tarball is uploaded to the orchestrator (admin console
`POST /api/bootstrap`) or copied to `/opt/agent-mesh/data/bootstrap/`.

## Releases (CI)

`.github/workflows/build-probe.yml` builds `linux-x64`, `win32-x64` (windows)
and `darwin-arm64` (macOS) on every push to `main` and attaches the packages to
a Release tagged `probe-v<VERSION>`. The Linux package is built inside
`manylinux2014` (CentOS 7, glibc 2.17) on purpose: PyInstaller freezes the build
host's `libpython`, so building on a newer distro would make every probe require
a newer glibc (e.g. `libpython3.12.so.1.0: GLIBC_2.38 not found` on old nodes).
The build script enforces the ceiling (`AGENT_MESH_MAX_GLIBC`, default `2.17`)
and records `glibc_min` in `MANIFEST.json`; `install.sh` refuses to install on a
host with an older glibc. Download the latest directly:

```
https://github.com/DeepDuY/agent-mesh-edge/releases/latest/download/agent-mesh-agent-linux-x64.tar.gz
https://github.com/DeepDuY/agent-mesh-edge/releases/latest/download/agent-mesh-agent-win32-x64.tar.gz
https://github.com/DeepDuY/agent-mesh-edge/releases/latest/download/agent-mesh-agent-darwin-arm64.tar.gz
```

Bump `VERSION` to publish a new tag (and to let nodes auto-upgrade). The
orchestrator can pull these into its cache (`data/bootstrap/`) with
`POST /api/bootstrap/sync` or `scripts/sync_probe_release.py`, or install
scripts can download them directly via the `bootstrap_download_base` setting.

## Install

Linux / macOS (systemd / LaunchDaemon):

```sh
TOKEN='<token>' bash <(curl -fsSL -H "Authorization: Bearer <token>" <url>/api/bootstrap/install.sh)
```

Windows (PowerShell, Administrator):

```powershell
$env:TOKEN='<token>'; irm -Headers @{Authorization="Bearer <token>"} <url>/api/bootstrap/install.ps1 | iex
```

Windows installs to `C:\ProgramData\agent-mesh-agent` and autostarts via a
Scheduled Task (`agent-mesh-edge`, SYSTEM) running the `.cmd` keepalive loop.
Self-upgrade is currently POSIX-only; a Windows node upgrades by reinstalling.

## Notes

- `agent_mesh` is a PEP 420 namespace package; this repo does **not** ship an
  `agent_mesh/__init__.py`.
- Keep `src/agent_mesh/shared/schemas.py` in sync with the orchestrator: it is
  the wire-protocol contract between probe and server.
