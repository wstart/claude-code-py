"""/status — Show session information."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class StatusCommand(SlashCommand):
    """Display current session metadata and statistics."""

    name = "status"
    description = "Show session info"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Show session ID, model, working directory, and token usage."""
        config = context.config
        session = context.session

        lines: list[str] = []

        if session:
            meta = session.metadata if hasattr(session, "metadata") else None
            if meta:
                lines.append(f"  Session ID:    {meta.id}")
                lines.append(f"  Session name:  {meta.name}")
                lines.append(f"  Messages:      {meta.message_count}")

        if config:
            lines.append(f"  Model:         {getattr(config, 'model', 'N/A')}")
            lines.append(f"  Working dir:   {getattr(config, 'working_directory', 'N/A')}")
            lines.append(f"  Permission:    {getattr(config, 'permission_mode', 'N/A')}")

        if context.query_engine:
            cost_info = context.query_engine.get_cost_info()
            lines.append(f"  Total tokens:  {cost_info.total_tokens:,}")

        if not lines:
            return "No active session."

        return "Session Status:\n" + "\n".join(lines)
