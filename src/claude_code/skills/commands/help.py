"""/help — Show available commands and usage information."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class HelpCommand(SlashCommand):
    """Display help text with all available slash commands."""

    name = "help"
    description = "Show help"
    aliases = ["?"]

    async def execute(self, args: str, context: CommandContext) -> str:
        """Show command listing or detailed help for a specific command."""
        if args:
            return await self._show_command_help(args, context)

        lines = [
            "Claude Code Py — Available Commands",
            "",
            "  /help [cmd]    Show help (optionally for a specific command)",
            "  /clear         Clear conversation history",
            "  /compact       Compress context to save tokens",
            "  /config        Show or edit configuration",
            "  /cost          Show token usage and cost",
            "  /doctor        Run environment diagnostics",
            "  /init          Initialize CLAUDE.md for this project",
            "  /memory        Show or edit CLAUDE.md content",
            "  /model [name]  Show or change the active model",
            "  /status        Show session information",
            "  /review        Trigger code review",
            "  /bug           Report a bug",
            "  /mcp           Show MCP server status",
            "  /add-dir       Add a working directory",
            "  /rename        Rename the current session",
            "  /permissions   Show permission rules",
            "  /hooks         Show active hooks",
            "  /agents        List and manage agents",
            "  /plugins       Manage plugins",
            "  /loop          Run a recurring task",
            "",
            "Shortcuts: Enter=submit, Shift+Enter=newline, Ctrl+C=cancel, Ctrl+D=exit",
        ]
        return "\n".join(lines)

    async def _show_command_help(self, cmd_name: str, context: CommandContext) -> str:
        """Show detailed help for a specific command."""
        cmd_name = cmd_name.lstrip("/").lower()

        # Check built-in commands from metadata
        command_registry = context.metadata.get("command_registry")
        if command_registry:
            command = command_registry.get(cmd_name)
            if command:
                return command.get_help()

        return f"No help available for '{cmd_name}'"
