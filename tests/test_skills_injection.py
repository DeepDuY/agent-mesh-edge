"""Task-requested skills: prompt injection and orchestrator fetch."""

from __future__ import annotations

import asyncio
import io
import zipfile

import httpx
import pytest

from agent_mesh.edge.execution.prompt import _wrap_llm_instruction
from agent_mesh.edge.execution.skills import SkillUnavailable, load_skills_block


def _skill_zip(name: str, content: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", content)
    return buf.getvalue()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://orch")


def test_wrap_uses_generic_skills_section_by_default():
    text = _wrap_llm_instruction("do it")
    assert "技能库（按需使用）" in text


def test_wrap_injects_only_requested_skills():
    block = "## 指定技能（必须遵守）\n\n### 技能：deploy-helper\n\n步骤一\n"
    text = _wrap_llm_instruction("do it", skills_block=block)
    assert "指定技能" in text
    assert "deploy-helper" in text
    assert "步骤一" in text
    assert "技能库（按需使用）" not in text


def test_wrap_injects_system_prompt():
    plain = _wrap_llm_instruction("do it")
    assert "角色与上下文" not in plain

    wrapped = _wrap_llm_instruction("do it", "你是运维专员。")
    assert "## 角色与上下文" in wrapped
    assert "你是运维专员。" in wrapped
    assert "do it" in wrapped


def test_load_skills_block_fetches_and_extracts(tmp_path):
    special = "---\nname: deploy-helper\ndescription: d\n---\n\nSPECIAL-CONTENT\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/skills/deploy-helper":
            return httpx.Response(200, json={"skill": {"version": 3, "enabled": True}})
        if request.url.path == "/api/skills/deploy-helper/download":
            return httpx.Response(200, content=_skill_zip("deploy-helper", special))
        return httpx.Response(404)

    async def _run():
        client = _client(handler)
        try:
            return await load_skills_block(
                ["deploy-helper"], tmp_path, "tok", "", cache_dir=tmp_path / "cache", client=client
            )
        finally:
            await client.aclose()

    block = asyncio.run(_run())
    assert "SPECIAL-CONTENT" in block
    assert "deploy-helper" in block
    assert (tmp_path / ".opencode" / "skills" / "deploy-helper" / "SKILL.md").is_file()


def test_load_skills_block_missing_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async def _run():
        client = _client(handler)
        try:
            await load_skills_block(["nope"], tmp_path, "tok", "", client=client)
        finally:
            await client.aclose()

    with pytest.raises(SkillUnavailable, match="not found"):
        asyncio.run(_run())


def test_load_skills_block_disabled_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"skill": {"version": 1, "enabled": False}})

    async def _run():
        client = _client(handler)
        try:
            await load_skills_block(["off"], tmp_path, "tok", "", client=client)
        finally:
            await client.aclose()

    with pytest.raises(SkillUnavailable, match="disabled"):
        asyncio.run(_run())


def test_no_skills_returns_empty(tmp_path):
    assert asyncio.run(load_skills_block(None, tmp_path, "tok", "")) == ""
    assert asyncio.run(load_skills_block([], tmp_path, "tok", "")) == ""
