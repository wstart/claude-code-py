"""/init — Initialize CLAUDE.md for a project."""

from __future__ import annotations

from pathlib import Path

from claude_code.skills.commands.base import CommandContext, SlashCommand


class InitCommand(SlashCommand):
    """Generate a starter CLAUDE.md in the current project root."""

    name = "init"
    description = "Initialize CLAUDE.md for this project"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Create CLAUDE.md with sensible defaults if it doesn't exist."""
        work_dir = ""
        if context.config:
            work_dir = getattr(context.config, "working_directory", "")
        if not work_dir:
            work_dir = "."

        claude_md = Path(work_dir) / "CLAUDE.md"

        if claude_md.exists():
            return f"CLAUDE.md already exists at {claude_md}"

        template = (
            "# Project Instructions\n\n"
            "## Overview\n"
            "<!-- Describe the project purpose and architecture -->\n\n"
            "## Tech Stack\n"
            "<!-- List languages, frameworks, and key dependencies -->\n\n"
            "## Code Style\n"
            "<!-- Naming conventions, formatting rules, linting tools -->\n\n"
            "## Build & Test\n"
            "<!-- How to build, run tests, and deploy -->\n\n"
            "## Common Tasks\n"
            "<!-- Frequently performed operations and their commands -->\n"
        )

        try:
            claude_md.write_text(template, encoding="utf-8")
            return (
                f"Created CLAUDE.md at {claude_md}\n"
                "Edit it to add project-specific instructions."
            )
        except OSError as exc:
            return f"Failed to create CLAUDE.md: {exc}"
