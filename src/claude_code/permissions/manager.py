"""4-layer permission manager — the main permission orchestrator.

Layers are evaluated in order:

1. **Configuration rules** — allow/deny rules from ``settings.json``
   and session-level shortcuts (always-allow / deny-all).
2. **Permission mode** — the global mode (``manual``, ``auto``,
   ``plan``, ``bypass``) decides the default stance.
3. **Interactive prompt** — if no rule or mode decision is conclusive,
   the user is asked.
4. **Sandbox enforcement** — sandbox metadata is attached to the
   decision so that the tool executor can wrap the command.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_code.core.config import AppConfig
from claude_code.permissions.modes import (
    PermissionMode,
    ToolRisk,
    get_risk_level,
)
from claude_code.permissions.prompt import (
    PermissionPrompt,
    PromptChoice,
    PromptResult,
)
from claude_code.permissions.rules import PermissionRule, RuleAction, RuleEngine
from claude_code.permissions.sandbox import SandboxConfig, SandboxRunner
from claude_code.utils.logging import get_logger

logger = get_logger("permissions.manager")


# ---------------------------------------------------------------------------
# Decision model
# ---------------------------------------------------------------------------

@dataclass
class PermissionDecision:
    """Result of a permission check.

    Attributes:
        allowed: Whether the tool call may proceed.
        reason: Human-readable explanation of the decision.
        risk_level: The tool's risk classification.
        rule_matched: The rule string that triggered the decision
            (``None`` if no rule matched).
        sandbox_info: Sandbox wrapping details, if applicable.
    """

    allowed: bool
    reason: str
    risk_level: ToolRisk = ToolRisk.SAFE
    rule_matched: str | None = None
    sandbox_info: dict[str, Any] | None = None

    def __repr__(self) -> str:
        status = "ALLOWED" if self.allowed else "DENIED"
        return f"<PermissionDecision {status}: {self.reason}>"


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class PermissionManager:
    """4-layer permission stack.

    Instantiate once per session and call :meth:`check` (or
    :meth:`check_batch`) before each tool execution.

    Args:
        config: Application configuration (carries ``permission_mode``,
            ``dangerously_skip_permissions``, allowed/denied tool lists).
        settings: Raw settings dict (for loading rules).
        prompt: Optional pre-configured prompt instance.
        sandbox_config: Optional sandbox configuration.
    """

    def __init__(
        self,
        config: AppConfig | None = None,
        settings: dict[str, Any] | None = None,
        prompt: PermissionPrompt | None = None,
        sandbox_config: SandboxConfig | None = None,
    ) -> None:
        self._config = config or AppConfig()
        self._prompt = prompt or PermissionPrompt()
        self._sandbox = SandboxRunner(sandbox_config)

        # Resolve mode.
        self._mode = self._resolve_mode()

        # Layer 1: Rule engine.
        self._rules = RuleEngine()
        self._load_rules(settings)

        logger.debug(
            "PermissionManager initialised: mode=%s, rules=%d, sandbox=%s",
            self._mode.value,
            len(self._rules),
            self._sandbox.available,
        )

    # -- properties --------------------------------------------------------

    @property
    def mode(self) -> PermissionMode:
        """Active permission mode."""
        return self._mode

    @property
    def rules(self) -> RuleEngine:
        """The underlying rule engine."""
        return self._rules

    @property
    def sandbox(self) -> SandboxRunner:
        """The sandbox runner."""
        return self._sandbox

    # -- core API ----------------------------------------------------------

    async def check(
        self,
        tool_name: str,
        params: dict[str, Any] | None = None,
    ) -> PermissionDecision:
        """Check if a tool call is permitted.

        Evaluates the 4-layer stack and returns a decision.

        Args:
            tool_name: The tool being called.
            params: Call parameters.

        Returns:
            A :class:`PermissionDecision`.
        """
        params = params or {}
        risk = get_risk_level(tool_name)

        # Layer 1: Configuration rules (first-match wins).
        rule_result = self._rules.check(tool_name, params)
        if rule_result == "allow":
            return PermissionDecision(
                allowed=True,
                reason="Allowed by configuration rule",
                risk_level=risk,
                rule_matched=f"allow:{tool_name}",
            )
        if rule_result == "deny":
            return PermissionDecision(
                allowed=False,
                reason="Denied by configuration rule",
                risk_level=risk,
                rule_matched=f"deny:{tool_name}",
            )

        # Layer 2: Permission mode.
        decision = self._check_mode(tool_name, params, risk)
        if decision is not None:
            return decision

        # Layer 3: Interactive prompt.
        prompt_result = await self._prompt.ask_permission(
            tool_name, params, risk,
        )
        decision = self._handle_prompt_result(tool_name, params, risk, prompt_result)

        # Layer 4: Attach sandbox info.
        if decision.allowed and tool_name == "Bash":
            decision.sandbox_info = self._get_sandbox_info(params)

        return decision

    async def check_batch(
        self,
        calls: list[dict[str, Any]],
    ) -> list[PermissionDecision]:
        """Check multiple tool calls.

        Each element in *calls* should have ``"tool_name"`` and
        optionally ``"params"`` keys.

        Args:
            calls: List of tool call descriptors.

        Returns:
            A list of decisions in the same order.
        """
        decisions: list[PermissionDecision] = []
        for call in calls:
            tool_name = call.get("tool_name", call.get("name", ""))
            params = call.get("params", call.get("input", {}))
            decision = await self.check(tool_name, params)
            decisions.append(decision)
        return decisions

    # -- layer 2: mode check -----------------------------------------------

    def _check_mode(
        self,
        tool_name: str,
        params: dict[str, Any],
        risk: ToolRisk,
    ) -> PermissionDecision | None:
        """Apply the permission mode rules.

        Returns a decision if the mode is conclusive, or ``None`` if
        the prompt layer should be consulted.
        """
        mode = self._mode

        # Bypass: allow everything.
        if mode == PermissionMode.BYPASS:
            return PermissionDecision(
                allowed=True,
                reason="Permission mode is bypass",
                risk_level=risk,
            )

        # Plan mode: allow read-only, deny everything else.
        if mode == PermissionMode.PLAN:
            if risk == ToolRisk.SAFE:
                return PermissionDecision(
                    allowed=True,
                    reason=f"Plan mode: {tool_name} is read-only",
                    risk_level=risk,
                )
            return PermissionDecision(
                allowed=False,
                reason=(
                    f"Plan mode: {tool_name} is a {risk.value} operation "
                    "and is not permitted"
                ),
                risk_level=risk,
            )

        # Auto mode: allow safe, prompt for the rest.
        if mode == PermissionMode.AUTO and risk == ToolRisk.SAFE:
            return PermissionDecision(
                allowed=True,
                reason=f"Auto mode: {tool_name} is safe",
                risk_level=risk,
            )

        # Manual or auto-with-dangerous: fall through to prompt.
        return None

    # -- layer 3: prompt handling ------------------------------------------

    def _handle_prompt_result(
        self,
        tool_name: str,
        params: dict[str, Any],
        risk: ToolRisk,
        result: PromptResult,
    ) -> PermissionDecision:
        """Convert a prompt response into a decision and update rules."""
        # If the user chose to add a session rule, register it.
        if result.add_rule and result.choice == PromptChoice.ALWAYS_ALLOW:
            try:
                rule = PermissionRule.parse(result.add_rule, RuleAction.ALLOW)
                self._rules.add_rule(rule)
                logger.debug("Session allow rule added: %s", result.add_rule)
            except ValueError:
                pass

        if result.add_rule and result.choice == PromptChoice.DENY_ALL:
            try:
                rule = PermissionRule.parse(result.add_rule, RuleAction.DENY)
                self._rules.add_rule(rule)
                logger.debug("Session deny rule added: %s", result.add_rule)
            except ValueError:
                pass

        if result.choice in (PromptChoice.YES, PromptChoice.ALWAYS_ALLOW):
            decision = PermissionDecision(
                allowed=True,
                reason="Allowed by user",
                risk_level=risk,
            )
            # Attach sandbox info for shell commands.
            if tool_name == "Bash":
                decision.sandbox_info = self._get_sandbox_info(params)
            return decision

        return PermissionDecision(
            allowed=False,
            reason="Denied by user",
            risk_level=risk,
        )

    # -- layer 4: sandbox info ---------------------------------------------

    def _get_sandbox_info(
        self,
        params: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Build sandbox metadata for a Bash command."""
        if not self._sandbox.available:
            return None

        cmd = params.get("command", "")
        wrapped = self._sandbox.wrap_command(cmd)

        return {
            "backend": self._sandbox.backend,
            "wrapped_command": wrapped,
            "available": True,
        }

    # -- initialisation helpers --------------------------------------------

    def _resolve_mode(self) -> PermissionMode:
        """Determine the effective permission mode from config."""
        # dangerously_skip_permissions → bypass.
        if self._config.dangerously_skip_permissions:
            logger.warning(
                "dangerously_skip_permissions is set — using BYPASS mode"
            )
            return PermissionMode.BYPASS

        raw = self._config.permission_mode
        try:
            return PermissionMode(raw)
        except ValueError:
            logger.warning(
                "Unknown permission mode %r, falling back to MANUAL", raw,
            )
            return PermissionMode.MANUAL

    def _load_rules(self, settings: dict[str, Any] | None) -> None:
        """Load rules from settings and config tool lists."""
        # From settings.json permissions.allow / deny.
        if settings:
            self._rules.load_from_settings(settings)

        # From config's allowed_tools / denied_tools (CLI flags).
        for tool_pat in self._config.allowed_tools:
            try:
                self._rules.add_rule(
                    PermissionRule.parse(tool_pat, RuleAction.ALLOW)
                )
            except ValueError as exc:
                logger.warning("Invalid allowed_tools entry: %s", exc)

        for tool_pat in self._config.denied_tools:
            try:
                self._rules.add_rule(
                    PermissionRule.parse(tool_pat, RuleAction.DENY)
                )
            except ValueError as exc:
                logger.warning("Invalid denied_tools entry: %s", exc)
