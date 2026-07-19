"""/mcp — Show MCP server status and available MCP tools."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class McpCommand(SlashCommand):
    """Display connected MCP servers and their available tools."""

    name = "mcp"
    description = "Show MCP server status"

    async def execute(self, args: str, context: CommandContext) -> str:
        """List MCP servers and optionally show their tools."""
        config = context.config
        if config is None:
            return "No configuration loaded."

        servers = getattr(config, "mcp_servers", {})
        if not servers:
            return "No MCP servers configured.\nAdd servers to settings.json under 'mcp_servers'."

        lines: list[str] = ["MCP Servers:"]

        for name, server_config in servers.items():
            server_type = server_config.get("type", "unknown")
            status = server_config.get("status", "configured")
            lines.append(f"\n  {name} ({server_type}) — {status}")

            tools = server_config.get("tools", [])
            if tools:
                for tool_name in tools:
                    lines.append(f"    • {tool_name}")

        return "\n".join(lines)
