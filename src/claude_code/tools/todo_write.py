"""TodoWrite tool — update the task list.

Updates the todo list stored in :attr:`ToolContext.metadata`.
Enforces that only one task can be ``in_progress`` at a time and
validates status values.
"""

from __future__ import annotations

from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Must match the key used by TodoRead
from claude_code.tools.todo_read import TODO_METADATA_KEY

_VALID_STATUSES = frozenset({"pending", "in_progress", "completed"})
_VALID_PRIORITIES = frozenset({"low", "medium", "high"})


class TodoWriteTool(Tool):
    """Update the task/todo list in context metadata."""

    name = "TodoWrite"
    description = (
        "Updates the task list. Accepts a list of todos with content, "
        "status (pending/in_progress/completed), priority (low/medium/high), "
        "and id. Only one task can be in_progress at a time."
    )
    category = "todo"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "description": "List of task objects.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Task description.",
                        },
                        "status": {
                            "type": "string",
                            "description": "Task status.",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "priority": {
                            "type": "string",
                            "description": "Task priority.",
                            "enum": ["low", "medium", "high"],
                        },
                        "id": {
                            "type": "string",
                            "description": "Task identifier.",
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["todos"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        todos: list[dict[str, Any]] = kwargs["todos"]

        if not isinstance(todos, list):
            return ToolResult.error("'todos' must be a list")

        # Validate each todo
        errors: list[str] = []
        in_progress_count = 0

        for i, todo in enumerate(todos):
            if not isinstance(todo, dict):
                errors.append(f"todos[{i}] must be an object")
                continue

            if not todo.get("content"):
                errors.append(f"todos[{i}] missing 'content'")

            status = todo.get("status", "pending")
            if status not in _VALID_STATUSES:
                errors.append(
                    f"todos[{i}] invalid status '{status}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_STATUSES))}"
                )

            if status == "in_progress":
                in_progress_count += 1

            priority = todo.get("priority", "medium")
            if priority not in _VALID_PRIORITIES:
                errors.append(
                    f"todos[{i}] invalid priority '{priority}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_PRIORITIES))}"
                )

        if errors:
            return ToolResult.error(
                "Validation errors:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        if in_progress_count > 1:
            return ToolResult.error(
                f"Only one task can be 'in_progress' at a time, "
                f"found {in_progress_count}."
            )

        # Normalise and store
        normalised: list[dict[str, Any]] = []
        for i, todo in enumerate(todos):
            normalised.append({
                "id": todo.get("id", str(i + 1)),
                "content": todo["content"],
                "status": todo.get("status", "pending"),
                "priority": todo.get("priority", "medium"),
            })

        self.context.metadata[TODO_METADATA_KEY] = normalised

        # Build summary
        pending = sum(1 for t in normalised if t["status"] == "pending")
        in_progress = sum(1 for t in normalised if t["status"] == "in_progress")
        completed = sum(1 for t in normalised if t["status"] == "completed")

        lines: list[str] = [
            f"Task list updated ({len(normalised)} tasks):"
        ]
        for todo in normalised:
            status_mark = {
                "completed": "[x]",
                "in_progress": "[>]",
                "pending": "[ ]",
            }.get(todo["status"], "[?]")
            lines.append(f"  {status_mark} [{todo['id']}] {todo['content']}")

        lines.append("")
        lines.append(
            f"Summary: {completed} completed, "
            f"{in_progress} in progress, "
            f"{pending} pending"
        )

        return ToolResult.success("\n".join(lines))
