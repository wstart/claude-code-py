"""/plugins — Manage plugins."""

from __future__ import annotations

from claude_code.skills.commands.base import CommandContext, SlashCommand


class PluginsCommand(SlashCommand):
    """List, install, enable, disable, or remove plugins."""

    name = "plugins"
    description = "Manage plugins"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Dispatch to subcommands: list, install, enable, disable, remove."""
        app = context.app
        if app is None:
            return "No application context available."

        lifecycle = context.metadata.get("plugin_lifecycle")
        if lifecycle is None:
            # Try to get from app
            lifecycle = getattr(app, "plugin_lifecycle", None)

        parts = args.strip().split(maxsplit=1) if args else []
        subcommand = parts[0].lower() if parts else "list"
        sub_args = parts[1] if len(parts) > 1 else ""

        if subcommand == "list" or not args:
            return await self._list_plugins(lifecycle)
        elif subcommand == "install":
            return await self._install_plugin(lifecycle, sub_args)
        elif subcommand == "enable":
            return await self._enable_plugin(lifecycle, sub_args)
        elif subcommand == "disable":
            return await self._disable_plugin(lifecycle, sub_args)
        elif subcommand == "remove":
            return await self._remove_plugin(lifecycle, sub_args)
        else:
            return (
                "Usage: /plugins <subcommand> [args]\n\n"
                "Subcommands:\n"
                "  list              List installed plugins\n"
                "  install <path>    Install from local path\n"
                "  enable <name>     Enable a plugin\n"
                "  disable <name>    Disable a plugin\n"
                "  remove <name>     Remove a plugin"
            )

    async def _list_plugins(self, lifecycle: object | None) -> str:
        """List all installed plugins."""
        if lifecycle is None:
            return "Plugin system not initialized."

        installed = lifecycle.list_installed()  # type: ignore[attr-defined]
        if not installed:
            return "No plugins installed."

        lines = ["Installed Plugins:"]
        for plugin in installed:
            status = "✓" if plugin.get("enabled", True) else "✗"
            name = plugin.get("name", "unknown")
            version = plugin.get("version", "?")
            desc = plugin.get("description", "")
            lines.append(f"  {status} {name} v{version} — {desc}")

        return "\n".join(lines)

    async def _install_plugin(self, lifecycle: object | None, source: str) -> str:
        """Install a plugin from a path."""
        if lifecycle is None:
            return "Plugin system not initialized."
        if not source:
            return "Usage: /plugins install <path>"

        try:
            plugin = await lifecycle.install(source)  # type: ignore[attr-defined]
            return f"Installed plugin: {plugin.manifest.name}"
        except Exception as exc:
            return f"Failed to install: {exc}"

    async def _enable_plugin(self, lifecycle: object | None, name: str) -> str:
        """Enable a plugin."""
        if lifecycle is None:
            return "Plugin system not initialized."
        if not name:
            return "Usage: /plugins enable <name>"

        try:
            await lifecycle.enable(name)  # type: ignore[attr-defined]
            return f"Plugin enabled: {name}"
        except Exception as exc:
            return f"Failed to enable: {exc}"

    async def _disable_plugin(self, lifecycle: object | None, name: str) -> str:
        """Disable a plugin."""
        if lifecycle is None:
            return "Plugin system not initialized."
        if not name:
            return "Usage: /plugins disable <name>"

        try:
            await lifecycle.disable(name)  # type: ignore[attr-defined]
            return f"Plugin disabled: {name}"
        except Exception as exc:
            return f"Failed to disable: {exc}"

    async def _remove_plugin(self, lifecycle: object | None, name: str) -> str:
        """Remove a plugin."""
        if lifecycle is None:
            return "Plugin system not initialized."
        if not name:
            return "Usage: /plugins remove <name>"

        try:
            await lifecycle.remove(name)  # type: ignore[attr-defined]
            return f"Plugin removed: {name}"
        except Exception as exc:
            return f"Failed to remove: {exc}"
