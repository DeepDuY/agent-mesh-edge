"""Artifact transfer timeout + failure reporting (server-configurable)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from agent_mesh.edge.config import EdgeConfig
from agent_mesh.edge.config_writer import apply_llm_config
from agent_mesh.edge.rest_client import EdgeRestClient
from agent_mesh.edge.runner import _artifact_error_reason


def test_edge_config_timeout_default(monkeypatch):
    monkeypatch.delenv("EDGE_ARTIFACT_TIMEOUT_S", raising=False)
    assert EdgeConfig().artifact_timeout_s == 300.0


def test_edge_config_timeout_from_env(monkeypatch):
    monkeypatch.setenv("EDGE_ARTIFACT_TIMEOUT_S", "600")
    assert EdgeConfig().artifact_timeout_s == 600.0


def test_edge_config_timeout_empty_falls_back(monkeypatch):
    monkeypatch.setenv("EDGE_ARTIFACT_TIMEOUT_S", "")
    assert EdgeConfig().artifact_timeout_s == 300.0


def test_rest_client_artifact_timeout_setter():
    client = EdgeRestClient("http://example.invalid", "tok", artifact_timeout_s=120)
    assert client._artifact_timeout.read == 120.0
    client.set_artifact_timeout(900)
    assert client._artifact_timeout.read == 900.0
    # Invalid values are ignored (keeps the previous timeout).
    client.set_artifact_timeout("nope")
    assert client._artifact_timeout.read == 900.0


def test_apply_llm_config_persists_timeout(tmp_path: Path):
    install = tmp_path / "agent"
    (install / "etc").mkdir(parents=True)
    (install / "etc" / "edge.env").write_text("EDGE_AGENT_ID=node\n", encoding="utf-8")

    apply_llm_config(
        str(install),
        {"artifact_timeout_s": "600", "llm_model": "anthropic/x"},
        "1",
    )
    env = (install / "etc" / "edge.env").read_text(encoding="utf-8")
    assert "EDGE_ARTIFACT_TIMEOUT_S=600" in env


def test_apply_llm_config_timeout_missing_writes_empty(tmp_path: Path):
    install = tmp_path / "agent2"
    (install / "etc").mkdir(parents=True)
    (install / "etc" / "edge.env").write_text("EDGE_AGENT_ID=node\n", encoding="utf-8")

    apply_llm_config(str(install), {}, "1")
    env = (install / "etc" / "edge.env").read_text(encoding="utf-8")
    # Empty is tolerated by EdgeConfig's validator (falls back to 300).
    assert "EDGE_ARTIFACT_TIMEOUT_S=" in env


def test_artifact_error_reason_http_status():
    request = httpx.Request("POST", "http://example.invalid/api/artifacts/t-1")
    response = httpx.Response(
        413, json={"detail": "artifact big.bin exceeds max size 100MB"}, request=request
    )
    exc = httpx.HTTPStatusError("too large", request=request, response=response)
    reason = _artifact_error_reason(exc)
    assert "413" in reason
    assert "exceeds max size" in reason


def test_artifact_error_reason_generic():
    assert _artifact_error_reason(TimeoutError("timed out")) == "timed out"
