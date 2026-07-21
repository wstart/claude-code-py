"""Grep tool — search file contents with regular expressions.

Prefers ``ripgrep`` (``rg``) when available for speed, falling back to
pure-Python :mod:`re` search.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult


def _has_ripgrep() -> bool:
    """Return ``True`` if ``rg`` is on PATH."""
    return shutil.which("rg") is not None


class GrepTool(Tool):
    """Search file contents using a regular expression.

    Uses ``ripgrep`` if available, otherwise falls back to Python's
    :mod:`re` module.  Results are sorted by file modification time
    (newest first).
    """

    name = "Grep"
    description = (
        "Searches file contents using a regular expression pattern. "
        "Returns matching lines with file paths and line numbers. "
        "Results sorted by file modification time (newest first)."
    )
    category = "file"
    read_only = True
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": (
                    "File or directory to search in. Defaults to the "
                    "current working directory."
                ),
            },
            "include": {
                "type": "string",
                "description": (
                    "Glob pattern to filter files (e.g. '*.py', '*.ts'). "
                    "Only files matching this pattern will be searched."
                ),
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        pattern: str = kwargs["pattern"]
        search_path_str: str = kwargs.get("path", "") or self.context.cwd
        include: str | None = kwargs.get("include")

        search_path = Path(search_path_str).expanduser().resolve()

        if not search_path.exists():
            return ToolResult.error(f"Path not found: {search_path}")

        # Validate regex
        try:
            re.compile(pattern)
        except re.error as exc:
            return ToolResult.error(f"Invalid regex pattern: {exc}")

        if _has_ripgrep():
            return await self._grep_ripgrep(pattern, search_path, include)
        return await self._grep_python(pattern, search_path, include)

    # -- ripgrep backend ---------------------------------------------------

    async def _grep_ripgrep(
        self,
        pattern: str,
        search_path: Path,
        include: str | None,
    ) -> ToolResult:
        """Use ripgrep for fast searching."""
        cmd = [
            "rg",
            "--no-heading",
            "--line-number",
            "--color", "never",
            pattern,
            str(search_path),
        ]

        if include:
            cmd.extend(["--glob", include])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            # rg disappeared — fall back
            return await self._grep_python(pattern, search_path, include)

        if proc.returncode == 1:
            # rg returns 1 when no matches found
            return ToolResult.success(f"No matches for '{pattern}' in {search_path}")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return ToolResult.error(f"ripgrep error: {err}")

        output = stdout.decode("utf-8", errors="replace").strip()
        if not output:
            return ToolResult.success(f"No matches for '{pattern}' in {search_path}")

        # Sort results by file modification time
        lines = output.split("\n")
        sorted_output = _sort_by_mtime(lines, search_path)

        match_count = len(lines)
        header = f"Found {match_count} matches for '{pattern}':\n\n"
        return ToolResult.success(header + sorted_output)

    # -- Python fallback ---------------------------------------------------

    async def _grep_python(
        self,
        pattern: str,
        search_path: Path,
        include: str | None,
    ) -> ToolResult:
        """Pure-Python grep fallback using re module."""
        compiled = re.compile(pattern)
        matches: list[str] = []
        file_mtimes: dict[str, float] = {}

        # Collect files to search
        if search_path.is_file():
            files = [search_path]
        else:
            files = _collect_files(search_path, include)

        for file_path in files:
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (PermissionError, OSError):
                continue

            mtime = _safe_mtime(file_path)
            rel_path = str(
                file_path.relative_to(search_path)
                if file_path.is_relative_to(search_path)
                else file_path
            )
            file_mtimes[rel_path] = mtime

            for line_num, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    matches.append(f"{rel_path}:{line_num}:{line}")

        if not matches:
            return ToolResult.success(f"No matches for '{pattern}' in {search_path}")

        # Sort by file mtime (newest first), then line number
        def sort_key(match: str) -> tuple[float, str]:
            parts = match.split(":", 2)
            fp = parts[0] if parts else ""
            return (-file_mtimes.get(fp, 0.0), fp)

        matches.sort(key=sort_key)

        header = f"Found {len(matches)} matches for '{pattern}':\n\n"
        return ToolResult.success(header + "\n".join(matches))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Directories the pure-Python fallback skips (ripgrep skips these via
# .gitignore by default; the fallback has no gitignore parser).
_IGNORED_DIRS = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vscode",
    "dist", "build", ".next", ".cache", "target",
})


def _collect_files(root: Path, include: str | None) -> list[Path]:
    """Walk *root* and return files matching the optional *include* glob."""
    results: list[Path] = []
    root = Path(root)

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune ignored directories in place so os.walk doesn't descend.
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS]
        dp = Path(dirpath)
        for fname in filenames:
            fp = dp / fname
            if include:
                rel = fp.relative_to(root).as_posix()
                if not (_matches_glob(fname, include) or _matches_glob(rel, include)):
                    continue
            # Skip binary-looking files
            if fp.suffix.lower() in _BINARY_EXTENSIONS:
                continue
            results.append(fp)

    return results


_BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".avi", ".mov", ".mkv",
})


def _matches_glob(name: str, pattern: str) -> bool:
    """Check if *name* matches a glob *pattern* (supports ``**`` and paths)."""
    from fnmatch import fnmatch

    # `**` should cross path separators; fnmatch treats `*` greedily over
    # `/` already, so normalise `**` down to `*` for this simple matcher.
    return fnmatch(name, pattern.replace("**", "*"))


def _sort_by_mtime(lines: list[str], base_path: Path) -> str:
    """Sort ripgrep output lines by the file's modification time."""
    # Group lines by file
    file_lines: dict[str, list[str]] = {}
    for line in lines:
        parts = line.split(":", 1)
        if parts:
            fp = parts[0]
            file_lines.setdefault(fp, []).append(line)

    # Sort files by mtime
    sorted_files = sorted(
        file_lines.keys(),
        key=lambda fp: -_safe_mtime(base_path / fp if not Path(fp).is_absolute() else Path(fp)),
    )

    result_lines: list[str] = []
    for fp in sorted_files:
        result_lines.extend(file_lines[fp])

    return "\n".join(result_lines)


def _safe_mtime(path: Path) -> float:
    """Return modification time, or 0 on error."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0
