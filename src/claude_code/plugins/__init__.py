"""Plugin system — discovery, loading, registry, and lifecycle management."""

from claude_code.plugins.lifecycle import PluginLifecycle, PluginState
from claude_code.plugins.loader import Plugin, PluginLoader, PluginManifest
from claude_code.plugins.registry import PluginRegistry

__all__ = [
    "Plugin",
    "PluginLifecycle",
    "PluginLoader",
    "PluginManifest",
    "PluginRegistry",
    "PluginState",
]
