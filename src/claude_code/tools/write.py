"""Write tool — create or overwrite files atomically.

Existing files must be read before they can be written (enforced via
the shared :class:`ToolContext` read-file tracker).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult


class WriteTool(Tool):
    """Write content to a file, creating parent directories as needed.

    Writes are atomic: content is written to a temporary file in the same
    directory and then renamed into place.
    """

    name = "Write"
    description = (
        "Writes content to a file at the given path. Creates parent "
        "directories if they do not exist. For existing files, the file "
        "must have been read previously in this session (use the Read tool first)."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        import aiofiles

        file_path_str: str = kwargs["file_path"]
        content: str = kwargs["content"]

        path = Path(file_path_str).expanduser().resolve()

        # -- guard: must read before overwriting existing files ------------
        if path.exists() and not self.context.has_read(str(path)):
            return ToolResult.error(
                f"File '{path}' already exists but has not been read. "
                "Please use the Read tool first before overwriting."
            )

        # -- create parent directories ------------------------------------
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return ToolResult.error(
                f"Permission denied creating directory: {path.parent}"
            )
        except OSError as exc:
            return ToolResult.error(f"Cannot create directory: {exc}")

        # -- atomic write -------------------------------------------------
        try:
            encoded = content.encode("utf-8")

            # Write to a temp file in the same directory, then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent),
                prefix=f".{path.name}.tmp.",
            )
            try:
                async with aiofiles.open(fd, "wb") as f:
                    await f.write(encoded)
                # Atomic rename (POSIX guarantees this)
                os.replace(tmp_path, str(path))
            except Exception:
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        except PermissionError:
            return ToolResult.error(f"Permission denied writing: {path}")
        except OSError as exc:
            return ToolResult.error(f"Failed to write file: {exc}")

        # Mark as read so subsequent edits are allowed
        self.context.mark_read(str(path))

        return ToolResult.success(
            f"Successfully wrote {len(content)} characters to {path}"
        )
