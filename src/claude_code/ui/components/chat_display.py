"""Chat display component for rendering conversation messages.

Handles formatting of user messages, assistant responses, tool calls,
and streaming text within the conversation view.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme


class MessageRole(str, Enum):
    """Roles in a conversation."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


@dataclass
class DisplayMessage:
    """A message to display in the chat view."""

    role: MessageRole
    content: str
    tool_name: Optional[str] = None
    tool_params: Optional[dict[str, Any]] = None
    is_error: bool = False
    is_streaming: bool = False


class ChatDisplay:
    """Manages the display of conversation messages.

    Renders user messages, assistant responses with markdown,
    tool calls inline, and supports streaming display.

    Attributes:
        console: The rich Console for output.
        theme: The current theme configuration.
    """

    def __init__(
        self,
        console: Console,
        theme: Optional[ThemeConfig] = None,
    ) -> None:
        """Initialize the chat display.

        Args:
            console: Rich Console instance.
            theme: Optional theme config.
        """
        self.console = console
        self.theme = theme or get_theme("dark")
        self._stream_buffer: str = ""

    def display_message(self, message: DisplayMessage) -> None:
        """Display a single message based on its role.

        Args:
            message: The DisplayMessage to render.
        """
        if message.role == MessageRole.USER:
            self._render_user(message)
        elif message.role == MessageRole.ASSISTANT:
            self._render_assistant(message)
        elif message.role == MessageRole.TOOL:
            self._render_tool_result(message)
        elif message.role == MessageRole.SYSTEM:
            self._render_system(message)

    def _render_user(self, message: DisplayMessage) -> None:
        """Render a user message."""
        text = Text()
        text.append("You", style=f"bold {self.theme.scheme.primary}")
        text.append("\n")
        text.append(message.content, style=self.theme.scheme.user_message)
        self.console.print(text)
        self.console.print()

    def _render_assistant(self, message: DisplayMessage) -> None:
        """Render an assistant message with markdown support."""
        label = Text()
        label.append("Claude", style=f"bold {self.theme.scheme.accent}")
        self.console.print(label)

        if message.content:
            md = Markdown(message.content, code_theme=self.theme.code_theme)
            self.console.print(md)
        self.console.print()

    def _render_tool_result(self, message: DisplayMessage) -> None:
        """Render a tool call or result inline."""
        name = message.tool_name or "tool"
        color = self.theme.get_tool_style(name)

        if message.tool_params is not None:
            # This is a tool invocation
            param_text = self._format_params(message.tool_params)
            content = Text()
            content.append(f"⚙ {name}\n", style=f"bold {color}")
            content.append(param_text, style="dim")
        else:
            # This is a tool result
            icon = "✗" if message.is_error else "✓"
            content = Text()
            content.append(f"{icon} {name}\n", style=f"bold {color}")
            display = message.content
            if len(display) > 500:
                display = display[:500] + f"\n... ({len(message.content)} chars total)"
            content.append(display, style="red" if message.is_error else "dim")

        panel = Panel(
            content,
            border_style=color,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def _render_system(self, message: DisplayMessage) -> None:
        """Render a system message."""
        text = Text(message.content, style="dim italic")
        self.console.print(text)

    def start_stream(self) -> None:
        """Prepare for streaming assistant output."""
        self._stream_buffer = ""
        label = Text()
        label.append("Claude", style=f"bold {self.theme.scheme.accent}")
        self.console.print(label)

    def append_stream(self, chunk: str) -> None:
        """Append a chunk of streaming text.

        Args:
            chunk: Text chunk to append.
        """
        self._stream_buffer += chunk
        # Print raw text incrementally; final render happens in end_stream
        self.console.print(chunk, end="", highlight=False)

    def end_stream(self) -> None:
        """Finalize streaming display."""
        self.console.print()  # newline after stream
        self._stream_buffer = ""

    def display_tool_call(self, name: str, params: dict[str, Any]) -> None:
        """Display a tool invocation.

        Args:
            name: Tool name.
            params: Tool parameters.
        """
        msg = DisplayMessage(
            role=MessageRole.TOOL,
            content="",
            tool_name=name,
            tool_params=params,
        )
        self._render_tool_result(msg)

    def display_tool_result(
        self,
        name: str,
        result: str,
        is_error: bool = False,
    ) -> None:
        """Display a tool result.

        Args:
            name: Tool name.
            result: Result text.
            is_error: Whether this is an error result.
        """
        msg = DisplayMessage(
            role=MessageRole.TOOL,
            content=result,
            tool_name=name,
            is_error=is_error,
        )
        self._render_tool_result(msg)

    def _format_params(self, params: dict[str, Any]) -> str:
        """Format tool parameters for display.

        Args:
            params: Parameters dictionary.

        Returns:
            Formatted string representation.
        """
        lines: list[str] = []
        for key, value in params.items():
            val_str = str(value)
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            lines.append(f"  {key}: {val_str}")
        return "\n".join(lines) if lines else "  (no parameters)"
