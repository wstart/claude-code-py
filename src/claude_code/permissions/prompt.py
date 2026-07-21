"""Interactive permission confirmation prompt.

Displays a formatted prompt asking the user to approve or deny a tool
call, with options to always-allow or deny-all for the session.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from claude_code.permissions.modes import ToolRisk
from claude_code.utils.logging import get_logger
from claude_code.utils.platform import is_tty

logger = get_logger("permissions.prompt")

# ANSI colour per risk level.
_RISK_COLORS: dict[ToolRisk, str] = {
    ToolRisk.SAFE: "green",
    ToolRisk.CAUTION: "yellow",
    ToolRisk.DANGEROUS: "red",
}

_RISK_LABELS: dict[ToolRisk, str] = {
    ToolRisk.SAFE: "SAFE",
    ToolRisk.CAUTION: "CAUTION",
    ToolRisk.DANGEROUS: "DANGEROUS",
}


class PromptChoice(StrEnum):
    """User's response to a permission prompt."""

    YES = "yes"               # Allow this one call
    NO = "no"                 # Deny this one call
    ALWAYS_ALLOW = "always"   # Allow this tool+pattern for the session
    DENY_ALL = "deny_all"     # Deny this tool for the session


@dataclass
class PromptResult:
    """Outcome of a permission prompt.

    Attributes:
        choice: The user's selection.
        add_rule: If the user chose ALWAYS_ALLOW or DENY_ALL, this
            contains a rule string (e.g. ``"Bash(npm test)"``) to be
            added to the session rules.  ``None`` otherwise.
    """

    choice: PromptChoice
    add_rule: str | None = None


@dataclass
class PermissionPrompt:
    """Interactive permission prompt using Rich.

    In non-interactive mode (no TTY), all requests are auto-denied
    unless *default_allow* is set to ``True``.

    Attributes:
        console: Rich console for output.
        default_allow: Auto-approve when no TTY is available.
        _always_allow: Session-level always-allow rule strings.
        _deny_all: Session-level deny-all rule strings.
    """

    console: Console = field(default_factory=lambda: Console(stderr=True))
    default_allow: bool = False
    _always_allow: list[str] = field(default_factory=list)
    _deny_all: list[str] = field(default_factory=list)

    # -- public API --------------------------------------------------------

    async def ask_permission(
        self,
        tool_name: str,
        params: dict[str, Any],
        risk_level: ToolRisk,
    ) -> PromptResult:
        """Prompt the user for permission to run a tool call.

        If no TTY is available the call is auto-denied (returns
        :attr:`PromptChoice.NO`) unless *default_allow* was set.

        Args:
            tool_name: Tool being called.
            params: Call parameters.
            risk_level: Risk classification.

        Returns:
            The user's decision.
        """
        # Check session-level shortcuts first.
        if self._matches_session_allow(tool_name, params):
            return PromptResult(choice=PromptChoice.YES)
        if self._matches_session_deny(tool_name):
            return PromptResult(choice=PromptChoice.NO)

        # Non-interactive fallback.
        if not is_tty():
            if self.default_allow:
                logger.debug("No TTY; auto-allowing %s", tool_name)
                return PromptResult(choice=PromptChoice.YES)
            logger.debug("No TTY; auto-denying %s", tool_name)
            return PromptResult(choice=PromptChoice.NO)

        # Display the prompt panel.
        self._render_prompt(tool_name, params, risk_level)

        # Read response in a thread to avoid blocking the event loop.
        choice = await self._read_choice()

        # Build optional session rule.
        add_rule: str | None = None
        if choice == PromptChoice.ALWAYS_ALLOW:
            rule_str = self._build_rule_string(tool_name, params)
            self._always_allow.append(rule_str)
            add_rule = rule_str
        elif choice == PromptChoice.DENY_ALL:
            rule_str = tool_name  # Deny all calls to this tool.
            self._deny_all.append(rule_str)
            add_rule = rule_str

        return PromptResult(choice=choice, add_rule=add_rule)

    # -- rendering ---------------------------------------------------------

    def _render_prompt(
        self,
        tool_name: str,
        params: dict[str, Any],
        risk_level: ToolRisk,
    ) -> None:
        """Render the permission request panel."""
        color = _RISK_COLORS.get(risk_level, "white")
        risk_label = _RISK_LABELS.get(risk_level, risk_level.value.upper())

        header = Text()
        header.append(f" {tool_name} ", style=f"bold {color}")
        header.append(f"[{risk_label}]", style=color)

        # Parameter summary
        param_lines: list[str] = []
        for key, value in params.items():
            val_str = str(value)
            if len(val_str) > 120:
                val_str = val_str[:117] + "..."
            param_lines.append(f"  {key}: {val_str}")

        param_block = "\n".join(param_lines) if param_lines else "  (no parameters)"

        # Options footer
        options = (
            "  [Y]es  [N]o  [A]lways allow  [D]eny all for session"
        )

        content = Text()
        content.append(param_block, style="")
        content.append("\n\n")
        content.append(options, style="dim")

        panel = Panel(
            content,
            title=str(header),
            border_style=color,
            padding=(0, 1),
            expand=False,
        )
        self.console.print(panel)

    async def _read_choice(self) -> PromptChoice:
        """Read and parse the user's input."""
        loop = asyncio.get_running_loop()
        response = await loop.run_in_executor(None, self._blocking_input)
        return self._parse_response(response)

    @staticmethod
    def _blocking_input() -> str:
        """Read a single line from stdin (runs in executor)."""
        try:
            return input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return "n"

    @staticmethod
    def _parse_response(response: str) -> PromptChoice:
        """Map raw user input to a :class:`PromptChoice`."""
        if response in ("y", "yes"):
            return PromptChoice.YES
        if response in ("a", "always"):
            return PromptChoice.ALWAYS_ALLOW
        if response in ("d", "deny_all", "deny all"):
            return PromptChoice.DENY_ALL
        # Default to deny for safety.
        return PromptChoice.NO

    # -- session shortcuts -------------------------------------------------

    def _build_rule_string(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> str:
        """Build a rule string like ``Bash(npm *)`` for session reuse."""
        from claude_code.permissions.rules import _PRIMARY_PARAM

        primary_key = _PRIMARY_PARAM.get(tool_name)
        if primary_key and primary_key in params:
            val = str(params[primary_key])
            # Use a loose glob: first two tokens + wildcard.
            tokens = val.split()
            if len(tokens) >= 2:
                hint = " ".join(tokens[:2]) + "*"
            else:
                hint = val + "*"
            return f"{tool_name}({hint})"
        return f"{tool_name}(*)"

    def _matches_session_allow(
        self,
        tool_name: str,
        params: dict[str, Any],
    ) -> bool:
        """Check if the call matches a session always-allow rule."""
        from claude_code.permissions.rules import PermissionRule, RuleAction

        for raw in self._always_allow:
            try:
                rule = PermissionRule.parse(raw, RuleAction.ALLOW)
            except ValueError:
                continue
            from claude_code.permissions.rules import (
                _params_match,
                _tool_matches,
            )
            if _tool_matches(rule, tool_name) and _params_match(
                rule, tool_name, params
            ):
                return True
        return False

    def _matches_session_deny(self, tool_name: str) -> bool:
        """Check if the tool is in the session deny-all list."""
        import fnmatch

        return any(
            fnmatch.fnmatch(tool_name, pat) for pat in self._deny_all
        )
