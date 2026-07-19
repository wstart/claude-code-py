"""/model — Show or switch the active model."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class ModelCommand(SlashCommand):
    """Display or change the current LLM model."""

    name = "model"
    description = "Show or switch model"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Show current model or switch to a new one."""
        config = context.config
        if config is None:
            return "No configuration loaded."

        if not args:
            return f"Current model: {getattr(config, 'model', 'N/A')}"

        new_model = args.strip()
        old_model = getattr(config, "model", "unknown")
        config.model = new_model

        # Update UI status bar if available
        if context.ui:
            context.ui.update_status(model=new_model)

        return f"Model: {old_model} → {new_model}"
