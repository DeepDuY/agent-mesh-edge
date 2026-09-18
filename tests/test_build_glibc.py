"""Build-time GLIBC floor guard.

PyInstaller freezes the build host's libpython, so building the Linux probe on
a new distro silently raises the minimum glibc of every target machine. The
build script must detect and refuse that.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import sys

import pytest


def _load_build_script():
    repo = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "build_agent_bootstrap", repo / "scripts" / "build-agent-bootstrap.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parse_glibc():
    bab = _load_build_script()
    assert bab._parse_glibc("2.17") == (2, 17)
    assert bab._parse_glibc(" 2.38 ") == (2, 38)
    assert bab._parse_glibc("") is None
    assert bab._parse_glibc("glibc-2.17") is None


def test_default_ceiling_is_centos7():
    bab = _load_build_script()
    assert bab._parse_glibc(bab.MAX_GLIBC) == (2, 17)


def test_max_glibc_version_missing_path():
    bab = _load_build_script()
    assert bab._max_glibc_version(None) is None
    assert bab._max_glibc_version(pathlib.Path("/nonexistent/does-not-exist")) is None


@pytest.mark.skipif(
    shutil.which("objdump") is None and shutil.which("readelf") is None,
    reason="no ELF inspection tool available",
)
def test_max_glibc_version_reads_real_elf():
    bab = _load_build_script()
    version = bab._max_glibc_version(pathlib.Path(sys.executable))
    assert version is not None
    assert version >= (2, 2)
