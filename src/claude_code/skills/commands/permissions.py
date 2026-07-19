"""/permissions — Show active permission rules."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class PermissionsCommand(SlashCommand):
    """Display the current allow/deny permission rules."""

    name = "permissions"
    description = "Show permission rules"
    aliases = ["perms"]

    async def execute(self, args: str, context: CommandContext) -> str:
        """List all configured permission rules."""
        app = context.app
        if app is None:
            return "No application context available."

        perm_manager = getattr(app, "permission_manager", None)
        if perm_manager is None:
            return "No permission manager configured."

        config = context.config
        mode = getattr(config, "permission_mode", "unknown") if config else "unknown"

        lines = [f"Permission mode: {mode}", ""]

        # Try to get rules from the permission manager
        engine = getattr(perm_manager, "engine", None) or getattr(perm_manager, "rule_engine", None)
        if engine and hasattr(engine, "rules"):
            rules = engine.rules
            if not rules:
                lines.append("No custom rules configured.")
            else:
                allow_rules = [r for r in rules if r.action.value == "allow"]
                deny_rules = [r for r in rules if r.action.value == "deny"]

                if allow_rules:
                    lines.append("Allow rules:")
                    for rule in allow_rules:
                        lines.append(f"  ✓ {rule.tool_pattern}")

                if deny_rules:
                    lines.append("Deny rules:")
                    for rule in deny_rules:
                        lines.append(f"  ✗ {rule.tool_pattern}")

                if not allow_rules and not deny_rules:
                    lines.append("No custom rules configured.")
        else:
            # Fallback: show allowed/denied tools from config
            if config:
                allowed = getattr(config, "allowed_tools", [])
                denied = getattr(config, "denied_tools", [])
                if allowed:
                    lines.append(f"Allowed tools: {', '.join(allowed)}")
                if denied:
                    lines.append(f"Denied tools: {', '.join(denied)}")
                if not allowed and not denied:
                    lines.append("No custom tool rules configured.")

        return "\n".join(lines)
