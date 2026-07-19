"""Tool system for Claude Code Py.

Public API
----------
- :class:`Tool` — abstract base class for all tools.
- :class:`ToolResult` — result container returned by tool execution.
- :class:`ToolContext` — shared mutable state across tools.
- :class:`ToolRegistry` — central registry that validates and dispatches.

Quick-start::

    from claude_code.tools import create_default_registry

    registry = create_default_registry()
    result = await registry.execute("Read", {"file_path": "/tmp/hello.py"})
"""

from claude_code.tools.base import Tool, ToolContext, ToolResult
from claude_code.tools.registry import ToolRegistry, ToolRegistryError

__all__ = [
    "Tool",
    "ToolContext",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolResult",
    "create_default_registry",
]


def create_default_registry(context: ToolContext | None = None) -> ToolRegistry:
    """Create a :class:`ToolRegistry` pre-loaded with all built-in tools.

    This is the recommended entry point for application code that wants
    the standard set of file, shell, search, web, and orchestration tools.
    """
    from claude_code.tools.ask_user import AskUserTool
    from claude_code.tools.bash import BashTool
    from claude_code.tools.edit import EditTool
    from claude_code.tools.exit_plan_mode import ExitPlanModeTool
    from claude_code.tools.glob_tool import GlobTool
    from claude_code.tools.grep import GrepTool
    from claude_code.tools.ls import LSTool
    from claude_code.tools.multi_edit import MultiEditTool
    from claude_code.tools.notebook_edit import NotebookEditTool
    from claude_code.tools.notebook_read import NotebookReadTool
    from claude_code.tools.read import ReadTool
    from claude_code.tools.task import TaskTool
    from claude_code.tools.todo_read import TodoReadTool
    from claude_code.tools.todo_write import TodoWriteTool
    from claude_code.tools.web_fetch import WebFetchTool
    from claude_code.tools.web_search import WebSearchTool
    from claude_code.tools.write import WriteTool

    registry = ToolRegistry(context=context)

    # Register in a deterministic order by category
    for tool_cls in (
        # File tools
        ReadTool,
        WriteTool,
        EditTool,
        MultiEditTool,
        NotebookReadTool,
        NotebookEditTool,
        # Search tools
        GlobTool,
        GrepTool,
        LSTool,
        # Shell
        BashTool,
        # Web tools
        WebFetchTool,
        WebSearchTool,
        # Orchestration
        TaskTool,
        ExitPlanModeTool,
        # Todo
        TodoReadTool,
        TodoWriteTool,
        # Interaction
        AskUserTool,
    ):
        registry.register(tool_cls())

    return registry
