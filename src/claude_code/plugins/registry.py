"""Plugin registry — central catalogue of loaded plugins.

Provides look-up by name and aggregation of tools/commands from all
registered plugins.
"""

from __future__ import annotations

from typing import Any

from claude_code.plugins.loader import Plugin
from claude_code.utils.logging import get_logger

logger = get_logger("plugins.registry")


class PluginRegistry:
    """Registry for loaded plugins.

    Plugins are stored by name. Duplicate registrations replace the
    previous entry (with a warning).
    """

    def __init__(self) -> None:
        self._plugins: dict[str, Plugin] = {}

    def register(self, plugin: Plugin) -> None:
        """Register a plugin.

        Args:
            plugin: The loaded :class:`Plugin` to register.
        """
        name = plugin.manifest.name
        if name in self._plugins:
            logger.warning("Replacing existing plugin %r", name)
        self._plugins[name] = plugin
        logger.debug("Plugin registered: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a plugin by name.

        Calls the plugin's ``teardown()`` hook if available.

        Args:
            name: Plugin name to remove.

        Raises:
            KeyError: If no plugin with *name* is registered.
        """
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            raise KeyError(f"Plugin '{name}' is not registered")

        teardown = getattr(plugin.module, "teardown", None) if plugin.module else None
        if callable(teardown):
            try:
                teardown(plugin)
            except Exception:
                logger.exception("Plugin %r teardown() failed", name)

        logger.debug("Plugin unregistered: %s", name)

    def get(self, name: str) -> Plugin | None:
        """Look up a plugin by name.

        Args:
            name: Plugin name.

        Returns:
            The :class:`Plugin` if found, ``None`` otherwise.
        """
        return self._plugins.get(name)

    def list_all(self) -> list[Plugin]:
        """Return all registered plugins in registration order."""
        return list(self._plugins.values())

    def get_tools(self) -> list[Any]:
        """Aggregate tool instances from all enabled plugins.

        Returns:
            Flat list of tool instances.
        """
        tools: list[Any] = []
        for plugin in self._plugins.values():
            if plugin.enabled:
                tools.extend(plugin.tool_instances)
        return tools

    def get_commands(self) -> list[Any]:
        """Aggregate command instances from all enabled plugins.

        Returns:
            Flat list of command instances.
        """
        commands: list[Any] = []
        for plugin in self._plugins.values():
            if plugin.enabled:
                commands.extend(plugin.command_instances)
        return commands

    def __len__(self) -> int:
        return len(self._plugins)

    def __contains__(self, name: str) -> bool:
        return name in self._plugins
