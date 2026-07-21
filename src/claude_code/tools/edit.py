"""Edit tool — exact string replacement in files.

Mirrors Claude Code's TypeScript ``Edit`` tool semantics:
- Requires the file to have been read first.
- Fails if ``old_string`` is not found or is ambiguous (multiple matches).
- Returns a unified diff of the changes made.

Matching is exact. Line endings are normalised to ``\\n`` for matching and
the original CRLF style is restored on write, but whitespace is never
fuzzily normalised — an approximate match could silently edit the wrong
line, so a failed exact match is reported as an error instead.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult
from claude_code.utils.file_utils import read_file_with_encoding

# Regex used to strip ``cat -n`` line-number prefixes that the model
# sometimes echoes back into ``old_string``.
_LINE_NUM_RE = re.compile(r"^\s*\d+\t", re.MULTILINE)


def _strip_line_numbers(text: str) -> str:
    """Remove ``cat -n`` style line-number prefixes from *text*."""
    return _LINE_NUM_RE.sub("", text)


class EditTool(Tool):
    """Replace an exact string occurrence in a file.

    The ``old_string`` must match exactly (after optional line-number
    stripping and CRLF normalisation).
    """

    name = "Edit"
    description = (
        "Performs exact string replacement in a file. The old_string must "
        "appear exactly once (unless replace_all is true). The file must have "
        "been read previously. Returns a diff of the changes made."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "The exact text to find and replace. Must match exactly "
                    "including indentation and whitespace."
                ),
            },
            "new_string": {
                "type": "string",
                "description": "The text to replace old_string with.",
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "If true, replace all occurrences instead of requiring "
                    "a unique match. Defaults to false."
                ),
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        file_path_str: str = kwargs["file_path"]
        old_string: str = kwargs["old_string"]
        new_string: str = kwargs["new_string"]
        replace_all: bool = kwargs.get("replace_all", False)

        path = Path(file_path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult.error(f"File not found: {path}")

        if not self.context.has_read(str(path)):
            return ToolResult.error(
                f"File '{path}' has not been read. "
                "Please use the Read tool before editing."
            )

        # -- read current content ------------------------------------------
        try:
            raw_content, encoding = read_file_with_encoding(path)
        except (PermissionError, OSError) as exc:
            return ToolResult.error(f"Cannot read file: {exc}")

        # Normalise line endings for matching; restore the original style
        # on write so a CRLF file is not silently converted to LF.
        uses_crlf = "\r\n" in raw_content
        content = raw_content.replace("\r\n", "\n")

        # Strip line-number prefixes the model may have included
        old_string = _strip_line_numbers(old_string)

        # -- find matches -------------------------------------------------
        new_content, error = _find_and_replace(content, old_string, new_string, replace_all)

        if error:
            return ToolResult.error(error)

        if new_content == content:
            return ToolResult.success("No changes needed — old_string equals new_string.")

        # -- write back ---------------------------------------------------
        out = new_content.replace("\n", "\r\n") if uses_crlf else new_content
        try:
            data = out.encode(encoding)
        except LookupError:
            # Unknown encoding name — fall back to utf-8.
            data = out.encode("utf-8")
        except UnicodeEncodeError:
            return ToolResult.error(
                f"新内容含 {encoding} 编码无法表示的字符，已取消写入"
                "（以免静默改变文件编码）。"
            )
        try:
            path.write_bytes(data)
        except (PermissionError, OSError) as exc:
            return ToolResult.error(f"Failed to write file: {exc}")

        # Mark as read (content has changed)
        self.context.mark_read(str(path))

        # -- produce diff -------------------------------------------------
        diff = _make_diff(content, new_content, str(path))

        return ToolResult.success(f"Edit applied to {path}\n\n{diff}")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _find_and_replace(
    content: str,
    old: str,
    new: str,
    replace_all: bool,
) -> tuple[str, str | None]:
    """Return ``(new_content, error_message)`` using exact matching only.

    On success, returns ``(new_content, None)``.
    On failure, returns ``(original_content, error_message)``.

    Matching is exact — no whitespace normalisation. An approximate match
    can land on the wrong occurrence and silently corrupt the file, so a
    failed match is reported rather than guessed.
    """
    count = content.count(old)

    if count == 0:
        return content, "old_string not found in file."

    if count > 1 and not replace_all:
        return content, (
            f"Found {count} occurrences of old_string. "
            "Make it more specific or set replace_all=true."
        )

    if replace_all:
        return content.replace(old, new), None
    return content.replace(old, new, 1), None


def _make_diff(old_content: str, new_content: str, path: str) -> str:
    """Produce a unified diff string."""
    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f"a/{path}",
        tofile=f"b/{path}",
        lineterm="",
    )
    return "".join(diff)
