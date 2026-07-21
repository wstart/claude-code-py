"""Tests for the Write tool — real file operations via tmp_path."""

from __future__ import annotations

from pathlib import Path

from claude_code.tools.base import ToolContext
from claude_code.tools.read import ReadTool
from claude_code.tools.write import WriteTool


async def test_write_new_file(tmp_path: Path) -> None:
    f = tmp_path / "new.txt"
    ctx = ToolContext()
    result = await WriteTool(ctx).execute(file_path=str(f), content="hello world")

    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "hello world"
    # Successful write marks the file read so later edits are allowed.
    assert ctx.has_read(str(f)) is True


async def test_write_creates_parent_directories(tmp_path: Path) -> None:
    f = tmp_path / "a" / "b" / "c.txt"
    result = await WriteTool(ToolContext()).execute(file_path=str(f), content="deep")
    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "deep"


async def test_write_existing_file_without_read_is_blocked(tmp_path: Path) -> None:
    f = tmp_path / "exists.txt"
    f.write_text("original", encoding="utf-8")

    result = await WriteTool(ToolContext()).execute(file_path=str(f), content="overwrite")
    assert result.is_error is True
    assert "has not been read" in result.content
    # File must be left untouched.
    assert f.read_text(encoding="utf-8") == "original"


async def test_write_existing_file_after_read_succeeds(tmp_path: Path) -> None:
    f = tmp_path / "exists.txt"
    f.write_text("original", encoding="utf-8")
    ctx = ToolContext()

    await ReadTool(ctx).execute(file_path=str(f))
    result = await WriteTool(ctx).execute(file_path=str(f), content="overwritten")

    assert result.is_error is False
    assert f.read_text(encoding="utf-8") == "overwritten"


async def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    f = tmp_path / "round.txt"
    ctx = ToolContext()
    await WriteTool(ctx).execute(file_path=str(f), content="roundtrip body")

    read_result = await ReadTool(ctx).execute(file_path=str(f))
    assert read_result.is_error is False
    assert "roundtrip body" in read_result.content


async def test_write_reports_character_count(tmp_path: Path) -> None:
    f = tmp_path / "count.txt"
    result = await WriteTool(ToolContext()).execute(file_path=str(f), content="12345")
    assert "5 characters" in result.content


async def test_write_unicode_content(tmp_path: Path) -> None:
    f = tmp_path / "u.txt"
    await WriteTool(ToolContext()).execute(file_path=str(f), content="日本語テスト")
    assert f.read_text(encoding="utf-8") == "日本語テスト"
