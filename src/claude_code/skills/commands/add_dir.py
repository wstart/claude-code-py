"""/add-dir — Add a working directory to the session."""

from __future__ import annotations

from pathlib import Path

from claude_code.skills.commands.base import CommandContext, SlashCommand


class AddDirCommand(SlashCommand):
    """Add an additional working directory for tool access."""

    name = "add-dir"
    description = "Add a working directory"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Add a directory to the allowed working directories list."""
        if not args:
            return "Usage: /add-dir <path>"

        dir_path = Path(args.strip()).resolve()

        if not dir_path.exists():
            return f"Directory does not exist: {dir_path}"

        if not dir_path.is_dir():
            return f"Not a directory: {dir_path}"

        config = context.config
        if config is None:
            return "No configuration loaded."

        # Add to additional_dirs
        additional = getattr(config, "additional_dirs", [])
        dir_str = str(dir_path)
        if dir_str in additional:
            return f"Directory already added: {dir_path}"

        additional.append(dir_str)
        config.additional_dirs = additional

        # Also add to allowed_dirs for permission checks
        allowed = getattr(config, "allowed_dirs", [])
        if dir_str not in allowed:
            allowed.append(dir_str)
            config.allowed_dirs = allowed

        return f"Added working directory: {dir_path}"
