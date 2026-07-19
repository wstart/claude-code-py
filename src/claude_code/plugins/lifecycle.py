"""Plugin lifecycle management — install, enable, disable, update, remove.

Manages the full lifecycle of plugins including installation from a
source (local path or URL), enabling/disabling, and removal.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from claude_code.plugins.loader import Plugin, PluginLoader, PluginManifest
from claude_code.plugins.registry import PluginRegistry
from claude_code.utils.logging import get_logger
from claude_code.utils.path_utils import expand_user

logger = get_logger("plugins.lifecycle")

# Persistent state file for plugin enable/disable status
_STATE_FILE = expand_user("~/.claude/plugins_state.json")


@dataclass
class PluginState:
    """Persistent per-plugin state.

    Attributes:
        enabled: Whether the plugin is active.
        source: Installation source (path or URL).
    """

    enabled: bool = True
    source: str = ""


class PluginLifecycle:
    """Manage plugin install/enable/disable/update/remove.

    Uses a :class:`PluginLoader` for discovery/loading and a
    :class:`PluginRegistry` to track active plugins.
    """

    def __init__(
        self,
        loader: PluginLoader | None = None,
        registry: PluginRegistry | None = None,
        install_dir: str | None = None,
    ) -> None:
        """Initialize lifecycle manager.

        Args:
            loader: Plugin loader instance. Created if not provided.
            registry: Plugin registry instance. Created if not provided.
            install_dir: Directory for installed plugins. Defaults to
                ``~/.claude/plugins/``.
        """
        self._loader = loader or PluginLoader()
        self._registry = registry or PluginRegistry()
        self._install_dir = Path(install_dir or expand_user("~/.claude/plugins"))
        self._states: dict[str, PluginState] = {}
        self._load_states()

    @property
    def registry(self) -> PluginRegistry:
        """Access the underlying plugin registry."""
        return self._registry

    async def install(self, source: str) -> Plugin:
        """Install a plugin from a local path.

        Copies the plugin directory into the install directory, then
        loads and registers it.

        Args:
            source: Local directory path containing the plugin.

        Returns:
            The installed and loaded :class:`Plugin`.

        Raises:
            FileNotFoundError: If *source* does not exist.
            ValueError: If the plugin has no valid manifest.
        """
        src = Path(source).resolve()
        if not src.is_dir():
            raise FileNotFoundError(f"Plugin source not found: {source}")

        manifest_file = src / "manifest.json"
        if not manifest_file.exists():
            raise ValueError(f"No manifest.json found in {source}")

        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest = PluginManifest.from_dict(data, path=str(src))

        if not manifest.name:
            raise ValueError("Plugin manifest has no name")

        dest = self._install_dir / manifest.name
        if dest.exists():
            logger.info("Plugin %r already installed, updating", manifest.name)
            shutil.rmtree(dest)

        self._install_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)

        manifest.path = str(dest)
        self._states[manifest.name] = PluginState(enabled=True, source=source)
        self._save_states()

        plugin = self._loader.load(manifest)
        self._registry.register(plugin)

        logger.info("Installed plugin %r from %s", manifest.name, source)
        return plugin

    async def enable(self, name: str) -> None:
        """Enable a previously disabled plugin.

        Args:
            name: Plugin name.

        Raises:
            KeyError: If the plugin is not installed.
        """
        state = self._states.get(name)
        if state is None:
            raise KeyError(f"Plugin '{name}' is not installed")

        state.enabled = True
        self._save_states()

        plugin = self._registry.get(name)
        if plugin:
            plugin.enabled = True

        logger.info("Plugin enabled: %s", name)

    async def disable(self, name: str) -> None:
        """Disable a plugin without removing it.

        Args:
            name: Plugin name.

        Raises:
            KeyError: If the plugin is not installed.
        """
        state = self._states.get(name)
        if state is None:
            raise KeyError(f"Plugin '{name}' is not installed")

        state.enabled = False
        self._save_states()

        plugin = self._registry.get(name)
        if plugin:
            plugin.enabled = False

        logger.info("Plugin disabled: %s", name)

    async def update(self, name: str) -> None:
        """Re-load a plugin from its install directory.

        Useful after manually updating plugin files on disk.

        Args:
            name: Plugin name.

        Raises:
            KeyError: If the plugin is not installed.
        """
        plugin_dir = self._install_dir / name
        manifest_file = plugin_dir / "manifest.json"
        if not manifest_file.exists():
            raise KeyError(f"Plugin '{name}' manifest not found at {plugin_dir}")

        # Unregister old version
        try:
            self._registry.unregister(name)
        except KeyError:
            pass

        data = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest = PluginManifest.from_dict(data, path=str(plugin_dir))
        plugin = self._loader.load(manifest)

        state = self._states.get(name, PluginState())
        plugin.enabled = state.enabled

        self._registry.register(plugin)
        logger.info("Plugin updated: %s", name)

    async def remove(self, name: str) -> None:
        """Remove an installed plugin and its files.

        Args:
            name: Plugin name.

        Raises:
            KeyError: If the plugin is not installed.
        """
        try:
            self._registry.unregister(name)
        except KeyError:
            pass

        plugin_dir = self._install_dir / name
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)

        self._states.pop(name, None)
        self._save_states()

        logger.info("Plugin removed: %s", name)

    def list_installed(self) -> list[dict[str, Any]]:
        """List all installed plugins with their state.

        Returns:
            List of dicts with keys: name, version, description,
            enabled, source.
        """
        result: list[dict[str, Any]] = []

        for manifest in self._loader.discover():
            state = self._states.get(manifest.name, PluginState())
            result.append({
                "name": manifest.name,
                "version": manifest.version,
                "description": manifest.description,
                "enabled": state.enabled,
                "source": state.source,
            })

        return result

    # -- state persistence -------------------------------------------------

    def _load_states(self) -> None:
        """Load persistent plugin states from disk."""
        if not _STATE_FILE.exists():
            return
        try:
            data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
            for name, state_data in data.items():
                self._states[name] = PluginState(
                    enabled=state_data.get("enabled", True),
                    source=state_data.get("source", ""),
                )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load plugin states: %s", exc)

    def _save_states(self) -> None:
        """Persist plugin states to disk."""
        data = {
            name: {"enabled": s.enabled, "source": s.source}
            for name, s in self._states.items()
        }
        try:
            _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(
                json.dumps(data, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("Failed to save plugin states: %s", exc)
