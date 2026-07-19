"""MCP dynamic tool discovery.

Queries connected MCP servers for their tools and converts them to the
internal :class:`~claude_code.tools.base.Tool` format so they can be
used transparently alongside built-in tools.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from claude_code.utils.logging import get_logger

if TYPE_CHECKING:
    from claude_code.mcp.client import MCPClient
    from claude_code.mcp.server_manager import MCPServerManager
    from claude_code.tools.base import ToolContext

logger = get_logger("mcp.tool_discovery")


class MCPToolDiscovery:
    """Discover and register MCP tools dynamically.

    Scans all connected servers, converts their tool schemas into the
    internal format, and creates proxy objects that forward calls.

    Args:
        server_manager: The server manager to query.
    """

    def __init__(self, server_manager: MCPServerManager) -> None:
        self._manager = server_manager

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Query all servers for their available tools.

        Returns:
            A flat list of tool dicts, each annotated with ``_server``.
        """
        return await self._manager.list_all_tools()

    def mcp_to_tool_definition(
        self,
        mcp_tool: dict[str, Any],
        server_name: str,
    ) -> dict[str, Any]:
        """Convert an MCP tool schema to our internal ToolDefinition format.

        The MCP ``inputSchema`` maps directly to our ``input_schema``.
        The tool name is prefixed with ``mcp__{server}__`` to avoid
        collisions with built-in tools.

        Args:
            mcp_tool: Tool dict from the MCP ``tools/list`` response.
            server_name: Server that owns this tool.

        Returns:
            A dict suitable for :class:`MCPToolProxy` initialisation.
        """
        name = mcp_tool.get("name", "unknown")
        description = mcp_tool.get("description", "")
        input_schema = mcp_tool.get("inputSchema", {})

        # Ensure the schema is an object type
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        input_schema.setdefault("type", "object")

        qualified_name = f"mcp__{server_name}__{name}"

        return {
            "name": qualified_name,
            "description": description,
            "input_schema": input_schema,
            "server_name": server_name,
            "tool_name": name,
        }

    async def create_tool_proxy(
        self,
        server_name: str,
        tool_name: str,
        context: ToolContext | None = None,
    ) -> Any:
        """Create a proxy Tool that forwards to an MCP server.

        Args:
            server_name: Target MCP server.
            tool_name: Tool to proxy.
            context: Optional ToolContext to share with the proxy.

        Returns:
            An :class:`MCPToolProxy` instance.

        Raises:
            ValueError: If the server or tool is not found.
        """
        from claude_code.tools.mcp_tool import MCPToolProxy

        client = self._manager.get_client(server_name)
        if client is None:
            raise ValueError(f"MCP server '{server_name}' not connected")

        server = self._manager.get_server(server_name)
        if server is None:
            raise ValueError(f"MCP server '{server_name}' not found")

        # Find the tool in the server's tool list
        tools = await server.list_tools()
        tool_def: dict[str, Any] | None = None
        for t in tools:
            if t.get("name") == tool_name:
                tool_def = t
                break

        if tool_def is None:
            raise ValueError(
                f"Tool '{tool_name}' not found on server '{server_name}'"
            )

        converted = self.mcp_to_tool_definition(tool_def, server_name)

        return MCPToolProxy(
            server_name=server_name,
            tool_name=tool_name,
            description=converted["description"],
            input_schema=converted["input_schema"],
            client=client,
            context=context,
        )

    async def create_all_proxies(
        self,
        context: ToolContext | None = None,
    ) -> list[Any]:
        """Create proxies for every tool on every connected server.

        Returns:
            List of :class:`MCPToolProxy` instances.
        """
        from claude_code.tools.mcp_tool import MCPToolProxy

        proxies: list[MCPToolProxy] = []
        all_tools = await self.discover_tools()

        for tool in all_tools:
            server_name = tool.get("_server", "")
            client = self._manager.get_client(server_name)
            if client is None:
                continue

            converted = self.mcp_to_tool_definition(tool, server_name)
            proxies.append(MCPToolProxy(
                server_name=server_name,
                tool_name=converted["tool_name"],
                description=converted["description"],
                input_schema=converted["input_schema"],
                client=client,
                context=context,
            ))

        return proxies
