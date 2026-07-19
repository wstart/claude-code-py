"""Status bar component displayed at the bottom of the terminal.

Shows model name, session ID, token count, cost, and working
directory in a compact bar that updates in real-time.
"""

import os
from dataclasses import dataclass
from typing import Optional

from rich.console import Console
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme


@dataclass
class StatusInfo:
    """Data displayed in the status bar."""

    model: str = ""
    session_id: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    working_directory: str = ""

    @property
    def short_session_id(self) -> str:
        """Return a short version of the session ID (first 8 chars)."""
        return self.session_id[:8] if self.session_id else "—"

    @property
    def short_cwd(self) -> str:
        """Return abbreviated working directory.

        Replaces the home directory with '~' for compactness.
        """
        cwd = self.working_directory or os.getcwd()
        home = os.path.expanduser("~")
        if cwd.startswith(home):
            return "~" + cwd[len(home):]
        return cwd


class StatusBar:
    """Displays session status information in a compact bar.

    Shows model name, session ID, token count, cost estimate,
    and current working directory.

    Attributes:
        console: The rich Console for output.
        theme: The current theme configuration.
        info: The current status information.
    """

    def __init__(
        self,
        console: Console,
        theme: Optional[ThemeConfig] = None,
    ) -> None:
        """Initialize the status bar.

        Args:
            console: Rich Console instance.
            theme: Optional theme config.
        """
        self.console = console
        self.theme = theme or get_theme("dark")
        self.info = StatusInfo()

    def update(
        self,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        tokens_used: Optional[int] = None,
        cost_usd: Optional[float] = None,
        working_directory: Optional[str] = None,
    ) -> None:
        """Update status bar fields.

        Only provided fields are updated; others remain unchanged.

        Args:
            model: Model name (e.g., "claude-sonnet-4-20250514").
            session_id: Session UUID.
            tokens_used: Total tokens consumed.
            cost_usd: Estimated cost in USD.
            working_directory: Current working directory path.
        """
        if model is not None:
            self.info.model = model
        if session_id is not None:
            self.info.session_id = session_id
        if tokens_used is not None:
            self.info.tokens_used = tokens_used
        if cost_usd is not None:
            self.info.cost_usd = cost_usd
        if working_directory is not None:
            self.info.working_directory = working_directory

    def render(self) -> Text:
        """Build the status bar text.

        Returns:
            Text object with the formatted status bar.
        """
        bar = Text()
        scheme = self.theme.scheme

        # Model name
        if self.info.model:
            bar.append(f" {self.info.model}", style=f"bold {scheme.primary}")
            bar.append(" │", style="dim")

        # Session ID
        if self.info.session_id:
            bar.append(f" {self.info.short_session_id}", style=scheme.secondary)
            bar.append(" │", style="dim")

        # Token count
        if self.info.tokens_used > 0:
            bar.append(f" {self.info.tokens_used:,} tok", style=scheme.info)
            bar.append(" │", style="dim")

        # Cost
        if self.info.cost_usd > 0:
            bar.append(f" ${self.info.cost_usd:.4f}", style=scheme.warning)
            bar.append(" │", style="dim")

        # Working directory
        bar.append(f" {self.info.short_cwd}", style="dim")

        return bar

    def show(self) -> None:
        """Print the status bar to the console."""
        self.console.print(self.render())
