"""Logging setup with configurable levels and categories."""

import logging
import os
import sys
from pathlib import Path

# Default log directory under user's home
_LOG_DIR = Path.home() / ".claude" / "logs"

# Debug category prefix
_DEBUG_CATEGORY_PREFIX = "claude_code."

# Module-level logger registry for category support
_loggers: dict[str, logging.Logger] = {}


def setup_logging(
    level: str = "INFO",
    debug_categories: list[str] | None = None,
    log_file: str | None = None,
    log_dir: Path | None = None,
) -> logging.Logger:
    """Configure the application-wide logging.

    Args:
        level: Base log level (DEBUG, INFO, WARNING, ERROR).
        debug_categories: List of module categories to enable debug logging for.
            For example ["tools", "providers"] enables debug for
            claude_code.tools and claude_code.providers.
        log_file: Optional explicit log file path.
        log_dir: Directory for log files. Defaults to ~/.claude/logs/.

    Returns:
        The root application logger.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    root_logger = logging.getLogger("claude_code")
    root_logger.setLevel(logging.DEBUG)  # Allow all, filter at handler level

    # Remove existing handlers to avoid duplication
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(log_level)
    console_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    root_logger.addHandler(console_handler)

    # File handler
    file_path = _resolve_log_path(log_file, log_dir)
    if file_path:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(str(file_path), encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        file_handler.setFormatter(file_fmt)
        root_logger.addHandler(file_handler)

    # Apply debug categories
    if debug_categories:
        for category in debug_categories:
            _enable_debug_category(category)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Get a logger for a specific module/category.

    If the name doesn't start with 'claude_code.', it's prefixed automatically.

    Args:
        name: Logger name (e.g. "tools.bash" becomes "claude_code.tools.bash").

    Returns:
        A configured logger instance.
    """
    if not name.startswith(_DEBUG_CATEGORY_PREFIX):
        full_name = f"claude_code.{name}"
    else:
        full_name = name

    if full_name not in _loggers:
        _loggers[full_name] = logging.getLogger(full_name)
    return _loggers[full_name]


def _enable_debug_category(category: str) -> None:
    """Enable DEBUG level for a specific category.

    Args:
        category: Short category name like "tools", "providers", "mcp".
    """
    full_name = f"claude_code.{category}"
    logger = logging.getLogger(full_name)
    logger.setLevel(logging.DEBUG)


def _resolve_log_path(
    log_file: str | None = None,
    log_dir: Path | None = None,
) -> Path | None:
    """Determine the log file path.

    Returns None if the CLAUDE_NO_LOG env var is set.
    """
    if os.environ.get("CLAUDE_NO_LOG"):
        return None

    if log_file:
        return Path(log_file)

    directory = log_dir or _LOG_DIR
    return directory / "claude-code.log"
