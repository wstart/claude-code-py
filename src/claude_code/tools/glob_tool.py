"""Glob tool — find files matching a pattern.

Uses :mod:`pathlib` for glob matching and sorts results by modification
time (newest first).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult


class GlobTool(Tool):
    """Find files matching a glob pattern.

    Supports ``**`` for recursive matching.  Results are sorted by
    modification time with the most recently modified files first.
    """

    name = "Glob"
    description = (
        "Finds files matching a glob pattern. Supports ** for recursive "
        "matching. Returns paths sorted by modification time (newest first)."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": (
                    "Glob pattern to match files (e.g. '**/*.py', 'src/*.ts')."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Directory to search in. Defaults to the current working "
                    "directory."
                ),
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        pattern: str = kwargs["pattern"]
        search_path_str: str = kwargs.get("path", "") or self.context.cwd

        search_path = Path(search_path_str).expanduser().resolve()

        if not search_path.exists():
            return ToolResult.error(f"Directory not found: {search_path}")

        if not search_path.is_dir():
            return ToolResult.error(f"Not a directory: {search_path}")

        # -- glob ----------------------------------------------------------
        try:
            matches = list(search_path.glob(pattern))
        except ValueError as exc:
            return ToolResult.error(f"Invalid glob pattern '{pattern}': {exc}")

        if not matches:
            return ToolResult.success(
                f"No files matching '{pattern}' in {search_path}"
            )

        # Filter out directories (keep files and symlinks to files)
        file_matches = [m for m in matches if m.is_file() or m.is_symlink()]

        if not file_matches:
            return ToolResult.success(
                f"No files matching '{pattern}' in {search_path} "
                f"(found {len(matches)} directories)"
            )

        # Sort by modification time, newest first
        file_matches.sort(key=lambda p: _safe_mtime(p), reverse=True)

        # Format output
        lines = [f"Found {len(file_matches)} files matching '{pattern}':\n"]
        for p in file_matches:
            rel = p.relative_to(search_path) if p.is_relative_to(search_path) else p
            lines.append(str(rel))

        return ToolResult.success("\n".join(lines))


def _safe_mtime(path: Path) -> float:
    """Return modification time, or 0 on error."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
