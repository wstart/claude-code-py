"""Code execution tool — run code snippets in a subprocess.

Executes code in an isolated subprocess with a configurable timeout.
Supports Python natively and other languages via their interpreters.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolContext, ToolResult
from claude_code.utils.logging import get_logger

logger = get_logger("tools.execute_code")

# Default execution timeout in seconds
_DEFAULT_TIMEOUT = 30

# Language → interpreter command mapping
_LANGUAGE_COMMANDS: dict[str, dict[str, Any]] = {
    "python": {
        "command": ["python3"],
        "extension": ".py",
        "use_stdin": True,
    },
    "javascript": {
        "command": ["node"],
        "extension": ".js",
        "use_stdin": True,
    },
    "typescript": {
        "command": ["npx", "ts-node", "--transpile-only"],
        "extension": ".ts",
        "use_stdin": False,  # ts-node needs a file
    },
    "ruby": {
        "command": ["ruby"],
        "extension": ".rb",
        "use_stdin": True,
    },
    "bash": {
        "command": ["bash"],
        "extension": ".sh",
        "use_stdin": True,
    },
    "shell": {
        "command": ["sh"],
        "extension": ".sh",
        "use_stdin": True,
    },
    "lua": {
        "command": ["lua"],
        "extension": ".lua",
        "use_stdin": True,
    },
    "go": {
        "command": ["go", "run"],
        "extension": ".go",
        "use_stdin": False,
    },
    "rust": {
        "command": ["rust-script"],
        "extension": ".rs",
        "use_stdin": False,
    },
}


class ExecuteCodeTool(Tool):
    """Execute code in a subprocess and return stdout/stderr."""

    name = "ExecuteCode"
    description = (
        "Execute a code snippet in a subprocess and return stdout/stderr. "
        "Supports Python (default), JavaScript, TypeScript, Ruby, Bash, "
        "Lua, Go, and Rust. Code runs in a temporary directory with a "
        "configurable timeout (default 30s)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The source code to execute.",
            },
            "language": {
                "type": "string",
                "description": (
                    "Programming language. One of: python, javascript, "
                    "typescript, ruby, bash, shell, lua, go, rust. "
                    "Defaults to python."
                ),
                "default": "python",
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds (default 30, max 120).",
                "default": 30,
            },
        },
        "required": ["code"],
    }
    category = "execution"

    def __init__(self, context: ToolContext | None = None) -> None:
        super().__init__(context)

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Execute the code snippet."""
        code: str = kwargs.get("code", "")
        language: str = kwargs.get("language", "python").lower().strip()
        timeout: int = min(int(kwargs.get("timeout", _DEFAULT_TIMEOUT)), 120)

        if not code.strip():
            return ToolResult.error("No code provided.")

        lang_config = _LANGUAGE_COMMANDS.get(language)
        if lang_config is None:
            supported = ", ".join(sorted(_LANGUAGE_COMMANDS.keys()))
            return ToolResult.error(
                f"Unsupported language: {language}. Supported: {supported}"
            )

        command: list[str] = list(lang_config["command"])
        extension: str = lang_config["extension"]
        use_stdin: bool = lang_config["use_stdin"]

        cwd = self.context.cwd if self.context else os.getcwd()
        tmp_dir = tempfile.mkdtemp(prefix="claude_exec_")

        try:
            if use_stdin:
                # Pipe code via stdin
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(input=code.encode("utf-8")),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    return ToolResult.error(
                        f"Execution timed out after {timeout}s"
                    )
            else:
                # Write code to a temp file and run it
                code_file = Path(tmp_dir) / f"main{extension}"
                code_file.write_text(code, encoding="utf-8")
                proc = await asyncio.create_subprocess_exec(
                    *command,
                    str(code_file),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
                try:
                    stdout, stderr = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    return ToolResult.error(
                        f"Execution timed out after {timeout}s"
                    )

            # Format output
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            exit_code = proc.returncode

            parts: list[str] = []
            if out:
                parts.append(out)
            if err:
                parts.append(f"[stderr]\n{err}")
            if exit_code != 0:
                parts.append(f"[exit code: {exit_code}]")

            result_text = "\n".join(parts) if parts else "(no output)"

            if exit_code != 0:
                return ToolResult(
                    content=result_text,
                    is_error=True,
                )
            return ToolResult.success(result_text)

        except FileNotFoundError as exc:
            return ToolResult.error(
                f"Interpreter not found for {language}: {exc}. "
                f"Make sure {command[0]} is installed and on PATH."
            )
        except Exception as exc:
            return ToolResult.error(f"Execution failed: {exc}")
        finally:
            # Clean up temp directory
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)
