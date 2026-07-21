"""NotebookEdit tool — edit Jupyter notebook (.ipynb) cells.

Supports three modes:
- **replace**: overwrite an existing cell's source content.
- **insert**: add a new cell after the given cell index.
- **delete**: remove a cell.

The notebook is validated after each edit and saved atomically.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Valid cell types for insert/replace operations
_VALID_CELL_TYPES = frozenset({"code", "markdown", "raw"})

# Valid edit modes
_VALID_EDIT_MODES = frozenset({"replace", "insert", "delete"})


class NotebookEditTool(Tool):
    """Edit cells in a Jupyter notebook."""

    name = "NotebookEdit"
    description = (
        "Edits a Jupyter notebook (.ipynb) file. Supports replacing cell "
        "content, inserting new cells, and deleting cells. The notebook "
        "must have been read previously with NotebookRead."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Absolute path to the .ipynb file.",
            },
            "cell_id": {
                "type": "integer",
                "description": (
                    "Zero-based cell index. Required for replace/delete; "
                    "optional for insert (omit to insert at beginning)."
                ),
                "minimum": 0,
            },
            "new_source": {
                "type": "string",
                "description": "New source content for the cell. Required for replace/insert.",
            },
            "cell_type": {
                "type": "string",
                "description": "Cell type for new cells: code, markdown, or raw. Defaults to code.",
                "enum": ["code", "markdown", "raw"],
            },
            "edit_mode": {
                "type": "string",
                "description": "Edit operation: replace, insert, or delete. Defaults to replace.",
                "enum": ["replace", "insert", "delete"],
            },
        },
        "required": ["notebook_path"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        notebook_path_str: str = kwargs["notebook_path"]
        edit_mode: str = kwargs.get("edit_mode", "replace")
        cell_type: str = kwargs.get("cell_type", "code")
        cell_id: int | None = kwargs.get("cell_id")
        new_source: str | None = kwargs.get("new_source")

        if edit_mode not in _VALID_EDIT_MODES:
            valid = ", ".join(sorted(_VALID_EDIT_MODES))
            return ToolResult.error(f"Invalid edit_mode '{edit_mode}'. Must be one of: {valid}")

        path = Path(notebook_path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult.error(f"File not found: {path}")

        if not self.context.has_read(str(path)):
            return ToolResult.error(
                f"Notebook '{path}' has not been read. "
                "Please use NotebookRead first."
            )

        # Read and parse
        try:
            raw = path.read_text(encoding="utf-8")
            notebook = json.loads(raw)
        except (PermissionError, OSError) as exc:
            return ToolResult.error(f"Cannot read file: {exc}")
        except json.JSONDecodeError as exc:
            return ToolResult.error(f"Invalid notebook JSON: {exc}")

        if not isinstance(notebook, dict) or "cells" not in notebook:
            return ToolResult.error("Invalid notebook structure: missing 'cells'")

        cells: list[dict[str, Any]] = notebook["cells"]

        # Validate cell_id
        if edit_mode in ("replace", "delete") and cell_id is None:
            return ToolResult.error(f"cell_id is required for '{edit_mode}' mode")

        if cell_id is not None and (cell_id < 0 or cell_id >= len(cells)):
            return ToolResult.error(
                f"cell_id {cell_id} out of range "
                f"(notebook has {len(cells)} cells, 0-{len(cells) - 1})"
            )

        # Dispatch
        if edit_mode == "replace":
            result = _do_replace(cells, cell_id, new_source, cell_type)  # type: ignore[arg-type]
        elif edit_mode == "insert":
            result = _do_insert(cells, cell_id, new_source, cell_type)
        else:  # delete
            result = _do_delete(cells, cell_id)  # type: ignore[arg-type]

        if isinstance(result, str):
            return ToolResult.error(result)

        # Save atomically
        try:
            new_json = json.dumps(notebook, indent=1, ensure_ascii=False) + "\n"
            fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent),
                prefix=f".{path.name}.tmp.",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(new_json)
                os.replace(tmp_path, str(path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except (PermissionError, OSError) as exc:
            return ToolResult.error(f"Failed to save notebook: {exc}")

        # Mark as read (content changed)
        self.context.mark_read(str(path))

        cell_label = cell_id if cell_id is not None else "(new)"
        return ToolResult.success(
            f"Notebook updated: {edit_mode} cell {cell_label} in {path.name}"
        )


# ---------------------------------------------------------------------------
# Edit operations
# ---------------------------------------------------------------------------


def _do_replace(
    cells: list[dict[str, Any]],
    cell_id: int,
    new_source: str | None,
    cell_type: str,
) -> str | None:
    """Replace cell content. Returns error string or None on success."""
    if new_source is None:
        return "new_source is required for 'replace' mode"

    if cell_type not in _VALID_CELL_TYPES:
        return f"Invalid cell_type '{cell_type}'"

    cell = cells[cell_id]
    cell["source"] = _split_source(new_source)
    if cell_type != cell.get("cell_type"):
        cell["cell_type"] = cell_type

    return None


def _do_insert(
    cells: list[dict[str, Any]],
    cell_id: int | None,
    new_source: str | None,
    cell_type: str,
) -> str | None:
    """Insert a new cell. Returns error string or None on success."""
    if new_source is None:
        return "new_source is required for 'insert' mode"

    if cell_type not in _VALID_CELL_TYPES:
        return f"Invalid cell_type '{cell_type}'"

    new_cell: dict[str, Any] = {
        "cell_type": cell_type,
        "source": _split_source(new_source),
        "metadata": {},
    }

    if cell_type == "code":
        new_cell["outputs"] = []
        new_cell["execution_count"] = None

    # Insert position: after cell_id, or at beginning if cell_id is None
    if cell_id is None:
        insert_idx = 0
    else:
        insert_idx = cell_id + 1

    cells.insert(insert_idx, new_cell)
    return None


def _do_delete(
    cells: list[dict[str, Any]],
    cell_id: int,
) -> str | None:
    """Delete a cell. Returns error string or None on success."""
    if len(cells) <= 1:
        return "Cannot delete the last cell in the notebook"

    del cells[cell_id]
    return None


# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------


def _split_source(source: str) -> list[str]:
    """Split source text into a list of lines (notebook format).

    Each line except the last gets a trailing newline, matching the
    standard .ipynb source array format.
    """
    if not source:
        return []

    lines = source.split("\n")
    result: list[str] = []
    for i, line in enumerate(lines):
        if i < len(lines) - 1:
            result.append(line + "\n")
        else:
            # Last line: no trailing newline
            if line:
                result.append(line)
    return result
