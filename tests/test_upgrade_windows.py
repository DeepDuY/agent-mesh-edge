"""Windows self-upgrade: staging, launcher swap contract and rollback cleanup."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import tarfile
from pathlib import Path

from agent_mesh.edge.upgrade import (
    UpgradeMixin,
    cleanup_upgrade_tmp,
)

ROOT = "agent-mesh-agent-win32-x64"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_package(path: Path, binary: bytes, opencode: bytes) -> dict:
    manifest = {
        "version": "9.9.9",
        "files": {
            "bin/agent-mesh-edge.exe": {"size": len(binary), "sha256": _sha256(binary)},
            "bin/opencode.exe": {"size": len(opencode), "sha256": _sha256(opencode)},
        },
    }
    with tarfile.open(path, "w:gz") as tar:
        for name, data in (
            (f"{ROOT}/VERSION", b"9.9.9"),
            (f"{ROOT}/MANIFEST.json", json.dumps(manifest).encode()),
            (f"{ROOT}/bin/agent-mesh-edge.exe", binary),
            (f"{ROOT}/bin/opencode.exe", opencode),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return manifest


def _install_layout(tmp_path: Path) -> Path:
    install = tmp_path / "install"
    (install / "bin").mkdir(parents=True)
    (install / "etc").mkdir(parents=True)
    (install / "bin" / "agent-mesh-edge.bin.exe").write_bytes(b"OLD-BIN")
    (install / "bin" / "agent-mesh-edge.cmd").write_text("@echo off\r\n")
    (install / "etc" / "agent_version").write_text("1.0.0")
    return install


class _FakeClient:
    def __init__(self, package: Path) -> None:
        self.base_url = "http://orchestrator:8000"
        self.package = package
        self.downloaded: list[tuple[str, str]] = []

    async def download_to(self, url: str, dest: str) -> None:
        self.downloaded.append((url, dest))
        Path(dest).write_bytes(self.package.read_bytes())


class _FakeAgent(UpgradeMixin):
    def __init__(self, install: Path, client: _FakeClient) -> None:
        self.install_dir = str(install)
        self.client = client
        self._stopped = False
        self._running: dict = {}
        self._upgrade_failures = 0
        self._upgrade_exhausted = False
        self._last_upgrade_attempt = 0.0

    def _request_stop(self) -> None:
        self._stopped = True


def test_windows_upgrade_stages_binary_and_markers(tmp_path):
    install = _install_layout(tmp_path)
    pkg = tmp_path / "pkg.tar.gz"
    _make_package(pkg, b"NEW-BIN", b"OPENCODE")
    agent = _FakeAgent(install, _FakeClient(pkg))

    ok = asyncio.run(agent._perform_upgrade_windows({"version": "9.9.9"}))

    assert ok is True
    assert agent._stopped is True
    assert (install / "bin" / "agent-mesh-edge.bin.exe.new").read_bytes() == b"NEW-BIN"
    # opencode was missing locally -> staged for replacement.
    assert (install / "bin" / "opencode.exe.new").read_bytes() == b"OPENCODE"
    assert (install / "etc" / "agent_version").read_text() == "9.9.9"
    assert (install / "etc" / "agent_version.bak").read_text() == "1.0.0"
    assert (install / "etc" / "upgrading").exists()
    assert not (install / "etc" / "upgrade-started").exists()
    # Staging dir is always cleaned up.
    assert not (install / ".upgrade").exists()
    # The url points at the orchestrator's bootstrap endpoint.
    assert agent.client.downloaded[0][0].endswith("/api/bootstrap/agent-mesh-agent-win32-x64.tar.gz")


def test_windows_upgrade_skips_unchanged_opencode(tmp_path):
    install = _install_layout(tmp_path)
    opencode = b"OPENCODE-STABLE"
    (install / "bin" / "opencode.exe").write_bytes(opencode)
    pkg = tmp_path / "pkg.tar.gz"
    _make_package(pkg, b"NEW-BIN", opencode)
    agent = _FakeAgent(install, _FakeClient(pkg))

    assert asyncio.run(agent._perform_upgrade_windows({"version": "9.9.9"})) is True
    assert not (install / "bin" / "opencode.exe.new").exists()


def test_windows_upgrade_requires_new_layout(tmp_path):
    install = tmp_path / "legacy"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "agent-mesh-edge.exe").write_bytes(b"OLD")
    pkg = tmp_path / "pkg.tar.gz"
    _make_package(pkg, b"NEW-BIN", b"OPENCODE")
    agent = _FakeAgent(install, _FakeClient(pkg))

    # Legacy (pre-indirection) layout: skip cleanly instead of looping.
    assert asyncio.run(agent._perform_upgrade_windows({"version": "9.9.9"})) is False
    assert agent._stopped is False


def test_confirm_healthy_removes_windows_rollback_backup(tmp_path):
    install = _install_layout(tmp_path)
    (install / "bin" / "agent-mesh-edge.bin.exe.old").write_bytes(b"OLD")
    (install / "bin" / "agent-mesh-edge.bin.exe.new").write_bytes(b"NEW")
    (install / "etc" / "upgrading").write_text("")
    (install / "etc" / "upgrade-started").write_text("")

    class _A:  # only the attribute _confirm_upgrade_healthy needs
        install_dir = str(install)

    UpgradeMixin._confirm_upgrade_healthy(_A())

    assert not (install / "bin" / "agent-mesh-edge.bin.exe.old").exists()
    assert not (install / "bin" / "agent-mesh-edge.bin.exe.new").exists()
    assert not (install / "etc" / "upgrading").exists()
    assert not (install / "etc" / "upgrade-started").exists()


def test_cleanup_removes_windows_staging_files(tmp_path):
    install = _install_layout(tmp_path)
    (install / ".upgrade").mkdir()
    (install / ".upgrade" / "x").write_text("x")
    (install / "bin" / "opencode.exe.new").write_bytes(b"NEW")

    cleanup_upgrade_tmp(str(install))

    assert not (install / ".upgrade").exists()
    assert not (install / "bin" / "opencode.exe.new").exists()


def test_launcher_has_swap_and_rollback_contract():
    launcher = (
        Path(__file__).resolve().parent.parent / "bootstrap" / "agent-mesh-edge.cmd"
    ).read_text(encoding="utf-8")
    # Swap staged files, honour the markers, roll back, and delay without timeout.
    assert "agent-mesh-edge.bin.exe.new" in launcher
    assert "agent-mesh-edge.bin.exe.old" in launcher
    assert "etc\\upgrading" in launcher
    assert "etc\\upgrade-started" in launcher
    assert "move /y" in launcher
    assert "ping -n 6 127.0.0.1" in launcher
    assert "timeout /t" not in launcher
