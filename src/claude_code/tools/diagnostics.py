"""IDE diagnostics tool — retrieve compiler/linter errors and warnings.

Supports two backends:
1. LSP server (if configured and running)
2. Fallback to common CLI linters (flake8, mypy, pylint, ruff)
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolContext, ToolResult
from claude_code.utils.logging import get_logger

logger = get_logger("tools.diagnostics")


# Mapping of file extension → preferred linter commands
_LINTER_MAP: dict[str, list[list[str]]] = {
    ".py": [
        ["ruff", "check", "--output-format=json", "--stdin-filename={file}", "-"],
        ["flake8", "--format=json", "{file}"],
        ["mypy", "--show-error-codes", "--no-error-summary", "{file}"],
    ],
    ".js": [["eslint", "--format=json", "{file}"]],
    ".ts": [["eslint", "--format=json", "{file}"]],
    ".tsx": [["eslint", "--format=json", "{file}"]],
    ".jsx": [["eslint", "--format=json", "{file}"]],
    ".go": [["golangci-lint", "run", "--out-format=json", "{file}"]],
    ".rs": [["cargo", "check", "--message-format=json"]],
}


class DiagnosticsTool(Tool):
    """Get compiler/linter diagnostics for a file or project."""

    name = "Diagnostics"
    description = (
        "Get compiler and linter diagnostics for a file or the whole project. "
        "Returns errors, warnings, and hints from the language server or "
        "fallback linters (ruff, flake8, mypy, eslint, etc.)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": (
                    "Absolute path to a file to check. If omitted, runs "
                    "diagnostics on the whole project (if supported)."
                ),
            },
        },
        "required": [],
    }
    category = "ide"

    def __init__(self, context: ToolContext | None = None) -> None:
        super().__init__(context)
        self._lsp_client: Any = None

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Run diagnostics and return formatted results."""
        file_path = kwargs.get("file_path", "")

        # Try LSP first
        lsp_results = await self._try_lsp(file_path)
        if lsp_results is not None:
            return lsp_results

        # Fallback to CLI linters
        if file_path:
            return await self._run_cli_linter(file_path)

        return ToolResult.error(
            "No LSP server available and no file_path provided. "
            "Specify a file to check with CLI linters."
        )

    async def _try_lsp(self, file_path: str) -> ToolResult | None:
        """Attempt diagnostics via LSP. Returns None if no LSP available."""
        # Check if an LSP client is attached to the tool context
        lsp = self.context.metadata.get("lsp_client") if self.context else None
        if lsp is None:
            return None

        if not file_path:
            return ToolResult.error("file_path required for LSP diagnostics")

        try:
            diagnostics = await lsp.get_diagnostics(file_path)
            if not diagnostics:
                return ToolResult.success(f"No diagnostics found for {file_path}")

            lines = [f"Diagnostics for {file_path}:\n"]
            for d in diagnostics:
                lines.append(f"  {d.format()}")
            return ToolResult.success("\n".join(lines))
        except Exception as exc:
            logger.debug(f"LSP diagnostics failed: {exc}")
            return None

    async def _run_cli_linter(self, file_path: str) -> ToolResult:
        """Run a CLI linter as fallback."""
        path = Path(file_path)
        if not path.exists():
            return ToolResult.error(f"File not found: {file_path}")

        ext = path.suffix.lower()
        linter_commands = _LINTER_MAP.get(ext, [])

        if not linter_commands:
            return ToolResult.error(
                f"No linter configured for {ext} files. "
                f"Supported: {', '.join(sorted(_LINTER_MAP.keys()))}"
            )

        # Try each linter in order until one is available
        for cmd_template in linter_commands:
            cmd_name = cmd_template[0]
            if not shutil.which(cmd_name):
                continue

            # Build actual command
            cmd = [
                part.replace("{file}", file_path) for part in cmd_template
            ]

            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=self.context.cwd if self.context else None,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=30.0
                )

                output = stdout.decode("utf-8", errors="replace").strip()
                err_output = stderr.decode("utf-8", errors="replace").strip()

                if not output and not err_output:
                    return ToolResult.success(f"No diagnostics found for {file_path}")

                # Format output
                result_parts = []
                if output:
                    result_parts.append(output)
                if err_output and proc.returncode != 0:
                    result_parts.append(err_output)

                combined = "\n".join(result_parts)
                if proc.returncode == 0:
                    return ToolResult.success(f"Diagnostics for {file_path}:\n{combined}")
                else:
                    # Linters exit non-zero when they find issues
                    return ToolResult.success(f"Diagnostics for {file_path}:\n{combined}")

            except asyncio.TimeoutError:
                return ToolResult.error(f"Linter {cmd_name} timed out")
            except Exception as exc:
                logger.debug(f"Linter {cmd_name} failed: {exc}")
                continue

        return ToolResult.error(
            f"No available linter found for {ext} files. "
            f"Install one of: {', '.join(cmd[0] for cmd in linter_commands)}"
        )
