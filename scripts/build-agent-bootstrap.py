#!/usr/bin/env python3
"""Build a self-contained agent bootstrap package for Linux/macOS.

Run on a build machine; `opencode` is bundled so target machines need no
internet / no manual install. If opencode is not installed locally it is
downloaded automatically from the official releases
(github.com/sst/opencode/releases/latest). Produces:

    <output-dir>/agent-mesh-agent-{linux|darwin}-{x64|arm64}.tar.gz
    <output-dir>/install.sh
    <output-dir>/VERSION

The package contains:
    bin/agent-mesh-edge   PyInstaller-built edge agent binary
    bin/opencode          opencode CLI binary (REQUIRED for llm tasks)
    install.sh            one-command installer
    VERSION

Usage:
    python scripts/build-agent-bootstrap.py
    python scripts/build-agent-bootstrap.py --opencode /path/to/opencode
    python scripts/build-agent-bootstrap.py --output-dir /opt/agent-mesh/data/bootstrap
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DEFAULT_BOOTSTRAP_DIR = PROJECT_ROOT / "dist"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"

# Official opencode release assets (github.com/sst/opencode). The "latest" URL
# redirects to the newest release, so no API token / rate-limit handling needed.
_OPENCODE_ASSETS = {
    ("linux", "x64"): "opencode-linux-x64.tar.gz",
    ("linux", "arm64"): "opencode-linux-arm64.tar.gz",
    ("darwin", "x64"): "opencode-darwin-x64.zip",
    ("darwin", "arm64"): "opencode-darwin-arm64.zip",
    ("win32", "x64"): "opencode-windows-x64.zip",
    ("win32", "arm64"): "opencode-windows-arm64.zip",
}
_OPENCODE_DOWNLOAD_BASE = os.environ.get(
    "OPENCODE_DOWNLOAD_BASE",
    "https://github.com/sst/opencode/releases/latest/download",
)

# Highest GLIBC symbol version a Linux build may require. PyInstaller freezes
# the build host's libpython, so its glibc floor becomes the target machines'
# floor; building on a new distro (e.g. ubuntu-24.04 -> GLIBC_2.38) silently
# breaks older nodes. Keep linux builds on a glibc 2.17 (CentOS 7 /
# manylinux2014) base unless this ceiling is explicitly raised.
MAX_GLIBC = os.environ.get("AGENT_MESH_MAX_GLIBC", "2.17")


def _run(cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_GLIBC_RE = re.compile(r"GLIBC_(\d+)\.(\d+)")


def _parse_glibc(spec: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\.(\d+)\s*", spec or "")
    return (int(match.group(1)), int(match.group(2))) if match else None


def _max_glibc_version(path: Path | None) -> tuple[int, int] | None:
    """Highest GLIBC_x.y symbol version referenced by an ELF file, if any.

    Uses whichever of ``objdump``/``readelf`` is available; returns ``None``
    when the tool is missing (or the file is not an ELF) rather than failing
    the build on a best-effort diagnostic.
    """
    if path is None or not path.exists():
        return None
    objdump = shutil.which("objdump")
    if objdump:
        cmd = [objdump, "-T", str(path)]
    else:
        readelf = shutil.which("readelf")
        if not readelf:
            return None
        cmd = [readelf, "--version-info", str(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError:
        return None
    versions = [(int(a), int(b)) for a, b in _GLIBC_RE.findall(proc.stdout)]
    return max(versions) if versions else None


def _archive_glibc_floor(exe: Path) -> tuple[tuple[int, int] | None, list[tuple[str, tuple[int, int] | None]]]:
    """Scan every shared library PyInstaller bundled into ``exe``.

    Checking only libpython is not enough: PyInstaller also collects system
    libraries (notably ``libgcc_s.so.1``), and a modern build host's copies
    drag in a much newer glibc floor. Returns the worst version plus per-file
    details for diagnostics.
    """
    from PyInstaller.archive.readers import CArchiveReader

    reader = CArchiveReader(str(exe))
    worst: tuple[int, int] | None = None
    details: list[tuple[str, tuple[int, int] | None]] = []
    with tempfile.TemporaryDirectory(prefix="glibc-scan-") as td:
        tmpdir = Path(td)
        for name, entry in reader.toc.items():
            if entry[-1] != "b":
                continue
            if not (name.endswith(".so") or ".so." in name):
                continue
            try:
                data = reader.extract(name)
            except Exception:  # pragma: no cover - malformed archive
                continue
            tmp = tmpdir / Path(name).name
            tmp.write_bytes(data)
            version = _max_glibc_version(tmp)
            details.append((name, version))
            if version and (worst is None or version > worst):
                worst = version
    return worst, details


def _linux_glibc_floor(exe: Path) -> tuple[int, int] | None:
    """Worst-case GLIBC requirement across the frozen binary's bundled libs."""
    worst, details = _archive_glibc_floor(exe)
    if not details:
        print("WARNING: no bundled shared libraries found; glibc floor check skipped")
        return None
    for name, version in details:
        if version:
            print(f"    {name}: GLIBC_{version[0]}.{version[1]}")
    return worst


