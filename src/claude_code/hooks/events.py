"""Hook event types, payloads, decisions, and configuration models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class HookEvent(StrEnum):
    """Events that hooks can subscribe to (mirrors Claude Code's set)."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    NOTIFICATION = "Notification"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    SUBAGENT_STOP = "SubagentStop"
    PRE_COMPACT = "PreCompact"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"


class HookPayload(BaseModel):
    """Payload passed to hook handlers when an event fires.

    Attributes:
        event: The event type that triggered this hook.
        session_id: Current session identifier.
        working_directory: The working directory at event time.
        tool_name: Name of the tool (for tool-related events).
        tool_input: Input parameters passed to the tool.
        tool_output: Output from the tool (post-tool-use only).
        is_error: Whether the tool returned an error.
        user_input: Raw user input text (for UserPromptSubmit).
        metadata: Arbitrary extra data for the hook.
    """

    event: HookEvent
    session_id: str = ""
    working_directory: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = Field(default_factory=dict)
    tool_output: str = ""
    is_error: bool = False
    user_input: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class HookDecision(StrEnum):
    """Decision returned by a hook handler."""

    ALLOW = "allow"       # Proceed normally
    DENY = "deny"         # Block the action
    MODIFY = "modify"     # Use modified params
    SKIP = "skip"         # Skip silently


class HookResult(BaseModel):
    """Result from a hook handler.

    Attributes:
        decision: What action to take.
        reason: Human-readable explanation.
        modified_input: Replacement tool input (only when decision is MODIFY).
        suppress_output: When True, hide the tool output from the user.
    """

    decision: HookDecision = HookDecision.ALLOW
    reason: str = ""
    modified_input: dict[str, Any] | None = None
    suppress_output: bool = False


class HookEntry(BaseModel):
    """A single hook registration entry (from settings.json).

    Attributes:
        command: Shell command or script path to execute.
        matcher: Optional glob matched against the tool name (empty = all).
        priority: Higher priority hooks run first (default 0).
        timeout: Per-hook timeout in seconds.
        enabled: Whether this hook is active.
    """

    command: str
    matcher: str = ""
    priority: int = 0
    timeout: float = 30.0
    enabled: bool = True


class HookConfig(BaseModel):
    """Top-level hooks configuration, matching the settings.json ``hooks`` key.

    The mapping is event name -> list of hook entries. Two settings.json
    shapes are accepted:

    Flat::

        {"PreToolUse": [{"command": "check-safety.sh", "priority": 10}]}

    Nested (the format Claude Code writes)::

        {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "check-safety.sh"}
            ]}
        ]}
    """

    hooks: dict[str, list[HookEntry]] = Field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> HookConfig:
        """Build a HookConfig from the raw ``hooks`` dict in settings.json.

        Accepts both the flat and the nested (matcher + inner hooks) shapes.
        Malformed entries are skipped rather than raising.

        Args:
            settings: The ``hooks`` section of settings.json.

        Returns:
            A validated HookConfig instance.
        """
        entries: dict[str, list[HookEntry]] = {}
        for event_name, hook_list in settings.items():
            if not isinstance(hook_list, list):
                continue
            parsed: list[HookEntry] = []
            for item in hook_list:
                parsed.extend(_parse_hook_item(item))
            entries[event_name] = parsed
        return cls(hooks=entries)


def _parse_hook_item(item: Any) -> list[HookEntry]:
    """Parse one settings.json hook entry (flat, nested, or string)."""
    if isinstance(item, str):
        return [HookEntry(command=item)]
    if not isinstance(item, dict):
        return []

    # Nested shape: {"matcher": "...", "hooks": [{"type": "command", ...}]}
    if isinstance(item.get("hooks"), list):
        matcher = item.get("matcher", "") or ""
        result: list[HookEntry] = []
        for inner in item["hooks"]:
            entry = _hook_entry(inner, matcher)
            if entry is not None:
                result.append(entry)
        return result

    # Flat shape: {"command": "...", ...}
    entry = _hook_entry(item, item.get("matcher", "") or "")
    return [entry] if entry is not None else []


def _hook_entry(data: Any, matcher: str) -> HookEntry | None:
    """Build a HookEntry from an inner/flat dict, or None if it has no command."""
    if isinstance(data, str):
        return HookEntry(command=data, matcher=matcher)
    if not isinstance(data, dict):
        return None
    command = data.get("command")
    if not command:
        return None
    return HookEntry(
        command=command,
        matcher=data.get("matcher", matcher) or matcher,
        priority=int(data.get("priority", 0)),
        timeout=float(data.get("timeout", 30.0)),
        enabled=bool(data.get("enabled", True)),
    )
