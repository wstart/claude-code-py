"""Base classes for the tool system.

All tools inherit from :class:`Tool` and return :class:`ToolResult`.
A :class:`ToolContext` carries shared state (read-file tracking, cwd, etc.).
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class ToolResult(BaseModel):
    """Result returned by every tool execution.

    Attributes:
        content: Either a plain string or a list of content blocks
                 (e.g. ``[{"type": "text", "text": "..."}]``).
        is_error: When ``True`` the content describes a failure.
    """

    content: str | list[dict[str, Any]]
    is_error: bool = False

    @classmethod
    def error(cls, message: str) -> ToolResult:
        """Convenience constructor for error results."""
        return cls(content=message, is_error=True)

    @classmethod
    def success(cls, message: str) -> ToolResult:
        """Convenience constructor for success results."""
        return cls(content=message, is_error=False)


@dataclass
class ToolContext:
    """Shared mutable state passed to every tool.

    The context tracks which files have been read (required before
    write/edit), the current working directory, and arbitrary metadata
    that tools may need.
    """

    read_files: set[str] = field(default_factory=set)
    cwd: str = field(default_factory=lambda: os.getcwd())
    working_directory: str = field(default_factory=lambda: os.getcwd())
    allowed_directories: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- helpers -----------------------------------------------------------

    def mark_read(self, path: str) -> None:
        """Record that *path* has been read (canonicalised)."""
        self.read_files.add(str(Path(path).resolve()))

    def has_read(self, path: str) -> bool:
        """Return whether *path* was previously read."""
        return str(Path(path).resolve()) in self.read_files


class Tool(ABC):
    """Abstract base for every tool.

    Subclasses must set :attr:`name`, :attr:`description`, and
    :attr:`input_schema`, then implement :meth:`execute`.
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    # Category hint for UI / permission grouping.
    category: str = "general"

    # Read-only tools are safe to run concurrently; mutating tools are
    # serialized by the query engine to avoid write races.
    read_only: bool = False

    def __init__(self, context: ToolContext | None = None) -> None:
        self.context = context or ToolContext()

    # -- abstract ----------------------------------------------------------

    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool with already-validated parameters."""
        ...

    # -- helpers -----------------------------------------------------------

    def get_definition(self) -> dict[str, Any]:
        """Return the JSON object sent to the LLM API as a tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Tool {self.name}>"
