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
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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
    StreamEvent,
    ToolDefinition,
)
from claude_code.providers.retry import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    _compute_delay,
    is_retryable,
)
from claude_code.providers.streaming import CancelToken, TextBuffer, ToolUseBuffer, UsageTracker
from claude_code.services.cost_tracker import CostTracker
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

    on_stream_text: StreamCallback | None = None
    on_tool_call: ToolCallCallback | None = None
    on_tool_result: ToolResultCallback | None = None
    on_error: ErrorCallback | None = None
    on_usage: UsageCallback | None = None
    on_spinner_show: SpinnerCallback | None = None
    on_spinner_hide: SpinnerHideCallback | None = None


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
    error: str | None = None


class _OutputFlag:
    """Mutable flag: set once the model streams any visible output."""

    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value = False


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
        config: AppConfig,
        callbacks: QueryEngineCallbacks | None = None,
    ) -> None:
        self.provider = provider
        self.tools = tool_registry
        self.config = config
        self.callbacks = callbacks or QueryEngineCallbacks()

        self.conversation = Conversation()
        self.usage_tracker = UsageTracker()
        self.cost_tracker = CostTracker(model=config.model or "")
        self.cancel_token = CancelToken()
        self._retry_config: RetryConfig = DEFAULT_RETRY_CONFIG

        # Lazy system prompt — built on first API call, not at init
        self._system_prompt: str | None = None

        # Limits — max_turns == 0 means unlimited (per config docs).
        self._max_turns: float = (
            config.max_turns if config.max_turns > 0 else float("inf")
        )
        self._max_budget_usd: float | None = config.max_budget_usd

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

            # ---- Budget check ----
            if self._max_budget_usd:
                spent = self.cost_tracker.get_estimated_cost()
                if spent >= self._max_budget_usd:
                    result.stop_reason = "budget_exceeded"
                    result.error = (
                        f"Budget limit reached: ${spent:.4f} spent "
                        f"of ${self._max_budget_usd:.2f} allowed"
                    )
                    if self.callbacks.on_error:
                        self.callbacks.on_error(result.error)
                    break

            # ---- Call LLM ----
            try:
                assistant_msg, stop_reason = await self._call_provider()
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

            # Report usage to UI (tracked in _process_stream)
            if self.callbacks.on_usage:
                self.callbacks.on_usage(self.usage_tracker.to_dict())

            # ---- Check for tool calls ----
            tool_uses = assistant_msg.tool_uses()
            if not tool_uses:
                # Model is done — surface the real stop reason (e.g. max_tokens)
                result.stop_reason = stop_reason or "end_turn"
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
                content = tr.content if isinstance(tr.content, str) else json.dumps(tr.content)
                result_blocks.append(
                    ToolResultContent(
                        tool_use_id=tu.id,
                        content=content,
                        is_error=tr.is_error,
                    )
                )

            tool_result_msg = Message(role="user", content=result_blocks)
            self.conversation.add_message(tool_result_msg)

            # ---- Context compression ----
            # Uses the model context window (context.DEFAULT_CONTEXT_LIMIT),
            # not config.max_tokens which caps a single response.
            api_messages = self.conversation.to_api_messages()
            if needs_compression(api_messages):
                compressed = compress(api_messages)
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
        self.cost_tracker.reset()

    def restore_conversation(self, api_messages: list[dict[str, Any]]) -> None:
        """Rebuild the conversation from persisted API-format messages.

        Reconstructs typed content blocks (text, tool_use, tool_result) so a
        resumed session retains its full tool history.
        """
        self._rebuild_conversation(api_messages)

    def get_cost_info(self) -> CostInfo:
        """Get current cost tracking info."""
        usage = self.usage_tracker.to_dict()
        return CostInfo(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            estimated_cost_usd=self.cost_tracker.get_estimated_cost(),
        )

    def get_system_prompt(self) -> str:
        """Return the current system prompt."""
        return self._get_system_prompt()

    # ------------------------------------------------------------------
    # Private — Provider call
    # ------------------------------------------------------------------

    async def _call_provider(self) -> tuple[Message, str]:
        """Call the provider with retry on transient, pre-output failures.

        A retryable error (429/5xx/overloaded) is retried with exponential
        backoff — but only while no output has been streamed yet. Once the
        model has produced visible text or a tool call, retrying would
        duplicate output, so the error propagates instead.
        """
        cfg = self._retry_config
        last_exc: Exception | None = None

        for attempt in range(cfg.max_retries + 1):
            produced = _OutputFlag()
            try:
                return await self._call_provider_once(produced)
            except ProviderError as exc:
                last_exc = exc
                if (
                    not is_retryable(exc)
                    or produced.value
                    or attempt >= cfg.max_retries
                ):
                    raise
                retry_after = getattr(exc, "retry_after", None)
                delay = _compute_delay(attempt, cfg, retry_after)
                logger.info(
                    "Retrying provider call in %.2fs (attempt %d/%d): %s",
                    delay, attempt + 1, cfg.max_retries, exc,
                )
                await asyncio.sleep(delay)

        assert last_exc is not None
        raise last_exc

    async def _call_provider_once(self, produced: _OutputFlag) -> tuple[Message, str]:
        """Issue a single provider call and consume its stream."""
        api_messages = self.conversation.to_api_messages()
        tool_defs = self._get_tool_definitions()

        if self.callbacks.on_spinner_show:
            self.callbacks.on_spinner_show("Thinking")

        try:
            response_stream = await self.provider.create_message(
                messages=api_messages,
                system=self._get_system_prompt(),
                tools=tool_defs if tool_defs else None,
                model=self.config.model,
                max_tokens=self.config.max_tokens or 16384,
                stream=True,
            )

            # Process streaming response
            return await self._process_stream(response_stream, produced)

        finally:
            if self.callbacks.on_spinner_hide:
                self.callbacks.on_spinner_hide()

    async def _process_stream(
        self, stream: AsyncIterator[StreamEvent], produced: _OutputFlag | None = None
    ) -> tuple[Message, str]:
        """Consume a stream of events; build the assistant Message and stop reason."""
        text_buffer = TextBuffer()
        tool_buffers: dict[str, ToolUseBuffer] = {}
        current_tool_id: str | None = None
        stop_reason = "end_turn"
        usage: dict[str, int] = {}

        async for event in stream:
            self.cancel_token.check()

            if event.type == "text_delta":
                if produced is not None:
                    produced.value = True
                text_buffer.append(event.content)
                if self.callbacks.on_stream_text:
                    self.callbacks.on_stream_text(event.content)

            elif event.type == "tool_use_start":
                if produced is not None:
                    produced.value = True
                tb = ToolUseBuffer(tool_id=event.tool_id, tool_name=event.tool_name)
                tool_buffers[event.tool_id] = tb
                current_tool_id = event.tool_id

                if self.callbacks.on_spinner_show:
                    self.callbacks.on_spinner_show(f"Running {event.tool_name}")

            elif event.type == "tool_use_delta":
                if current_tool_id and current_tool_id in tool_buffers:
                    tool_buffers[current_tool_id].append_input(event.content)

            elif event.type == "tool_use_stop":
                if event.tool_id in tool_buffers and self.callbacks.on_spinner_hide:
                    self.callbacks.on_spinner_hide()

            elif event.type == "message_start":
                # Anthropic/Bedrock/Vertex report input_tokens here; without
                # this branch the whole prompt cost was dropped. Later
                # message_delta events overwrite the running output_tokens.
                if event.usage:
                    usage.update(event.usage)

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
        for tb in tool_buffers.values():
            parsed_input = tb.parse_input()
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
            self.cost_tracker.record_usage(
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )

        if not content_blocks:
            content_blocks.append(TextContent(text=""))

        return Message(role="assistant", content=content_blocks), stop_reason

    # ------------------------------------------------------------------
    # Private — Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool_uses(
        self, tool_uses: list[ToolUseContent]
    ) -> list[ToolResult]:
        """Execute tool calls, concurrently when the batch is read-only."""
        if len(tool_uses) == 1:
            return [await self._execute_single_tool(tool_uses[0])]

        if self._all_read_only(tool_uses):
            tasks = [self._execute_single_tool(tu) for tu in tool_uses]
            return list(await asyncio.gather(*tasks, return_exceptions=False))

        # Batch contains mutating tools (Edit/Write/Bash…) — run in order
        # to avoid write races between concurrent tool calls.
        return [await self._execute_single_tool(tu) for tu in tool_uses]

    def _all_read_only(self, tool_uses: list[ToolUseContent]) -> bool:
        """Return True when every tool in the batch is marked read-only."""
        for tu in tool_uses:
            try:
                tool = self.tools.get(tu.name)
            except Exception:
                return False
            if not getattr(tool, "read_only", False):
                return False
        return True

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

    def _get_system_prompt(self) -> str:
        """Return the system prompt, building it lazily on first access."""
        if self._system_prompt is None:
            self._system_prompt = self._build_system_prompt()
        return self._system_prompt

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
