"""Lightweight .env loading into os.environ (no external dependency).

Both ``python main.py`` and the ``aka`` console script call this so the CLI
behaves the same however it was launched: a ``.env`` in the current directory
(or ``~/.aka/.env`` / ``~/.claude/.env``) is picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_one(path: Path) -> None:
    """Load a single .env file into os.environ without overriding existing vars."""
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        # Real environment variables take precedence over .env files.
        if key and key not in os.environ:
            os.environ[key] = value


def load_dotenv_files(cwd: str | Path | None = None) -> None:
    """Load .env files into os.environ from the usual locations.

    Search order (earlier wins, and none override an already-set variable):

    1. ``<cwd>/.env`` — project-local config
    2. ``~/.aka/.env`` — install.sh location
    3. ``~/.claude/.env`` — user-level config
    """
    base = Path(cwd) if cwd is not None else Path.cwd()
    for path in (
        base / ".env",
        Path.home() / ".aka" / ".env",
        Path.home() / ".claude" / ".env",
    ):
        _load_one(path)
