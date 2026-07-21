"""MultiEdit tool — apply multiple edits atomically.

All edits are validated before any are applied, so a failure in one
edit does not leave the file in a partially-edited state.
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult
from claude_code.tools.edit import _find_and_replace, _strip_line_numbers
from claude_code.utils.file_utils import read_file_with_encoding


class MultiEditTool(Tool):
    """Apply a batch of string replacements to a single file.

    Each edit in the ``edits`` list is an object with ``old_string``,
    ``new_string``, and optional ``replace_all``.  The first edit may
    use an empty ``old_string`` to create a new file.

    If any edit fails validation, the entire batch is rejected and the
    file is left unchanged.
    """

    name = "MultiEdit"
    description = (
        "Applies multiple edits to a file atomically. All edits are "
        "validated before any are applied. Each edit specifies an "
        "old_string/new_string pair. The file must have been read "
        "previously (unless creating a new file)."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit.",
            },
            "edits": {
                "type": "array",
                "description": "List of edit operations to apply in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {
                            "type": "string",
                            "description": "Text to find (empty string to create new file).",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "Replacement text.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace all occurrences.",
                        },
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["file_path", "edits"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        file_path_str: str = kwargs["file_path"]
        edits: list[dict[str, Any]] = kwargs["edits"]

        path = Path(file_path_str).expanduser().resolve()

        if not edits:
            return ToolResult.error("No edits provided.")

        # Determine if this is a file creation (first edit with empty old_string)
        is_creation = not path.exists() and edits[0].get("old_string", "") == ""

        if not is_creation:
            if not path.exists():
                return ToolResult.error(f"File not found: {path}")
            if not self.context.has_read(str(path)):
                return ToolResult.error(
                    f"File '{path}' has not been read. "
                    "Please use the Read tool before editing."
                )

        # -- read or initialise content ------------------------------------
        encoding = "utf-8"
        uses_crlf = False
        if is_creation:
            content = ""
            path.parent.mkdir(parents=True, exist_ok=True)
        else:
            try:
                raw_content, encoding = read_file_with_encoding(path)
            except (PermissionError, OSError) as exc:
                return ToolResult.error(f"Cannot read file: {exc}")
            # Normalise line endings for matching; restored on write.
            uses_crlf = "\r\n" in raw_content
            content = raw_content.replace("\r\n", "\n")

        original_content = content

        # -- apply edits sequentially, collecting errors -------------------
        errors: list[str] = []

        for i, edit in enumerate(edits):
            old_string = _strip_line_numbers(edit.get("old_string", ""))
            new_string = edit.get("new_string", "")
            replace_all = edit.get("replace_all", False)

            if not old_string and i == 0 and is_creation:
                # Creating a new file: content starts as new_string
                content = new_string
                continue

            new_content, error = _find_and_replace(content, old_string, new_string, replace_all)

            if error:
                # Error message
                errors.append(f"Edit {i + 1}: {error}")
            else:
                content = new_content

        if errors:
            return ToolResult.error(
                "MultiEdit failed (file unchanged):\n" + "\n".join(errors)
            )

        if content == original_content:
            return ToolResult.success("No changes — edits produced identical content.")

        # -- write back ----------------------------------------------------
        out = content.replace("\n", "\r\n") if uses_crlf else content
        try:
            data = out.encode(encoding)
        except LookupError:
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

        self.context.mark_read(str(path))

        # -- diff ----------------------------------------------------------
        diff_lines = difflib.unified_diff(
            original_content.splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
        diff = "".join(diff_lines)

        action = "Created" if is_creation else "Edited"
        return ToolResult.success(
            f"{action} {path} ({len(edits)} edits applied)\n\n{diff}"
        )
