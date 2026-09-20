"""Command mode streams both stdout and stderr as raw live log lines."""

from __future__ import annotations

import asyncio

from agent_mesh.edge.execution import command as cmd_mod


class _Constraints:
    output_limit = 100000
    timeout_s = 10


class _Task:
    task_id = "t1"
    instruction = "printf 'out-line\\n'; printf 'err-line\\n' 1>&2"
    constraints = _Constraints()


def test_command_reports_stdout_and_stderr(tmp_path):
    entries: list[dict] = []

    async def log_callback(batch):
        entries.extend(batch)

    outcome = asyncio.run(
        cmd_mod.run_command(
            _Task(),
            tmp_path,
            log_callback=log_callback,
            permission={"bash": "allow"},
            shell="bash",
        )
    )

    contents = [e["content"] for e in entries]
    assert "out-line" in contents
    assert "err-line" in contents
    assert all(e["kind"] == "raw" for e in entries)
    assert outcome.exit_code == 0
    assert "err-line" in outcome.stderr_tail
