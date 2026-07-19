"""Permission system for Claude Code Py.

Controls whether tool calls are allowed, denied, or require user
confirmation before execution.  The system is built on four layers:

1. **Configuration rules** — allow/deny patterns from settings files.
2. **Permission mode** — global stance (manual / auto / plan / bypass).
3. **Interactive prompt** — ask the user when layers 1-2 are inconclusive.
4. **Sandbox enforcement** — OS-level restrictions for shell commands.

Public API
----------

- :class:`PermissionManager` — main entry point for permission checks.
- :class:`PermissionDecision` — result of a single check.
- :class:`PermissionMode` — mode enum (manual / auto / plan / bypass).
- :class:`ToolRisk` — risk level enum (safe / caution / dangerous).
- :class:`RuleEngine` — pattern-based allow/deny rule matching.
- :class:`PermissionRule` — a single rule model.
- :class:`PermissionPrompt` — interactive confirmation UI.
- :class:`SandboxRunner` — OS-level command sandboxing.
- :class:`SandboxConfig` — sandbox configuration.
"""

from claude_code.permissions.manager import PermissionDecision, PermissionManager
from claude_code.permissions.modes import (
    PermissionMode,
    ToolRisk,
    get_risk_level,
    is_write_tool,
    needs_permission,
)
from claude_code.permissions.prompt import (
    PermissionPrompt,
    PromptChoice,
    PromptResult,
)
from claude_code.permissions.rules import (
    PermissionRule,
    RuleAction,
    RuleEngine,
)
from claude_code.permissions.sandbox import (
    SandboxConfig,
    SandboxRunner,
    create_bwrap_args,
    create_seatbelt_profile,
    is_sandbox_available,
)

__all__ = [
    # Manager
    "PermissionManager",
    "PermissionDecision",
    # Modes
    "PermissionMode",
    "ToolRisk",
    "get_risk_level",
    "is_write_tool",
    "needs_permission",
    # Rules
    "RuleEngine",
    "PermissionRule",
    "RuleAction",
    # Prompt
    "PermissionPrompt",
    "PromptChoice",
    "PromptResult",
    # Sandbox
    "SandboxRunner",
    "SandboxConfig",
    "create_seatbelt_profile",
    "create_bwrap_args",
    "is_sandbox_available",
]
