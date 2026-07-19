"""NotebookRead tool — read Jupyter notebook (.ipynb) files.

Parses the JSON structure of ``.ipynb`` files and returns cells with
their type, source content, and rendered outputs.  Cell numbers are
included for reference when using NotebookEdit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolResult


class NotebookReadTool(Tool):
    """Read a Jupyter notebook and return its cells with outputs."""

    name = "NotebookRead"
    description = (
        "Reads a Jupyter notebook (.ipynb) file and returns its cells "
        "with cell_type, source, and rendered outputs. Cell numbers are "
        "shown for reference. Handles code, markdown, and raw cells."
    )
    category = "file"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Absolute path to the .ipynb file.",
            },
        },
        "required": ["notebook_path"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        notebook_path_str: str = kwargs["notebook_path"]
        path = Path(notebook_path_str).expanduser().resolve()

        if not path.exists():
            return ToolResult.error(f"File not found: {path}")

        if not path.is_file():
            return ToolResult.error(f"Not a file: {path}")

        if path.suffix.lower() != ".ipynb":
            return ToolResult.error(
                f"Expected .ipynb file, got '{path.suffix}'"
            )

        try:
            raw = path.read_text(encoding="utf-8")
        except PermissionError:
            return ToolResult.error(f"Permission denied: {path}")
        except OSError as exc:
            return ToolResult.error(f"Cannot read file: {exc}")

        try:
            notebook = json.loads(raw)
        except json.JSONDecodeError as exc:
            return ToolResult.error(f"Invalid notebook JSON: {exc}")

        # Validate basic structure
        if not isinstance(notebook, dict) or "cells" not in notebook:
            return ToolResult.error(
                "Invalid notebook structure: missing 'cells' key"
            )

        cells: list[dict[str, Any]] = notebook["cells"]
        if not isinstance(cells, list):
            return ToolResult.error("Invalid notebook: 'cells' is not a list")

        self.context.mark_read(str(path))

        # Render cells
        lines: list[str] = []
        metadata = notebook.get("metadata", {})
        kernel = metadata.get("kernelspec", {}).get("display_name", "unknown")
        lines.append(f"Notebook: {path.name} ({len(cells)} cells, kernel: {kernel})")
        lines.append("")

        for idx, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "unknown")
            source = _join_source(cell.get("source", []))
            outputs = cell.get("outputs", [])

            lines.append(f"<cell id=\"{idx}\">")
            lines.append(f"Cell {idx} [{cell_type}]")

            if source:
                lines.append(source)

            if cell_type == "code" and outputs:
                rendered = _render_outputs(outputs)
                if rendered:
                    lines.append("Output:")
                    lines.append(rendered)

            lines.append(f"</cell>")
            lines.append("")

        return ToolResult.success("\n".join(lines))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _join_source(source: Any) -> str:
    """Join notebook cell source into a single string."""
    if isinstance(source, list):
        return "".join(source)
    if isinstance(source, str):
        return source
    return ""


def _render_outputs(outputs: list[Any]) -> str:
    """Render cell outputs into a readable text representation."""
    parts: list[str] = []

    for output in outputs:
        if not isinstance(output, dict):
            continue

        output_type = output.get("output_type", "")

        if output_type == "stream":
            text = _join_source(output.get("text", []))
            parts.append(text)

        elif output_type in ("display_data", "execute_result"):
            data = output.get("data", {})
            # Prefer text/plain, then text/html summary
            if "text/plain" in data:
                text = _join_source(data["text/plain"])
                parts.append(text)
            elif "text/html" in data:
                html = _join_source(data["text/html"])
                # Simple HTML strip for readability
                parts.append(f"[HTML output: {len(html)} chars]")
            elif "image/png" in data:
                parts.append("[image/png output]")
            elif "image/jpeg" in data:
                parts.append("[image/jpeg output]")

        elif output_type == "error":
            ename = output.get("ename", "Error")
            evalue = output.get("evalue", "")
            parts.append(f"{ename}: {evalue}")

    return "\n".join(parts)
