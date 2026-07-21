"""MCP transport layer — stdio and SSE.

Provides :class:`StdioTransport` for spawning a child process and
communicating via JSON lines over stdin/stdout, and :class:`SSETransport`
for HTTP-based MCP servers using Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

from claude_code.utils.logging import get_logger

logger = get_logger("mcp.transport")

# Default timeouts (seconds)
_CONNECT_TIMEOUT = 30.0
_RECEIVE_TIMEOUT = 120.0
_CLOSE_TIMEOUT = 10.0

# SSE reconnection
_SSE_INITIAL_RETRY_MS = 1000
_SSE_MAX_RETRY_MS = 30_000


class TransportError(Exception):
    """Raised when a transport-level error occurs."""


class Transport(ABC):
    """Abstract transport for MCP communication.

    Subclasses implement spawning a process (stdio) or connecting to a
    remote HTTP endpoint (SSE) and exchanging JSON-RPC messages.
    """

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection."""
        ...

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message."""
        ...

    @abstractmethod
    async def receive(self) -> dict[str, Any]:
        """Receive the next JSON-RPC message.

        Blocks until a message is available or the connection is closed.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the transport and release resources."""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """Whether the transport is currently connected."""
        ...


# ---------------------------------------------------------------------------
# Stdio transport
# ---------------------------------------------------------------------------

class StdioTransport(Transport):
    """JSON-RPC over stdio — spawns a child process.

    Each message is a single JSON object serialised on one line (newline
    delimited).  Stderr is captured for logging but not used for framing.

    Args:
        command: Executable to run (e.g. ``"npx"``).
        args: Command-line arguments.
        env: Extra environment variables merged with ``os.environ``.
        cwd: Working directory for the child process.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self._command = command
        self._args = args or []
        self._env = env
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._connected = False
        self._read_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Spawn the child process."""
        if self._connected:
            return

        proc_env = {**os.environ}
        if self._env:
            proc_env.update(self._env)

        cmd = [self._command, *self._args]
        logger.debug("Starting stdio transport: %s", " ".join(cmd))

        try:
            self._proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=proc_env,
                    cwd=self._cwd,
                    start_new_session=True,
                ),
                timeout=_CONNECT_TIMEOUT,
            )
        except TimeoutError:
            raise TransportError(
                f"Timed out spawning process '{self._command}' "
                f"after {_CONNECT_TIMEOUT}s"
            )
        except FileNotFoundError:
            raise TransportError(
                f"Command not found: '{self._command}'. "
                "Make sure it is installed and on PATH."
            )
        except OSError as exc:
            raise TransportError(
                f"Failed to spawn '{self._command}': {exc}"
            )

        self._connected = True
        # Start a background task to drain stderr for logging
        asyncio.create_task(self._drain_stderr())
        logger.debug("Stdio transport connected (pid=%s)", self._proc.pid)

    async def send(self, message: dict[str, Any]) -> None:
        """Write a JSON line to the process stdin."""
        if not self._connected or self._proc is None:
            raise TransportError("Transport is not connected")
        assert self._proc.stdin is not None

        line = json.dumps(message, ensure_ascii=False) + "\n"
        async with self._write_lock:
            try:
                self._proc.stdin.write(line.encode("utf-8"))
                await self._proc.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                self._connected = False
                raise TransportError(f"Process stdin closed: {exc}")

    async def receive(self) -> dict[str, Any]:
        """Read a JSON line from the process stdout."""
        if not self._connected or self._proc is None:
            raise TransportError("Transport is not connected")
        assert self._proc.stdout is not None

        async with self._read_lock:
            # Skip blank lines within the same lock acquisition — recursing
            # into receive() here would deadlock on the non-reentrant lock.
            while True:
                try:
                    raw = await self._proc.stdout.readline()
                except (ConnectionResetError, ValueError) as exc:
                    self._connected = False
                    raise TransportError(f"Read error: {exc}")

                if not raw:
                    self._connected = False
                    raise TransportError("Process stdout closed (server exited)")

                text = raw.decode("utf-8", errors="replace").strip()
                if text:
                    break
                # Blank line — some servers emit them; read the next line.

            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise TransportError(
                    f"Invalid JSON from server: {exc}\nLine: {text[:200]}"
                )

    async def close(self) -> None:
        """Terminate the child process."""
        if self._proc is None:
            return

        self._connected = False

        # Close stdin to signal the process
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except Exception:
            pass

        # Give it a moment to exit gracefully
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=_CLOSE_TIMEOUT)
        except TimeoutError:
            logger.warning(
                "Process pid=%s did not exit in %ss, terminating",
                self._proc.pid, _CLOSE_TIMEOUT,
            )
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except (TimeoutError, ProcessLookupError):
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass

        self._proc = None
        logger.debug("Stdio transport closed")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._proc is not None

    # -- internal -----------------------------------------------------------

    async def _drain_stderr(self) -> None:
        """Read stderr in the background for logging."""
        if self._proc is None or self._proc.stderr is None:
            return
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    logger.debug("[mcp:stderr] %s", text)
        except (ValueError, ConnectionResetError):
            pass


# ---------------------------------------------------------------------------
# SSE transport
# ---------------------------------------------------------------------------

class SSETransport(Transport):
    """Server-Sent Events transport for HTTP-based MCP servers.

    Uses ``aiohttp`` for HTTP requests and SSE streaming.  The transport
    opens a long-lived GET connection to receive server messages and
    sends client messages via POST to a separate endpoint advertised by
    the server.

    Args:
        url: SSE endpoint URL (e.g. ``"https://mcp.example.com/sse"``).
        headers: Extra HTTP headers (e.g. ``{"Authorization": "Bearer ..."}``).
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._connected = False
        self._session: Any = None  # aiohttp.ClientSession (lazy import)
        self._sse_response: Any = None  # aiohttp.ClientResponse
        self._post_url: str | None = None
        self._message_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._sse_task: asyncio.Task[None] | None = None
        self._retry_ms = _SSE_INITIAL_RETRY_MS

    async def connect(self) -> None:
        """Connect to the SSE endpoint."""
        if self._connected:
            return

        try:
            import aiohttp
        except ImportError:
            raise TransportError(
                "SSE transport requires 'aiohttp'. "
                "Install it with: pip install aiohttp"
            )

        self._session = aiohttp.ClientSession(
            headers=self._headers,
            timeout=aiohttp.ClientTimeout(total=None, sock_read=None),
        )

        try:
            self._sse_response = await asyncio.wait_for(
                self._session.get(
                    self._url,
                    headers={"Accept": "text/event-stream"},
                ),
                timeout=_CONNECT_TIMEOUT,
            )
            self._sse_response.raise_for_status()
        except TimeoutError:
            await self._cleanup_session()
            raise TransportError(
                f"Timed out connecting to SSE endpoint after {_CONNECT_TIMEOUT}s"
            )
        except Exception as exc:
            await self._cleanup_session()
            raise TransportError(f"Failed to connect to SSE endpoint: {exc}")

        self._connected = True
        self._retry_ms = _SSE_INITIAL_RETRY_MS

        # Start background reader
        self._sse_task = asyncio.create_task(self._read_sse_stream())
        logger.debug("SSE transport connected to %s", self._url)

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON-RPC message via POST."""
        if not self._connected:
            raise TransportError("Transport is not connected")

        if self._post_url is None:
            raise TransportError(
                "Server has not advertised a POST endpoint yet. "
                "Wait for the 'endpoint' event."
            )

        try:
            resp = await self._session.post(
                self._post_url,
                json=message,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
        except Exception as exc:
            raise TransportError(f"POST failed: {exc}")

    async def receive(self) -> dict[str, Any]:
        """Receive the next JSON-RPC message from the SSE stream."""
        if not self._connected:
            raise TransportError("Transport is not connected")

        try:
            return await asyncio.wait_for(
                self._message_queue.get(),
                timeout=_RECEIVE_TIMEOUT,
            )
        except TimeoutError:
            raise TransportError(
                f"No message received within {_RECEIVE_TIMEOUT}s"
            )

    async def close(self) -> None:
        """Close the SSE connection and HTTP session."""
        self._connected = False

        if self._sse_task and not self._sse_task.done():
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sse_task = None

        await self._cleanup_session()
        logger.debug("SSE transport closed")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # -- internal -----------------------------------------------------------

    async def _read_sse_stream(self) -> None:
        """Parse the SSE event stream in the background."""
        assert self._sse_response is not None

        event_type = ""
        data_lines: list[str] = []

        try:
            async for raw_line in self._sse_response.content:
                line = raw_line.decode("utf-8", errors="replace").rstrip("\n")

                if not line:
                    # Blank line = event dispatch
                    if data_lines:
                        data = "\n".join(data_lines)
                        await self._handle_sse_event(event_type, data)
                    event_type = ""
                    data_lines = []
                    continue

                if line.startswith(":"):
                    # Comment — ignore
                    continue

                if line.startswith("event:"):
                    event_type = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:"):].strip())
                elif line.startswith("retry:"):
                    try:
                        self._retry_ms = int(line[len("retry:"):].strip())
                    except ValueError:
                        pass

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("SSE stream error: %s", exc)
            if self._connected:
                self._connected = False
                # Attempt reconnection
                asyncio.create_task(self._reconnect())

    async def _handle_sse_event(self, event_type: str, data: str) -> None:
        """Process a parsed SSE event."""
        if event_type == "endpoint":
            # Server advertises the POST URL
            self._post_url = self._resolve_url(data)
            logger.debug("SSE server POST endpoint: %s", self._post_url)

        elif event_type == "message" or event_type == "":
            # JSON-RPC message
            try:
                msg = json.loads(data)
                await self._message_queue.put(msg)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid JSON in SSE message: %s", exc)

        elif event_type == "ping":
            pass  # keep-alive

        else:
            logger.debug("Unknown SSE event type '%s': %s", event_type, data[:200])

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        while not self._connected:
            delay = self._retry_ms / 1000.0
            logger.info("Reconnecting to SSE in %.1fs ...", delay)
            await asyncio.sleep(delay)

            try:
                await self.connect()
                logger.info("SSE reconnected successfully")
                return
            except TransportError as exc:
                logger.warning("SSE reconnect failed: %s", exc)
                self._retry_ms = min(self._retry_ms * 2, _SSE_MAX_RETRY_MS)

    def _resolve_url(self, path: str) -> str:
        """Resolve a possibly-relative URL against the base SSE URL."""
        from urllib.parse import urljoin
        return urljoin(self._url, path)

    async def _cleanup_session(self) -> None:
        """Close the aiohttp session."""
        if self._sse_response is not None:
            try:
                self._sse_response.close()
            except Exception:
                pass
            self._sse_response = None

        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                pass
            self._session = None
