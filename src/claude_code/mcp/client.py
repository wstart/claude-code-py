"""MCP JSON-RPC 2.0 client.

Implements the client side of the Model Context Protocol, handling
request/response correlation, notifications, and error responses.
"""

from __future__ import annotations

import asyncio
from typing import Any

from claude_code.mcp.transport import Transport, TransportError
from claude_code.utils.logging import get_logger

logger = get_logger("mcp.client")

# Default request timeout (seconds)
_DEFAULT_TIMEOUT = 60.0

# MCP protocol version
_PROTOCOL_VERSION = "2024-11-05"

# Client info sent during initialization
_CLIENT_INFO = {
    "name": "claude-code-py",
    "version": "0.1.0",
}


class MCPError(Exception):
    """Base class for MCP protocol errors."""


class MCPRpcError(MCPError):
    """A JSON-RPC error response from the server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class MCPClient:
    """MCP protocol client using JSON-RPC 2.0.

    Manages the lifecycle of an MCP connection: initialisation, tool
    discovery, tool invocation, resource access, and shutdown.

    Args:
        transport: The transport layer (stdio or SSE).
        default_timeout: Default timeout for requests in seconds.
    """

    def __init__(
        self,
        transport: Transport,
        default_timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._transport = transport
        self._timeout = default_timeout
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._initialized = False
        self._server_capabilities: dict[str, Any] = {}
        self._server_info: dict[str, Any] = {}
        self._notification_handlers: dict[str, list[Any]] = {}

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect the transport and start reading messages."""
        await self._transport.connect()
        self._reader_task = asyncio.create_task(self._read_loop())

    async def initialize(self) -> dict[str, Any]:
        """Send the ``initialize`` request and negotiate capabilities.

        Must be called after :meth:`connect` and before any other request.

        Returns:
            The server's ``ServerCapabilities`` dict.
        """
        result = await self._request("initialize", {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": _CLIENT_INFO,
        })

        self._server_capabilities = result.get("capabilities", {})
        self._server_info = result.get("serverInfo", {})
        logger.info(
            "MCP initialized: server=%s version=%s",
            self._server_info.get("name", "?"),
            self._server_info.get("version", "?"),
        )

        # Send the initialized notification (no response expected)
        await self._notify("notifications/initialized", {})
        self._initialized = True
        return result

    async def close(self) -> None:
        """Shut down the client cleanly."""
        # Cancel pending requests
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        # Stop reader
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
            self._reader_task = None

        # Close transport
        await self._transport.close()
        self._initialized = False
        logger.debug("MCP client closed")

    # -- MCP methods --------------------------------------------------------

    async def list_tools(self) -> list[dict[str, Any]]:
        """Get available tools from the server."""
        self._check_initialized()
        result = await self._request("tools/list", {})
        return result.get("tools", [])

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Call a tool on the server.

        Args:
            name: Tool name.
            arguments: Tool arguments.
            timeout: Override the default timeout for this call.

        Returns:
            The ``result`` dict (contains ``content`` and optionally
            ``isError``).
        """
        self._check_initialized()
        return await self._request(
            "tools/call",
            {"name": name, "arguments": arguments},
            timeout=timeout,
        )

    async def list_resources(self) -> list[dict[str, Any]]:
        """Get available resources."""
        self._check_initialized()
        result = await self._request("resources/list", {})
        return result.get("resources", [])

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource by URI.

        Returns:
            Dict with ``contents`` list.
        """
        self._check_initialized()
        return await self._request("resources/read", {"uri": uri})

    async def list_prompts(self) -> list[dict[str, Any]]:
        """Get available prompts."""
        self._check_initialized()
        result = await self._request("prompts/list", {})
        return result.get("prompts", [])

    async def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get a prompt by name.

        Returns:
            Dict with ``messages`` list.
        """
        self._check_initialized()
        params: dict[str, Any] = {"name": name}
        if arguments is not None:
            params["arguments"] = arguments
        return await self._request("prompts/get", params)

    # -- notifications ------------------------------------------------------

    def on_notification(self, method: str, handler: Any) -> None:
        """Register a handler for server notifications.

        Args:
            method: The notification method (e.g.
                ``"notifications/tools/list_changed"``).
            handler: An async callable receiving the notification params.
        """
        self._notification_handlers.setdefault(method, []).append(handler)

    # -- properties ---------------------------------------------------------

    @property
    def server_capabilities(self) -> dict[str, Any]:
        """Server capabilities from the initialize handshake."""
        return self._server_capabilities

    @property
    def server_info(self) -> dict[str, Any]:
        """Server info from the initialize handshake."""
        return self._server_info

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    # -- internals ----------------------------------------------------------

    async def _request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC request and wait for the response."""
        req_id = self._next_id
        self._next_id += 1

        message = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._transport.send(message)
            logger.debug("Sent request id=%s method=%s", req_id, method)
        except TransportError:
            self._pending.pop(req_id, None)
            raise

        effective_timeout = timeout if timeout is not None else self._timeout
        try:
            result = await asyncio.wait_for(future, timeout=effective_timeout)
        except TimeoutError:
            self._pending.pop(req_id, None)
            raise MCPError(
                f"Request '{method}' (id={req_id}) timed out "
                f"after {effective_timeout}s"
            )
        except asyncio.CancelledError:
            self._pending.pop(req_id, None)
            raise

        return result

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        message = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        await self._transport.send(message)
        logger.debug("Sent notification method=%s", method)

    async def _read_loop(self) -> None:
        """Continuously read and dispatch messages from the transport."""
        try:
            while self._transport.is_connected:
                try:
                    message = await self._transport.receive()
                except TransportError as exc:
                    logger.debug("Transport read ended: %s", exc)
                    # Fail all pending requests
                    self._fail_all_pending(str(exc))
                    return

                await self._dispatch(message)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.error("Unexpected error in read loop: %s", exc)
            self._fail_all_pending(str(exc))

    async def _dispatch(self, message: dict[str, Any]) -> None:
        """Route an incoming message to the right handler."""
        method = message.get("method")
        msg_id = message.get("id")

        # A message carrying a `method` is server-initiated (a request or a
        # notification) even when it also has an `id` (JSON-RPC requests do).
        # A response has an `id` and NO `method`.
        if method is not None:
            handlers = self._notification_handlers.get(method, [])
            params = message.get("params", {})
            for handler in handlers:
                try:
                    await handler(params)
                except Exception as exc:
                    logger.warning(
                        "Notification handler for '%s' failed: %s",
                        method, exc,
                    )
            return

        if msg_id is not None:
            # Response (success or error)
            future = self._pending.pop(msg_id, None)
            if future is None:
                logger.warning("Received response for unknown id=%s", msg_id)
                return

            if future.done():
                return

            if "error" in message:
                err = message["error"]
                future.set_exception(
                    MCPRpcError(
                        code=err.get("code", -1),
                        message=err.get("message", "Unknown error"),
                        data=err.get("data"),
                    )
                )
            else:
                future.set_result(message.get("result", {}))

    def _fail_all_pending(self, reason: str) -> None:
        """Fail all pending requests with a transport error."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(MCPError(f"Transport error: {reason}"))
        self._pending.clear()

    def _check_initialized(self) -> None:
        """Ensure the client has been initialized."""
        if not self._initialized:
            raise MCPError(
                "Client not initialized. Call initialize() first."
            )
