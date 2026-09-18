"""Windows support: platform defaults, command shell selection, task kill path."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import tarfile

import pytest

from agent_mesh.edge import execution as exec_pkg
from agent_mesh.edge.config import EdgeConfig, default_install_dir
from agent_mesh.edge.execution import process as proc


def test_default_install_dir_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert default_install_dir() == "/opt/agent-mesh-agent"


def test_default_install_dir_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert default_install_dir() == r"C:\ProgramData\agent-mesh-agent"


def test_config_agent_id_does_not_use_os_uname(monkeypatch):
    # os.uname is absent on Windows; platform.node() must be used instead.
    monkeypatch.delenv("EDGE_AGENT_ID", raising=False)
    monkeypatch.delenv("EDGE_SHELL", raising=False)
    cfg = EdgeConfig(_env_file=None)
    assert cfg.agent_id
    assert cfg.shell == ""
    assert cfg.install_dir


def test_config_reads_shell_override(monkeypatch):
    monkeypatch.setenv("EDGE_SHELL", "powershell")
    cfg = EdgeConfig(_env_file=None)
    assert cfg.shell == "powershell"


def test_shell_command_posix_default(monkeypatch):
    monkeypatch.setattr(proc, "_IS_WINDOWS", False)
    assert proc.shell_command("echo hi") == ["bash", "-c", "echo hi"]


def test_shell_command_windows_cmd(monkeypatch):
    monkeypatch.setattr(proc, "_IS_WINDOWS", True)
    assert proc.shell_command("dir") == ["cmd", "/d", "/s", "/c", "dir"]


def test_shell_command_windows_powershell(monkeypatch):
    monkeypatch.setattr(proc, "_IS_WINDOWS", True)
    assert proc.shell_command("Get-ChildItem", "powershell") == [
        "powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-ChildItem",
    ]
    assert proc.shell_command("Get-ChildItem", r"C:\pwsh\pwsh.exe") == [
        r"C:\pwsh\pwsh.exe", "-NoProfile", "-NonInteractive", "-Command", "Get-ChildItem",
    ]


def test_executor_passes_shell_to_command(monkeypatch, tmp_path):
    captured: dict = {}

    async def fake_run_command(task, workdir, cancel_event, log_callback, permission=None, shell=""):
        captured["shell"] = shell
        return None

    monkeypatch.setattr(exec_pkg, "run_command", fake_run_command)

    class _Task:
        mode = "command"
        task_id = "t1"

        class constraints:  # noqa: N801
            workdir = "."

    asyncio.run(exec_pkg.Executor(
        runtime="opencode", llm_api_key="", llm_base_url="", llm_model="",
        default_workdir=str(tmp_path), shell="cmd",
    ).run_task(_Task()))
    assert captured["shell"] == "cmd"


@pytest.mark.skipif(os.name != "posix", reason="uses a POSIX `sleep` helper process")
def test_signal_tree_windows_terminates_process():
    # _signal_tree_windows uses psutil (cross-platform), so the tree-kill path
    # can be exercised on the Linux test host.
    pytest.importorskip("psutil")

    async def _run():
        child = await asyncio.create_subprocess_exec(
            "sleep", "30",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            proc._signal_tree_windows(child, force=False)
            await asyncio.wait_for(child.wait(), timeout=10)
            assert child.returncode is not None
        finally:
            if child.returncode is None:
                child.kill()
                await child.wait()

    asyncio.run(_run())


def test_build_win32_package_layout(monkeypatch, tmp_path):
    """The Windows package must ship install.ps1, the .cmd launcher and .exes."""
    repo = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "build_agent_bootstrap", repo / "scripts" / "build-agent-bootstrap.py"
    )
    bab = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bab)

    dummy = tmp_path / "agent-mesh-edge.exe"
    dummy.write_bytes(b"FAKE-EXE")
    monkeypatch.setattr(bab, "_build_binary", lambda os_name: dummy)

    pkg = bab.build(
        os_name="win32",
        arch="x64",
        opencode=None,
        output_dir=str(tmp_path / "out"),
        allow_missing_opencode=True,
        download_opencode=False,
    )
    with tarfile.open(pkg) as tf:
        members = {name.split("/")[-1] for name in tf.getnames()}
    assert {"install.ps1", "agent-mesh-edge.cmd", "agent-mesh-edge.exe", "VERSION"}.issubset(members)
    assert (tmp_path / "out" / "install.ps1").exists()

