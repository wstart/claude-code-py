"""BashOutput tool — stream stdout from a background task.

Reads accumulated output from a background shell process identified
by *task_id*.  Supports non-blocking reads that return whatever output
is currently available.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_code.tools.base import Tool, ToolContext, ToolResult

logger = logging.getLogger(__name__)

# Maximum lines to return in a single read
_MAX_OUTPUT_LINES = 200


class BashOutputTool(Tool):
    """Read output from a running background shell task.

    Returns the most recent stdout lines from the specified background
    task.  If the task has already exited, returns all remaining output.
    """

    name = "BashOutput"
    description = (
        "Read stdout output from a background shell task. Returns the "
        "most recent output lines without blocking. Use this to check "
        "progress of a long-running background command."
    )
    category = "shell"
    read_only = True
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "The ID of the background task to read output from."
                ),
            },
        },
        "required": ["task_id"],
    }

    def __init__(self, context: ToolContext | None = None) -> None:
        super().__init__(context)
        # The background task registry is injected via context metadata
        # under the key "background_tasks" (dict[str, _BackgroundTask]).

    async def execute(self, **kwargs: Any) -> ToolResult:
        task_id: str = kwargs["task_id"]

        tasks = self.context.metadata.get("background_tasks", {})
        task = tasks.get(task_id)

        if task is None:
            return ToolResult.error(
                f"No background task found with ID '{task_id}'. "
                "Use the Bash tool with run_in_background=true to start one."
            )

        try:
            output = await self._read_output(task)
        except Exception as exc:
            return ToolResult.error(f"Failed to read output: {exc}")

        if not output:
            status = "still running" if self._is_task_running(task) else "completed"
            return ToolResult.success(f"(No new output — task is {status})")

        # Truncate if too long
        lines = output.splitlines(keepends=True)
        if len(lines) > _MAX_OUTPUT_LINES:
            truncated = lines[-_MAX_OUTPUT_LINES:]
            header = f"... ({len(lines) - _MAX_OUTPUT_LINES} earlier lines omitted) ...\n"
            output = header + "".join(truncated)

        # Add status info
        if not self._is_task_running(task):
            exit_code = getattr(task, "exit_code", "unknown")
            output += f"\n[Task completed with exit code: {exit_code}]"

        return ToolResult.success(output)

    async def _read_output(self, task: Any) -> str:
        """Read available output from the task."""
        # Support tasks that expose an output buffer (list of lines)
        if hasattr(task, "get_output"):
            return await task.get_output()

        # Support tasks with an accumulated_output attribute
        if hasattr(task, "accumulated_output"):
            output = task.accumulated_output
            task.accumulated_output = ""
            return output

        # Support tasks with a process that has stdout
        if hasattr(task, "process") and task.process is not None:
            proc = task.process
            if proc.stdout is None:
                return ""
            lines: list[str] = []
            try:
                while True:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(), timeout=0.1,
                    )
                    if not line_bytes:
                        break
                    lines.append(line_bytes.decode("utf-8", errors="replace"))
            except TimeoutError:
                pass
            return "".join(lines)

        return ""

    @staticmethod
    def _is_task_running(task: Any) -> bool:
        """Check whether the background task is still running."""
        if hasattr(task, "is_running"):
            if callable(task.is_running):
                return task.is_running()
            return bool(task.is_running)
        if hasattr(task, "process") and task.process is not None:
            return task.process.returncode is None
        return False
