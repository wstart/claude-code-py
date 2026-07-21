"""Tests for the Glob tool — pattern matching via tmp_path."""

from __future__ import annotations

from pathlib import Path

from claude_code.tools.base import ToolContext
from claude_code.tools.glob_tool import GlobTool


def _tool(cwd: Path | None = None) -> GlobTool:
    ctx = ToolContext()
    if cwd is not None:
        ctx.cwd = str(cwd)
    return GlobTool(ctx)


async def test_glob_missing_directory_errors(tmp_path: Path) -> None:
    result = await _tool().execute(pattern="*.py", path=str(tmp_path / "missing"))
    assert result.is_error is True
    assert "Directory not found" in result.content


async def test_glob_path_is_file_errors(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    result = await _tool().execute(pattern="*", path=str(f))
    assert result.is_error is True
    assert "Not a directory" in result.content


async def test_glob_matches_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.py").write_text("x", encoding="utf-8")
    (tmp_path / "c.txt").write_text("x", encoding="utf-8")

    result = await _tool().execute(pattern="*.py", path=str(tmp_path))
    assert result.is_error is False
    assert "a.py" in result.content
    assert "b.py" in result.content
    assert "c.txt" not in result.content
    assert "Found 2 files" in result.content


async def test_glob_no_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    result = await _tool().execute(pattern="*.py", path=str(tmp_path))
    assert result.is_error is False
    assert "No files matching" in result.content


async def test_glob_recursive_double_star(tmp_path: Path) -> None:
    (tmp_path / "top.py").write_text("x", encoding="utf-8")
    nested = tmp_path / "pkg" / "sub"
    nested.mkdir(parents=True)
    (nested / "deep.py").write_text("x", encoding="utf-8")

    result = await _tool().execute(pattern="**/*.py", path=str(tmp_path))
    assert result.is_error is False
    assert "deep.py" in result.content


async def test_glob_only_directories_match(tmp_path: Path) -> None:
    (tmp_path / "somedir").mkdir()
    result = await _tool().execute(pattern="somedir", path=str(tmp_path))
    assert result.is_error is False
    assert "directories" in result.content


async def test_glob_defaults_to_context_cwd(tmp_path: Path) -> None:
    (tmp_path / "here.py").write_text("x", encoding="utf-8")
    # No explicit path -> falls back to ctx.cwd.
    result = await _tool(cwd=tmp_path).execute(pattern="*.py")
    assert result.is_error is False
    assert "here.py" in result.content


async def test_glob_sorts_newest_first(tmp_path: Path) -> None:
    import os
    import time

    old = tmp_path / "old.py"
    new = tmp_path / "new.py"
    old.write_text("x", encoding="utf-8")
    new.write_text("x", encoding="utf-8")
    # Force distinct mtimes: old is older.
    past = time.time() - 1000
    os.utime(old, (past, past))

    result = await _tool().execute(pattern="*.py", path=str(tmp_path))
    body = result.content
    assert body.index("new.py") < body.index("old.py")
