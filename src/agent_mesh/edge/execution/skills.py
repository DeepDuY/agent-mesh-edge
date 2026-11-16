"""Fetch task-requested skills from the orchestrator and inject them.

The orchestrator's skill library stores skills as zips that contain a
``SKILL.md``. When a task carries ``constraints.skills`` the edge downloads each
skill with its own token, extracts it under
``<workdir>/.opencode/skills/<name>/`` (so opencode also discovers it natively)
and returns a prompt block embedding the ``SKILL.md`` text.

Any skill that is missing, disabled, or unfetchable raises
:class:`SkillUnavailable`; the caller fails the llm task with a clear reason.
"""

from __future__ import annotations

import io
import logging
import os
import tempfile
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class SkillUnavailable(Exception):
    """A requested skill is missing, disabled, or could not be fetched."""


def _skill_md_member(zf: zipfile.ZipFile) -> str | None:
    candidates = [n for n in zf.namelist() if n.endswith("SKILL.md")]
    if not candidates:
        return None
    candidates.sort(key=len)
    return candidates[0]


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract into ``dest``, rejecting entries that escape it (zip-slip)."""
    base = dest.resolve()
    for info in zf.infolist():
        target = (base / info.filename).resolve()
        if target != base and not str(target).startswith(str(base) + os.sep):
            raise SkillUnavailable(f"unsafe path in skill archive: {info.filename}")
    zf.extractall(base)


def _cache_dir(cache_dir: str | Path | None) -> Path:
    if cache_dir:
        return Path(cache_dir).expanduser()
    return Path(tempfile.gettempdir()) / "agent-mesh-skills"


async def _fetch_skill_meta(client: httpx.AsyncClient, name: str) -> dict:
    resp = await client.get(f"/api/skills/{name}")
    if resp.status_code == 404:
        raise SkillUnavailable(f"skill not found: {name}")
    resp.raise_for_status()
    return resp.json().get("skill", {})


async def _skill_zip_bytes(
    client: httpx.AsyncClient, name: str, version: int, cache: Path
) -> bytes:
    cached = cache / name / f"v{version}.zip"
    if cached.is_file():
        return cached.read_bytes()
    resp = await client.get(f"/api/skills/{name}/download")
    if resp.status_code == 404:
        raise SkillUnavailable(f"skill has no archive: {name}")
    resp.raise_for_status()
    data = resp.content
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    except OSError as e:  # non-fatal: just skip caching
        logger.warning("could not cache skill %s: %s", name, e)
    return data


async def load_skills_block(
    names: list[str] | None,
    workdir: str | Path,
    token: str,
    orchestrator_base_url: str,
    cache_dir: str | Path | None = None,
    client: httpx.AsyncClient | None = None,
    tls_fingerprint: str = "",
) -> str:
    """Return a prompt block embedding the requested skills' SKILL.md text.

    ``client`` is for tests; production creates an authenticated client.
    """
    wanted = [n.strip() for n in (names or []) if n and n.strip()]
    if not wanted:
        return ""
    base = (orchestrator_base_url or "").rstrip("/")
    if client is None and not base:
        raise SkillUnavailable("orchestrator URL is not configured; cannot fetch skills")

    own_client = client is None
    if own_client:
        from agent_mesh.edge.rest_client import tls_verify

        client = httpx.AsyncClient(
            base_url=base,
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(60.0),
            verify=tls_verify(tls_fingerprint) if base.startswith("https://") else True,
        )
    cache = _cache_dir(cache_dir)
    sections: list[tuple[str, str]] = []
    try:
        for name in wanted:
            try:
                meta = await _fetch_skill_meta(client, name)
            except httpx.HTTPStatusError as e:
                raise SkillUnavailable(
                    f"skill metadata fetch failed: {name} (HTTP {e.response.status_code})"
                ) from e
            except httpx.HTTPError as e:
                raise SkillUnavailable(f"skill metadata fetch failed: {name} ({e})") from e
            if not meta.get("enabled", True):
                raise SkillUnavailable(f"skill is disabled: {name}")
            version = int(meta.get("version") or 0)
            try:
                data = await _skill_zip_bytes(client, name, version, cache)
            except httpx.HTTPStatusError as e:
                raise SkillUnavailable(
                    f"skill fetch failed: {name} (HTTP {e.response.status_code})"
                ) from e
            except httpx.HTTPError as e:
                raise SkillUnavailable(f"skill fetch failed: {name} ({e})") from e
            try:
                zf = zipfile.ZipFile(io.BytesIO(data))
            except zipfile.BadZipFile as e:
                raise SkillUnavailable(f"skill archive is invalid: {name}") from e
            with zf:
                member = _skill_md_member(zf)
                if member is None:
                    raise SkillUnavailable(f"skill has no SKILL.md: {name}")
                content = zf.read(member).decode("utf-8", errors="replace")
                try:
                    _safe_extract(zf, Path(workdir) / ".opencode" / "skills" / name)
                except OSError as e:
                    logger.warning("could not extract skill %s: %s", name, e)
            sections.append((name, content))
    finally:
        if own_client:
            await client.aclose()

    if not sections:
        return ""
    parts = [
        "## 指定技能（必须遵守）",
        "调用方为本次任务指定了以下技能，请严格按其说明执行：",
        "",
    ]
    for name, content in sections:
        parts.append(f"### 技能：{name}")
        parts.append("")
        parts.append(content.strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n\n"
