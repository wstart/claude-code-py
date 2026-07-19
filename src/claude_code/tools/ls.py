"""LS tool — list directory contents with metadata.

Returns file and directory listings with sizes, types, and modification
times, respecting ignore patterns.
"""

from __future__ import annotations

import fnmatch
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult


class LSTool(Tool):
    """List directory contents with metadata.

    Returns a formatted listing of files and subdirectories, including
    file sizes, types, and modification times.  Supports ignore
    patterns to exclude entries from the output.
    """

    name = "LS"
    description = (
        "Lists the contents of a directory with file sizes, types, and "
        "modification times. Supports ignore patterns to exclude entries."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Absolute path to the directory to list. "
                    "Defaults to the current working directory."
                ),
            },
            "ignore": {
                "type": "array",
                "description": (
                    "List of glob patterns to exclude from the listing "
                    "(e.g. ['*.pyc', 'node_modules'])."
                ),
                "items": {"type": "string"},
            },
        },
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        path_str: str = kwargs.get("path", "") or self.context.cwd
        ignore_patterns: list[str] = kwargs.get("ignore", [])

        path = Path(path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult.error(f"Path not found: {path}")

        if not path.is_dir():
            return ToolResult.error(f"Not a directory: {path}")

        # -- list entries --------------------------------------------------
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except OSError as exc:
            return ToolResult.error(f"Cannot list directory: {exc}")

        # -- filter by ignore patterns ------------------------------------
        if ignore_patterns:
            entries = [
                e for e in entries
                if not _should_ignore(e.name, ignore_patterns)
            ]

        if not entries:
            return ToolResult.success(f"Directory is empty: {path}")

        # -- format output -------------------------------------------------
        lines: list[str] = [f"Directory: {path}\n"]

        dirs: list[str] = []
        files: list[str] = []

        for entry in entries:
            try:
                stat = entry.stat()
            except OSError:
                continue

            if entry.is_dir():
                dirs.append(_format_dir_entry(entry, stat))
            else:
                files.append(_format_file_entry(entry, stat))

        if dirs:
            lines.append(f"Directories ({len(dirs)}):")
            lines.extend(dirs)
            lines.append("")

        if files:
            lines.append(f"Files ({len(files)}):")
            lines.extend(files)

        total = len(dirs) + len(files)
        lines.append(f"\nTotal: {len(dirs)} directories, {len(files)} files")

        return ToolResult.success("\n".join(lines))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _should_ignore(name: str, patterns: list[str]) -> bool:
    """Return ``True`` if *name* matches any of the ignore *patterns*."""
    return any(fnmatch.fnmatch(name, pat) for pat in patterns)


def _format_size(size: int) -> str:
    """Format a byte count into a human-readable string."""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _format_mtime(mtime: float) -> str:
    """Format a modification timestamp."""
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc).astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def _format_dir_entry(entry: Path, stat: os.stat_result) -> str:
    """Format a directory entry."""
    mtime = _format_mtime(stat.st_mtime)
    return f"  📁 {entry.name}/  ({mtime})"


def _format_file_entry(entry: Path, stat: os.stat_result) -> str:
    """Format a file entry with size and modification time."""
    size = _format_size(stat.st_size)
    mtime = _format_mtime(stat.st_mtime)
    suffix = entry.suffix or "(no ext)"
    return f"  📄 {entry.name}  {size:>8}  {suffix:<8}  ({mtime})"
