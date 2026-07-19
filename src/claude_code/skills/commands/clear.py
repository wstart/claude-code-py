"""/clear — Clear the current conversation."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class ClearCommand(SlashCommand):
    """Clear all conversation messages and reset context."""

    name = "clear"
    description = "Clear conversation history"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Reset the query engine conversation."""
        if context.query_engine:
            context.query_engine.reset()
            return "Conversation cleared."
        return "No active conversation to clear."
