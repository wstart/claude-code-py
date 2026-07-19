"""Loading spinner component using rich.live.

Shows an animated spinner with elapsed time and current action
while waiting for API responses or tool execution.
"""

import time
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.spinner import Spinner as RichSpinner
from rich.table import Table
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme


class Spinner:
    """Animated spinner with elapsed time and action display.

    Uses rich.live.Live to continuously update the display with
    a spinner animation, elapsed time counter, and a description
    of the current action.

    Attributes:
        console: The rich Console for output.
        theme: The current theme configuration.
    """

    def __init__(
        self,
        console: Console,
        theme: Optional[ThemeConfig] = None,
    ) -> None:
        """Initialize the spinner.

        Args:
            console: Rich Console instance.
            theme: Optional theme config.
        """
        self.console = console
        self.theme = theme or get_theme("dark")
        self._live: Optional[Live] = None
        self._start_time: float = 0.0
        self._action: str = "Thinking"

    def start(self, action: str = "Thinking") -> None:
        """Start the spinner with an action description.

        Args:
            action: Description of what is happening (e.g., "Thinking...",
                    "Running tool: Bash...").
        """
        self._action = action
        self._start_time = time.monotonic()
        self._live = Live(
            self._build_display(),
            console=self.console,
            refresh_per_second=10,
            transient=True,  # Remove spinner when done
        )
        self._live.start()

    def update_action(self, action: str) -> None:
        """Update the displayed action.

        Args:
            action: New action description.
        """
        self._action = action
        if self._live is not None:
            self._live.update(self._build_display())

    def stop(self) -> None:
        """Stop and remove the spinner."""
        if self._live is not None:
            self._live.stop()
            self._live = None

    @property
    def is_active(self) -> bool:
        """Whether the spinner is currently running."""
        return self._live is not None

    def _build_display(self) -> Text:
        """Build the display content for the spinner.

        Returns:
            Text object with spinner, action, and elapsed time.
        """
        elapsed = time.monotonic() - self._start_time
        minutes, seconds = divmod(int(elapsed), 60)
        time_str = f"{minutes}:{seconds:02d}" if minutes else f"{seconds}s"

        s = self.theme.scheme
        display = Text()
        display.append("  ◌ ", style=f"bold {s.spinner}")
        display.append(self._action, style=s.text_secondary)
        display.append(f"  {time_str}", style=s.text_dim)
        return display
