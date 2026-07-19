"""ExitPlanMode tool — signal that planning is complete.

Sets a flag in :attr:`ToolContext.metadata` that the query engine
checks to know when to stop the planning loop and present the plan
to the user.
"""

from __future__ import annotations

from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Metadata key for the plan-mode exit flag
EXIT_PLAN_MODE_KEY = "exit_plan_mode"
PLAN_CONTENT_KEY = "plan_content"


class ExitPlanModeTool(Tool):
    """Signal that planning is complete and present the plan."""

    name = "ExitPlanMode"
    description = (
        "Signals that planning is complete. The plan text is displayed "
        "to the user for review before implementation begins."
    )
    category = "orchestration"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "plan": {
                "type": "string",
                "description": "The complete plan to show the user.",
            },
        },
        "required": ["plan"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        plan: str = kwargs["plan"]

        if not plan.strip():
            return ToolResult.error("Plan cannot be empty.")

        # Set the flag and content in metadata
        self.context.metadata[EXIT_PLAN_MODE_KEY] = True
        self.context.metadata[PLAN_CONTENT_KEY] = plan

        return ToolResult.success(
            f"Planning complete. Presenting plan to user:\n\n{plan}"
        )
