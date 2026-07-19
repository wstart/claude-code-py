"""MCP server lifecycle management.

Manages starting, stopping, and querying multiple MCP server instances.
Reads configuration from the ``mcpServers`` section of settings files.
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel, Field

from claude_code.mcp.client import MCPClient, MCPError
from claude_code.mcp.transport import SSETransport, StdioTransport, TransportError
from claude_code.utils.logging import get_logger

logger = get_logger("mcp.server_manager")


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server.

    Exactly one of ``command`` (stdio) or ``url`` (SSE) must be set.
    """

    name: str
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    cwd: str | None = None


class MCPServer:
    """A running MCP server instance — transport + client pair."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.client: MCPClient | None = None
        self._tools_cache: list[dict[str, Any]] | None = None

    async def start(self) -> None:
        """Create transport, connect, and initialise the MCP client."""
        if self.config.command:
            transport = StdioTransport(
                command=self.config.command,
                args=self.config.args,
                env=self.config.env or None,
                cwd=self.config.cwd,
            )
        elif self.config.url:
            transport = SSETransport(
                url=self.config.url,
                headers=self.config.headers or None,
            )
        else:
            raise MCPError(
                f"Server '{self.config.name}': "
                "either 'command' or 'url' must be set"
            )

        self.client = MCPClient(transport)

        try:
            await self.client.connect()
            await self.client.initialize()
        except (TransportError, MCPError) as exc:
            logger.error(
                "Failed to start MCP server '%s': %s",
                self.config.name, exc,
            )
            await self.stop()
            raise

        logger.info("MCP server '%s' started", self.config.name)

    async def stop(self) -> None:
        """Stop the server and release resources."""
        if self.client:
            try:
                await self.client.close()
            except Exception as exc:
                logger.warning(
                    "Error stopping MCP server '%s': %s",
                    self.config.name, exc,
                )
            self.client = None
        self._tools_cache = None

    async def list_tools(self) -> list[dict[str, Any]]:
        """Get tools from this server, using cache when available."""
        if not self.client or not self.client.is_initialized:
            return []

        if self._tools_cache is not None:
            return self._tools_cache

        try:
            self._tools_cache = await self.client.list_tools()
            return self._tools_cache
        except (MCPError, TransportError) as exc:
            logger.warning(
                "Failed to list tools from '%s': %s",
                self.config.name, exc,
            )
            return []

    def invalidate_tools_cache(self) -> None:
        """Force a fresh tool listing on next call."""
        self._tools_cache = None


class MCPServerManager:
    """Manages MCP server connections.

    Provides methods to add/remove servers, query tools across all
    servers, and call tools on specific servers.
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServer] = {}

    async def add_server(self, config: MCPServerConfig) -> None:
        """Start and register an MCP server.

        Args:
            config: Server configuration.

        Raises:
            MCPError: If a server with the same name exists or startup fails.
        """
        if config.name in self._servers:
            raise MCPError(
                f"Server '{config.name}' is already registered"
            )

        server = MCPServer(config)
        await server.start()
        self._servers[config.name] = server

    async def remove_server(self, name: str) -> None:
        """Stop and unregister a server."""
        server = self._servers.pop(name, None)
        if server is None:
            logger.warning("Server '%s' not found for removal", name)
            return
        await server.stop()
        logger.info("Removed MCP server '%s'", name)

    def get_client(self, name: str) -> MCPClient | None:
        """Get the client for a server.

        Returns:
            The MCPClient instance, or None if not found.
        """
        server = self._servers.get(name)
        if server is None:
            return None
        return server.client

    def get_server(self, name: str) -> MCPServer | None:
        """Get a server instance by name."""
        return self._servers.get(name)

    def list_servers(self) -> list[str]:
        """Return names of all registered servers."""
        return list(self._servers.keys())

    async def list_all_tools(self) -> list[dict[str, Any]]:
        """Get tools from all connected servers.

        Each tool dict gets an extra ``_server`` key identifying which
        server it belongs to.
        """
        all_tools: list[dict[str, Any]] = []

        tasks = [
            (name, server.list_tools())
            for name, server in self._servers.items()
        ]

        results = await asyncio.gather(
            *(t for _, t in tasks),
            return_exceptions=True,
        )

        for (name, _), result in zip(tasks, results):
            if isinstance(result, Exception):
                logger.warning(
                    "Error listing tools from '%s': %s", name, result,
                )
                continue
            for tool in result:
                tool["_server"] = name
                all_tools.append(tool)

        return all_tools

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Call a tool on a specific server.

        Args:
            server_name: Name of the MCP server.
            tool_name: Tool to call.
            arguments: Tool arguments.
            timeout: Optional timeout override.

        Returns:
            Tool result dict.
        """
        server = self._servers.get(server_name)
        if server is None:
            raise MCPError(f"Server '{server_name}' not found")

        if server.client is None or not server.client.is_initialized:
            raise MCPError(f"Server '{server_name}' is not connected")

        return await server.client.call_tool(
            tool_name, arguments, timeout=timeout,
        )

    async def shutdown(self) -> None:
        """Stop all servers."""
        names = list(self._servers.keys())
        await asyncio.gather(
            *(self.remove_server(n) for n in names),
            return_exceptions=True,
        )
        logger.info("All MCP servers shut down")

    def load_config(self, config: dict[str, Any]) -> list[MCPServerConfig]:
        """Parse server configs from a ``mcpServers`` dict.

        This matches the format in ``settings.json``::

            {
                "mcpServers": {
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                        "env": {}
                    },
                    "github": {
                        "url": "https://mcp.github.com/sse",
                        "headers": {"Authorization": "Bearer ..."}
                    }
                }
            }

        Returns:
            List of parsed configs (callers should then call
            :meth:`add_server` for each).
        """
        mcp_servers = config.get("mcpServers", config)
        configs: list[MCPServerConfig] = []

        for name, server_cfg in mcp_servers.items():
            if not isinstance(server_cfg, dict):
                logger.warning(
                    "Invalid config for MCP server '%s': not a dict", name,
                )
                continue

            try:
                cfg = MCPServerConfig(
                    name=name,
                    command=server_cfg.get("command"),
                    args=server_cfg.get("args", []),
                    url=server_cfg.get("url"),
                    env=server_cfg.get("env", {}),
                    headers=server_cfg.get("headers", {}),
                    cwd=server_cfg.get("cwd"),
                )
                configs.append(cfg)
            except Exception as exc:
                logger.warning(
                    "Failed to parse config for MCP server '%s': %s",
                    name, exc,
                )

        return configs

    async def start_from_config(self, config: dict[str, Any]) -> None:
        """Parse and start all servers from a config dict.

        Servers that fail to start are logged and skipped.
        """
        configs = self.load_config(config)
        for cfg in configs:
            try:
                await self.add_server(cfg)
            except (MCPError, TransportError) as exc:
                logger.error(
                    "Failed to start MCP server '%s': %s", cfg.name, exc,
                )
