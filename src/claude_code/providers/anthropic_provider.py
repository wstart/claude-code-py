"""Anthropic API provider using the official ``anthropic`` Python SDK."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from claude_code.providers.base import (
    AuthenticationError,
    BaseProvider,
    OverloadedError,
    ProviderError,
    ProviderResponse,
    RateLimitError,
    StreamEvent,
    ToolDefinition,
)

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-20250514"


def _map_error(exc: anthropic.APIError) -> ProviderError:
    """Convert an Anthropic SDK error to our unified error hierarchy.

    Args:
        exc: An ``anthropic.APIError`` (or subclass) from the SDK.

    Returns:
        A ``ProviderError`` subclass appropriate for the status code.
    """
    status = getattr(exc, "status_code", None)
    message = str(exc)

    if status == 429:
        retry_after: float | None = None
        response = getattr(exc, "response", None)
        if response is not None:
            header = response.headers.get("retry-after")
            if header is not None:
                try:
                    retry_after = float(header)
                except (ValueError, TypeError):
                    pass
        return RateLimitError(message, retry_after=retry_after)

    if status in (401, 403):
        return AuthenticationError(message, status_code=status or 401)

    if status == 529:
        return OverloadedError(message)

    retryable = status is not None and status in {408, 500, 502, 503, 504}
    return ProviderError(message, status_code=status, retryable=retryable)


def _build_system_param(
    system: str | list[dict[str, Any]],
) -> str | list[dict[str, Any]]:
    """Normalize the system prompt into the format the SDK expects.

    Args:
        system: System prompt as a string or list of content blocks.

    Returns:
        The system prompt in a format accepted by the Anthropic API.
    """
    return system


def _convert_tools(
    tools: list[ToolDefinition] | None,
) -> list[dict[str, Any]] | None:
    """Convert our ToolDefinition list to Anthropic API tool format.

    Args:
        tools: Tool definitions in our unified format.

    Returns:
        List of tool dicts in Anthropic API format, or ``None``.
    """
    if not tools:
        return None
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


async def _stream_events(
    stream: anthropic.AsyncMessageStream,
) -> AsyncIterator[StreamEvent]:
    """Map Anthropic SDK errors raised during streaming to ProviderError.

    The SDK stream is lazy — the request is issued on first iteration, so
    HTTP errors (429/5xx) surface here rather than at ``create_message``.
    """
    try:
        async for event in _stream_events_raw(stream):
            yield event
    except anthropic.APIError as exc:
        raise _map_error(exc) from exc


def _content_block_index(event: Any) -> int:
    return int(getattr(event, "index", 0) or 0)


async def _stream_events_raw(
    stream: anthropic.AsyncMessageStream,
) -> AsyncIterator[StreamEvent]:
    """Convert Anthropic SDK stream to our unified StreamEvent format.

    Handles all Anthropic event types:
    - ``message_start`` → ``message_start``
    - ``content_block_start`` → ``text_delta`` or ``tool_use_start``
    - ``content_block_delta`` → ``text_delta`` or ``tool_use_delta``
    - ``content_block_stop`` → ``tool_use_stop``
    - ``message_delta`` → ``message_delta``
    - ``message_stop`` → ``message_stop``

    Args:
        stream: The raw Anthropic ``AsyncMessageStream``.

    Yields:
        Normalized ``StreamEvent`` objects.
    """
    # Map content-block index → real tool id so delta/stop events carry the
    # same tool_id as the start event (the SDK only puts the id on start).
    tool_ids: dict[int, str] = {}

    async with stream as s:
        async for event in s:
            event_type = getattr(event, "type", "")

            if event_type == "message_start":
                message = event.message
                yield StreamEvent(
                    type="message_start",
                    usage={
                        "input_tokens": getattr(message.usage, "input_tokens", 0),
                        "output_tokens": getattr(message.usage, "output_tokens", 0),
                    },
                    raw=event,
                )

            elif event_type == "content_block_start":
                block = event.content_block
                block_type = getattr(block, "type", "")

                if block_type == "text":
                    text = getattr(block, "text", "")
                    if text:
                        yield StreamEvent(
                            type="text_delta",
                            content=text,
                            raw=event,
                        )

                elif block_type == "tool_use":
                    idx = _content_block_index(event)
                    tid = getattr(block, "id", "") or str(idx)
                    tool_ids[idx] = tid
                    yield StreamEvent(
                        type="tool_use_start",
                        tool_id=tid,
                        tool_name=getattr(block, "name", ""),
                        raw=event,
                    )
                    # Tool use may have partial input already
                    partial = getattr(block, "input", "")
                    if partial:
                        yield StreamEvent(
                            type="tool_use_delta",
                            tool_id=tid,
                            content=partial,
                            raw=event,
                        )

            elif event_type == "content_block_delta":
                delta = event.delta
                delta_type = getattr(delta, "type", "")

                if delta_type == "text_delta":
                    yield StreamEvent(
                        type="text_delta",
                        content=getattr(delta, "text", ""),
                        raw=event,
                    )

                elif delta_type == "input_json_delta":
                    idx = _content_block_index(event)
                    yield StreamEvent(
                        type="tool_use_delta",
                        tool_id=tool_ids.get(idx, str(idx)),
                        content=getattr(delta, "partial_json", ""),
                        raw=event,
                    )

            elif event_type == "content_block_stop":
                idx = _content_block_index(event)
                yield StreamEvent(
                    type="tool_use_stop",
                    tool_id=tool_ids.get(idx, str(idx)),
                    raw=event,
                )

            elif event_type == "message_delta":
                delta = event.delta
                usage_obj = getattr(event, "usage", None)
                usage: dict[str, int] = {}
                if usage_obj:
                    usage = {
                        "output_tokens": getattr(usage_obj, "output_tokens", 0),
                    }

                yield StreamEvent(
                    type="message_delta",
                    stop_reason=getattr(delta, "stop_reason", ""),
                    usage=usage,
                    raw=event,
                )

            elif event_type == "message_stop":
                yield StreamEvent(type="message_stop", raw=event)


class AnthropicProvider(BaseProvider):
    """Provider for the Anthropic Messages API.

    Wraps the official ``anthropic`` async client and normalizes
    streaming events to our unified ``StreamEvent`` format.

    Args:
        api_key: Anthropic API key. If ``None``, reads from the
            ``ANTHROPIC_API_KEY`` environment variable.
        base_url: Optional custom API base URL.
        default_model: Default model to use when none is specified.
        **client_kwargs: Additional keyword arguments for
            ``anthropic.AsyncAnthropic``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = DEFAULT_MODEL,
        verify_ssl: bool = True,
        **client_kwargs: Any,
    ) -> None:
        self._default_model = default_model
        if not verify_ssl and "http_client" not in client_kwargs:
            import httpx
            logger.warning(
                "TLS certificate verification is DISABLED for %s (insecure).",
                base_url or "the default endpoint",
            )
            client_kwargs["http_client"] = httpx.AsyncClient(verify=False)
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            **client_kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "anthropic"

    async def create_message(
        self,
        messages: list[dict[str, Any]],
        system: str | list[dict[str, Any]] = "",
        tools: list[ToolDefinition] | None = None,
        model: str | None = None,
        max_tokens: int = 16384,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[StreamEvent] | ProviderResponse:
        """Send messages to Anthropic and get a streaming or complete response.

        Args:
            messages: Conversation messages in Anthropic message format.
            system: System prompt string or list of content blocks.
            tools: Optional tool definitions.
            model: Model identifier or alias (e.g. "sonnet", "opus").
            max_tokens: Maximum output tokens.
            stream: Whether to stream the response.
            **kwargs: Extra parameters forwarded to the Anthropic API
                (e.g. ``temperature``, ``top_p``, ``thinking``).

        Returns:
            Async iterator of ``StreamEvent`` if streaming, otherwise
            a ``ProviderResponse``.

        Raises:
            ProviderError: On API errors (mapped from Anthropic SDK errors).
        """
        resolved_model = self.resolve_model(model) or self._default_model
        api_tools = _convert_tools(tools)
        sys_param = _build_system_param(system)

        try:
            if stream:
                api_stream = self._client.messages.stream(
                    model=resolved_model,
                    max_tokens=max_tokens,
                    messages=messages,
                    system=sys_param,
                    tools=api_tools,  # type: ignore[arg-type]
                    **kwargs,
                )
                return _stream_events(api_stream)
            else:
                response = await self._client.messages.create(
                    model=resolved_model,
                    max_tokens=max_tokens,
                    messages=messages,
                    system=sys_param,
                    tools=api_tools,  # type: ignore[arg-type]
                    stream=False,
                    **kwargs,
                )
                return _build_response(response, resolved_model)

        except anthropic.APIError as exc:
            raise _map_error(exc) from exc

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()


def _build_response(
    response: anthropic.types.Message,
    model: str,
) -> ProviderResponse:
    """Convert a complete Anthropic response to our unified format.

    Args:
        response: Anthropic ``Message`` response object.
        model: Model identifier used for this request.

    Returns:
        A ``ProviderResponse`` with normalized content blocks.
    """
    content_blocks: list[dict[str, Any]] = []

    for block in response.content:
        block_type = getattr(block, "type", "")
        if block_type == "text":
            content_blocks.append({
                "type": "text",
                "text": getattr(block, "text", ""),
            })
        elif block_type == "tool_use":
            content_blocks.append({
                "type": "tool_use",
                "id": getattr(block, "id", ""),
                "name": getattr(block, "name", ""),
                "input": getattr(block, "input", {}),
            })

    usage_obj = response.usage
    usage: dict[str, int] = {
        "input_tokens": getattr(usage_obj, "input_tokens", 0),
        "output_tokens": getattr(usage_obj, "output_tokens", 0),
    }

    return ProviderResponse(
        content=content_blocks,
        stop_reason=getattr(response, "stop_reason", "") or "",
        usage=usage,
        model=model,
    )
