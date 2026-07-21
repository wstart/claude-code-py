"""Centralized reactive state store (Zustand-like pattern)."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from claude_code.core.message import Conversation, CostInfo


@dataclass
class PermissionRule:
    """A permission rule entry."""

    tool: str
    pattern: str = "*"
    allow: bool = True


@dataclass
class TodoItem:
    """A task/todo item for tracking progress."""

    id: str
    subject: str
    status: str = "pending"  # pending | in_progress | completed
    description: str = ""


@dataclass
class AppState:
    """The full application state."""

    # Conversation state
    messages: list[dict[str, Any]] = field(default_factory=list)
    current_session: Conversation | None = None

    # Configuration
    config: dict[str, Any] = field(default_factory=dict)

    # Task tracking
    todos: list[TodoItem] = field(default_factory=list)

    # Permissions
    permission_rules: list[PermissionRule] = field(default_factory=list)

    # Cost tracking
    cost_info: CostInfo = field(default_factory=CostInfo)

    # Working directory
    working_directory: str = ""

    # UI state
    is_streaming: bool = False
    current_tool: str | None = None

    # Session metadata
    session_id: str = ""
    model: str = ""


# Callback type for state subscribers
Subscriber = Callable[["AppState", dict[str, Any] | None], None]


class Store:
    """Thread-safe reactive state store.

    Provides get/update/subscribe semantics similar to Zustand.
    All mutations are protected by an asyncio lock.
    """

    def __init__(self, initial_state: AppState | None = None) -> None:
        self._state: AppState = initial_state or AppState()
        self._subscribers: list[Subscriber] = []
        self._lock = asyncio.Lock()

    def get_state(self) -> AppState:
        """Get a snapshot of the current state.

        Returns:
            A deep copy of the current AppState.
        """
        return copy.deepcopy(self._state)

    def get(self, attr: str, default: Any = None) -> Any:
        """Get a single state attribute.

        Args:
            attr: The attribute name.
            default: Value to return if attribute doesn't exist.

        Returns:
            The attribute value or default.
        """
        return getattr(self._state, attr, default)

    async def update_state(self, updates: dict[str, Any]) -> None:
        """Update multiple state fields atomically.

        Notifies all subscribers after the update completes.

        Args:
            updates: Dict of attribute names to new values.
        """
        async with self._lock:
            for key, value in updates.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
            # Notify outside the critical section isn't needed here
            # since we hold the lock for the full update.
            self._notify(updates)

    def update_state_sync(self, updates: dict[str, Any]) -> None:
        """Synchronous version of update_state for non-async contexts.

        Args:
            updates: Dict of attribute names to new values.
        """
        for key, value in updates.items():
            if hasattr(self._state, key):
                setattr(self._state, key, value)
        self._notify(updates)

    def subscribe(self, callback: Subscriber) -> Callable[[], None]:
        """Register a subscriber to be notified on state changes.

        Args:
            callback: Function called with (new_state, updates_dict) on change.

        Returns:
            An unsubscribe function.
        """
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    async def reset(self) -> None:
        """Reset the store to its initial state."""
        async with self._lock:
            self._state = AppState()
            self._notify(None)

    def reset_sync(self) -> None:
        """Synchronous version of reset."""
        self._state = AppState()
        self._notify(None)

    def _notify(self, updates: dict[str, Any] | None) -> None:
        """Notify all subscribers of a state change.

        Args:
            updates: The dict of changes, or None for a full reset.
        """
        state_snapshot = self.get_state()
        for callback in self._subscribers:
            try:
                callback(state_snapshot, updates)
            except Exception:
                # Don't let a broken subscriber crash the store
                pass


# Singleton store instance
_global_store: Store | None = None


def get_store() -> Store:
    """Get or create the global store singleton.

    Returns:
        The application-wide Store instance.
    """
    global _global_store
    if _global_store is None:
        _global_store = Store()
    return _global_store
