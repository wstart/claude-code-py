"""/agents — List and manage agents."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class AgentsCommand(SlashCommand):
    """List available agent types and their status."""

    name = "agents"
    description = "List and manage agents"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Show available agent definitions."""
        app = context.app
        if app is None:
            return "No application context available."

        lines: list[str] = ["Agent Types:"]

        # List built-in agent types
        builtin_agents = [
            ("fork", "Fork of the current session (inherits full context)"),
            ("general-purpose", "General-purpose research and multi-step tasks"),
            ("plan", "Software architect for implementation planning"),
            ("explore", "Read-only code search and exploration"),
        ]

        for name, desc in builtin_agents:
            lines.append(f"  • {name}: {desc}")

        # Check for custom agents from config
        config = context.config
        if config:
            agent_config = getattr(config, "agents", {})
            if isinstance(agent_config, dict):
                for agent_name in agent_config:
                    lines.append(f"  • {agent_name}: (custom)")

        # Show active background agents if available
        store = getattr(app, "store", None)
        if store:
            active = await store.get_state("active_agents", [])
            if active:
                lines.append(f"\nActive agents: {len(active)}")
                for agent_info in active:
                    agent_name = agent_info.get("name", "unnamed")
                    status = agent_info.get("status", "unknown")
                    lines.append(f"  [{status}] {agent_name}")

        return "\n".join(lines)
