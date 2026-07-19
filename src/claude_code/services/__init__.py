"""Services — cost tracking, telemetry, auth, updates, LSP, and utilities."""

from .auth import AuthError, AuthManager
from .context_pruner import ContextPruner
from .cost_tracker import CostTracker
from .lsp_client import CompletionItem, Diagnostic, LSPClient
from .telemetry import TelemetryEvent, TelemetryManager
from .updater import UpdateChecker, UpdateInfo

__all__ = [
    "AuthError",
    "AuthManager",
    "CompletionItem",
    "ContextPruner",
    "CostTracker",
    "Diagnostic",
    "LSPClient",
    "TelemetryEvent",
    "TelemetryManager",
    "UpdateChecker",
    "UpdateInfo",
]
