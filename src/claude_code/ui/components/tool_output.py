"""Tool output display component.

Renders collapsible, color-coded tool output sections with
truncation for long results.
"""

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme

# Maximum characters to display before truncating
_MAX_OUTPUT_LENGTH = 500


class ToolOutput:
    """Displays tool invocations and results with color coding.

    Tool outputs are color-coded by category:
    - File operations: blue
    - Shell commands: green
    - Web operations: cyan
    - Errors: red

    Long outputs are truncated with a character count indicator.

    Attributes:
        console: The rich Console for output.
        theme: The current theme configuration.
    """

    def __init__(
        self,
        console: Console,
        theme: ThemeConfig | None = None,
    ) -> None:
        """Initialize tool output display.

        Args:
            console: Rich Console instance.
            theme: Optional theme config.
        """
        self.console = console
        self.theme = theme or get_theme("dark")

    def show_call(self, name: str, params: dict[str, Any]) -> None:
        """Display a tool invocation.

        Args:
            name: Tool name.
            params: Tool parameters.
        """
        color = self.theme.get_tool_color(name)
        param_text = self._format_params(params)

        content = Text()
        content.append(f"⚙ {name}", style=f"bold {color}")
        if param_text:
            content.append("\n")
            content.append(param_text, style="dim")

        panel = Panel(
            content,
            border_style=color,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def show_result(
        self,
        name: str,
        result: str,
        is_error: bool = False,
        max_length: int = _MAX_OUTPUT_LENGTH,
    ) -> None:
        """Display a tool result.

        Args:
            name: Tool name.
            result: Result text.
            is_error: Whether the result is an error.
            max_length: Maximum characters to display before truncating.
        """
        if is_error:
            color = self.theme.scheme.error
            icon = "✗"
            text_style = "red"
        else:
            color = self.theme.get_tool_color(name)
            icon = "✓"
            text_style = "dim"

        content = Text()
        content.append(f"{icon} {name}", style=f"bold {color}")

        # Truncate if needed
        display_result = result
        truncated = False
        if len(result) > max_length:
            display_result = result[:max_length]
            truncated = True

        content.append("\n")
        content.append(display_result, style=text_style)

        if truncated:
            content.append(f"\n… ({len(result):,} chars total)", style="dim italic")

        panel = Panel(
            content,
            border_style=color,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def show_error(self, name: str, error: str) -> None:
        """Display a tool error.

        Args:
            name: Tool name.
            error: Error message.
        """
        self.show_result(name, error, is_error=True)

    def _format_params(self, params: dict[str, Any]) -> str:
        """Format tool parameters for display.

        Args:
            params: Parameters dictionary.

        Returns:
            Formatted parameter string.
        """
        lines: list[str] = []
        for key, value in params.items():
            val_str = str(value)
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            lines.append(f"  {key}: {val_str}")
        return "\n".join(lines)
