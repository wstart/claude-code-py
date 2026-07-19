"""Chat display — clean, Claude Code inspired message rendering."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
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
    """Renders chat messages with Claude Code aesthetic."""

    def __init__(
        self,
        console: Optional[Console] = None,
        theme: Optional[ThemeConfig] = None,
    ) -> None:
        self.console = console or Console()
        self.theme = theme or get_theme("dark")
        self._stream_buffer: str = ""

    # ------------------------------------------------------------------
    # Message display
    # ------------------------------------------------------------------

    def display_message(self, message: DisplayMessage) -> None:
        """Display a message based on its role."""
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
    # User messages
    # ------------------------------------------------------------------

    def _render_user(self, text: str) -> None:
        """Render user input with a subtle marker."""
        s = self.theme.scheme
        label = Text()
        label.append("❯ ", style=f"bold {s.user_blue}")
        label.append(text, style=s.text_primary)
        self.console.print(label)
        self.console.print()

    # ------------------------------------------------------------------
    # Assistant messages
    # ------------------------------------------------------------------

    def _render_assistant(self, text: str) -> None:
        """Render assistant response with markdown."""
        s = self.theme.scheme
        if not text:
            return

        label = Text()
        label.append("Sentinel", style=f"bold {s.claude_orange}")
        self.console.print(label)

        # Markdown content with indentation
        try:
            md = Markdown(text, code_theme=self.theme.code_theme)
            self.console.print(md, padding=(0, 0, 0, 2))
        except Exception:
            self.console.print(f"  {text}")
        self.console.print()

    # ------------------------------------------------------------------
    # Tool calls & results
    # ------------------------------------------------------------------

    def display_tool_call(self, name: str, params: dict) -> None:
        """Display a tool invocation compactly."""
        s = self.theme.scheme
        color = self.theme.get_tool_color(name)

        # Format params summary
        summary = self._format_params(params)

        line = Text()
        line.append("  ⚡ ", style=f"bold {color}")
        line.append(name, style=f"bold {color}")
        if summary:
            line.append(f" {summary}", style=s.text_secondary)
        self.console.print(line)

    def show_result(self, name: str, result: str, is_error: bool = False) -> None:
        """Display a tool result."""
        s = self.theme.scheme
        color = self.theme.get_tool_color(name)

        if not result:
            return

        # Truncate long results
        max_len = 500
        truncated = len(result) > max_len
        display = result[:max_len] if truncated else result

        icon = "✗" if is_error else "✓"
        style = s.error if is_error else s.text_secondary

        # Compact result display
        lines = display.strip().split("\n")
        if len(lines) <= 5:
            for line in lines:
                self.console.print(f"    {line}", style=style)
        else:
            for line in lines[:3]:
                self.console.print(f"    {line}", style=style)
            self.console.print(f"    ... ({len(lines)} lines)", style=s.text_dim)

        if truncated:
            self.console.print(
                f"    ... ({len(result)} chars total)", style=s.text_dim
            )

    # ------------------------------------------------------------------
    # System messages
    # ------------------------------------------------------------------

    def _render_system(self, text: str) -> None:
        """Render system/info messages."""
        s = self.theme.scheme
        self.console.print(f"  ℹ {text}", style=s.text_secondary)
        self.console.print()

    def render_error(self, text: str) -> None:
        """Render error messages."""
        s = self.theme.scheme
        self.console.print(f"  ✗ {text}", style=f"bold {s.error}")
        self.console.print()

    def render_info(self, text: str) -> None:
        """Render info messages."""
        self._render_system(text)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def start_stream(self) -> None:
        """Prepare for streaming output."""
        self._stream_buffer = ""
        s = self.theme.scheme
        label = Text()
        label.append("Sentinel", style=f"bold {s.claude_orange}")
        label.append("\n", style="")
        self.console.print(label)

    def append_stream(self, chunk: str) -> None:
        """Append streaming text chunk."""
        self._stream_buffer += chunk
        self.console.print(chunk, end="", highlight=False)

    def end_stream(self) -> None:
        """Finalize streaming."""
        self.console.print()  # newline
        self.console.print()  # spacing
        self._stream_buffer = ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_params(params: dict) -> str:
        """Format tool params into a compact summary."""
        parts = []
        for key, val in params.items():
            if key in ("file_path", "path", "notebook_path"):
                # Show just filename for paths
                from pathlib import Path
                parts.append(str(Path(str(val)).name))
            elif key == "command":
                # Truncate long commands
                cmd = str(val)
                parts.append(cmd[:60] + "..." if len(cmd) > 60 else cmd)
            elif key == "pattern":
                parts.append(str(val))
            elif key in ("content", "new_source") and isinstance(val, str):
                # Skip large content
                parts.append(f"({len(val)} chars)")
            elif key in ("old_string", "new_string"):
                val_s = str(val)
                parts.append(f"{key}={val_s[:30]}...")
        return " ".join(parts)
