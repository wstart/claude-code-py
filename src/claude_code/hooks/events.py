"""Hook event types, payloads, decisions, and configuration models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HookEvent(str, Enum):
    """Events that hooks can subscribe to."""

    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    NOTIFICATION = "Notification"
    STOP = "Stop"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
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


class HookDecision(str, Enum):
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
        priority: Higher priority hooks run first (default 0).
        timeout: Per-hook timeout in seconds.
        enabled: Whether this hook is active.
    """

    command: str
    priority: int = 0
    timeout: float = 30.0
    enabled: bool = True


class HookConfig(BaseModel):
    """Top-level hooks configuration, matching the settings.json ``hooks`` key.

    The mapping is event name -> list of hook entries.

    Example::

        {
            "PreToolUse": [
                {"command": "check-safety.sh", "priority": 10},
                {"command": "lint-check.py", "priority": 5}
            ],
            "PostToolUse": [
                {"command": "format-output.sh"}
            ]
        }
    """

    hooks: dict[str, list[HookEntry]] = Field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: dict[str, Any]) -> HookConfig:
        """Build a HookConfig from the raw ``hooks`` dict in settings.json.

        Args:
            settings: The ``hooks`` section of settings.json.

        Returns:
            A validated HookConfig instance.
        """
        entries: dict[str, list[HookEntry]] = {}
        for event_name, hook_list in settings.items():
            parsed: list[HookEntry] = []
            for item in hook_list:
                if isinstance(item, str):
                    parsed.append(HookEntry(command=item))
                elif isinstance(item, dict):
                    parsed.append(HookEntry(**item))
            entries[event_name] = parsed
        return cls(hooks=entries)
