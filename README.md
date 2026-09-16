# agent-mesh-edge

Self-contained source for the **agent-mesh edge probe** (the agent that runs on
each remote machine, polls the orchestrator for tasks and reports results).

This repository is the single source of truth for the probe. The orchestrator
repository (`agent-mesh`) only consumes the built bootstrap package.

## Branches

| Branch | Purpose | Version |
| --- | --- | --- |
| `main` | Standard probe, installed as a **systemd** service (Linux) / LaunchDaemon (macOS). | `1.6.3` |
| `nosystemd` | Same probe plus a **no-systemd** installer (Firecracker microVMs, plain containers): autostart via `/etc/profile.d` + `setsid`. | `1.6.3-docker` |

## Layout

```
src/agent_mesh/edge/     probe source (agent, runner, execution, upgrade, config)
src/agent_mesh/shared/   vendored protocol subset (constants, schemas, permissions)
scripts/                 PyInstaller spec + bootstrap package builder
bootstrap/install.sh     canonical installer shipped inside the package
VERSION                  probe version (also embedded in the package)
```

## Build

```sh
python scripts/build-agent-bootstrap.py            # -> dist/agent-mesh-edge-nosystemd-<VERSION>.tar.gz
python scripts/build-agent-bootstrap.py --opencode /path/to/opencode
```

The script downloads `opencode` automatically when it is not installed locally.
The resulting package bundles `install-nosystemd.sh` + `start.sh`/`stop.sh`/
`status.sh`/`keepalive.sh` and is distributed manually (e.g. copied to the
microVM management platform); it is not uploaded to the orchestrator bootstrap
endpoint.

## Notes

- `agent_mesh` is a PEP 420 namespace package; this repo does **not** ship an
  `agent_mesh/__init__.py`.
- Keep `src/agent_mesh/shared/schemas.py` in sync with the orchestrator: it is
  the wire-protocol contract between probe and server.
