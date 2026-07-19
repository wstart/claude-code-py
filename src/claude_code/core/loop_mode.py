"""Loop mode — recurring task execution in foreground."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_code.core.app import ClaudeApp


async def run_loop_mode(app: ClaudeApp) -> None:
    """Run loop iterations in the foreground, blocking normal input."""
    task_desc = app._loop_desc
    iteration = 0
    loop_prompt = (
        f"You are running in a recurring loop. The task is:\n\n"
        f"  {task_desc}\n\n"
        f"Execute one iteration now. Be concise.\n"
        f"When done, output [LOOP:STOP] if the task is complete. "
        f"Otherwise output [LOOP:CONTINUE] to keep going."
    )

    app.ui.show_info(f"Loop: {task_desc}")

    while app._loop_active and app._running:
        iteration += 1
        app.ui.show_info(f"── iteration #{iteration} ──")

        result = await app.query_engine.run(loop_prompt)

        had_stream = app.ui._streaming
        app.ui.end_stream()
        if result.text and not had_stream:
            app.ui.display_assistant_message(result.text)

        # Check stop signal
        text = result.text or ""
        if "[LOOP:STOP]" in text:
            app.ui.show_info("Loop complete.")
            app._loop_active = False
            break

        # Wait before next iteration
        app.ui.show_info("Next iteration in 30s... (Ctrl+C to stop)")
        try:
            await asyncio.sleep(30)
        except KeyboardInterrupt:
            app._loop_active = False
            break
