"""Chat display — Cyberpunk Security Terminal aesthetic.

Precision-engineered message rendering with high contrast,
tight spacing, and clear visual hierarchy.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SYSTEM = "system"


class DisplayMessage:
    """A message to display in the chat."""

    def __init__(
        self,
        role: MessageRole,
        content: str,
        tool_name: str = "",
        is_error: bool = False,
        metadata: Optional[dict] = None,
    ) -> None:
        self.role = role
        self.content = content
        self.tool_name = tool_name
        self.is_error = is_error
        self.metadata = metadata or {}


class ChatDisplay:
    """Renders chat messages with cyberpunk security aesthetic."""

    def __init__(
        self,
        console: Optional[Console] = None,
        theme: Optional[ThemeConfig] = None,
    ) -> None:
        self.console = console or Console()
        self.theme = theme or get_theme("dark")
        self._stream_buffer: str = ""

    # ------------------------------------------------------------------
    # Message dispatch
    # ------------------------------------------------------------------

    def display_message(self, message: DisplayMessage) -> None:
        if message.role == MessageRole.USER:
            self._render_user(message.content)
        elif message.role == MessageRole.ASSISTANT:
            self._render_assistant(message.content)
        elif message.role == MessageRole.TOOL:
            self._render_tool(message)
        elif message.role == MessageRole.SYSTEM:
            self._render_system(message.content)

    def display_user_message(self, text: str) -> None:
        self._render_user(text)

    def display_assistant_message(self, text: str) -> None:
        self._render_assistant(text)

    # ------------------------------------------------------------------
    # User — cyan accent, minimal chrome
    # ------------------------------------------------------------------

    def _render_user(self, text: str) -> None:
        """Render user input — only used for session resume history."""
        s = self.theme.scheme
        label = Text()
        label.append("❯ ", style=f"bold {s.user_cyan}")
        label.append(text, style=s.text_secondary)
        self.console.print(label)

    # ------------------------------------------------------------------
    # Assistant — red accent, markdown rendered
    # ------------------------------------------------------------------

    def _render_assistant(self, text: str) -> None:
        s = self.theme.scheme
        if not text:
            return

        # Header
        header = Text()
        header.append("▎ ", style=f"bold {s.aka_red}")
        header.append("AKA", style=f"bold {s.aka_red}")
        self.console.print(header)

        # Content
        try:
            md = Markdown(text, code_theme=self.theme.code_theme)
            self.console.print(md, padding=(0, 0, 0, 2))
        except Exception:
            self.console.print(f"  {text}")
        self.console.print()

    # ------------------------------------------------------------------
    # Tool calls — icons, color-coded, compact
    # ------------------------------------------------------------------

    def display_tool_call(self, name: str, params: dict) -> None:
        s = self.theme.scheme
        color = self.theme.get_tool_color(name)
        icon = self.theme.get_tool_icon(name)
        summary = self._format_params(params)

        line = Text()
        line.append(f"  {icon} ", style=color)
        line.append(name, style=f"bold {color}")
        if summary:
            line.append(f"  ", style="")
            line.append(summary, style=s.text_dim)
        self.console.print(line)

    def show_result(self, name: str, result: str, is_error: bool = False) -> None:
        s = self.theme.scheme
        if not result:
            return

        max_len = 600
        truncated = len(result) > max_len
        display = result[:max_len] if truncated else result

        style = s.error if is_error else s.text_secondary
        icon = "✗" if is_error else "✓"
        color = s.error if is_error else s.accent_green

        lines = display.strip().split("\n")
        if len(lines) <= 8:
            for line in lines:
                self.console.print(f"    {line}", style=style)
        else:
            for line in lines[:5]:
                self.console.print(f"    {line}", style=style)
            self.console.print(
                f"    ⋯ ({len(lines) - 5} more lines)", style=s.text_dim
            )

        if truncated:
            self.console.print(
                f"    ⋯ ({len(result)} chars total)", style=s.text_dim
            )

    # ------------------------------------------------------------------
    # System / info / error
    # ------------------------------------------------------------------

    def _render_system(self, text: str) -> None:
        s = self.theme.scheme
        self.console.print(f"  ℹ {text}", style=s.text_dim)

    def render_error(self, text: str) -> None:
        s = self.theme.scheme
        self.console.print(f"  ✗ {text}", style=f"bold {s.error}")
        self.console.print()

    def render_info(self, text: str) -> None:
        self._render_system(text)

    # ------------------------------------------------------------------
    # Streaming — real-time output
    # ------------------------------------------------------------------

    def start_stream(self) -> None:
        self._stream_buffer = ""
        s = self.theme.scheme
        header = Text()
        header.append("▎ ", style=f"bold {s.aka_red}")
        header.append("AKA", style=f"bold {s.aka_red}")
        self.console.print(header)

    def append_stream(self, chunk: str) -> None:
        self._stream_buffer += chunk
        self.console.print(chunk, end="", highlight=False)

    def end_stream(self) -> None:
        self.console.print()
        self._stream_buffer = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_params(params: dict) -> str:
        parts = []
        for key, val in params.items():
            if key in ("file_path", "path", "notebook_path"):
                from pathlib import Path
                parts.append(str(Path(str(val)).name))
            elif key == "command":
                cmd = str(val)
                parts.append(cmd[:50] + "…" if len(cmd) > 50 else cmd)
            elif key == "pattern":
                parts.append(str(val))
            elif key in ("content", "new_source") and isinstance(val, str):
                parts.append(f"({len(val)} chars)")
            elif key in ("old_string", "new_string"):
                v = str(val)
                parts.append(f"{key}={v[:25]}…")
        return " ".join(parts)
