"""Core agentic loop — the heart of Claude Code.

The :class:`QueryEngine` drives the conversational cycle:

1. User submits a message.
2. Messages are sent to the LLM provider (streaming).
3. If the response contains ``tool_use`` blocks, execute tools and feed
   results back.  Repeat until the model returns ``end_turn``.
4. Context compression kicks in when the window fills up.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Callable, Optional

from claude_code.core.context import compress, needs_compression
from claude_code.core.message import (
    Conversation,
    CostInfo,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)
from claude_code.providers.base import (
    BaseProvider,
    ProviderError,
    ProviderResponse,
    StreamEvent,
    ToolDefinition,
)
from claude_code.providers.retry import with_retry
from claude_code.providers.streaming import CancelToken, TextBuffer, ToolUseBuffer, UsageTracker
from claude_code.tools.base import ToolResult
from claude_code.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from claude_code.core.config import AppConfig

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Callback types for UI integration
# ---------------------------------------------------------------------------

StreamCallback = Callable[[str], None]  # text chunk → UI
ToolCallCallback = Callable[[str, dict[str, Any]], None]  # (name, params)
ToolResultCallback = Callable[[str, ToolResult], None]  # (name, result)
ErrorCallback = Callable[[str], None]
UsageCallback = Callable[[dict[str, int]], None]
SpinnerCallback = Callable[[str], None]  # show spinner with label
SpinnerHideCallback = Callable[[], None]


@dataclass
class QueryEngineCallbacks:
    """Collection of optional UI callbacks."""

    on_stream_text: Optional[StreamCallback] = None
    on_tool_call: Optional[ToolCallCallback] = None
    on_tool_result: Optional[ToolResultCallback] = None
    on_error: Optional[ErrorCallback] = None
    on_usage: Optional[UsageCallback] = None
    on_spinner_show: Optional[SpinnerCallback] = None
    on_spinner_hide: Optional[SpinnerHideCallback] = None


# ---------------------------------------------------------------------------
# Query result
# ---------------------------------------------------------------------------


@dataclass
class QueryResult:
    """Result of a single query (one user turn)."""

    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str = "end_turn"
    usage: dict[str, int] = field(default_factory=dict)
    num_turns: int = 0
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# QueryEngine
# ---------------------------------------------------------------------------


class QueryEngine:
    """Drives the agentic conversation loop.

    Parameters
    ----------
    provider:
        The LLM backend to use.
    tool_registry:
        Registry holding all available tools.
    config:
        Application configuration.
    callbacks:
        Optional UI callbacks for streaming / tool display.
    """

    def __init__(
        self,
        provider: BaseProvider,
        tool_registry: ToolRegistry,
        config: "AppConfig",
        callbacks: Optional[QueryEngineCallbacks] = None,
    ) -> None:
        self.provider = provider
        self.tools = tool_registry
        self.config = config
        self.callbacks = callbacks or QueryEngineCallbacks()

        self.conversation = Conversation()
        self.usage_tracker = UsageTracker()
        self.cancel_token = CancelToken()

        # Build system prompt
        self._system_prompt = self._build_system_prompt()

        # Limits
        self._max_turns: int = config.max_turns or 50
        self._max_budget_usd: Optional[float] = config.max_budget_usd

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, user_message: str) -> QueryResult:
        """Process a user message through the full agentic loop.

        Returns the aggregated :class:`QueryResult` after the model
        finishes all tool-use rounds.
        """
        self.cancel_token = CancelToken()

        # Add user message
        msg = Message.user(user_message)
        self.conversation.add_message(msg)

        result = QueryResult()
        turn = 0

        while turn < self._max_turns:
            turn += 1
            result.num_turns = turn

            if self.cancel_token.is_cancelled:
                result.error = "Cancelled"
                break

            # ---- Call LLM ----
            try:
                assistant_msg = await self._call_provider()
            except ProviderError as exc:
                result.error = str(exc)
                if self.callbacks.on_error:
                    self.callbacks.on_error(str(exc))
                break
            except Exception as exc:
                logger.exception("Unexpected error in provider call")
                result.error = f"Unexpected error: {exc}"
                if self.callbacks.on_error:
                    self.callbacks.on_error(f"Unexpected error: {exc}")
                break

            # Record assistant message
            self.conversation.add_message(assistant_msg)

            # Extract text
            text = assistant_msg.text()
            if text:
                result.text = text

            # Track usage
            if assistant_msg.content:
                for block in assistant_msg.content:
                    if isinstance(block, dict) and "usage" in block:
                        self.usage_tracker.update(block["usage"])

            # Report usage to UI
            if self.callbacks.on_usage:
                self.callbacks.on_usage(self.usage_tracker.to_dict())

            # ---- Check for tool calls ----
            tool_uses = assistant_msg.tool_uses()
            if not tool_uses:
                # end_turn — model is done
                result.stop_reason = "end_turn"
                break

            result.stop_reason = "tool_use"

            # ---- Execute tools ----
            tool_results = await self._execute_tool_uses(tool_uses)
            result.tool_calls.extend(
                [{"name": tu.name, "input": tu.input} for tu in tool_uses]
            )

            # Build tool_result message
            result_blocks: list[Any] = []
            for tu, tr in zip(tool_uses, tool_results):
                result_blocks.append(
                    ToolResultContent(
                        tool_use_id=tu.id,
                        content=tr.content if isinstance(tr.content, str) else json.dumps(tr.content),
                        is_error=tr.is_error,
                    )
                )

            tool_result_msg = Message(role="user", content=result_blocks)
            self.conversation.add_message(tool_result_msg)

            # ---- Context compression ----
            api_messages = self.conversation.to_api_messages()
            if needs_compression(api_messages, self.config.max_tokens or 200_000):
                compressed = compress(
                    api_messages,
                    max_tokens=self.config.max_tokens or 200_000,
                )
                # Rebuild conversation from compressed messages
                self._rebuild_conversation(compressed)

        else:
            # Hit max turns
            result.stop_reason = "max_turns"
            if self.callbacks.on_error:
                self.callbacks.on_error(f"Reached maximum turns limit ({self._max_turns})")

        return result

    async def run_print(self, user_message: str) -> str:
        """Non-interactive mode: run query and return text output."""
        result = await self.run(user_message)
        if result.error:
            return f"Error: {result.error}"
        return result.text

    def cancel(self) -> None:
        """Cancel the current query."""
        self.cancel_token.cancel()

    def reset(self) -> None:
        """Reset conversation state."""
        self.conversation = Conversation()
        self.usage_tracker = UsageTracker()

    def get_cost_info(self) -> CostInfo:
        """Get current cost tracking info."""
        usage = self.usage_tracker.to_dict()
        return CostInfo(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )

    def get_system_prompt(self) -> str:
        """Return the current system prompt."""
        return self._system_prompt

    # ------------------------------------------------------------------
    # Private — Provider call
    # ------------------------------------------------------------------

    async def _call_provider(self) -> Message:
        """Call the LLM provider with current conversation and tools."""
        api_messages = self.conversation.to_api_messages()
        tool_defs = self._get_tool_definitions()

        if self.callbacks.on_spinner_show:
            self.callbacks.on_spinner_show("Thinking")

        try:
            response_stream = await self.provider.create_message(
                messages=api_messages,
                system=self._system_prompt,
                tools=tool_defs if tool_defs else None,
                model=self.config.model,
                max_tokens=self.config.max_tokens or 16384,
                stream=True,
            )

            # Process streaming response
            assistant_msg = await self._process_stream(response_stream)

        finally:
            if self.callbacks.on_spinner_hide:
                self.callbacks.on_spinner_hide()

        return assistant_msg

    async def _process_stream(
        self, stream: AsyncIterator[StreamEvent]
    ) -> Message:
        """Consume a stream of events and build an assistant Message."""
        text_buffer = TextBuffer()
        tool_buffers: dict[str, ToolUseBuffer] = {}
        current_tool_id: Optional[str] = None
        stop_reason = "end_turn"
        usage: dict[str, int] = {}

        async for event in stream:
            self.cancel_token.check()

            if event.type == "text_delta":
                text_buffer.append(event.content)
                if self.callbacks.on_stream_text:
                    self.callbacks.on_stream_text(event.content)

            elif event.type == "tool_use_start":
                tb = ToolUseBuffer(tool_id=event.tool_id, tool_name=event.tool_name)
                tool_buffers[event.tool_id] = tb
                current_tool_id = event.tool_id

                if self.callbacks.on_spinner_show:
                    self.callbacks.on_spinner_show(f"Running {event.tool_name}")

            elif event.type == "tool_use_delta":
                if current_tool_id and current_tool_id in tool_buffers:
                    tool_buffers[current_tool_id].append_input(event.content)

            elif event.type == "tool_use_stop":
                tb = tool_buffers.get(event.tool_id)
                if tb:
                    parsed = tb.parse_input()
                    tb._parsed_input = parsed

                    if self.callbacks.on_spinner_hide:
                        self.callbacks.on_spinner_hide()

            elif event.type == "message_delta":
                if event.stop_reason:
                    stop_reason = event.stop_reason
                if event.usage:
                    usage.update(event.usage)

            elif event.type == "message_stop":
                if event.stop_reason:
                    stop_reason = event.stop_reason
                if event.usage:
                    usage.update(event.usage)

            elif event.type == "error":
                if self.callbacks.on_error:
                    self.callbacks.on_error(event.content)

            elif event.type == "done":
                if event.stop_reason:
                    stop_reason = event.stop_reason
                if event.usage:
                    usage.update(event.usage)
                break

        # Build content blocks
        content_blocks: list[Any] = []

        # Text content
        text = text_buffer.text
        if text:
            content_blocks.append(TextContent(text=text))

        # Tool use content
        for tid, tb in tool_buffers.items():
            parsed_input = getattr(tb, "_parsed_input", None) or tb.parse_input()
            content_blocks.append(
                ToolUseContent(
                    id=tb.tool_id,
                    name=tb.tool_name,
                    input=parsed_input,
                )
            )

            if self.callbacks.on_tool_call:
                self.callbacks.on_tool_call(tb.tool_name, parsed_input)

        # Track usage
        if usage:
            self.usage_tracker.update(usage)

        if not content_blocks:
            content_blocks.append(TextContent(text=""))

        return Message(role="assistant", content=content_blocks)

    # ------------------------------------------------------------------
    # Private — Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_uses(
        self, tool_uses: list[ToolUseContent]
    ) -> list[ToolResult]:
        """Execute tool calls, potentially in parallel."""
        if len(tool_uses) == 1:
            return [await self._execute_single_tool(tool_uses[0])]

        # Execute independent tools concurrently
        tasks = [self._execute_single_tool(tu) for tu in tool_uses]
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _execute_single_tool(self, tool_use: ToolUseContent) -> ToolResult:
        """Execute a single tool call."""
        name = tool_use.name
        params = tool_use.input

        logger.info("Executing tool: %s", name)
        logger.debug("Tool params: %s", params)

        try:
            result = await self.tools.execute(name, params)
        except Exception as exc:
            logger.exception("Tool %s raised an exception", name)
            result = ToolResult(content=f"Error: {exc}", is_error=True)

        if self.callbacks.on_tool_result:
            self.callbacks.on_tool_result(name, result)

        return result

    # ------------------------------------------------------------------
    # Private — Helpers
    # ------------------------------------------------------------------

    def _get_tool_definitions(self) -> list[ToolDefinition]:
        """Convert registered tools to API tool definitions."""
        return [
            ToolDefinition(
                name=td["name"],
                description=td["description"],
                input_schema=td["input_schema"],
            )
            for td in self.tools.get_definitions()
        ]

    def _build_system_prompt(self) -> str:
        """Assemble the system prompt using SystemPromptBuilder."""
        from claude_code.core.system_prompt import SystemPromptBuilder

        builder = SystemPromptBuilder(
            config=self.config,
            tool_registry=self.tools,
        )
        return builder.build()

    def _rebuild_conversation(self, compressed_api_messages: list[dict[str, Any]]) -> None:
        """Rebuild internal conversation from compressed API messages."""
        new_conv = Conversation()
        for api_msg in compressed_api_messages:
            role = api_msg.get("role", "user")
            content = api_msg.get("content", "")

            if isinstance(content, str):
                msg = Message(role=role, content=[TextContent(text=content)])
            elif isinstance(content, list):
                blocks: list[Any] = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            blocks.append(TextContent(text=block.get("text", "")))
                        elif block.get("type") == "tool_use":
                            blocks.append(
                                ToolUseContent(
                                    id=block.get("id", ""),
                                    name=block.get("name", ""),
                                    input=block.get("input", {}),
                                )
                            )
                        elif block.get("type") == "tool_result":
                            blocks.append(
                                ToolResultContent(
                                    tool_use_id=block.get("tool_use_id", ""),
                                    content=block.get("content", ""),
                                    is_error=block.get("is_error", False),
                                )
                            )
                        else:
                            blocks.append(TextContent(text=str(block)))
                    else:
                        blocks.append(TextContent(text=str(block)))
                msg = Message(role=role, content=blocks)
            else:
                msg = Message(role=role, content=[TextContent(text=str(content))])

            new_conv.add_message(msg)

        self.conversation = new_conv
