"""Bash tool — persistent shell session with PTY support.

Maintains a long-lived shell subprocess so that state (environment
variables, working directory, shell functions) persists between
tool invocations within the same session.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Any

from claude_code.tools.base import Tool, ToolContext, ToolResult

# Default timeout in milliseconds
_DEFAULT_TIMEOUT_MS = 120_000

# Maximum output size (bytes) before truncation
_MAX_OUTPUT_SIZE = 100_000

# Marker used to detect command completion in the persistent shell
_SENTINEL = "___CLAUDE_CMD_DONE___"


class _PersistentShell:
    """Manages a single long-lived shell subprocess.

    The shell is started once and reused for all subsequent commands.
    State (env vars, cwd, aliases) persists between calls.
    """

    def __init__(self, cwd: str, timeout_ms: int = _DEFAULT_TIMEOUT_MS) -> None:
        self._cwd = cwd
        self._timeout_ms = timeout_ms
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._started = False

    async def start(self) -> None:
        """Start the persistent shell process."""
        if self._started:
            return

        shell = os.environ.get("SHELL", "/bin/bash")

        self._proc = await asyncio.create_subprocess_exec(
            shell,
            "-i",  # interactive mode for aliases/functions
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
            # Start in a new process group so we can kill children
            start_new_session=True,
            env={
                **os.environ,
                # Disable prompts and colour for cleaner output
                "PS1": "",
                "PROMPT_COMMAND": "",
                "TERM": "dumb",
                "NO_COLOR": "1",
                # Prevent interactive pagers
                "PAGER": "cat",
                "GIT_PAGER": "cat",
            },
        )
        self._started = True

        # Let the shell initialise
        await asyncio.sleep(0.1)

    async def execute(
        self,
        command: str,
        timeout_ms: int | None = None,
    ) -> tuple[str, int]:
        """Run *command* in the persistent shell.

        Returns ``(output, exit_code)``.  If the command times out, the
        process group is killed and partial output is returned with
        exit code -1.
        """
        async with self._lock:
            if not self._started:
                await self.start()

            assert self._proc is not None
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None

            timeout_s = (timeout_ms or self._timeout_ms) / 1000.0

            # Send the command followed by a sentinel that echoes the exit code
            full_command = (
                f"{command}\n"
                f'echo "{_SENTINEL} $?"\n'
            )
            self._proc.stdin.write(full_command.encode("utf-8"))
            await self._proc.stdin.drain()

            # Read output until we see the sentinel or timeout
            output_lines: list[str] = []
            exit_code = -1
            start_time = time.monotonic()

            try:
                while True:
                    elapsed = time.monotonic() - start_time
                    remaining = timeout_s - elapsed

                    if remaining <= 0:
                        raise asyncio.TimeoutError()

                    try:
                        line_bytes = await asyncio.wait_for(
                            self._proc.stdout.readline(),
                            timeout=min(remaining, 1.0),
                        )
                    except asyncio.TimeoutError:
                        if time.monotonic() - start_time >= timeout_s:
                            raise
                        continue

                    if not line_bytes:
                        # EOF — shell died
                        break

                    line = line_bytes.decode("utf-8", errors="replace")

                    # Check for sentinel
                    if _SENTINEL in line:
                        # Extract exit code
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            try:
                                exit_code = int(parts[-1])
                            except ValueError:
                                exit_code = -1
                        break

                    output_lines.append(line)

                    # Safety: truncate huge output
                    total = sum(len(l) for l in output_lines)
                    if total > _MAX_OUTPUT_SIZE:
                        output_lines.append(
                            "\n... (output truncated at "
                            f"{_MAX_OUTPUT_SIZE} bytes) ...\n"
                        )
                        break

            except asyncio.TimeoutError:
                # Kill the process group
                await self._kill()
                output_lines.append(
                    f"\n... (command timed out after {timeout_s:.0f}s) ...\n"
                )
                exit_code = -1
                # Restart shell for next command
                self._started = False
                await self.start()

            output = "".join(output_lines)
            return output, exit_code

    async def _kill(self) -> None:
        """Kill the shell process group."""
        if self._proc is None:
            return
        try:
            pgid = os.getpgid(self._proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass
        try:
            self._proc.kill()
        except ProcessLookupError:
            pass

    async def stop(self) -> None:
        """Gracefully stop the shell."""
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            self._proc.terminate()
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            await self._kill()
        self._started = False

    @property
    def is_alive(self) -> bool:
        return self._started and self._proc is not None and self._proc.returncode is None


class BashTool(Tool):
    """Execute shell commands in a persistent session.

    The shell state (working directory, environment variables, aliases)
    persists between invocations.  Each command runs with a configurable
    timeout and returns stdout/stderr merged together.
    """

    name = "Bash"
    description = (
        "Executes a shell command in a persistent session. State (cwd, "
        "env vars, aliases) persists between calls. Supports timeouts "
        "and returns merged stdout/stderr output."
    )
    category = "shell"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    "Timeout in milliseconds. Defaults to 120000 (2 minutes)."
                ),
                "minimum": 1000,
                "maximum": 600000,
            },
            "description": {
                "type": "string",
                "description": (
                    "A short human-readable description of what the command "
                    "does (for logging/display purposes)."
                ),
            },
        },
        "required": ["command"],
    }

    def __init__(self, context: ToolContext | None = None) -> None:
        super().__init__(context)
        self._shell: _PersistentShell | None = None

    async def _get_shell(self) -> _PersistentShell:
        """Return the persistent shell, creating it if necessary."""
        if self._shell is None or not self._shell.is_alive:
            self._shell = _PersistentShell(cwd=self.context.cwd)
            await self._shell.start()
        return self._shell

    async def execute(self, **kwargs: Any) -> ToolResult:
        command: str = kwargs["command"]
        timeout_ms: int = kwargs.get("timeout", _DEFAULT_TIMEOUT_MS)
        description: str = kwargs.get("description", "")

        if not command.strip():
            return ToolResult.error("Empty command.")

        shell = await self._get_shell()

        # Execute
        output, exit_code = await shell.execute(command, timeout_ms)

        # Track cwd changes
        cwd_output, _ = await shell.execute("pwd", timeout_ms=5000)
        new_cwd = cwd_output.strip()
        if new_cwd and os.path.isdir(new_cwd):
            self.context.cwd = new_cwd

        # Build result
        header = ""
        if description:
            header = f"$ {description}\n"

        result_text = header + output
        if exit_code != 0:
            result_text += f"\n[Exit code: {exit_code}]"

        return ToolResult(content=result_text, is_error=(exit_code != 0))

    async def cleanup(self) -> None:
        """Stop the persistent shell.  Call when the session ends."""
        if self._shell is not None:
            await self._shell.stop()
            self._shell = None
