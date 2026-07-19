"""TodoRead tool — read the current task list.

Reads the todo list from :attr:`ToolContext.metadata` and returns it
formatted with id, content, status, and priority.
"""

from __future__ import annotations

from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Metadata key where the todo list is stored
TODO_METADATA_KEY = "todo_list"


class TodoReadTool(Tool):
    """Read the current task/todo list from context metadata."""

    name = "TodoRead"
    description = (
        "Reads the current task list. Returns all tasks with their id, "
        "content, status (pending/in_progress/completed), and priority."
    )
    category = "todo"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        todos: list[dict[str, Any]] = self.context.metadata.get(
            TODO_METADATA_KEY, []
        )

        if not todos:
            return ToolResult.success("No tasks in the list.")

        lines: list[str] = [f"Task list ({len(todos)} tasks):\n"]

        for todo in todos:
            task_id = todo.get("id", "?")
            content = todo.get("content", "")
            status = todo.get("status", "pending")
            priority = todo.get("priority", "medium")

            # Status emoji
            status_indicator = _status_indicator(status)

            lines.append(f"  {status_indicator} [{task_id}] {content}")
            lines.append(f"      Status: {status} | Priority: {priority}")

        # Summary counts
        pending = sum(1 for t in todos if t.get("status") == "pending")
        in_progress = sum(1 for t in todos if t.get("status") == "in_progress")
        completed = sum(1 for t in todos if t.get("status") == "completed")

        lines.append("")
        lines.append(
            f"Summary: {completed} completed, "
            f"{in_progress} in progress, "
            f"{pending} pending"
        )

        return ToolResult.success("\n".join(lines))


def _status_indicator(status: str) -> str:
    """Return a text indicator for task status."""
    return {
        "completed": "[x]",
        "in_progress": "[>]",
        "pending": "[ ]",
    }.get(status, "[?]")
