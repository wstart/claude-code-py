"""Permission mode definitions and tool risk classification.

Each tool call is assigned a :class:`ToolRisk` level.  Combined with the
active :class:`PermissionMode`, the system decides whether the call needs
interactive confirmation before execution.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PermissionMode(str, Enum):
    """How the system handles tool-call permissions."""

    MANUAL = "manual"       # Ask user for every tool call
    AUTO = "auto"           # Auto-approve safe operations, ask for dangerous
    PLAN = "plan"           # Read-only mode, no write/execute allowed
    BYPASS = "bypass"       # Skip all permission checks (DANGEROUS)


class ToolRisk(str, Enum):
    """Risk classification for a single tool call."""

    SAFE = "safe"           # Read-only (Read, Glob, Grep, LS)
    CAUTION = "caution"     # File writes (Write, Edit, MultiEdit)
    DANGEROUS = "dangerous"  # Shell / network (Bash, WebFetch, NotebookEdit)


# ---------------------------------------------------------------------------
# Tool → risk mapping
# ---------------------------------------------------------------------------

# Default mapping.  Unknown tools are treated as DANGEROUS so that nothing
# sneaks through without explicit approval.
_TOOL_RISK_MAP: dict[str, ToolRisk] = {
    # Safe — read-only
    "Read": ToolRisk.SAFE,
    "Glob": ToolRisk.SAFE,
    "Grep": ToolRisk.SAFE,
    "LS": ToolRisk.SAFE,
    "ListDirectory": ToolRisk.SAFE,
    "NotebookRead": ToolRisk.SAFE,
    "WebSearch": ToolRisk.SAFE,
    "ListDocTemplates": ToolRisk.SAFE,
    "SearchDocuments": ToolRisk.SAFE,
    "GetDocumentContent": ToolRisk.SAFE,
    "GetDocumentInfo": ToolRisk.SAFE,

    # Caution — file writes
    "Write": ToolRisk.CAUTION,
    "Edit": ToolRisk.CAUTION,
    "MultiEdit": ToolRisk.CAUTION,
    "NotebookEdit": ToolRisk.CAUTION,
    "CreateDocument": ToolRisk.CAUTION,
    "UpdateDocument": ToolRisk.CAUTION,
    "DeleteDocument": ToolRisk.CAUTION,
    "CopyDocument": ToolRisk.CAUTION,
    "MoveDocument": ToolRisk.CAUTION,
    "RenameDocument": ToolRisk.CAUTION,
    "CreateFolder": ToolRisk.CAUTION,
    "CreateFile": ToolRisk.CAUTION,

    # Dangerous — arbitrary code execution / external side-effects
    "Bash": ToolRisk.DANGEROUS,
    "WebFetch": ToolRisk.DANGEROUS,
    "Monitor": ToolRisk.DANGEROUS,
    "CronCreate": ToolRisk.DANGEROUS,
    "CronDelete": ToolRisk.DANGEROUS,
    "Agent": ToolRisk.DANGEROUS,
    "SubmitExportJob": ToolRisk.DANGEROUS,
}


def get_risk_level(tool_name: str) -> ToolRisk:
    """Return the risk level for *tool_name*.

    Unknown tools default to :attr:`ToolRisk.DANGEROUS`.

    Args:
        tool_name: The registered name of the tool (e.g. ``"Bash"``).
    """
    return _TOOL_RISK_MAP.get(tool_name, ToolRisk.DANGEROUS)


def is_write_tool(tool_name: str) -> bool:
    """Return ``True`` if the tool mutates files or external state."""
    return get_risk_level(tool_name) in (ToolRisk.CAUTION, ToolRisk.DANGEROUS)


def needs_permission(
    mode: PermissionMode,
    risk_level: ToolRisk,
) -> bool:
    """Decide whether a tool call needs interactive confirmation.

    Args:
        mode: The active permission mode.
        risk_level: The risk level of the tool being called.

    Returns:
        ``True`` when the user **must** be prompted, ``False`` when the
        call can proceed automatically.
    """
    if mode == PermissionMode.BYPASS:
        return False

    if mode == PermissionMode.PLAN:
        # In plan mode every non-safe tool is *denied* (not asked).
        # The manager handles denial separately; here we signal "needs gate".
        return risk_level != ToolRisk.SAFE

    if mode == PermissionMode.AUTO:
        # Auto-approve safe, ask for everything else.
        return risk_level != ToolRisk.SAFE

    # MANUAL — ask for everything.
    return True
