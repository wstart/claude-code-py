"""Main terminal UI application.

Combines all UI components (chat display, input box, spinner,
tool output, status bar) into a cohesive interactive terminal
application.
"""

import asyncio
import sys
from typing import Any, Optional

from rich.console import Console

from claude_code.ui.components.chat_display import ChatDisplay, DisplayMessage, MessageRole
from claude_code.ui.components.input_box import InputBox
from claude_code.ui.components.spinner import Spinner
from claude_code.ui.components.status_bar import StatusBar
from claude_code.ui.components.theme import ThemeConfig, get_theme
from claude_code.ui.components.tool_output import ToolOutput
from claude_code.ui.renderer import RichRenderer


class AppUI:
    """Main terminal UI application coordinating all components.

    Manages the layout and interaction between the chat display,
    input box, spinner, tool output, and status bar. Handles
    keyboard shortcuts and terminal resize events.

    Attributes:
        console: The rich Console for output.
        renderer: The RichRenderer for formatted output.
        chat: The ChatDisplay for conversation messages.
        input_box: The InputBox for user input.
        spinner: The Spinner for loading indicators.
        tool_output: The ToolOutput for tool results.
        status_bar: The StatusBar for session info.
        theme: The current theme configuration.
    """

    def __init__(
        self,
        theme: Optional[ThemeConfig] = None,
        console: Optional[Console] = None,
    ) -> None:
        """Initialize the UI application.

        Args:
            theme: Optional theme config. Defaults to dark theme.
            console: Optional pre-configured Console.
        """
        self.theme = theme or get_theme("dark")
        self.console = console or Console(theme=self.theme.rich_theme)

        # Initialize components
        self.renderer = RichRenderer(console=self.console, theme=self.theme)
        self.chat = ChatDisplay(console=self.console, theme=self.theme)
        self.input_box = InputBox()
        self.spinner = Spinner(console=self.console, theme=self.theme)
        self.tool_output = ToolOutput(console=self.console, theme=self.theme)
        self.status_bar = StatusBar(console=self.console, theme=self.theme)

        self._running = False

    async def run(self) -> None:
        """Start the interactive session.

        Enters the main input loop, reading user input and
        dispatching it for processing. Returns when the user
        exits (Ctrl+C or Ctrl+D).
        """
        self._running = True
        self._print_welcome()

        while self._running:
            try:
                user_input = await self.get_input()
                if user_input is None:
                    # Ctrl+C or empty input after Ctrl+D
                    self._running = False
                    break

                if user_input.lower() in ("/exit", "/quit"):
                    self._running = False
                    break

                # Yield control to allow the caller to process the input
                # The actual processing is done by ClaudeApp which calls
                # our display methods
                if self._on_input:
                    await self._on_input(user_input)

            except (KeyboardInterrupt, EOFError):
                self._running = False
                break

        self.shutdown()

    def set_input_handler(self, handler) -> None:
        """Set the callback for processing user input.

        Args:
            handler: Async callable that takes a user input string.
        """
        self._on_input = handler

    _on_input = None

    async def get_input(self) -> Optional[str]:
        """Get user input from the input box.

        Returns:
            The input string, or None if cancelled/exited.
        """
        return await self.input_box.get_input_async()

    def display_message(self, msg: DisplayMessage) -> None:
        """Display a message in the chat area.

        Args:
            msg: The DisplayMessage to show.
        """
        self.chat.display_message(msg)

    def display_user_message(self, text: str) -> None:
        """Display a user message.

        Args:
            text: User's input text.
        """
        self.chat.display_message(
            DisplayMessage(role=MessageRole.USER, content=text)
        )

    def display_assistant_message(self, text: str) -> None:
        """Display an assistant message with markdown rendering.

        Args:
            text: Assistant's response text.
        """
        self.chat.display_message(
            DisplayMessage(role=MessageRole.ASSISTANT, content=text)
        )

    def show_tool_call(self, name: str, params: dict[str, Any]) -> None:
        """Display a tool invocation.

        Args:
            name: Tool name.
            params: Tool parameters.
        """
        self.tool_output.show_call(name, params)

    def show_tool_result(
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
        self.tool_output.show_result(name, result, is_error=is_error)

    def show_error(self, msg: str) -> None:
        """Display an error message.

        Args:
            msg: Error message text.
        """
        self.renderer.render_error(msg)

    def show_info(self, msg: str) -> None:
        """Display an info message.

        Args:
            msg: Info message text.
        """
        self.renderer.render_info(msg)

    def show_stream(self, text_chunk: str) -> None:
        """Append streaming text to the current assistant response.

        Args:
            text_chunk: Text chunk to append.
        """
        if not self.spinner.is_active:
            self.chat.start_stream()
        self.chat.append_stream(text_chunk)

    def end_stream(self) -> None:
        """Finalize streaming display."""
        self.chat.end_stream()

    def show_spinner(self, text: str = "Thinking") -> None:
        """Show a loading indicator.

        Args:
            text: Action description to display.
        """
        if self.spinner.is_active:
            self.spinner.update_action(text)
        else:
            self.spinner.start(text)

    def hide_spinner(self) -> None:
        """Hide the loading indicator."""
        self.spinner.stop()

    def update_status(
        self,
        model: Optional[str] = None,
        session_id: Optional[str] = None,
        tokens_used: Optional[int] = None,
        cost_usd: Optional[float] = None,
        working_directory: Optional[str] = None,
    ) -> None:
        """Update the status bar information.

        Args:
            model: Model name.
            session_id: Session UUID.
            tokens_used: Total tokens consumed.
            cost_usd: Estimated cost in USD.
            working_directory: Current working directory.
        """
        self.status_bar.update(
            model=model,
            session_id=session_id,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            working_directory=working_directory,
        )

    def show_status(self) -> None:
        """Display the status bar."""
        self.status_bar.show()

    def shutdown(self) -> None:
        """Clean up and shut down the UI."""
        self._running = False
        self.hide_spinner()
        self.console.print()
        self.console.print(
            "[dim]Goodbye![/dim]"
        )

    def _print_welcome(self) -> None:
        """Print the welcome banner."""
        self.console.print(
            "[bold bright_cyan]claude-code-py[/bold bright_cyan] "
            "[dim]v0.1.0[/dim]"
        )
        self.console.print(
            "[dim]Type a message or /help for commands. "
            "Ctrl+C to exit.[/dim]"
        )
        self.console.print()