def _find_opencode(explicit: str | None = None, os_name: str = "linux") -> Path | None:
    """Locate a locally installed opencode CLI, in preference order.

    When building for ``win32``, non-``.exe`` candidates are skipped so the
    host's POSIX binary is never bundled under the name ``opencode.exe``
    (PyInstaller cannot cross-compile anyway, so the intended binary must be a
    Windows one).
    """
    candidates: list[Path] = []
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"ERROR: --opencode path does not exist: {explicit}")
        candidates.append(p)
    env_path = os.environ.get("OPENCODE_BIN")
    if env_path:
        candidates.append(Path(env_path))
    which = shutil.which("opencode")
    if which:
        candidates.append(Path(which))
    candidates += [
        Path.home() / ".opencode" / "bin" / "opencode",
        Path.home() / ".opencode" / "bin" / "opencode.exe",
        Path("/opt/agent-mesh-agent/bin/opencode"),
    ]
    want_exe = os_name == "win32"
    for c in candidates:
        if not c or not c.exists():
            continue
        if want_exe and not explicit and c.suffix.lower() != ".exe":
            continue
        return c
    return None


def _opencode_download_url(os_name: str, arch: str) -> str | None:
    asset = _OPENCODE_ASSETS.get((os_name, arch))
    if not asset:
        return None
    return f"{_OPENCODE_DOWNLOAD_BASE.rstrip('/')}/{asset}"


