"""Base class for slash commands.

All built-in and plugin-provided slash commands inherit from
:class:`SlashCommand` and implement :meth:`execute`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandContext:
    """Runtime context passed to every command execution.

    Attributes:
        app: The :class:`ClaudeApp` instance.
        session: Current session metadata.
        config: Application configuration.
        ui: The terminal UI interface (may be ``None`` in headless mode).
        tool_registry: The active tool registry.
        query_engine: The active query engine.
        metadata: Arbitrary extra data.
    """

    app: Any = None
    session: Any = None
    config: Any = None
    ui: Any = None
    tool_registry: Any = None
    query_engine: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


class SlashCommand(ABC):
    """Base class for slash commands.

    Subclasses must set :attr:`name` and :attr:`description`, and
    implement :meth:`execute`.

    Attributes:
        name: Command name without the leading ``/`` (e.g. ``"help"``).
        description: One-line help text shown in ``/help``.
        aliases: Alternative names that also invoke this command.
    """

    name: str = ""
    description: str = ""
    aliases: list[str] = []

    @abstractmethod
    async def execute(self, args: str, context: CommandContext) -> str:
        """Run the command.

        Args:
            args: Everything after the command name (already stripped).
            context: Runtime :class:`CommandContext`.

        Returns:
            A string message to display to the user.
        """
        ...

    def get_help(self) -> str:
        """Return detailed help text for this command.

        Override to provide usage examples or extended descriptions.

        Returns:
            Multi-line help string.
        """
        alias_str = ""
        if self.aliases:
            alias_str = f" (aliases: {', '.join(self.aliases)})"
        return f"/{self.name}{alias_str} — {self.description}"

    def __repr__(self) -> str:
        return f"<SlashCommand /{self.name}>"
