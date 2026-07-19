"""Basic Language Server Protocol (LSP) client.

Provides async access to LSP features like diagnostics, completions,
and hover information by spawning a language server subprocess and
communicating over stdio.

This is a minimal implementation that supports the subset of LSP
needed for IDE-style diagnostics in a terminal coding tool.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from claude_code.utils.logging import get_logger

logger = get_logger("services.lsp_client")


@dataclass
class Diagnostic:
    """A single LSP diagnostic (error, warning, etc.)."""

    severity: int  # 1=error, 2=warning, 3=info, 4=hint
    line: int  # 0-based
    col: int  # 0-based
    end_line: int
    end_col: int
    message: str
    source: str = ""
    code: str = ""

    @property
    def severity_label(self) -> str:
        """Human-readable severity."""
        return {1: "error", 2: "warning", 3: "info", 4: "hint"}.get(self.severity, "unknown")

    def format(self) -> str:
        """Format as a one-line string."""
        return f"{self.severity_label}: {self.message} (line {self.line + 1})"


@dataclass
class CompletionItem:
    """A single completion suggestion."""

    label: str
    kind: int = 1  # LSP CompletionItemKind
    detail: str = ""
    insert_text: str = ""


class LSPClient:
    """Basic Language Server Protocol client.

    Spawns a language server process and communicates via JSON-RPC
    over stdin/stdout.

    Parameters
    ----------
    command:
        The language server executable (e.g. ``"pylsp"``, ``"rust-analyzer"``).
    args:
        Additional arguments for the server process.
    root_uri:
        Workspace root URI. Defaults to current directory.
    """

    def __init__(
        self,
        command: str,
        args: Optional[list[str]] = None,
        root_uri: str = "",
    ) -> None:
        self._command = command
        self._args = args or []
        self._root_uri = root_uri or f"file://{Path.cwd()}"

        self._process: Optional[asyncio.subprocess.Process] = None
        self._initialized = False
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._server_capabilities: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the language server and perform initialization handshake."""
        if self._process is not None:
            return

        self._process = await asyncio.create_subprocess_exec(
            self._command,
            *self._args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Start background reader
        self._reader_task = asyncio.create_task(self._read_loop())

        # Initialize handshake
        init_params = {
            "processId": None,
            "rootUri": self._root_uri,
            "capabilities": {
                "textDocument": {
                    "completion": {"completionItem": {"snippetSupport": False}},
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "publishDiagnostics": {"relatedInformation": False},
                },
            },
        }

        result = await self._request("initialize", init_params)
        if result:
            self._server_capabilities = result.get("capabilities", {})

        # Send initialized notification
        await self._notify("initialized", {})
        self._initialized = True
        logger.info(f"LSP server started: {self._command}")

    async def stop(self) -> None:
        """Gracefully shut down the language server."""
        if self._process is None:
            return

        try:
            await self._request("shutdown", None)
            await self._notify("exit", None)
        except Exception:
            pass

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()

        self._process = None
        self._initialized = False
        logger.info("LSP server stopped")

    # ------------------------------------------------------------------
    # LSP operations
    # ------------------------------------------------------------------

    async def get_diagnostics(self, file_path: str) -> list[Diagnostic]:
        """Request diagnostics for a file.

        Opens the document (if not already open), requests diagnostics,
        and returns them.

        Args:
            file_path: Absolute path to the file.

        Returns:
            List of :class:`Diagnostic` objects.
        """
        self._ensure_started()
        uri = self._path_to_uri(file_path)

        # Open the document
        content = ""
        try:
            content = Path(file_path).read_text(encoding="utf-8")
        except OSError as exc:
            logger.debug(f"Cannot read file for diagnostics: {exc}")
            return []

        await self._notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": self._guess_language(file_path),
                "version": 1,
                "text": content,
            },
        })

        # Request diagnostics (pull model — some servers push via publishDiagnostics)
        try:
            result = await self._request("textDocument/diagnostic", {
                "textDocument": {"uri": uri},
            })
        except Exception:
            # Server may not support pull diagnostics; return empty
            return []

        if not result:
            return []

        items = result.get("items", [])
        return [self._parse_diagnostic(d) for d in items]

    async def get_completions(
        self, file_path: str, line: int, col: int
    ) -> list[CompletionItem]:
        """Request completions at a position.

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            col: 0-based column number.

        Returns:
            List of :class:`CompletionItem` objects.
        """
        self._ensure_started()
        uri = self._path_to_uri(file_path)

        result = await self._request("textDocument/completion", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })

        if not result:
            return []

        items = result if isinstance(result, list) else result.get("items", [])
        return [
            CompletionItem(
                label=item.get("label", ""),
                kind=item.get("kind", 1),
                detail=item.get("detail", ""),
                insert_text=item.get("insertText", item.get("label", "")),
            )
            for item in items
        ]

    async def get_hover(self, file_path: str, line: int, col: int) -> str:
        """Request hover information at a position.

        Args:
            file_path: Absolute path to the file.
            line: 0-based line number.
            col: 0-based column number.

        Returns:
            Hover text as a string, or empty string if unavailable.
        """
        self._ensure_started()
        uri = self._path_to_uri(file_path)

        result = await self._request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": col},
        })

        if not result:
            return ""

        contents = result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", "")
        if isinstance(contents, list):
            parts = []
            for c in contents:
                if isinstance(c, dict):
                    parts.append(c.get("value", ""))
                elif isinstance(c, str):
                    parts.append(c)
            return "\n".join(parts)
        return str(contents)

    # ------------------------------------------------------------------
    # JSON-RPC transport
    # ------------------------------------------------------------------

    async def _request(self, method: str, params: Any) -> Any:
        """Send a JSON-RPC request and wait for the response."""
        request_id = str(uuid.uuid4())
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future

        await self._send(message)

        try:
            return await asyncio.wait_for(future, timeout=10.0)
        except asyncio.TimeoutError:
            self._pending.pop(request_id, None)
            raise RuntimeError(f"LSP request {method} timed out")

    async def _notify(self, method: str, params: Any) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        await self._send(message)

    async def _send(self, message: dict[str, Any]) -> None:
        """Write a JSON-RPC message to the server's stdin."""
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("LSP server not started")

        body = json.dumps(message)
        header = f"Content-Length: {len(body)}\r\n\r\n"
        self._process.stdin.write(header.encode("ascii") + body.encode("utf-8"))
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """Background loop that reads JSON-RPC messages from stdout."""
        if self._process is None or self._process.stdout is None:
            return

        try:
            while True:
                # Read headers
                headers: dict[str, str] = {}
                while True:
                    line_bytes = await self._process.stdout.readline()
                    if not line_bytes:
                        return
                    line = line_bytes.decode("ascii").strip()
                    if not line:
                        break
                    if ":" in line:
                        key, _, value = line.partition(":")
                        headers[key.strip()] = value.strip()

                content_length = int(headers.get("Content-Length", "0"))
                if content_length == 0:
                    continue

                # Read body
                body = await self._process.stdout.readexactly(content_length)
                message = json.loads(body.decode("utf-8"))

                # Dispatch
                msg_id = message.get("id")
                if msg_id and msg_id in self._pending:
                    future = self._pending.pop(msg_id)
                    if "error" in message:
                        future.set_exception(
                            RuntimeError(
                                f"LSP error: {message['error'].get('message', 'unknown')}"
                            )
                        )
                    else:
                        future.set_result(message.get("result"))

        except asyncio.CancelledError:
            pass
        except asyncio.IncompleteReadError:
            pass
        except Exception as exc:
            logger.debug(f"LSP reader error: {exc}")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        """Raise if the server is not running."""
        if not self._initialized or self._process is None:
            raise RuntimeError("LSP client not started. Call start() first.")

    @staticmethod
    def _path_to_uri(path: str) -> str:
        """Convert a file path to a file:// URI."""
        abs_path = str(Path(path).resolve())
        return f"file://{abs_path}"

    @staticmethod
    def _guess_language(file_path: str) -> str:
        """Guess the LSP languageId from file extension."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".jsx": "javascriptreact",
            ".rs": "rust",
            ".go": "go",
            ".java": "java",
            ".c": "c",
            ".cpp": "cpp",
            ".h": "c",
            ".hpp": "cpp",
            ".rb": "ruby",
            ".lua": "lua",
            ".sh": "shellscript",
        }
        ext = Path(file_path).suffix.lower()
        return ext_map.get(ext, "plaintext")

    @staticmethod
    def _parse_diagnostic(raw: dict[str, Any]) -> Diagnostic:
        """Parse a raw LSP diagnostic dict into our dataclass."""
        r = raw.get("range", {})
        start = r.get("start", {})
        end = r.get("end", {})
        return Diagnostic(
            severity=raw.get("severity", 1),
            line=start.get("line", 0),
            col=start.get("character", 0),
            end_line=end.get("line", 0),
            end_col=end.get("character", 0),
            message=raw.get("message", ""),
            source=raw.get("source", ""),
            code=str(raw.get("code", "")),
        )
