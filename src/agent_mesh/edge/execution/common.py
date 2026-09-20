from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_LOG_FLUSH_INTERVAL_S = 0.5

# Sentinel pushed onto the log queue to tell the flusher to drain and exit.
_LOG_STOP = object()


@dataclass
class ExecutionOutcome:
    exit_code: int
    stdout_tail: str
    stderr_tail: str
    duration_ms: int
    summary: str
    mode: str = "llm"
    artifacts: list = field(default_factory=list)
    artifacts_paths: list[str] = field(default_factory=list)
    session_id: str | None = None


def _error_outcome(mode: str, summary: str) -> ExecutionOutcome:
    return ExecutionOutcome(
        exit_code=1,
        stdout_tail="",
        stderr_tail=summary,
        duration_ms=0,
        summary=summary,
        mode=mode,
    )


async def stream_task_log(log_callback, queue: asyncio.Queue, cancel_event: asyncio.Event | None) -> None:
    """Periodically flush buffered log entries to the orchestrator via callback.

    Exits when a ``_LOG_STOP`` sentinel is received (after flushing everything
    queued before it) or when ``cancel_event`` is set.
    """
    while True:
        entries: list[dict[str, str]] = []
        stopped = False
        try:
            first = await asyncio.wait_for(queue.get(), timeout=_LOG_FLUSH_INTERVAL_S)
        except asyncio.TimeoutError:
            first = None
        if first is _LOG_STOP:
            stopped = True
        elif first is not None:
            entries.append(first)
        while not queue.empty():
            item = queue.get_nowait()
            if item is _LOG_STOP:
                stopped = True
                break
            entries.append(item)
        if entries and log_callback is not None:
            try:
                await log_callback(entries)
            except Exception:
                logger.warning("task log upload failed", exc_info=True)
        if stopped:
            return
        if cancel_event is not None and cancel_event.is_set():
            return


async def stop_task_log(queue: asyncio.Queue | None, task: asyncio.Task | None) -> None:
    """Flush any buffered entries and wait for the flusher to finish."""
    if task is None:
        return
    if queue is not None:
        queue.put_nowait(_LOG_STOP)
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:  # pragma: no cover - defensive
        logger.debug("task log flusher error", exc_info=True)
