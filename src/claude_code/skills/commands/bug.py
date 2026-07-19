"""/bug — Report a bug or issue."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class BugCommand(SlashCommand):
    """Open a bug report template or direct the user to the issue tracker."""

    name = "bug"
    description = "Report a bug"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Display bug reporting instructions."""
        description = args.strip() if args else ""

        lines = [
            "Bug Report",
            "",
            "To report a bug, please include:",
            "  1. What you did (steps to reproduce)",
            "  2. What you expected to happen",
            "  3. What actually happened",
            "  4. Environment info (OS, Python version, package version)",
        ]

        if description:
            lines.append(f"\nYour description: {description}")
            lines.append(
                "\nConsider filing this at the project's issue tracker."
            )
        else:
            lines.append("\nUsage: /bug <description of the issue>")

        return "\n".join(lines)
