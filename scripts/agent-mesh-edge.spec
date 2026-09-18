# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for agent-mesh edge agent."""
from pathlib import Path

project_root = Path(SPECPATH).parent.resolve()
src_root = project_root / "src"

a = Analysis(
    [str(src_root / "agent_mesh" / "edge" / "agent.py")],
    pathex=[str(src_root)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "httpx",
        "pydantic",
        "pydantic_settings",
        "psutil",
        "agent_mesh.edge.agent",
        "agent_mesh.edge.config",
        "agent_mesh.edge.config_writer",
        "agent_mesh.edge.execution",
        "agent_mesh.edge.execution.common",
        "agent_mesh.edge.execution.command",
        "agent_mesh.edge.execution.llm",
        "agent_mesh.edge.rest_client",
        "agent_mesh.edge.runner",
        "agent_mesh.edge.upgrade",
        "agent_mesh.shared.constants",
        "agent_mesh.shared.schemas",
        "agent_mesh.shared.permissions",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# Never bundle the build host's libgcc_s: on modern hosts (Ubuntu 24.04) it is
# linked against a newer glibc (GLIBC_2.35) than the probe targets, so the
# frozen app dies on old distros with "libgcc_s.so.1: GLIBC_2.35 not found".
# Target machines provide their own libgcc_s (part of gcc-libs on every glibc
# distro). No-op on Windows/macOS, whose gcc runtime is named differently.
a.binaries = [b for b in a.binaries if not b[0].startswith("libgcc_s")]

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="agent-mesh-edge",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
