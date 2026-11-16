"""TLS upgrade: a plaintext probe pins the announced certificate and switches."""

from __future__ import annotations

import asyncio

from agent_mesh.edge.agent import EdgeAgent
from agent_mesh.edge.config_writer import persist_tls


def test_enable_tls_switches_scheme_and_persists(tmp_path):
    agent = EdgeAgent(
        agent_id="node",
        orchestrator_url="http://10.0.0.5:8000",
        token="t",
        runtime="opencode",
        workdir=".",
        llm_api_key="",
        llm_base_url="",
        llm_model="",
        install_dir=str(tmp_path),
    )
    assert agent.client.base_url == "http://10.0.0.5:8000"

    asyncio.run(agent._maybe_enable_tls({"enabled": True, "fingerprint": "ab" * 32}))

    assert agent.client.base_url == "https://10.0.0.5:8000"
    assert agent.executor.orchestrator_base_url == "https://10.0.0.5:8000"
    env = (tmp_path / "etc" / "edge.env").read_text()
    assert "EDGE_ORCHESTRATOR_URL=https://10.0.0.5:8000" in env
    assert "EDGE_TLS=1" in env
    assert f"EDGE_TLS_FINGERPRINT={'ab' * 32}" in env

    # Already on HTTPS: a repeat announcement must not churn the client.
    same = agent.client
    asyncio.run(agent._maybe_enable_tls({"enabled": True, "fingerprint": "ab" * 32}))
    assert agent.client is same


def test_tls_announcement_ignored_without_fingerprint(tmp_path):
    agent = EdgeAgent(
        agent_id="node", orchestrator_url="http://h:8000", token="t",
        runtime="opencode", workdir=".", llm_api_key="", llm_base_url="",
        llm_model="", install_dir=str(tmp_path),
    )
    asyncio.run(agent._maybe_enable_tls({"enabled": True}))
    assert agent.client.base_url == "http://h:8000"
    assert not (tmp_path / "etc" / "edge.env").exists()


def test_persist_tls_updates_existing_env(tmp_path):
    env = tmp_path / "etc"
    env.mkdir(parents=True)
    (env / "edge.env").write_text("EDGE_TOKEN=abc\nEDGE_ORCHESTRATOR_URL=http://h:8000\n")
    persist_tls(str(tmp_path), "https://h:8000", "ff" * 32)
    text = (env / "edge.env").read_text()
    assert "EDGE_TOKEN=abc" in text
    assert text.count("EDGE_ORCHESTRATOR_URL=") == 1
    assert "EDGE_ORCHESTRATOR_URL=https://h:8000" in text
