"""/rename — Rename the current session."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class RenameCommand(SlashCommand):
    """Give the current session a human-readable name."""

    name = "rename"
    description = "Rename the current session"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Set a new name for the active session."""
        if not args:
            session = context.session
            if session and hasattr(session, "metadata"):
                return f"Current session name: {session.metadata.name}\nUsage: /rename <new-name>"
            return "Usage: /rename <new-name>"

        session = context.session
        if session is None:
            return "No active session to rename."

        new_name = args.strip()
        old_name = session.metadata.name if hasattr(session, "metadata") else "unknown"
        session.metadata.name = new_name

        return f"Session renamed: '{old_name}' → '{new_name}'"
