"""Plugin discovery and loading.

Scans configured directories for plugin manifests and dynamically
loads Python modules declared as entry points.
"""

from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claude_code.utils.logging import get_logger
from claude_code.utils.path_utils import expand_user

logger = get_logger("plugins.loader")

# Default directories to scan for plugins
_DEFAULT_PLUGIN_DIRS: list[str] = [
    "~/.claude/plugins/",
    ".claude-plugin/",
]


@dataclass
class PluginManifest:
    """Deserialized plugin manifest (``manifest.json``).

    Attributes:
        name: Unique plugin identifier.
        version: Semver version string.
        description: Human-readable description.
        entry_point: Dotted Python module path (e.g. ``my_plugin.main``).
        tools: Tool class names exported by the plugin.
        commands: Slash-command class names exported by the plugin.
        path: Filesystem path to the plugin directory.
    """

    name: str
    version: str = "0.0.0"
    description: str = ""
    entry_point: str = ""
    tools: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: str = "") -> PluginManifest:
        """Create a manifest from a parsed JSON dict."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version", "0.0.0"),
            description=data.get("description", ""),
            entry_point=data.get("entry_point", ""),
            tools=data.get("tools", []),
            commands=data.get("commands", []),
            path=path,
        )


@dataclass
class Plugin:
    """A loaded plugin with its instantiated tools and commands.

    Attributes:
        manifest: The plugin's manifest metadata.
        module: The imported Python module.
        tool_instances: Instantiated tool objects from the plugin.
        command_instances: Instantiated command objects from the plugin.
        enabled: Whether the plugin is currently active.
    """

    manifest: PluginManifest
    module: Any = None
    tool_instances: list[Any] = field(default_factory=list)
    command_instances: list[Any] = field(default_factory=list)
    enabled: bool = True


class PluginLoader:
    """Discover and load plugins from directories.

    Each plugin directory must contain a ``manifest.json`` file with at
    least ``name`` and ``entry_point`` fields.
    """

    def __init__(self, plugin_dirs: list[str] | None = None) -> None:
        """Initialize with directories to scan for plugins.

        Args:
            plugin_dirs: Directories to search. Defaults to
                ``~/.claude/plugins/`` and ``.claude-plugin/``.
        """
        self._plugin_dirs = plugin_dirs or list(_DEFAULT_PLUGIN_DIRS)

    def discover(self) -> list[PluginManifest]:
        """Scan directories for plugin manifests.

        Returns:
            List of discovered :class:`PluginManifest` instances.
        """
        manifests: list[PluginManifest] = []
        for dir_path in self._plugin_dirs:
            resolved = expand_user(dir_path)
            if not resolved.is_dir():
                continue
            manifests.extend(self._scan_directory(resolved))
        return manifests

    def load(self, manifest: PluginManifest) -> Plugin:
        """Load a plugin from its manifest.

        Imports the entry-point module and instantiates declared tools
        and commands.

        Args:
            manifest: The plugin manifest to load.

        Returns:
            A :class:`Plugin` with instantiated components.

        Raises:
            ImportError: If the entry-point module cannot be imported.
        """
        if not manifest.entry_point:
            logger.warning("Plugin %r has no entry_point, skipping load", manifest.name)
            return Plugin(manifest=manifest)

        try:
            module = importlib.import_module(manifest.entry_point)
        except ImportError as exc:
            logger.error("Failed to import plugin %r: %s", manifest.name, exc)
            raise

        tool_instances = self._instantiate_exports(module, manifest.tools)
        command_instances = self._instantiate_exports(module, manifest.commands)

        plugin = Plugin(
            manifest=manifest,
            module=module,
            tool_instances=tool_instances,
            command_instances=command_instances,
        )

        # Call plugin setup hook if available
        setup_fn = getattr(module, "setup", None)
        if callable(setup_fn):
            try:
                setup_fn(plugin)
            except Exception:
                logger.exception("Plugin %r setup() failed", manifest.name)

        logger.info("Loaded plugin %r v%s", manifest.name, manifest.version)
        return plugin

    def load_all(self) -> list[Plugin]:
        """Discover and load all plugins.

        Returns:
            List of successfully loaded :class:`Plugin` instances.
        """
        plugins: list[Plugin] = []
        for manifest in self.discover():
            try:
                plugin = self.load(manifest)
                plugins.append(plugin)
            except Exception:
                logger.exception("Failed to load plugin %r", manifest.name)
        return plugins

    # -- internals ---------------------------------------------------------

    def _scan_directory(self, directory: Path) -> list[PluginManifest]:
        """Scan a single directory for plugin manifests."""
        manifests: list[PluginManifest] = []

        for child in directory.iterdir():
            if not child.is_dir():
                continue

            manifest_file = child / "manifest.json"
            if not manifest_file.exists():
                continue

            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                manifest = PluginManifest.from_dict(data, path=str(child))
                if manifest.name:
                    manifests.append(manifest)
                    logger.debug("Discovered plugin %r at %s", manifest.name, child)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Invalid manifest at %s: %s", manifest_file, exc)

        return manifests

    @staticmethod
    def _instantiate_exports(module: Any, names: list[str]) -> list[Any]:
        """Import and instantiate named classes from a module.

        Args:
            module: The loaded Python module.
            names: Class names to look up and instantiate.

        Returns:
            List of instantiated objects.
        """
        instances: list[Any] = []
        for name in names:
            cls = getattr(module, name, None)
            if cls is None:
                logger.warning("Export %r not found in module %s", name, module.__name__)
                continue
            try:
                instances.append(cls())
            except Exception:
                logger.exception("Failed to instantiate %r from %s", name, module.__name__)
        return instances
