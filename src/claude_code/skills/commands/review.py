"""/review — Trigger a code review on recent changes."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class ReviewCommand(SlashCommand):
    """Trigger an AI code review of recent git changes."""

    name = "review"
    description = "Trigger code review"
    aliases = ["code-review"]

    async def execute(self, args: str, context: CommandContext) -> str:
        """Request a review of staged or recent git changes."""
        # Build a review prompt
        scope = args.strip() if args else "staged changes"

        review_prompt = (
            f"Please review {scope}. "
            "Focus on:\n"
            "- Correctness and potential bugs\n"
            "- Security issues\n"
            "- Performance concerns\n"
            "- Code style and readability\n"
            "- Missing error handling\n"
            "- Test coverage gaps\n\n"
            "Provide specific, actionable feedback."
        )

        # If there's a query engine, submit the review as a query
        if context.query_engine:
            context.metadata["pending_review"] = review_prompt
            return (
                f"Code review triggered for: {scope}\n"
                "The review will be processed in the next turn."
            )

        return "No active query engine to process the review."
