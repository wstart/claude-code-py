"""/compact — Compress context to free up token space."""

from __future__ import annotations

from claude_code.core.context import compress
from claude_code.skills.commands.base import CommandContext, SlashCommand


class CompactCommand(SlashCommand):
    """Compress conversation context to fit more within the window."""

    name = "compact"
    description = "Compact context to save tokens"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Run context compression on the current conversation."""
        if not context.query_engine:
            return "No active conversation to compact."

        config = context.config
        max_tokens = getattr(config, "max_tokens", 200_000) if config else 200_000

        api_msgs = context.query_engine.conversation.to_api_messages()
        original_count = len(api_msgs)

        compressed = compress(api_msgs, max_tokens=max_tokens)
        context.query_engine._rebuild_conversation(compressed)

        return f"Compacted: {original_count} → {len(compressed)} messages"
