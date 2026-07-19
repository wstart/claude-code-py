"""KillShell tool — terminate a background shell task.

Kills a running background shell process identified by *task_id*.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from typing import Any

from claude_code.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)


class KillShellTool(Tool):
    """Terminate a running background shell task.

    Sends SIGTERM (then SIGKILL after a grace period) to the background
    process identified by *task_id*.
    """

    name = "KillShell"
    description = (
        "Terminate a running background shell task. Sends a termination "
        "signal to the process. Use this to stop long-running background "
        "commands."
    )
    category = "shell"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "The ID of the background task to terminate."
                ),
            },
        },
        "required": ["task_id"],
    }

    def __init__(self, context: ToolContext | None = None) -> None:
        super().__init__(context)

    async def execute(self, **kwargs: Any) -> ToolResult:
        task_id: str = kwargs["task_id"]

        tasks = self.context.metadata.get("background_tasks", {})
        task = tasks.get(task_id)

        if task is None:
            return ToolResult.error(
                f"No background task found with ID '{task_id}'. "
                "Check the task ID and try again."
            )

        # Try the task's own stop method first
        if hasattr(task, "stop"):
            try:
                if asyncio.iscoroutinefunction(task.stop):
                    await task.stop()
                else:
                    task.stop()
                del tasks[task_id]
                return ToolResult.success(
                    f"Background task '{task_id}' has been terminated."
                )
            except Exception as exc:
                logger.warning("Task.stop() failed: %s, trying process kill", exc)

        # Fallback: kill the process directly
        if hasattr(task, "process") and task.process is not None:
            killed = await self._kill_process(task.process)
            if killed:
                del tasks[task_id]
                return ToolResult.success(
                    f"Background task '{task_id}' has been terminated."
                )
            return ToolResult.error(
                f"Failed to terminate task '{task_id}'."
            )

        # If we can't find a process, just remove it from the registry
        del tasks[task_id]
        return ToolResult.success(
            f"Background task '{task_id}' removed (no active process found)."
        )

    async def _kill_process(self, proc: asyncio.subprocess.Process) -> bool:
        """Gracefully terminate then kill a subprocess.

        Returns True if the process was successfully terminated.
        """
        if proc.returncode is not None:
            return True  # already dead

        pid = proc.pid
        if pid is None:
            return False

        # Try SIGTERM first
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

        try:
            proc.terminate()
        except ProcessLookupError:
            return True

        # Wait up to 3 seconds for graceful shutdown
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
            return True
        except asyncio.TimeoutError:
            pass

        # Force kill
        try:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass

        return proc.returncode is not None
