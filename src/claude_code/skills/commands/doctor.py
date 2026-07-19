"""/doctor — Run diagnostics on the environment."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from claude_code.skills.commands.base import CommandContext, SlashCommand


class DoctorCommand(SlashCommand):
    """Run environment diagnostics to verify setup correctness."""

    name = "doctor"
    description = "Run diagnostics"

    async def execute(self, args: str, context: CommandContext) -> str:
        """Check Python, API keys, provider, tools, git, etc."""
        checks: list[str] = []

        # Python version
        checks.append(f"✓ Python {sys.version.split()[0]}")

        # API key
        if os.environ.get("ANTHROPIC_API_KEY"):
            checks.append("✓ ANTHROPIC_API_KEY is set")
        else:
            checks.append("✗ ANTHROPIC_API_KEY is not set")

        # Provider
        app = context.app
        if app and getattr(app, "provider", None):
            provider = app.provider
            checks.append(f"✓ Provider: {getattr(provider, 'provider_name', 'unknown')}")

        # Tools
        if context.tool_registry:
            names = context.tool_registry.list_names()
            checks.append(f"✓ Tools: {len(names)} registered")

        # Working directory
        config = context.config
        if config:
            wd = Path(getattr(config, "working_directory", "."))
            exists = wd.exists()
            checks.append(f"{'✓' if exists else '✗'} Working dir: {wd}")

        # Git
        try:
            from claude_code.utils.git_utils import is_git_repo
            wd_str = getattr(config, "working_directory", ".") if config else "."
            if is_git_repo(wd_str):
                checks.append("✓ Git repository detected")
            else:
                checks.append("○ Not a Git repository")
        except Exception:
            checks.append("○ Git check unavailable")

        # ripgrep
        try:
            from claude_code.utils.platform import has_command
            checks.append(f"{'✓' if has_command('rg') else '○'} ripgrep")
        except Exception:
            checks.append("○ ripgrep check unavailable")

        return "Diagnostics:\n\n" + "\n".join(checks)
