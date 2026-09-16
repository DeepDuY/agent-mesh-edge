from __future__ import annotations

import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EdgeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_id: str = Field(default_factory=lambda: os.uname().nodename)
    orchestrator_url: str = "http://127.0.0.1:8000"
    # Empty means "not configured"; edge/agent.py treats "" as the sentinel.
    token: str = ""
    heartbeat_s: float = 3.0
    runtime: str = "opencode"
    workdir: str = "."
    install_dir: str = "/opt/agent-mesh-agent"
    llm_api_key: str = ""
    llm_base_url: str = ""
    # No hardcoded model default: must be configured (or passed per task).
    llm_model: str = ""
    # Comma/newline separated available model ids (comma-joined when persisted
    # to edge.env as EDGE_LLM_MODELS).
    llm_models: str = ""
    # Node-level role/context injected at the top of every llm task prompt.
    system_prompt: str = ""
