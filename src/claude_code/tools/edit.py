"""Edit tool — exact string replacement in files.

Mirrors Claude Code's TypeScript ``Edit`` tool semantics:
- Requires the file to have been read first.
- Fails if ``old_string`` is not found or is ambiguous (multiple matches).
- Falls back to normalised-whitespace matching when exact match fails.
- Returns a unified diff of the changes made.
"""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any

import aiofiles

from claude_code.tools.base import Tool, ToolResult

# Regex used to strip ``cat -n`` line-number prefixes that the model
# sometimes echoes back into ``old_string``.
_LINE_NUM_RE = re.compile(r"^\s*\d+\t", re.MULTILINE)


def _strip_line_numbers(text: str) -> str:
    """Remove ``cat -n`` style line-number prefixes from *text*."""
    return _LINE_NUM_RE.sub("", text)


def _normalise_whitespace(text: str) -> str:
    """Collapse runs of whitespace to a single space per line."""
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


class EditTool(Tool):
    """Replace an exact string occurrence in a file.

    The ``old_string`` must match exactly (after optional line-number
    stripping).  If the exact match fails, a fallback pass normalises
    whitespace before comparing.
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
            content = path.read_text(encoding="utf-8")
        except (PermissionError, OSError) as exc:
            return ToolResult.error(f"Cannot read file: {exc}")

        # Strip line-number prefixes the model may have included
        old_string = _strip_line_numbers(old_string)

        # -- find matches -------------------------------------------------
        result = _find_and_replace(content, old_string, new_string, replace_all)

        if isinstance(result, str):
            return ToolResult.error(result)

        new_content = result

        if new_content == content:
            return ToolResult.success("No changes needed — old_string equals new_string.")

        # -- write back ---------------------------------------------------
        try:
            path.write_text(new_content, encoding="utf-8")
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
) -> str | None:
    """Return the new content string, or an error message.

    Tries exact match first, then normalised-whitespace fallback.
    """
    count = content.count(old)

    # Exact match
    if count == 1 or (replace_all and count > 0):
        if replace_all:
            return content.replace(old, new)
        return content.replace(old, new, 1)

    if count > 1 and not replace_all:
        return (
            f"Found {count} occurrences of old_string. "
            "Make it more specific or set replace_all=true."
        )

    # Fallback: normalised whitespace matching
    norm_content = _normalise_whitespace(content)
    norm_old = _normalise_whitespace(old)
    norm_count = norm_content.count(norm_old)

    if norm_count == 0:
        return "old_string not found in file (tried exact and whitespace-normalised match)."

    if norm_count > 1 and not replace_all:
        return (
            f"Found {norm_count} whitespace-normalised occurrences of old_string. "
            "Make it more specific or set replace_all=true."
        )

    # Apply replacement using normalised positions
    return _replace_normalised(content, old, new, replace_all)


def _replace_normalised(
    content: str,
    old: str,
    new: str,
    replace_all: bool,
) -> str:
    """Replace *old* with *new* using whitespace-normalised matching.

    We locate the positions in the normalised string, then map them back
    to the original content to preserve the original whitespace style.
    """
    # Build a mapping from normalised positions to original positions
    lines = content.split("\n")
    norm_lines: list[str] = []
    orig_line_indices: list[int] = []  # which original line each norm char came from

    for i, line in enumerate(lines):
        norm = " ".join(line.split())
        norm_lines.append(norm)

    norm_content = "\n".join(norm_lines)
    norm_old = _normalise_whitespace(old)
    norm_new = _normalise_whitespace(new)

    if replace_all:
        norm_result = norm_content.replace(norm_old, norm_new)
    else:
        norm_result = norm_content.replace(norm_old, norm_new, 1)

    # The normalised replacement may change line structure, so we
    # reconstruct by replacing matching line groups.  This is approximate
    # but works well enough for typical edits.
    # Strategy: split both original and normalised-result into lines,
    # then for each group of lines that matched norm_old, substitute
    # the corresponding norm_new lines, keeping original indentation.
    result_lines = norm_result.split("\n")

    # Simple approach: if line counts match, just replace line by line
    # preserving original indentation where possible.
    old_line_count = old.count("\n") + 1
    new_line_count = new.count("\n") + 1

    if old_line_count == new_line_count:
        # Line-for-line replacement preserving indentation
        old_lines = old.split("\n")
        new_lines = new.split("\n")
        result = content
        for o_line, n_line in zip(old_lines, new_lines):
            # Find the original line and replace it
            for i, line in enumerate(lines):
                if _normalise_whitespace_single(line) == _normalise_whitespace_single(o_line):
                    # Preserve original indentation
                    indent = len(line) - len(line.lstrip())
                    new_indent = len(n_line) - len(n_line.lstrip())
                    result = result.replace(line, " " * indent + n_line.lstrip(), 1)
                    break
        return result

    # Fallback: return the normalised result
    return norm_result


def _normalise_whitespace_single(line: str) -> str:
    """Normalise whitespace in a single line."""
    return " ".join(line.split())


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
