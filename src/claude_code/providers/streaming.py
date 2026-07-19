"""Unified stream processing for LLM provider events.

Normalizes raw provider streams into ``StreamEvent`` objects, buffers
partial content blocks, tracks token usage, and supports cancellation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import Any

from claude_code.providers.base import StreamEvent

logger = logging.getLogger(__name__)


class StreamCancelled(Exception):
    """Raised when a stream is cancelled via its token."""


class CancelToken:
    """Cooperative cancellation token for async streams.

    Usage::

        token = CancelToken()
        # In another task:
        token.cancel()
        # The stream processor checks is_cancelled() periodically.
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        """Signal cancellation."""
        self._cancelled = True

    @property
    def is_cancelled(self) -> bool:
        """Whether cancellation has been requested."""
        return self._cancelled

    def check(self) -> None:
        """Raise ``StreamCancelled`` if cancellation was requested."""
        if self._cancelled:
            raise StreamCancelled("Stream was cancelled")


class UsageTracker:
    """Tracks cumulative token usage across a stream.

    Attributes:
        input_tokens: Total input tokens consumed.
        output_tokens: Total output tokens consumed.
    """

    def __init__(self) -> None:
        self.input_tokens: int = 0
        self.output_tokens: int = 0

    def update(self, usage: dict[str, int]) -> None:
        """Merge a usage dict into the running totals.

        Args:
            usage: Dict with ``input_tokens`` and/or ``output_tokens`` keys.
        """
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)

    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, int]:
        """Return usage as a plain dict."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


class TextBuffer:
    """Buffers incremental text deltas into complete text content.

    Accumulates small ``text_delta`` events and exposes the full
    accumulated text via the ``text`` property.
    """

    def __init__(self) -> None:
        self._parts: list[str] = []

    def append(self, delta: str) -> None:
        """Append a text delta fragment.

        Args:
            delta: Text fragment to append.
        """
        if delta:
            self._parts.append(delta)

    @property
    def text(self) -> str:
        """The accumulated text so far."""
        return "".join(self._parts)

    def reset(self) -> str:
        """Return accumulated text and clear the buffer.

        Returns:
            The text that was accumulated before the reset.
        """
        result = self.text
        self._parts.clear()
        return result


class ToolUseBuffer:
    """Buffers partial tool-use input JSON across stream events.

    Providers stream tool input as incremental JSON strings.
    This buffer accumulates them and parses the complete JSON
    when the tool-use block is finished.
    """

    def __init__(self, tool_id: str = "", tool_name: str = ""):
        self.tool_id = tool_id
        self.tool_name = tool_name
        self._input_json: str = ""

    def append_input(self, delta: str) -> None:
        """Append a JSON fragment to the tool input.

        Args:
            delta: Partial JSON string fragment.
        """
        if delta:
            self._input_json += delta

    @property
    def raw_input(self) -> str:
        """Raw accumulated JSON string (may be incomplete)."""
        return self._input_json

    def parse_input(self) -> dict[str, Any]:
        """Parse the accumulated JSON into a dict.

        Returns:
            Parsed tool input dict. Returns empty dict on parse failure.
        """
        import json

        if not self._input_json:
            return {}
        try:
            return json.loads(self._input_json)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            logger.warning(
                "Failed to parse tool input JSON for tool %s: %s",
                self.tool_id,
                self._input_json[:200],
            )
            return {}


class StreamProcessor:
    """Processes a raw provider stream into normalized ``StreamEvent`` objects.

    This is the central stream coordinator. It:
    - Wraps any async iterator of ``StreamEvent``
    - Tracks token usage via ``UsageTracker``
    - Buffers text and tool-use blocks
    - Supports cooperative cancellation via ``CancelToken``
    - Emits a final ``done`` event with accumulated usage

    Usage::

        processor = StreamProcessor(raw_stream, cancel_token=token)
        async for event in processor:
            handle(event)
        print(processor.usage.to_dict())
    """

    def __init__(
        self,
        stream: AsyncIterator[StreamEvent],
        cancel_token: CancelToken | None = None,
    ) -> None:
        self._stream = stream
        self._cancel = cancel_token or CancelToken()
        self.usage = UsageTracker()
        self.text_buffer = TextBuffer()
        self._tool_buffers: dict[str, ToolUseBuffer] = {}
        self._stop_reason: str = ""
        self._content_blocks: list[dict[str, Any]] = []

    @property
    def stop_reason(self) -> str:
        """The stop reason from the model (set on message_delta/stop)."""
        return self._stop_reason

    @property
    def content_blocks(self) -> list[dict[str, Any]]:
        """Accumulated content blocks (text + tool_use) from the stream."""
        return self._content_blocks

    def _get_tool_buffer(self, tool_id: str, tool_name: str = "") -> ToolUseBuffer:
        """Get or create a tool-use buffer for the given tool ID.

        Args:
            tool_id: Unique identifier for this tool-use block.
            tool_name: Name of the tool being invoked.

        Returns:
            The buffer for this tool ID.
        """
        if tool_id not in self._tool_buffers:
            self._tool_buffers[tool_id] = ToolUseBuffer(
                tool_id=tool_id, tool_name=tool_name
            )
        return self._tool_buffers[tool_id]

    def _process_event(self, event: StreamEvent) -> StreamEvent:
        """Process a single event: update buffers and track state.

        Args:
            event: Incoming stream event from the provider.

        Returns:
            The event, possibly with enriched data (e.g. parsed tool_input
            on tool_use_stop).
        """
        match event.type:
            case "text_delta":
                self.text_buffer.append(event.content)

            case "tool_use_start":
                self._get_tool_buffer(event.tool_id, event.tool_name)

            case "tool_use_delta":
                buf = self._get_tool_buffer(event.tool_id)
                buf.append_input(event.content)

            case "tool_use_stop":
                buf = self._get_tool_buffer(event.tool_id)
                parsed = buf.parse_input()
                event.tool_input = parsed
                event.tool_name = buf.tool_name or event.tool_name
                self._content_blocks.append({
                    "type": "tool_use",
                    "id": event.tool_id,
                    "name": event.tool_name,
                    "input": parsed,
                })

            case "message_delta":
                if event.stop_reason:
                    self._stop_reason = event.stop_reason
                if event.usage:
                    self.usage.update(event.usage)

            case "message_start":
                if event.usage:
                    self.usage.update(event.usage)

            case "message_stop":
                # Flush any pending text block
                text = self.text_buffer.reset()
                if text:
                    self._content_blocks.insert(0, {
                        "type": "text",
                        "text": text,
                    })
                if event.usage:
                    self.usage.update(event.usage)

        # Attach cumulative usage to every event for UI display
        if not event.usage:
            event.usage = self.usage.to_dict()

        return event

    async def __aiter__(self) -> AsyncIterator[StreamEvent]:
        """Iterate over the stream, processing events and checking cancellation."""
        try:
            async for raw_event in self._stream:
                self._cancel.check()
                yield self._process_event(raw_event)

            # Emit final done event with full usage
            self._cancel.check()
            yield StreamEvent(
                type="done",
                stop_reason=self._stop_reason,
                usage=self.usage.to_dict(),
            )
        except StreamCancelled:
            logger.info("Stream cancelled")
            yield StreamEvent(type="done", stop_reason="cancelled")
        except Exception as exc:
            logger.error("Stream error: %s", exc)
            yield StreamEvent(
                type="error",
                content=str(exc),
                raw=exc,
            )


async def collect_stream(processor: StreamProcessor) -> list[StreamEvent]:
    """Consume a ``StreamProcessor`` and return all events as a list.

    Useful for non-interactive / testing scenarios where you want
    the complete set of events rather than processing them one-by-one.

    Args:
        processor: The stream processor to consume.

    Returns:
        List of all emitted ``StreamEvent`` objects.
    """
    events: list[StreamEvent] = []
    async for event in processor:
        events.append(event)
    return events
