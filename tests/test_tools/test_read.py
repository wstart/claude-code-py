"""Tests for the Read tool — real file operations via tmp_path."""

from __future__ import annotations

from pathlib import Path

from claude_code.tools.base import ToolContext
from claude_code.tools.read import ReadTool


def _tool(ctx: ToolContext | None = None) -> ReadTool:
    return ReadTool(ctx or ToolContext())


async def test_read_missing_file_errors(tmp_path: Path) -> None:
    result = await _tool().execute(file_path=str(tmp_path / "nope.txt"))
    assert result.is_error is True
    assert "File not found" in result.content


async def test_read_directory_errors(tmp_path: Path) -> None:
    result = await _tool().execute(file_path=str(tmp_path))
    assert result.is_error is True
    assert "Not a file" in result.content


async def test_read_text_file_with_line_numbers(tmp_path: Path) -> None:
    f = tmp_path / "sample.txt"
    f.write_text("first\nsecond\nthird\n", encoding="utf-8")
    result = await _tool().execute(file_path=str(f))

    assert result.is_error is False
    assert "3 lines" in result.content
    # cat -n style: right-aligned line number, tab, content.
    assert "     1\tfirst" in result.content
    assert "     2\tsecond" in result.content
    assert "     3\tthird" in result.content


async def test_read_marks_file_as_read(tmp_path: Path) -> None:
    f = tmp_path / "sample.txt"
    f.write_text("data", encoding="utf-8")
    ctx = ToolContext()
    await _tool(ctx).execute(file_path=str(f))
    assert ctx.has_read(str(f)) is True


async def test_read_empty_file(tmp_path: Path) -> None:
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    result = await _tool().execute(file_path=str(f))
    assert result.is_error is False
    assert "0 lines" in result.content


async def test_read_offset_and_limit(tmp_path: Path) -> None:
    f = tmp_path / "many.txt"
    f.write_text("\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8")
    result = await _tool().execute(file_path=str(f), offset=3, limit=2)

    assert "     3\tline3" in result.content
    assert "     4\tline4" in result.content
    assert "line2" not in result.content
    assert "line5" not in result.content
    # Header notes the visible range.
    assert "showing 3" in result.content


async def test_read_truncates_very_long_line(tmp_path: Path) -> None:
    f = tmp_path / "long.txt"
    f.write_text("A" * 3000, encoding="utf-8")
    result = await _tool().execute(file_path=str(f))
    assert "(truncated)" in result.content
    # Original 3000-char line is not present in full.
    assert "A" * 3000 not in result.content


async def test_read_utf8_content(tmp_path: Path) -> None:
    f = tmp_path / "unicode.txt"
    # A longer, mostly-CJK body so chardet reliably detects UTF-8.
    f.write_text("你好世界，这是一段中文测试文本。\n" * 5, encoding="utf-8")
    result = await _tool().execute(file_path=str(f))
    assert result.is_error is False
    assert "你好世界" in result.content
