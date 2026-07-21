"""Tests for the Grep tool — content search via tmp_path.

Assertions are kept backend-agnostic so they hold whether the tool uses
ripgrep or the pure-Python fallback (both emit a "Found N matches" header,
include the matched line text, and report "No matches" on empty results).
"""

from __future__ import annotations

from pathlib import Path

from claude_code.tools.base import ToolContext
from claude_code.tools.grep import GrepTool


def _tool() -> GrepTool:
    return GrepTool(ToolContext())


async def test_grep_missing_path_errors(tmp_path: Path) -> None:
    result = await _tool().execute(pattern="x", path=str(tmp_path / "missing"))
    assert result.is_error is True
    assert "Path not found" in result.content


async def test_grep_invalid_regex_errors(tmp_path: Path) -> None:
    result = await _tool().execute(pattern="(unclosed", path=str(tmp_path))
    assert result.is_error is True
    assert "Invalid regex" in result.content


async def test_grep_finds_match_in_directory(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello NEEDLE_TOKEN world\n", encoding="utf-8")
    result = await _tool().execute(pattern="NEEDLE_TOKEN", path=str(tmp_path))

    assert result.is_error is False
    assert "Found" in result.content
    assert "NEEDLE_TOKEN" in result.content
    assert "a.txt" in result.content


async def test_grep_finds_match_in_single_file(tmp_path: Path) -> None:
    f = tmp_path / "solo.txt"
    f.write_text("nothing\nUNIQUE_MARKER here\nmore\n", encoding="utf-8")
    result = await _tool().execute(pattern="UNIQUE_MARKER", path=str(f))

    assert result.is_error is False
    assert "UNIQUE_MARKER here" in result.content


async def test_grep_no_matches(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("just some text\n", encoding="utf-8")
    result = await _tool().execute(pattern="ZZZ_NOT_PRESENT", path=str(tmp_path))
    assert result.is_error is False
    assert "No matches" in result.content


async def test_grep_regex_pattern(tmp_path: Path) -> None:
    (tmp_path / "code.txt").write_text(
        "def foo():\n    return 42\ndef bar():\n", encoding="utf-8"
    )
    result = await _tool().execute(pattern=r"def \w+\(\)", path=str(tmp_path))
    assert result.is_error is False
    assert "foo" in result.content
    assert "bar" in result.content


async def test_grep_include_filter(tmp_path: Path) -> None:
    (tmp_path / "match.py").write_text("SHARED_TOKEN in python\n", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("SHARED_TOKEN in text\n", encoding="utf-8")

    result = await _tool().execute(
        pattern="SHARED_TOKEN", path=str(tmp_path), include="*.py"
    )
    assert result.is_error is False
    assert "match.py" in result.content
    assert "skip.txt" not in result.content
