"""Task tool — spawn an isolated sub-agent with read-only tools.

Creates a new :class:`QueryEngine` with a restricted tool set
(Read, Glob, Grep, LS) and runs the given prompt.  Returns a
consolidated summary from the sub-agent's work.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_code.tools.base import Tool, ToolContext, ToolResult

if TYPE_CHECKING:
    from claude_code.providers.base import BaseProvider
    from claude_code.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Tools available to the sub-agent (read-only)
_SUBAGENT_TOOL_NAMES = ("Read", "Glob", "Grep", "LS")


class TaskTool(Tool):
    """Spawn an isolated sub-agent that can use read-only tools."""

    name = "Task"
    description = (
        "Spawns an isolated sub-agent to perform a read-only research task. "
        "The sub-agent has access to Read, Glob, Grep, and LS tools only. "
        "Returns a consolidated summary of findings."
    )
    category = "orchestration"
    read_only = True
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "A short (3-5 word) description of the task.",
            },
            "prompt": {
                "type": "string",
                "description": "The detailed task for the sub-agent to perform.",
            },
        },
        "required": ["description", "prompt"],
    }

    def __init__(
        self,
        context: ToolContext | None = None,
        provider: BaseProvider | None = None,
        config: Any | None = None,
    ) -> None:
        super().__init__(context)
        self._provider = provider
        self._config = config

    def set_provider(self, provider: BaseProvider) -> None:
        """Set the LLM provider for sub-agent queries."""
        self._provider = provider

    def set_config(self, config: Any) -> None:
        """Set the application config for sub-agent queries."""
        self._config = config

    async def execute(self, **kwargs: Any) -> ToolResult:
        description: str = kwargs["description"]
        prompt: str = kwargs["prompt"]

        if self._provider is None:
            return ToolResult.error(
                "Task tool requires a provider to be configured. "
                "Set the provider via set_provider() before use."
            )

        if self._config is None:
            return ToolResult.error(
                "Task tool requires config to be set. "
                "Set the config via set_config() before use."
            )

        try:
            result = await self._run_subagent(description, prompt)
            return ToolResult.success(result)
        except Exception as exc:
            logger.exception("Sub-agent failed")
            return ToolResult.error(f"Sub-agent error: {exc}")

    async def _run_subagent(self, description: str, prompt: str) -> str:
        """Create a restricted QueryEngine and run the prompt."""
        # Import here to avoid circular imports
        from claude_code.core.config import AppConfig
        from claude_code.core.query_engine import QueryEngine
        from claude_code.tools.registry import ToolRegistry

        # Build a restricted registry with read-only tools
        sub_registry = ToolRegistry(context=self.context)
        self._register_readonly_tools(sub_registry)

        # Create a sub-config with a read-only system prompt
        # Use model_copy to avoid mutating the parent config
        if hasattr(self._config, "model_copy"):
            sub_config = self._config.model_copy(
                update={
                    "system_prompt": (
                        "You are a read-only research agent. You can read files, "
                        "search for patterns, and list directories. You cannot modify "
                        "any files. Provide a thorough, consolidated summary of your "
                        "findings."
                    ),
                    "max_turns": 10,
                }
            )
        else:
            sub_config = AppConfig(
                system_prompt=(
                    "You are a read-only research agent. You can read files, "
                    "search for patterns, and list directories. You cannot modify "
                    "any files. Provide a thorough, consolidated summary of your "
                    "findings."
                ),
                max_turns=10,
            )

        # Create a sub-engine
        engine = QueryEngine(
            provider=self._provider,  # type: ignore[arg-type]
            tool_registry=sub_registry,
            config=sub_config,
        )

        # Run the query
        full_prompt = f"Task: {description}\n\n{prompt}"
        query_result = await engine.run(full_prompt)

        # Extract the text response
        if query_result.text:
            return query_result.text
        if query_result.error:
            return f"Sub-agent completed with error: {query_result.error}"
        return "Sub-agent completed but produced no text output."

    def _register_readonly_tools(self, registry: ToolRegistry) -> None:
        """Register only read-only tools in the sub-agent registry."""
        from claude_code.tools.glob_tool import GlobTool
        from claude_code.tools.grep import GrepTool
        from claude_code.tools.ls import LSTool
        from claude_code.tools.read import ReadTool

        tool_map: dict[str, type[Tool]] = {
            "Read": ReadTool,
            "Glob": GlobTool,
            "Grep": GrepTool,
            "LS": LSTool,
        }

        for name in _SUBAGENT_TOOL_NAMES:
            tool_cls = tool_map.get(name)
            if tool_cls is not None:
                registry.register(tool_cls())
