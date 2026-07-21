"""Tests for the Edit tool — exact string replacement via tmp_path."""

from __future__ import annotations

from pathlib import Path

from claude_code.tools.base import ToolContext
from claude_code.tools.edit import EditTool
from claude_code.tools.read import ReadTool


async def _read_then(ctx: ToolContext, f: Path) -> None:
    await ReadTool(ctx).execute(file_path=str(f))


async def test_edit_missing_file_errors(tmp_path: Path) -> None:
    result = await EditTool(ToolContext()).execute(
        file_path=str(tmp_path / "nope.txt"), old_string="a", new_string="b"
    )
    assert result.is_error is True
    assert "File not found" in result.content


async def test_edit_requires_prior_read(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("hello", encoding="utf-8")
    result = await EditTool(ToolContext()).execute(
        file_path=str(f), old_string="hello", new_string="bye"
    )
    assert result.is_error is True
    assert "has not been read" in result.content
    assert f.read_text(encoding="utf-8") == "hello"  # untouched


async def test_edit_unique_replacement(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("the quick brown fox", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="quick", new_string="slow"
    )
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "the slow brown fox"


async def test_edit_old_string_not_found(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("alpha beta", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="gamma", new_string="delta"
    )
    assert result.is_error is True
    assert "not found" in result.content
    assert f.read_text(encoding="utf-8") == "alpha beta"


async def test_edit_multiple_matches_without_replace_all_errors(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x x x", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="x", new_string="y"
    )
    assert result.is_error is True
    assert "3 occurrences" in result.content
    assert f.read_text(encoding="utf-8") == "x x x"  # unchanged


async def test_edit_replace_all(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("x x x", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="x", new_string="y", replace_all=True
    )
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "y y y"


async def test_edit_no_op_when_old_equals_new(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("keep this line", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="keep", new_string="keep"
    )
    assert result.is_error is False
    assert "No changes needed" in result.content
    assert f.read_text(encoding="utf-8") == "keep this line"


async def test_edit_returns_diff(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_text("line one\nline two\n", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="line two", new_string="line 2"
    )
    assert result.is_error is False
    # Unified diff markers for the removed/added lines.
    assert "-line two" in result.content
    assert "+line 2" in result.content


async def test_edit_strips_line_number_prefix_from_old_string(tmp_path: Path) -> None:
    """old_string echoed back with a `cat -n` prefix still matches."""
    f = tmp_path / "f.txt"
    f.write_text("target line", encoding="utf-8")
    ctx = ToolContext()
    await _read_then(ctx, f)

    result = await EditTool(ctx).execute(
        file_path=str(f), old_string="     1\ttarget line", new_string="replaced"
    )
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "replaced"
