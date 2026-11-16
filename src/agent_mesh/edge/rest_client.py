from __future__ import annotations

import logging
import ssl
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def tls_verify(fingerprint: str) -> ssl.SSLContext:
    """SSL context that pins the orchestrator's certificate by SHA-256.

    The orchestrator uses a self-signed certificate, so the probe cannot rely on
    a public CA. The context disables CA and hostname checks; the exact
    fingerprint announced over the first plaintext poll is verified after the
    handshake (see ``_assert_fingerprint``), which also blocks a later downgrade.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _assert_fingerprint(response: httpx.Response, expected: str) -> None:
    if not expected:
        return
    stream = response.extensions.get("network_stream")
    sslobj = getattr(stream, "get_extra_info", lambda _k: None)("ssl_object")
    if sslobj is None:
        raise ssl.SSLError("TLS connection has no certificate to verify")
    import hashlib

    digest = hashlib.sha256(sslobj.getpeercert(binary_form=True)).hexdigest()
    if digest != expected.replace(":", "").strip().lower():
        raise ssl.SSLError(
            f"orchestrator certificate fingerprint mismatch: {digest} != {expected}"
        )


class EdgeRestClient:
    """Edge-agent REST client for talking to the orchestrator.

    This deliberately avoids MCP; edge agents use simple HTTPS + Bearer auth.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        artifact_timeout_s: float = 300.0,
        tls_fingerprint: str = "",
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.tls_fingerprint = tls_fingerprint
        self._artifact_timeout = httpx.Timeout(artifact_timeout_s)
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"},
            timeout=httpx.Timeout(30.0),
            verify=tls_verify(tls_fingerprint) if base_url.startswith("https://") else True,
        )

    def set_artifact_timeout(self, seconds: float) -> None:
        """Set the timeout used for artifact uploads (from config-sync)."""
        try:
            value = float(seconds)
        except (TypeError, ValueError):
            return
        if value > 0:
            self._artifact_timeout = httpx.Timeout(value)

    def set_token(self, token: str) -> None:
        """Switch the bearer token used for all requests (e.g. after the
        orchestrator issues this agent its own independent token)."""
        self.token = token
        self._client.headers["Authorization"] = f"Bearer {token}"

    async def enable_tls(self, fingerprint: str) -> None:
        """Switch this client from plaintext HTTP to HTTPS on the same port.

        Called once the orchestrator announces TLS. The base URL keeps its host
        and port; only the scheme changes, and the certificate is pinned to the
        announced fingerprint. Idempotent if already on HTTPS.
        """
        if self.base_url.startswith("https://") and self.tls_fingerprint == fingerprint:
            return
        self.base_url = "https://" + self.base_url.split("://", 1)[-1]
        self.tls_fingerprint = fingerprint
        old = self._client
        self._client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=httpx.Timeout(30.0),
            verify=tls_verify(fingerprint),
        )
        await old.aclose()

    async def _post(self, path: str, json: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/api{path}"
        resp = await self._client.post(url, json=json)
        _assert_fingerprint(resp, self.tls_fingerprint)
        resp.raise_for_status()
        return resp.json()

    async def _upload(self, task_id: str, files: list[tuple[str, bytes]]) -> dict[str, Any]:
        url = f"{self.base_url}/api/artifacts/{task_id}"
        multipart = [("files", (name, content, "application/octet-stream")) for name, content in files]
        resp = await self._client.post(
            url,
            files=multipart,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=self._artifact_timeout,
        )
        _assert_fingerprint(resp, self.tls_fingerprint)
        resp.raise_for_status()
        return resp.json()

    async def poll_for_task(
        self,
        agent_id: str,
        device_id: str,
        runtime: str,
        hostname: str,
        os: str,
        version: str,
        distro: str | None = None,
        arch: str | None = None,
        cpu_percent: float | None = None,
        mem_percent: float | None = None,
        mem_used_mb: float | None = None,
        mem_total_mb: float | None = None,
        running_tasks: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "device_id": device_id,
            "runtime": runtime,
            "hostname": hostname,
            "os": os,
            "distro": distro,
            "arch": arch,
            "version": version,
        }
        if cpu_percent is not None:
            body["cpu_percent"] = cpu_percent
        if mem_percent is not None:
            body["mem_percent"] = mem_percent
        if mem_used_mb is not None:
            body["mem_used_mb"] = mem_used_mb
        if mem_total_mb is not None:
            body["mem_total_mb"] = mem_total_mb
        if running_tasks:
            body["running_tasks"] = list(running_tasks)
        return await self._post("/edge/poll_for_task", body)

    async def post_task_log(self, task_id: str, entries: list[dict[str, str]]) -> dict[str, Any]:
        return await self._post("/edge/task_log", {"task_id": task_id, "entries": entries})

    async def submit_result(self, **kwargs: Any) -> dict[str, Any]:
        return await self._post("/edge/submit_result", kwargs)

    async def get_task_status(self, task_id: str) -> dict[str, Any]:
        return await self._post(
            "/edge/get_task_status", {"task_id": task_id}
        )

    async def mark_started(self, task_id: str) -> dict[str, Any]:
        return await self._post(
            "/edge/mark_started", {"task_id": task_id}
        )

    async def upload_artifacts(
        self, task_id: str, files: list[tuple[str, bytes]]
    ) -> dict[str, Any]:
        return await self._upload(task_id, files)

    async def download_to(self, url: str, dest_path: str) -> None:
        """Stream a file (e.g. an upgrade package) to disk with a long timeout."""
        import os

        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        with open(dest_path, "wb") as f:
            async with self._client.stream(
                "GET", url, timeout=httpx.Timeout(600.0)
            ) as resp:
                _assert_fingerprint(resp, self.tls_fingerprint)
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)

    async def download_file(self, url_path: str, dest_path: str) -> str:
        """Stream a library file (task attachment) to disk; returns its md5.

        ``url_path`` is the relative ``download_url`` from the task's
        ``attachments`` (e.g. ``/api/files/f-xxxx``). The caller verifies the
        returned md5 against the task's recorded value.
        """
        import hashlib
        import os

        md5 = hashlib.md5()
        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        url = f"{self.base_url}{url_path}"
        with open(dest_path, "wb") as f:
            async with self._client.stream(
                "GET", url, timeout=httpx.Timeout(120.0)
            ) as resp:
                _assert_fingerprint(resp, self.tls_fingerprint)
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    md5.update(chunk)
                    f.write(chunk)
        return md5.hexdigest()

    async def close(self) -> None:
        await self._client.aclose()
