"""/config — Show or edit configuration."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class ConfigCommand(SlashCommand):
    """Display current configuration or update a specific setting."""

    name = "config"
    description = "Show or edit configuration"
    aliases = ["settings"]

    async def execute(self, args: str, context: CommandContext) -> str:
        """Show config summary or set a specific key."""
        config = context.config
        if config is None:
            return "No configuration loaded."

        if not args:
            lines = [
                f"  Provider:      {getattr(config, 'provider', 'N/A')}",
                f"  Model:         {getattr(config, 'model', 'N/A')}",
                f"  Permission:    {getattr(config, 'permission_mode', 'N/A')}",
                f"  Working dir:   {getattr(config, 'working_directory', 'N/A')}",
                f"  Max tokens:    {getattr(config, 'max_tokens', 'N/A')}",
                f"  Max turns:     {getattr(config, 'max_turns', 'N/A')}",
                f"  Temperature:   {getattr(config, 'temperature', 'N/A')}",
                f"  Effort:        {getattr(config, 'effort', 'N/A')}",
            ]
            return "Configuration:\n" + "\n".join(lines)

        # Set a config value: /config key value
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return "Usage: /config <key> <value>"

        key, value = parts[0], parts[1]

        if hasattr(config, key):
            current = getattr(config, key)
            # Type-coerce to match existing field type
            if isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            elif isinstance(current, bool):
                value = value.lower() in ("1", "true", "yes")
            setattr(config, key, value)
            return f"Set {key} = {value}"

        return f"Unknown config key: {key}"
