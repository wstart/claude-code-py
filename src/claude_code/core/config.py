"""Configuration loading from multiple sources with merging."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from claude_code.utils.logging import get_logger
from claude_code.utils.path_utils import expand_user, find_upward, get_project_root

logger = get_logger("core.config")

# Default model
DEFAULT_MODEL = "claude-sonnet-4-20250514"


class AppConfig(BaseModel):
    """Application configuration merged from all sources."""

    # API settings
    api_key: str = ""
    api_base_url: str = ""
    base_url: str = ""  # Alias for api_base_url used by app.py
    provider: str = "anthropic"  # anthropic | openai
    model: str = DEFAULT_MODEL
    max_tokens: int = 16384
    temperature: float = 0.0

    # Behavior
    permission_mode: str = "bypass"  # manual | auto | plan | bypass
    output_format: str = "text"  # text | json | stream-json
    verbose: bool = False
    max_turns: int = 0  # 0 = unlimited
    max_budget_usd: float = 0.0  # 0 = unlimited

    # Prompt overrides
    system_prompt: str = ""
    append_system_prompt: str = ""  # Additional text appended to system prompt
    claude_md_content: str = ""
    effort: str = "medium"  # low | medium | high

    # Directories
    working_directory: str = ""
    additional_dirs: list[str] = Field(default_factory=list)
    allowed_dirs: list[str] = Field(default_factory=list)
    allowed_directories: list[str] = Field(default_factory=list)  # Alias used by app.py

    # UI
    no_color: bool = False
    dangerously_skip_permissions: bool = False

    # Permissions
    allowed_tools: list[str] = Field(default_factory=list)
    denied_tools: list[str] = Field(default_factory=list)

    # MCP servers
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Hooks
    hooks: dict[str, Any] = Field(default_factory=dict)


def load_config(
    working_dir: Optional[str] = None,
    overrides: Optional[dict[str, Any]] = None,
) -> AppConfig:
    """Load configuration by merging all sources in priority order.

    Priority (highest to lowest):
    1. Explicit overrides (CLI flags)
    2. Environment variables
    3. Local project settings (.claude/settings.local.json)
    4. Project settings (.claude/settings.json)
    5. User settings (~/.claude/settings.json)
    6. Defaults

    Also loads CLAUDE.md files from global and project locations.

    Args:
        working_dir: Project working directory for locating project-level files.
        overrides: Dict of explicit overrides (from CLI flags).

    Returns:
        Merged AppConfig.
    """
    config_data: dict[str, Any] = {}

    # 1. User-level settings
    user_settings = _load_json_file(_user_settings_path())
    config_data = _deep_merge(config_data, user_settings)

    # 2. Project-level settings
    project_root = get_project_root(working_dir)
    project_settings = _load_json_file(project_root / ".claude" / "settings.json")
    config_data = _deep_merge(config_data, project_settings)

    # 3. Local project settings (gitignored)
    local_settings = _load_json_file(project_root / ".claude" / "settings.local.json")
    config_data = _deep_merge(config_data, local_settings)

    # 4. Environment variables
    env_config = _load_env_config()
    config_data = _deep_merge(config_data, env_config)

    # 5. Explicit overrides
    if overrides:
        config_data = _deep_merge(config_data, overrides)

    # Load CLAUDE.md content
    claude_md = _load_claude_md(project_root)
    config_data.setdefault("claude_md_content", claude_md)

    # Set working directory
    if "working_directory" not in config_data:
        config_data["working_directory"] = str(project_root)

    try:
        return AppConfig(**config_data)
    except Exception as e:
        logger.warning(f"Config validation warning, using defaults where invalid: {e}")
        # Fallback: strip invalid keys and try again
        valid_keys = set(AppConfig.model_fields.keys())
        filtered = {k: v for k, v in config_data.items() if k in valid_keys}
        return AppConfig(**filtered)


def _user_settings_path() -> Path:
    """Get the path to user-level settings file."""
    return expand_user("~/.claude/settings.json")


def _load_json_file(path: Path) -> dict[str, Any]:
    """Load and parse a JSON settings file.

    Args:
        path: Path to the JSON file.

    Returns:
        Parsed dict, or empty dict if file doesn't exist or is invalid.
    """
    if not path.exists():
        return {}
    try:
        content = path.read_text(encoding="utf-8")
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        logger.warning(f"Settings file {path} does not contain a JSON object, ignoring")
        return {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load settings from {path}: {e}")
        return {}


def _load_env_config() -> dict[str, Any]:
    """Extract configuration from environment variables.

    Returns:
        Dict of config values found in environment.
    """
    env_map: dict[str, tuple[str, type]] = {
        "ANTHROPIC_API_KEY": ("api_key", str),
        "ANTHROPIC_AUTH_TOKEN": ("api_key", str),
        "ANTHROPIC_BASE_URL": ("base_url", str),
        "ANTHROPIC_MODEL": ("model", str),
        "ANTHROPIC_DEFAULT_SONNET_MODEL": ("model", str),
        "CLAUDE_MODEL": ("model", str),
        "CLAUDE_MAX_TOKENS": ("max_tokens", int),
        "CLAUDE_TEMPERATURE": ("temperature", float),
        "CLAUDE_PERMISSION_MODE": ("permission_mode", str),
        "CLAUDE_OUTPUT_FORMAT": ("output_format", str),
        "CLAUDE_MAX_TURNS": ("max_turns", int),
        "CLAUDE_MAX_BUDGET_USD": ("max_budget_usd", float),
        "CLAUDE_SYSTEM_PROMPT": ("system_prompt", str),
        "CLAUDE_VERBOSE": ("verbose", bool),
        "CLAUDE_NO_COLOR": ("no_color", bool),
        "CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS": ("dangerously_skip_permissions", bool),
        "CLAUDE_EFFORT": ("effort", str),
    }

    result: dict[str, Any] = {}
    for env_key, (config_key, value_type) in env_map.items():
        value = os.environ.get(env_key)
        if value is not None:
            try:
                if value_type is bool:
                    result[config_key] = value.lower() in ("1", "true", "yes")
                elif value_type is int:
                    result[config_key] = int(value)
                elif value_type is float:
                    result[config_key] = float(value)
                else:
                    result[config_key] = value
            except (ValueError, TypeError):
                logger.warning(f"Invalid value for {env_key}: {value!r}")

    return result


def _load_claude_md(project_root: Path) -> str:
    """Load and concatenate CLAUDE.md files from standard locations.

    Loads in order:
    1. ~/.claude/CLAUDE.md (global user instructions)
    2. <project_root>/CLAUDE.md (project conventions)
    3. <cwd>/CLAUDE.md (directory-specific, if different from project root)

    Args:
        project_root: The project root directory.

    Returns:
        Concatenated CLAUDE.md content, or empty string if none found.
    """
    parts: list[str] = []

    # Global CLAUDE.md
    global_md = expand_user("~/.claude/CLAUDE.md")
    if global_md.exists():
        try:
            content = global_md.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
        except OSError:
            pass

    # Project CLAUDE.md
    project_md = project_root / "CLAUDE.md"
    if project_md.exists():
        try:
            content = project_md.read_text(encoding="utf-8").strip()
            if content:
                parts.append(content)
        except OSError:
            pass

    # CWD CLAUDE.md (only if different from project root)
    cwd = Path.cwd().resolve()
    if cwd != project_root.resolve():
        cwd_md = cwd / "CLAUDE.md"
        if cwd_md.exists():
            try:
                content = cwd_md.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(content)
            except OSError:
                pass

    return "\n\n".join(parts)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep merge two dicts. Override values take precedence.

    Lists are replaced (not appended). Nested dicts are recursively merged.

    Args:
        base: Base dictionary.
        override: Override dictionary.

    Returns:
        Merged dictionary.
    """
    result = dict(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result
