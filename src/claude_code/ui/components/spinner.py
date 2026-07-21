"""Spinner — animated loading indicator with cyberpunk aesthetic."""

import time

from rich.console import Console
from rich.live import Live
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme

# Braille spinner frames
_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]


class Spinner:
    """Animated spinner with elapsed time and action display."""

    def __init__(
        self,
        console: Console | None = None,
        theme: ThemeConfig | None = None,
    ) -> None:
        self.console = console or Console()
        self.theme = theme or get_theme("dark")
        self._live: Live | None = None
        self._start_time: float = 0
        self._action: str = "Thinking"
        self._frame: int = 0

    def start(self, action: str = "Thinking") -> None:
        if self._live is not None:
            self.stop()
        self._action = action
        self._start_time = time.monotonic()
        self._frame = 0
        # Pass `self` (not a one-off Text) so Live re-renders via __rich__
        # on every refresh — otherwise the frame and elapsed time freeze.
        self._live = Live(
            self,
            console=self.console,
            refresh_per_second=12,
            transient=True,
        )
        self._live.start()

    def __rich__(self) -> Text:
        """Called by Live on every refresh — advances frame and elapsed time."""
        return self._build_display()

    def stop(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def update_action(self, action: str) -> None:
        self._action = action
        if self._live is not None:
            self._live.update(self._build_display())

    @property
    def is_active(self) -> bool:
        return self._live is not None

    def _build_display(self) -> Text:
        self._frame = (self._frame + 1) % len(_FRAMES)
        elapsed = time.monotonic() - self._start_time
        secs = int(elapsed)
        time_str = f"{secs // 60}:{secs % 60:02d}" if secs >= 60 else f"{secs}s"

        s = self.theme.scheme
        display = Text()
        display.append(f"  {_FRAMES[self._frame]} ", style=f"bold {s.spinner}")
        display.append(self._action, style=s.text_secondary)
        display.append(f"  {time_str}", style=s.text_dim)
        return display
