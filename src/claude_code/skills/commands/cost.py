"""/cost — Show token usage and estimated cost."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class CostCommand(SlashCommand):
    """Display token usage statistics for the current session."""

    name = "cost"
    description = "Show token usage and cost"
    aliases = ["usage"]

    async def execute(self, args: str, context: CommandContext) -> str:
        """Show token counts and estimated cost."""
        if not context.query_engine:
            return "No active session."

        cost_info = context.query_engine.get_cost_info()

        lines = [
            f"  Input tokens:  {cost_info.input_tokens:,}",
            f"  Output tokens: {cost_info.output_tokens:,}",
            f"  Total tokens:  {cost_info.total_tokens:,}",
        ]

        if hasattr(cost_info, "estimated_cost_usd") and cost_info.estimated_cost_usd:
            lines.append(f"  Est. cost:     ${cost_info.estimated_cost_usd:.4f}")

        return "Token Usage:\n" + "\n".join(lines)