def _download_opencode(os_name: str, arch: str) -> Path | None:
    """Download + extract the official opencode binary for the target platform."""
    url = _opencode_download_url(os_name, arch)
    if not url:
        print(f"WARNING: no official opencode asset known for {os_name}/{arch}")
        return None
    cache = BUILD_DIR / "opencode-cache"
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / Path(url).name
    print(f"==> downloading opencode: {url}")
    try:
        with urllib.request.urlopen(url, timeout=300) as resp, open(archive, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        print(f"WARNING: failed to download opencode: {e}")
        return None

    extract = cache / "extract"
    shutil.rmtree(extract, ignore_errors=True)
    extract.mkdir(parents=True)
    try:
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(extract)
        else:
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(extract, filter="data")
    except Exception as e:
        print(f"WARNING: failed to extract opencode archive: {e}")
        return None

    wanted = "opencode.exe" if os_name == "win32" else "opencode"
    for p in sorted(extract.rglob(wanted)):
        if p.is_file():
            os.chmod(p, 0o755)
            return p
    print("WARNING: opencode binary not found inside the downloaded archive")
    return None



def _detect_target() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        os_name = "darwin"
    elif system == "windows":
        os_name = "win32"
    else:
        os_name = "linux"
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        arch = "x64"
    return os_name, arch


def _bin_names(os_name: str) -> tuple[str, str]:
    """Package member names for the edge binary and opencode on ``os_name``."""
    suffix = ".exe" if os_name == "win32" else ""
    return f"agent-mesh-edge{suffix}", f"opencode{suffix}"


def _build_binary(os_name: str) -> Path:
    """Build the PyInstaller binary for the edge agent.

    Uses the currently-running interpreter's PyInstaller (``sys.executable
    -m PyInstaller``) instead of ``uv run pyinstaller``: the latter re-resolves
    the environment and can attempt a source build of ``asyncpg`` (no prebuilt
    wheel) that fails under conda-injected compiler flags. Requires pyinstaller
    installed in the running environment (dev extra: ``uv pip install pyinstaller``).

    PyInstaller cannot cross-compile: the Windows package must be built on a
    Windows host (where this script appends ``.exe`` automatically).
    """
    spec = PROJECT_ROOT / "scripts" / "agent-mesh-edge.spec"
    _run([
        sys.executable, "-m", "PyInstaller",
        str(spec),
        "--distpath", str(DIST_DIR),
        "--workpath", str(BUILD_DIR),
        "--noconfirm",
    ])

    edge_name, _ = _bin_names(os_name)
    exe = DIST_DIR / edge_name
    if not exe.exists():
        raise RuntimeError(f"PyInstaller did not produce dist/{edge_name}")
    return exe


def build(
    os_name: str | None = None,
    arch: str | None = None,
    opencode: str | None = None,
    output_dir: str | None = None,
    allow_missing_opencode: bool = False,
    download_opencode: bool = True,
) -> Path:
    detected_os, detected_arch = _detect_target()
    os_name = os_name or detected_os
    arch = arch or detected_arch
    bootstrap_dir = Path(output_dir).expanduser().resolve() if output_dir else DEFAULT_BOOTSTRAP_DIR
    bootstrap_dir.mkdir(parents=True, exist_ok=True)

    opencode_path = _find_opencode(opencode, os_name)
    if opencode_path is None and download_opencode:
        # Not installed locally -> fetch the official binary for the target platform.
        opencode_path = _download_opencode(os_name, arch)
    if opencode_path is None and not allow_missing_opencode:
        raise SystemExit(
            "ERROR: opencode binary not found and download failed; the probe must "
            "bundle it so target machines can run llm tasks.\n"
            "  Provide one of:\n"
            "    --opencode /path/to/opencode\n"
            "    OPENCODE_BIN=/path/to/opencode environment variable\n"
            "    install opencode so `which opencode` / ~/.opencode/bin/opencode works\n"
            "    ensure this machine can reach github.com/sst/opencode releases\n"
            "  Or pass --allow-missing-opencode to build a probe WITHOUT opencode "
            "(llm tasks will not work on installed nodes)."
        )
    if opencode_path is None:
        print("WARNING: building WITHOUT opencode (--allow-missing-opencode); llm tasks will fail")

    exe = _build_binary(os_name)
    edge_name, oc_name = _bin_names(os_name)

    # PyInstaller freezes the build host's libpython, so its glibc symbols
    # become the floor for every target. Refuse to publish a Linux package that
    # requires a newer glibc than the configured ceiling.
    glibc_min: tuple[int, int] | None = None
    if os_name == "linux":
        glibc_min = _linux_glibc_floor(exe)
        max_allowed = _parse_glibc(MAX_GLIBC)
        if glibc_min and max_allowed and glibc_min > max_allowed:
            raise SystemExit(
                f"ERROR: this Linux build requires GLIBC_{glibc_min[0]}.{glibc_min[1]} "
                f"but the ceiling is GLIBC_{max_allowed[0]}.{max_allowed[1]}.\n"
                "  Build on an older-glibc host (e.g. CentOS 7 / manylinux2014), or set\n"
                "  AGENT_MESH_MAX_GLIBC to acknowledge raising the compatibility floor."
            )
        if glibc_min:
            print(f"==> linux glibc floor: {glibc_min[0]}.{glibc_min[1]} (ceiling {MAX_GLIBC})")

    with tempfile.TemporaryDirectory(prefix="agent-bootstrap-") as td:
        work = Path(td)
        pkg = work / f"agent-mesh-agent-{os_name}-{arch}"
        pkg.mkdir()
        bin_dir = pkg / "bin"
        bin_dir.mkdir()

        # Copy PyInstaller binary (agent-mesh-edge / agent-mesh-edge.exe).
        shutil.copy2(exe, bin_dir / edge_name)
        os.chmod(bin_dir / edge_name, 0o755)

        # Copy opencode binary (REQUIRED unless explicitly skipped).
        if opencode_path is not None:
            shutil.copy2(opencode_path, bin_dir / oc_name)
            os.chmod(bin_dir / oc_name, 0o755)
            print(f"bundled opencode from {opencode_path}")

        # Copy the platform installer from bootstrap/ (canonical version).
        installer = "install.ps1" if os_name == "win32" else "install.sh"
        install_src = PROJECT_ROOT / "bootstrap" / installer
        if install_src.exists():
            shutil.copy2(install_src, pkg / installer)
            os.chmod(pkg / installer, 0o755)
        else:
            raise SystemExit(
                f"ERROR: {install_src} not found; cannot build a package without the installer"
            )

        # Windows also ships the keepalive launcher next to the installer
        # (install.ps1 copies it into <install>\bin\ for the Scheduled Task).
        if os_name == "win32":
            launcher_src = PROJECT_ROOT / "bootstrap" / "agent-mesh-edge.cmd"
            if not launcher_src.exists():
                raise SystemExit(
                    f"ERROR: {launcher_src} not found; cannot build a Windows package"
                )
            shutil.copy2(launcher_src, pkg / "agent-mesh-edge.cmd")

        # Write the version manifest into the package and the output dir.
        # The orchestrator reads <data_dir>/bootstrap/VERSION to decide whether an
        # edge agent's reported version is stale (see orchestrator/api/edge.py).
        from agent_mesh.shared.constants import VERSION

        (pkg / "VERSION").write_text(VERSION, encoding="utf-8")
        (bootstrap_dir / "VERSION").write_text(VERSION, encoding="utf-8")
        print(f"wrote VERSION={VERSION} to {bootstrap_dir / 'VERSION'}")

        # MANIFEST.json: lets the edge upgrade path skip re-extracting unchanged
        # large members (notably the ~180MB opencode binary) instead of unpacking
        # the whole archive on every upgrade.
        manifest_files: dict[str, dict[str, object]] = {}
        for rel in (f"bin/{edge_name}", f"bin/{oc_name}"):
            fp = pkg / rel
            if fp.exists():
                manifest_files[rel] = {
                    "size": fp.stat().st_size,
                    "sha256": _sha256(fp),
                }
        manifest = {"version": VERSION, "files": manifest_files}
        if glibc_min:
            manifest["glibc_min"] = f"{glibc_min[0]}.{glibc_min[1]}"
        (pkg / "MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("wrote MANIFEST.json")

        # Build tar.gz with Python's tarfile so packaging works on every host
        # (Windows has no reliable external `tar`).
        tar_name = f"agent-mesh-agent-{os_name}-{arch}.tar.gz"
        tar_path = bootstrap_dir / tar_name
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(pkg, arcname=pkg.name)
        print(f"created {tar_path}")

        # Also keep a copy of the installer at bootstrap root for direct use.
        shutil.copy2(pkg / installer, bootstrap_dir / installer)
        return tar_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the agent-mesh edge bootstrap package")
    parser.add_argument("--os", dest="os_name", default=None, help="target OS (linux/darwin/win32)")
    parser.add_argument("--arch", default=None, help="target arch (x64/arm64)")
    parser.add_argument("--opencode", default=None, help="path to the opencode binary to bundle")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="where to write the package (default: <repo>/dist)",
    )
    parser.add_argument(
        "--allow-missing-opencode",
        action="store_true",
        help="build even if opencode is missing (llm tasks will not work)",
    )
    parser.add_argument(
        "--no-download-opencode",
        action="store_true",
        help="do not auto-download opencode from GitHub when it is not installed locally",
    )
    args = parser.parse_args()
    build(
        os_name=args.os_name,
        arch=args.arch,
        opencode=args.opencode,
        output_dir=args.output_dir,
        allow_missing_opencode=args.allow_missing_opencode,
        download_opencode=not args.no_download_opencode,
    )


if __name__ == "__main__":
    main()
