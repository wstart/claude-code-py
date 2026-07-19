"""MCP tool proxy — forwards tool calls to an MCP server.

This module lives in ``tools/`` because it subclasses
:class:`~claude_code.tools.base.Tool`, but the proxy itself is created
by :class:`~claude_code.mcp.tool_discovery.MCPToolDiscovery`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from claude_code.tools.base import Tool, ToolContext, ToolResult
from claude_code.utils.logging import get_logger

if TYPE_CHECKING:
    from claude_code.mcp.client import MCPClient

logger = get_logger("tools.mcp_tool")


class MCPToolProxy(Tool):
    """Proxy tool that forwards calls to an MCP server.

    The proxy name follows the convention
    ``mcp__{server_name}__{tool_name}`` so that built-in tools and MCP
    tools never collide.

    Args:
        server_name: MCP server that owns the tool.
        tool_name: Original tool name on the server.
        description: Human-readable description.
        input_schema: JSON Schema for the tool's parameters.
        client: The :class:`MCPClient` to call through.
        context: Optional shared :class:`ToolContext`.
    """

    def __init__(
        self,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: dict[str, Any],
        client: MCPClient,
        context: ToolContext | None = None,
    ) -> None:
        super().__init__(context)
        self.name = f"mcp__{server_name}__{tool_name}"
        self.description = description
        self.input_schema = input_schema
        self._client = client
        self._tool_name = tool_name
        self._server_name = server_name
        self.category = "mcp"

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Forward the call to the MCP server.

        All keyword arguments are passed as the tool's ``arguments``.

        Returns:
            A :class:`ToolResult` built from the server's response.
        """
        try:
            result = await self._client.call_tool(self._tool_name, kwargs)
        except Exception as exc:
            logger.error(
                "MCP call %s.%s failed: %s",
                self._server_name, self._tool_name, exc,
            )
            return ToolResult.error(f"MCP tool error: {exc}")

        return self._parse_result(result)

    def _parse_result(self, result: dict[str, Any]) -> ToolResult:
        """Convert an MCP tool result into a :class:`ToolResult`.

        MCP results have a ``content`` field that is a list of content
        blocks.  We normalise plain-text blocks into a string and keep
        complex blocks as-is.
        """
        is_error = result.get("isError", False)
        content_blocks = result.get("content", [])

        if not isinstance(content_blocks, list):
            # Some servers return a plain string
            return ToolResult(content=str(content_blocks), is_error=is_error)

        # Extract text from blocks
        texts: list[str] = []
        complex_blocks: list[dict[str, Any]] = []

        for block in content_blocks:
            if isinstance(block, dict):
                if block.get("type") == "text" and "text" in block:
                    texts.append(block["text"])
                else:
                    complex_blocks.append(block)
            elif isinstance(block, str):
                texts.append(block)

        # Prefer complex blocks if present, else join texts
        if complex_blocks:
            return ToolResult(content=complex_blocks, is_error=is_error)

        return ToolResult(
            content="\n".join(texts) if texts else "",
            is_error=is_error,
        )
