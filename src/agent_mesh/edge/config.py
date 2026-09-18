from __future__ import annotations

import os
import platform

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def default_install_dir() -> str:
    """Per-platform default install root (POSIX vs Windows)."""
    if os.name == "nt":
        return r"C:\ProgramData\agent-mesh-agent"
    return "/opt/agent-mesh-agent"


class EdgeConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EDGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    agent_id: str = Field(default_factory=platform.node)
    orchestrator_url: str = "http://127.0.0.1:8000"
    # Empty means "not configured"; edge/agent.py treats "" as the sentinel.
    token: str = ""
    heartbeat_s: float = 3.0
    runtime: str = "opencode"
    # Command-mode shell. Empty = platform default (bash/sh on POSIX,
    # cmd.exe on Windows; pass "powershell"/"pwsh" for PowerShell).
    shell: str = ""
    workdir: str = "."
    install_dir: str = Field(default_factory=default_install_dir)
    llm_api_key: str = ""
    llm_base_url: str = ""
    # No hardcoded model default: must be configured (or passed per task).
    llm_model: str = ""
    # Comma/newline separated available model ids (comma-joined when persisted
    # to edge.env as EDGE_LLM_MODELS).
    llm_models: str = ""
    # Node-level role/context injected at the top of every llm task prompt.
    system_prompt: str = ""
    # HTTP timeout for artifact uploads (seconds). Larger artifacts need more
    # time; overridden at runtime by the orchestrator's config-sync.
    artifact_timeout_s: float = 300.0

    @field_validator("artifact_timeout_s", mode="before")
    @classmethod
    def _default_timeout(cls, value: object) -> object:
        # edge.env may carry an empty value (older config-writer); fall back.
        if value is None or value == "":
            return 300.0
        return value
