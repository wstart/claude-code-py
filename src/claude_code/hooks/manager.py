"""Hook lifecycle manager — registration, ordering, and execution."""

from __future__ import annotations

import logging
from typing import Any

from .events import (
    HookConfig,
    HookDecision,
    HookEntry,
    HookEvent,
    HookPayload,
    HookResult,
)
from .executor import HookExecutor

logger = logging.getLogger(__name__)


class HookManager:
    """Manage hook registration and fire them in priority order.

    Hooks are loaded from a config dict (the ``hooks`` key in settings.json)
    and can also be registered/unregistered at runtime.

    When an event fires, all registered hooks for that event execute in
    descending priority order. The first non-ALLOW result short-circuits
    the chain and is returned immediately.
    """

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        """Initialise the manager, optionally loading hooks from config.

        Args:
            config: The ``hooks`` section of settings.json. Each key is an
                event name and each value is a list of hook entries.
        """
        self._hooks: dict[HookEvent, list[tuple[int, HookEntry]]] = {}
        self._executor = HookExecutor()

        if config:
            self._load_config(config)

    # -- public API --------------------------------------------------------

    def register(
        self,
        event: HookEvent,
        command: str,
        priority: int = 0,
        timeout: float = 30.0,
    ) -> None:
        """Register a hook for an event.

        Args:
            event: Which event to listen for.
            command: Shell command or script path.
            priority: Higher values run first.
            timeout: Per-hook timeout in seconds.
        """
        entry = HookEntry(command=command, priority=priority, timeout=timeout)
        bucket = self._hooks.setdefault(event, [])
        # Avoid duplicate commands
        for _, existing in bucket:
            if existing.command == command:
                return
        bucket.append((priority, entry))
        bucket.sort(key=lambda t: t[0], reverse=True)

    def unregister(self, event: HookEvent, command: str) -> None:
        """Remove a hook by event and command.

        Args:
            event: The event the hook was registered for.
            command: The exact command string to remove.
        """
        bucket = self._hooks.get(event, [])
        self._hooks[event] = [
            (p, e) for p, e in bucket if e.command != command
        ]

    async def fire(self, event: HookEvent, payload: HookPayload) -> HookResult:
        """Fire all hooks for an event in priority order.

        Returns the first non-ALLOW result, or ALLOW if all hooks pass.

        Args:
            event: The event that occurred.
            payload: Data to pass to each hook.

        Returns:
            Aggregated HookResult.
        """
        bucket = self._hooks.get(event, [])
        for priority, entry in bucket:
            if not entry.enabled:
                continue
            logger.debug(
                "Firing hook %r (priority=%d) for %s",
                entry.command, priority, event.value,
            )
            result = await self._executor.execute(
                entry.command, payload, timeout=entry.timeout,
            )
            if result.decision != HookDecision.ALLOW:
                logger.info(
                    "Hook %r returned %s: %s",
                    entry.command, result.decision.value, result.reason,
                )
                return result
        return HookResult(decision=HookDecision.ALLOW)

    def list_hooks(self) -> dict[HookEvent, list[dict[str, Any]]]:
        """Return all registered hooks grouped by event.

        Returns:
            Mapping of event -> list of hook info dicts.
        """
        result: dict[HookEvent, list[dict[str, Any]]] = {}
        for event, bucket in self._hooks.items():
            result[event] = [
                {
                    "command": entry.command,
                    "priority": priority,
                    "timeout": entry.timeout,
                    "enabled": entry.enabled,
                }
                for priority, entry in bucket
            ]
        return result

    # -- internals ---------------------------------------------------------

    def _load_config(self, config: dict[str, Any]) -> None:
        """Load hooks from a settings.json ``hooks`` dict."""
        hook_config = HookConfig.from_settings(config)
        for event_name, entries in hook_config.hooks.items():
            try:
                event = HookEvent(event_name)
            except ValueError:
                logger.warning("Unknown hook event %r in config, skipping", event_name)
                continue
            for entry in entries:
                self.register(
                    event=event,
                    command=entry.command,
                    priority=entry.priority,
                    timeout=entry.timeout,
                )
