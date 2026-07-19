"""/memory — Show or edit CLAUDE.md content."""

from __future__ import annotations

from pathlib import Path

from claude_code.skills.commands.base import CommandContext, SlashCommand


class MemoryCommand(SlashCommand):
    """View or modify CLAUDE.md project instructions."""

    name = "memory"
    description = "Show or edit CLAUDE.md"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Display CLAUDE.md content, or append text to it."""
        config = context.config
        work_dir = getattr(config, "working_directory", ".") if config else "."
        claude_md = Path(work_dir) / "CLAUDE.md"

        if not args:
            # Show current content
            if claude_md.exists():
                try:
                    content = claude_md.read_text(encoding="utf-8").strip()
                    return f"CLAUDE.md ({claude_md}):\n\n{content}"
                except OSError as exc:
                    return f"Error reading CLAUDE.md: {exc}"

            # Check global CLAUDE.md
            global_md = Path.home() / ".claude" / "CLAUDE.md"
            if global_md.exists():
                content = global_md.read_text(encoding="utf-8").strip()
                return f"Global CLAUDE.md ({global_md}):\n\n{content}"

            return "No CLAUDE.md found. Use /init to create one."

        # Append to project CLAUDE.md
        if not claude_md.exists():
            claude_md.write_text("", encoding="utf-8")

        try:
            with claude_md.open("a", encoding="utf-8") as f:
                f.write(f"\n{args}\n")
            return f"Appended to {claude_md}"
        except OSError as exc:
            return f"Error writing to CLAUDE.md: {exc}"
