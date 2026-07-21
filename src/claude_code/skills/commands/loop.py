"""/loop — Run a prompt or slash command on a recurring interval."""

from __future__ import annotations

import asyncio
import re

from claude_code.skills.commands.base import CommandContext, SlashCommand


class LoopCommand(SlashCommand):
    """Schedule a recurring task at a fixed interval."""

    name = "loop"
    description = "Run a recurring task"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Parse interval and command, then start the loop."""
        if not args:
            return (
                "Usage: /loop <interval> <command>\n\n"
                "Examples:\n"
                "  /loop 5m /status\n"
                "  /loop 30s check deploy logs\n"
                "\nInterval format: 30s, 5m, 1h"
            )

        # Parse interval
        match = re.match(r"(\d+)([smh])\s+(.*)", args.strip())
        if not match:
            return "Invalid format. Use: /loop <interval> <command>\nExample: /loop 5m /status"

        amount = int(match.group(1))
        unit = match.group(2)
        command = match.group(3)

        multipliers = {"s": 1, "m": 60, "h": 3600}
        interval_seconds = amount * multipliers[unit]

        if interval_seconds < 5:
            return "Minimum interval is 5 seconds."

        # Store loop task in metadata for cancellation
        loops = context.metadata.setdefault("active_loops", {})
        loop_id = f"loop_{len(loops) + 1}"

        task = asyncio.create_task(
            self._run_loop(loop_id, interval_seconds, command, context)
        )
        loops[loop_id] = task

        return (
            f"Loop started ({loop_id}): every {amount}{unit} → {command}\n"
            f"Use /loop stop {loop_id} to cancel."
        )

    async def _run_loop(
        self,
        loop_id: str,
        interval: int,
        command: str,
        context: CommandContext,
    ) -> None:
        """Execute the command repeatedly at the given interval."""
        try:
            while True:
                await asyncio.sleep(interval)

                # Submit command to query engine if available
                if context.query_engine:
                    try:
                        await context.query_engine.run(
                            f"[Loop {loop_id}] {command}"
                        )
                    except Exception:
                        pass  # Silently continue on transient errors
        except asyncio.CancelledError:
            pass  # Expected when stopped


class LoopStopMixin:
    """Mixin to handle /loop stop subcommand."""

    pass
