"""Allow/deny rule engine for tool permissions.

Rules are loaded from settings files (``permissions.allow`` and
``permissions.deny`` lists) or added programmatically.  Each rule
specifies a tool name (optionally with a glob-constrained parameter hint)
and an action (``allow`` or ``deny``).

Rule syntax examples::

    "Read"                  # All Read calls
    "Bash(*)"               # All Bash calls
    "Bash(npm test)"        # Bash with command starting with "npm test"
    "Bash(npm *)"           # Bash with command matching "npm *" glob
    "Write(/tmp/**)"        # Write to paths matching glob
    "WebFetch(example.com)" # WebFetch with URL containing "example.com"
"""

from __future__ import annotations

import fnmatch
import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from claude_code.utils.logging import get_logger

logger = get_logger("permissions.rules")

# ---------------------------------------------------------------------------
# Primary parameter — the single param a parenthesised hint is matched
# against when no explicit key is given.
# ---------------------------------------------------------------------------

_PRIMARY_PARAM: dict[str, str] = {
    "Bash": "command",
    "Read": "file_path",
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    "LS": "path",
    "WebFetch": "url",
    "WebSearch": "query",
    "NotebookEdit": "notebook_path",
    "Monitor": "command",
}

# Rule string pattern:  ToolName  or  ToolName(pattern)
_RULE_RE = re.compile(
    r"^(?P<tool>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:\((?P<hint>.*)\))?$"
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class RuleAction(str, Enum):
    """Outcome of a matched rule."""

    ALLOW = "allow"
    DENY = "deny"


class PermissionRule(BaseModel):
    """A single allow or deny rule.

    Attributes:
        tool_pattern: Tool name or ``ToolName(glob)`` string.
        action: Whether this rule allows or denies the call.
        tool_name_glob: Extracted tool name (may itself be a glob, e.g. ``*``).
        param_hint: Glob applied to the tool's primary parameter (``None``
            means "match all").
        params_pattern: Optional dict of ``{key: glob}`` for multi-key
            matching.  Takes precedence over *param_hint* when provided.
    """

    tool_pattern: str
    action: RuleAction
    tool_name_glob: str = "*"
    param_hint: Optional[str] = None
    params_pattern: Optional[dict[str, str]] = None

    @classmethod
    def parse(cls, raw: str, action: RuleAction) -> PermissionRule:
        """Parse a rule string like ``"Bash(npm *)"``.

        Args:
            raw: The rule string.
            action: Whether the rule allows or denies.

        Returns:
            A new :class:`PermissionRule`.

        Raises:
            ValueError: If *raw* cannot be parsed.
        """
        m = _RULE_RE.match(raw.strip())
        if not m:
            raise ValueError(f"Invalid permission rule: {raw!r}")

        tool_glob = m.group("tool")
        hint = m.group("hint")  # None when no parentheses

        return cls(
            tool_pattern=raw.strip(),
            action=action,
            tool_name_glob=tool_glob,
            param_hint=hint,
        )


# ---------------------------------------------------------------------------
# Rule engine
# ---------------------------------------------------------------------------

class RuleEngine:
    """Ordered list of allow/deny rules evaluated first-match-wins.

    Rules are checked in insertion order.  The first rule whose tool
    pattern and parameter hint match the call wins; its action is
    returned.  If no rule matches, the engine returns ``"ask"`` so the
    next permission layer can decide.
    """

    def __init__(self) -> None:
        self._rules: list[PermissionRule] = []

    # -- mutation ----------------------------------------------------------

    def add_rule(self, rule: PermissionRule) -> None:
        """Append a rule to the end of the list."""
        self._rules.append(rule)
        logger.debug("Rule added: %s → %s", rule.tool_pattern, rule.action.value)

    def remove_rule(self, index: int) -> None:
        """Remove a rule by its zero-based index.

        Raises:
            IndexError: If *index* is out of range.
        """
        removed = self._rules.pop(index)
        logger.debug("Rule removed [%d]: %s", index, removed.tool_pattern)

    def clear(self) -> None:
        """Remove all rules."""
        self._rules.clear()

    @property
    def rules(self) -> list[PermissionRule]:
        """Read-only view of the current rule list."""
        return list(self._rules)

    # -- loading -----------------------------------------------------------

    def load_from_settings(self, settings: dict[str, Any]) -> None:
        """Bulk-load rules from a settings dict.

        Expected keys::

            {
                "permissions": {
                    "allow": ["Read", "Bash(npm test)"],
                    "deny": ["Bash(rm -rf *)"]
                }
            }

        Existing rules are **not** cleared — call :meth:`clear` first if
        a full replacement is desired.

        Args:
            settings: Parsed settings.json content.
        """
        perms = settings.get("permissions", {})
        if not isinstance(perms, dict):
            return

        for raw in perms.get("allow", []):
            if isinstance(raw, str):
                try:
                    self.add_rule(PermissionRule.parse(raw, RuleAction.ALLOW))
                except ValueError as exc:
                    logger.warning("Skipping invalid allow rule: %s", exc)

        for raw in perms.get("deny", []):
            if isinstance(raw, str):
                try:
                    self.add_rule(PermissionRule.parse(raw, RuleAction.DENY))
                except ValueError as exc:
                    logger.warning("Skipping invalid deny rule: %s", exc)

    # -- matching ----------------------------------------------------------

    def check(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> str:
        """Evaluate rules against a tool call.

        Args:
            tool_name: The tool being called (e.g. ``"Bash"``).
            params: The call parameters dict.

        Returns:
            ``"allow"`` if a matching allow rule fires, ``"deny"`` if a
            matching deny rule fires, or ``"ask"`` if no rule matches.
        """
        params = params or {}

        for rule in self._rules:
            if not _tool_matches(rule, tool_name):
                continue
            if not _params_match(rule, tool_name, params):
                continue
            # First match wins.
            return rule.action.value

        return "ask"

    # -- helpers -----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._rules)

    def __repr__(self) -> str:
        return f"<RuleEngine rules={len(self._rules)}>"


# ---------------------------------------------------------------------------
# Internal match helpers
# ---------------------------------------------------------------------------

def _tool_matches(rule: PermissionRule, tool_name: str) -> bool:
    """Return ``True`` if *rule*'s tool glob matches *tool_name*."""
    return fnmatch.fnmatch(tool_name, rule.tool_name_glob)


def _params_match(
    rule: PermissionRule,
    tool_name: str,
    params: dict[str, Any],
) -> bool:
    """Return ``True`` if *rule*'s parameter constraints match *params*."""
    # No constraint → match everything.
    if rule.param_hint is None and rule.params_pattern is None:
        return True

    # Explicit multi-key pattern takes precedence.
    if rule.params_pattern is not None:
        for key, glob_pat in rule.params_pattern.items():
            value = params.get(key, "")
            if not fnmatch.fnmatch(str(value), glob_pat):
                return False
        return True

    # Single param_hint — match against the tool's primary parameter.
    if rule.param_hint is not None:
        primary_key = _PRIMARY_PARAM.get(tool_name)
        if primary_key is None:
            # No known primary param — fall back to matching all string
            # values joined by space.
            haystack = " ".join(str(v) for v in params.values())
        else:
            haystack = str(params.get(primary_key, ""))
        return fnmatch.fnmatch(haystack, rule.param_hint)

    return True
