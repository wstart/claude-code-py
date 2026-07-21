"""Sub-agent with restricted tool set for delegated tasks.

A :class:`SubAgent` creates its own isolated :class:`ToolRegistry` and
:class:`QueryEngine`, running a prompt with only the tools it is allowed
to use.  This is the building block for task delegation — the parent
agent can spin up a sub-agent for read-only research, file generation,
or any other scoped work.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from claude_code.core.query_engine import QueryEngine
from claude_code.providers.streaming import CancelToken
from claude_code.tools.base import ToolContext
from claude_code.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from claude_code.core.config import AppConfig
    from claude_code.providers.base import BaseProvider

logger = logging.getLogger(__name__)

# Mapping of tool names to their classes for dynamic registry building.
_TOOL_CLASS_MAP: dict[str, str] = {
    "Read": "claude_code.tools.read.ReadTool",
    "Write": "claude_code.tools.write.WriteTool",
    "Edit": "claude_code.tools.edit.EditTool",
    "MultiEdit": "claude_code.tools.multi_edit.MultiEditTool",
    "Glob": "claude_code.tools.glob_tool.GlobTool",
    "Grep": "claude_code.tools.grep.GrepTool",
    "LS": "claude_code.tools.ls.LSTool",
    "Bash": "claude_code.tools.bash.BashTool",
    "WebFetch": "claude_code.tools.web_fetch.WebFetchTool",
    "WebSearch": "claude_code.tools.web_search.WebSearchTool",
    "NotebookRead": "claude_code.tools.notebook_read.NotebookReadTool",
    "NotebookEdit": "claude_code.tools.notebook_edit.NotebookEditTool",
}


def _import_tool_class(dotted_path: str) -> type:
    """Import a tool class from a dotted module path."""
    module_path, class_name = dotted_path.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class SubAgent:
    """An isolated agent with restricted tools for delegated tasks.

    Parameters
    ----------
    provider:
        The LLM provider to use for inference.
    config:
        Application configuration (a copy is made internally).
    allowed_tools:
        List of tool names this sub-agent may use.  ``None`` means
        all available tools.
    description:
        Human-readable description of what this sub-agent does.
    name:
        Optional name for identification in logs and manager listings.
    """

    def __init__(
        self,
        provider: BaseProvider,
        config: AppConfig,
        allowed_tools: list[str] | None = None,
        description: str = "",
        name: str = "",
    ) -> None:
        self._provider = provider
        self._config = config
        self._allowed_tools = allowed_tools
        self.description = description
        self.name = name or f"subagent-{id(self):x}"

        self._cancel_token = CancelToken()
        self._engine: QueryEngine | None = None
        self._result: str | None = None

    async def run(self, prompt: str) -> str:
        """Run a task and return the consolidated result text.

        Creates an isolated :class:`QueryEngine` with only the allowed
        tools, runs the prompt through the full agentic loop, and
        returns the final text output.

        Args:
            prompt: The task prompt for the sub-agent.

        Returns:
            The text result from the sub-agent's work.
        """
        registry = self._build_registry()
        sub_config = self._build_config()

        self._engine = QueryEngine(
            provider=self._provider,
            tool_registry=registry,
            config=sub_config,
        )

        full_prompt = prompt
        if self.description:
            full_prompt = f"Task: {self.description}\n\n{prompt}"

        logger.info(
            "SubAgent '%s' starting with tools: %s",
            self.name,
            registry.list_names(),
        )

        query_result = await self._engine.run(full_prompt)

        if query_result.error:
            self._result = f"Error: {query_result.error}"
        elif query_result.text:
            self._result = query_result.text
        else:
            self._result = "Sub-agent completed but produced no text output."

        logger.info("SubAgent '%s' completed", self.name)
        return self._result

    async def cancel(self) -> None:
        """Cancel the sub-agent's current operation."""
        self._cancel_token.cancel()
        if self._engine is not None:
            self._engine.cancel()

    @property
    def result(self) -> str | None:
        """The result text from the last run, or ``None`` if not yet run."""
        return self._result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_registry(self) -> ToolRegistry:
        """Create a ToolRegistry containing only the allowed tools."""
        context = ToolContext(cwd=self._config.working_directory or ".")
        registry = ToolRegistry(context=context)

        if self._allowed_tools is None:
            # All tools
            tool_names = list(_TOOL_CLASS_MAP.keys())
        else:
            tool_names = self._allowed_tools

        for name in tool_names:
            dotted = _TOOL_CLASS_MAP.get(name)
            if dotted is None:
                logger.warning("Unknown tool '%s' requested for SubAgent", name)
                continue
            try:
                tool_cls = _import_tool_class(dotted)
                registry.register(tool_cls())
            except Exception:
                logger.warning("Failed to import tool '%s'", name, exc_info=True)

        return registry

    def _build_config(self) -> AppConfig:
        """Create a sub-agent config with an appropriate system prompt."""
        read_only = self._allowed_tools is not None and all(
            t in ("Read", "Glob", "Grep", "LS", "WebFetch", "WebSearch",
                  "NotebookRead")
            for t in self._allowed_tools
        )

        if read_only:
            system_prompt = (
                "You are a read-only research agent. You can read files, "
                "search for patterns, and list directories. You cannot modify "
                "any files. Provide a thorough, consolidated summary of your "
                "findings."
            )
        else:
            system_prompt = (
                "You are a sub-agent working on a delegated task. "
                "Complete the task thoroughly and provide a clear summary "
                "of what you did and any findings."
            )

        if hasattr(self._config, "model_copy"):
            return self._config.model_copy(
                update={
                    "system_prompt": system_prompt,
                    "max_turns": 15,
                }
            )

        from claude_code.core.config import AppConfig

        return AppConfig(
            system_prompt=system_prompt,
            max_turns=15,
            provider=self._config.provider,
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            working_directory=self._config.working_directory,
        )
