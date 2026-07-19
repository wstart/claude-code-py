"""Lazy tool registration factory."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claude_code.tools.base import ToolContext
    from claude_code.tools.registry import ToolRegistry


def create_tool_registry(ctx: ToolContext) -> ToolRegistry:
    """Create and populate tool registry with lazy imports.

    All tool modules are imported inside this function so they
    don't slow down application startup.
    """
    from claude_code.tools.registry import ToolRegistry

    # Lazy imports — only loaded when this function is called
    from claude_code.tools.read import ReadTool
    from claude_code.tools.write import WriteTool
    from claude_code.tools.edit import EditTool
    from claude_code.tools.multi_edit import MultiEditTool
    from claude_code.tools.glob_tool import GlobTool
    from claude_code.tools.grep import GrepTool
    from claude_code.tools.ls import LSTool
    from claude_code.tools.bash import BashTool
    from claude_code.tools.notebook_read import NotebookReadTool
    from claude_code.tools.notebook_edit import NotebookEditTool
    from claude_code.tools.web_fetch import WebFetchTool
    from claude_code.tools.web_search import WebSearchTool
    from claude_code.tools.task import TaskTool
    from claude_code.tools.todo_read import TodoReadTool
    from claude_code.tools.todo_write import TodoWriteTool
    from claude_code.tools.exit_plan_mode import ExitPlanModeTool
    from claude_code.tools.ask_user import AskUserTool
    from claude_code.tools.diagnostics import DiagnosticsTool
    from claude_code.tools.execute_code import ExecuteCodeTool

    reg = ToolRegistry(context=ctx)
    for cls in [
        ReadTool, WriteTool, EditTool, MultiEditTool,
        GlobTool, GrepTool, LSTool, BashTool,
        NotebookReadTool, NotebookEditTool,
        WebFetchTool, WebSearchTool,
        TaskTool, TodoReadTool, TodoWriteTool,
        ExitPlanModeTool, AskUserTool,
        DiagnosticsTool, ExecuteCodeTool,
    ]:
        reg.register(cls(ctx))
    return reg
