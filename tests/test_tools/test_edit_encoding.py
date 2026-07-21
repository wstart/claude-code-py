"""Tests for Edit/Read encoding consistency, CRLF preservation, and the
removal of the fuzzy-normalise fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code.tools.base import ToolContext
from claude_code.tools.edit import EditTool
from claude_code.tools.read import ReadTool


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext()


async def test_edit_non_utf8_file_after_read(ctx: ToolContext, tmp_path: Path) -> None:
    p = tmp_path / "gbk.txt"
    p.write_bytes("第一行\n第二行\n".encode("gbk"))

    read = ReadTool(ctx)
    res = await read.execute(file_path=str(p))
    assert not res.is_error, res.content

    edit = EditTool(ctx)
    res = await edit.execute(file_path=str(p), old_string="第二行", new_string="改过了")
    assert not res.is_error, res.content
    # File is rewritten in its original (gbk) encoding, not utf-8.
    assert p.read_bytes().decode("gbk") == "第一行\n改过了\n"


async def test_edit_preserves_crlf(ctx: ToolContext, tmp_path: Path) -> None:
    p = tmp_path / "crlf.txt"
    p.write_bytes(b"alpha\r\nbeta\r\n")

    read = ReadTool(ctx)
    await read.execute(file_path=str(p))
    edit = EditTool(ctx)
    res = await edit.execute(file_path=str(p), old_string="alpha", new_string="ALPHA")
    assert not res.is_error, res.content
    assert p.read_bytes() == b"ALPHA\r\nbeta\r\n"


async def test_edit_lf_stays_lf(ctx: ToolContext, tmp_path: Path) -> None:
    p = tmp_path / "lf.txt"
    p.write_bytes(b"alpha\nbeta\n")
    read = ReadTool(ctx)
    await read.execute(file_path=str(p))
    edit = EditTool(ctx)
    res = await edit.execute(file_path=str(p), old_string="beta", new_string="BETA")
    assert not res.is_error, res.content
    assert p.read_bytes() == b"alpha\nBETA\n"


async def test_edit_whitespace_mismatch_errors_not_corrupts(
    ctx: ToolContext, tmp_path: Path
) -> None:
    # A near-miss (different internal spacing) must NOT silently edit a
    # different line — it must report "not found".
    p = tmp_path / "code.py"
    original = b"    x = 1\n    y = 2\n    x = 1\n"
    p.write_bytes(original)
    read = ReadTool(ctx)
    await read.execute(file_path=str(p))
    edit = EditTool(ctx)
    res = await edit.execute(
        file_path=str(p), old_string="x  =  1", new_string="x = 99"
    )
    assert res.is_error
    assert "not found" in res.content
    assert p.read_bytes() == original  # untouched


async def test_read_rejects_oversized_file(ctx: ToolContext, tmp_path: Path) -> None:
    p = tmp_path / "big.bin"
    with open(p, "wb") as f:
        f.seek(60 * 1024 * 1024)
        f.write(b"x")
    read = ReadTool(ctx)
    res = await read.execute(file_path=str(p))
    assert res.is_error
    assert "too large" in res.content
