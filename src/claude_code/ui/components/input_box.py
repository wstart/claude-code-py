"""Input box component using prompt_toolkit.

Provides multi-line input with history, tab completion for slash
commands, and keyboard shortcuts for the interactive terminal.
"""

from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import Completer, Completion, WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PTStyle

# Default slash commands for tab completion
_SLASH_COMMANDS = [
    "/help",
    "/clear",
    "/compact",
    "/cost",
    "/config",
    "/model",
    "/status",
    "/memory",
    "/vim",
    "/review",
    "/doctor",
    "/login",
    "/logout",
    "/resume",
]


class SlashCommandCompleter(Completer):
    """Tab completer that suggests slash commands.

    Only activates when the input starts with '/'.
    """

    def __init__(self, commands: Optional[list[str]] = None) -> None:
        """Initialize with available commands.

        Args:
            commands: List of slash command strings. Defaults to built-in set.
        """
        self._commands = commands or _SLASH_COMMANDS

    def get_completions(
        self,
        document: Document,
        complete_event: object,
    ):
        """Yield completions for the current input.

        Args:
            document: The current document state.
            complete_event: The completion event.

        Yields:
            Completion objects for matching commands.
        """
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        for cmd in self._commands:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text))


def _build_keybindings(on_submit: Optional[callable] = None) -> KeyBindings:
    """Build key bindings for the input box.

    Args:
        on_submit: Optional callback when Enter is pressed.

    Returns:
        KeyBindings instance.
    """
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event):
        """Submit input on Enter."""
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event):
        """Insert newline on Escape+Enter (multiline)."""
        event.current_buffer.insert_text("\n")

    @bindings.add("c-j")
    def _newline2(event):
        """Insert newline on Ctrl+J (alternative multiline)."""
        event.current_buffer.insert_text("\n")

    @bindings.add("c-c")
    def _cancel(event):
        """Cancel input on Ctrl+C."""
        event.current_buffer.reset()
        if on_submit:
            on_submit(None)

    return bindings


def _build_style() -> PTStyle:
    """Build the prompt_toolkit style."""
    return PTStyle([
        ("prompt", "bold #6cb6ff"),
        ("placeholder", "italic #484f58"),
    ])


class InputBox:
    """Multi-line input box with history and completion.

    Uses prompt_toolkit for rich input handling including
    multi-line editing, history navigation, and slash command
    tab completion.

    Attributes:
        history: The input history for up/down navigation.
    """

    def __init__(
        self,
        placeholder: str = "Ask Claude anything...",
        commands: Optional[list[str]] = None,
    ) -> None:
        """Initialize the input box.

        Args:
            placeholder: Placeholder text shown when input is empty.
            commands: Optional custom slash commands for completion.
        """
        self.history = InMemoryHistory()
        self._placeholder = placeholder
        self._completer = SlashCommandCompleter(commands)
        self._style = _build_style()
        self._keybindings = _build_keybindings()

    def get_input(self) -> Optional[str]:
        """Prompt for user input.

        Blocks until the user submits input (Enter) or cancels (Ctrl+C).

        Returns:
            The input string, or None if cancelled.
        """
        session: PromptSession[str] = PromptSession(
            history=self.history,
            completer=self._completer,
            auto_suggest=AutoSuggestFromHistory(),
            multiline=True,
            key_bindings=self._keybindings,
            style=self._style,
            prompt_continuation=lambda width, line, is_soft: "." * 3,
        )

        try:
            result = session.prompt(
                HTML("<prompt>> </prompt>"),
                placeholder=HTML(f"<placeholder>{self._placeholder}</placeholder>"),
            )
            text = result.strip()
            return text if text else None
        except (KeyboardInterrupt, EOFError):
            return None

    async def get_input_async(self) -> Optional[str]:
        """Async version of get_input.

        Returns:
            The input string, or None if cancelled.
        """
        session: PromptSession[str] = PromptSession(
            history=self.history,
            completer=self._completer,
            auto_suggest=AutoSuggestFromHistory(),
            multiline=True,
            key_bindings=self._keybindings,
            style=self._style,
            prompt_continuation=lambda width, line, is_soft: "." * 3,
        )

        try:
            result = await session.prompt_async(
                HTML("<prompt>> </prompt>"),
                placeholder=HTML(f"<placeholder>{self._placeholder}</placeholder>"),
            )
            text = result.strip()
            return text if text else None
        except (KeyboardInterrupt, EOFError):
            return None
