"""Hooks system — event-driven hook execution for tool lifecycle events."""

from .events import (
    HookConfig,
    HookDecision,
    HookEntry,
    HookEvent,
    HookPayload,
    HookResult,
)
from .executor import HookExecutor
from .manager import HookManager

__all__ = [
    "HookConfig",
    "HookDecision",
    "HookEntry",
    "HookEvent",
    "HookExecutor",
    "HookManager",
    "HookPayload",
    "HookResult",
]
