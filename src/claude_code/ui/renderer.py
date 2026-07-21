"""Rich-based output renderer for terminal UI.

Handles rendering of markdown, code blocks, tool calls, diffs,
and streaming text with proper syntax highlighting and formatting.
"""

import difflib
from io import StringIO

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from claude_code.ui.components.theme import ThemeConfig, get_theme


class RichRenderer:
    """Renders formatted output to the terminal using rich.

    Provides methods for rendering markdown, code, tool calls,
    diffs, errors, and streaming text.

    Attributes:
        console: The rich Console instance for output.
        theme: The current theme configuration.
    """

    def __init__(
        self,
        console: Console | None = None,
        theme: ThemeConfig | None = None,
    ) -> None:
        """Initialize the renderer.

        Args:
            console: Optional pre-configured Console. Created if not provided.
            theme: Optional theme config. Defaults to dark theme.
        """
        self.theme = theme or get_theme("dark")
        self.console = console or Console(theme=self.theme.rich_theme)
        self._stream_buffer = StringIO()
        self._stream_live: Live | None = None

    def render_markdown(self, text: str) -> None:
        """Render markdown text with syntax highlighting.

        Args:
            text: Markdown-formatted text to render.
        """
        md = Markdown(text, code_theme=self.theme.code_theme)
        self.console.print(md)

    def render_code(self, code: str, language: str = "python") -> None:
        """Render a syntax-highlighted code block.

        Args:
            code: Source code to display.
            language: Programming language for syntax highlighting.
        """
        syntax = Syntax(
            code,
            language,
            theme=self.theme.code_theme,
            line_numbers=False,
            word_wrap=True,
        )
        panel = Panel(
            syntax,
            border_style=self.theme.scheme.border,
            padding=(0, 1),
        )
        self.console.print(panel)

    def render_tool_call(self, name: str, params: dict) -> None:
        """Render a collapsed tool call display.

        Args:
            name: Tool name.
            params: Tool parameters dictionary.
        """
        color = self.theme.get_tool_color(name)

        # Build parameter summary
        param_lines: list[str] = []
        for key, value in params.items():
            val_str = str(value)
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            param_lines.append(f"  {key}: {val_str}")

        param_text = "\n".join(param_lines) if param_lines else "  (no parameters)"

        content = Text()
        content.append(f"⚙ {name}\n", style=f"bold {color}")
        content.append(param_text, style="dim")

        panel = Panel(
            content,
            border_style=color,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def render_tool_result(
        self,
        name: str,
        result: str,
        is_error: bool = False,
    ) -> None:
        """Render a tool result display.

        Args:
            name: Tool name.
            result: Result text from the tool.
            is_error: Whether the result is an error.
        """
        color = self.theme.scheme.error if is_error else self.theme.get_tool_color(name)
        icon = "✗" if is_error else "✓"
        title_style = f"bold {color}"

        # Truncate very long results
        max_length = 500
        truncated = False
        display_result = result
        if len(result) > max_length:
            display_result = result[:max_length] + "\n..."
            truncated = True

        content = Text()
        content.append(f"{icon} {name}", style=title_style)
        content.append("\n")
        content.append(display_result, style="dim" if not is_error else "red")
        if truncated:
            content.append(
                f"\n  ({len(result)} chars total, truncated)",
                style="dim italic",
            )

        panel = Panel(
            content,
            border_style=color,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def render_diff(self, old: str, new: str, context_lines: int = 3) -> None:
        """Render a side-by-side diff view.

        Args:
            old: Original text.
            new: Modified text.
            context_lines: Number of context lines around changes.
        """
        old_lines = old.splitlines(keepends=True)
        new_lines = new.splitlines(keepends=True)

        diff = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile="before",
                tofile="after",
                n=context_lines,
            )
        )

        if not diff:
            self.console.print(Text("No changes.", style="dim italic"))
            return

        diff_text = Text()
        for line in diff:
            if line.startswith("+++") or line.startswith("---"):
                diff_text.append(line, style="bold")
            elif line.startswith("@@"):
                diff_text.append(line, style="cyan")
            elif line.startswith("+"):
                diff_text.append(line, style="green")
            elif line.startswith("-"):
                diff_text.append(line, style="red")
            else:
                diff_text.append(line, style="dim")

        panel = Panel(
            diff_text,
            title="Diff",
            border_style=self.theme.scheme.border,
            padding=(0, 1),
        )
        self.console.print(panel)

    def render_error(self, message: str) -> None:
        """Render an error display.

        Args:
            message: Error message to display.
        """
        content = Text()
        content.append("Error: ", style="bold red")
        content.append(message, style="red")

        panel = Panel(
            content,
            border_style="red",
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    def render_info(self, message: str) -> None:
        """Render an info display.

        Args:
            message: Info message to display.
        """
        content = Text()
        content.append("ℹ ", style="bold cyan")
        content.append(message, style="cyan")
        self.console.print(content)

    def render_table(
        self,
        headers: list[str],
        rows: list[list[str]],
        title: str | None = None,
    ) -> None:
        """Render tabular data.

        Args:
            headers: Column header strings.
            rows: List of row data (each row is a list of strings).
            title: Optional table title.
        """
        table = Table(
            title=title,
            border_style=self.theme.scheme.border,
            show_header=True,
            header_style=f"bold {self.theme.scheme.text_primary}",
        )

        for header in headers:
            table.add_column(header)

        for row in rows:
            table.add_row(*row)

        self.console.print(table)

    def render_stream_text(self, text: str) -> None:
        """Append streaming text character by character.

        Initializes a Live display on first call, appends to the
        buffer, and updates the display.

        Args:
            text: Text chunk to append (may be a single character
                  or a longer chunk).
        """
        self._stream_buffer.write(text)
        current = self._stream_buffer.getvalue()

        if self._stream_live is None:
            self._stream_live = Live(
                Markdown(current),
                console=self.console,
                refresh_per_second=12,
                vertical_overflow="visible",
            )
            self._stream_live.start()
        else:
            self._stream_live.update(Markdown(current))

    def finalize_stream(self) -> None:
        """Finalize streaming output.

        Stops the Live display and prints the final rendered markdown.
        Call this when streaming is complete.
        """
        if self._stream_live is not None:
            self._stream_live.stop()
            self._stream_live = None

        final_text = self._stream_buffer.getvalue()
        if final_text:
            # Re-render as final markdown (Live display may have been imperfect)
            self.console.print(Markdown(final_text, code_theme=self.theme.code_theme))

        self._stream_buffer = StringIO()

    def render_user_message(self, text: str) -> None:
        """Render a user message with distinctive styling.

        Args:
            text: User's input text.
        """
        content = Text()
        content.append("> ", style=f"bold {self.theme.scheme.user_cyan}")
        content.append(text, style=self.theme.scheme.text_secondary)
        self.console.print(content)

    def render_assistant_label(self) -> Text:
        """Return a styled label for assistant messages.

        Returns:
            Text object with the assistant label.
        """
        label = Text()
        label.append("Claude", style=f"bold {self.theme.scheme.aka_red}")
        label.append(" │ ", style="dim")
        return label

    def blank_line(self) -> None:
        """Print a blank line for spacing."""
        self.console.print()
