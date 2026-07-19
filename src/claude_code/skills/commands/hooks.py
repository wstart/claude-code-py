"""/hooks — Show active hooks."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class HooksCommand(SlashCommand):
    """Display registered hooks grouped by event."""

    name = "hooks"
    description = "Show active hooks"

    async def execute(self, args: str, context: CommandContext) -> str:
        """List all hooks with their event, command, and priority."""
        app = context.app
        if app is None:
            return "No application context available."

        hook_manager = getattr(app, "hook_manager", None)
        if hook_manager is None:
            return "No hook manager configured."

        all_hooks = hook_manager.list_hooks()

        if not all_hooks:
            return "No hooks registered."

        lines: list[str] = ["Active Hooks:"]

        for event, hooks in all_hooks.items():
            event_name = event.value if hasattr(event, "value") else str(event)
            lines.append(f"\n  [{event_name}]")
            for hook in hooks:
                enabled = "✓" if hook.get("enabled", True) else "✗"
                cmd = hook.get("command", "unknown")
                priority = hook.get("priority", 0)
                timeout = hook.get("timeout", 30)
                lines.append(f"    {enabled} {cmd} (priority={priority}, timeout={timeout}s)")

        return "\n".join(lines)
