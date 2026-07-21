"""OpenAI-compatible API provider.

Supports any OpenAI-compatible endpoint: OpenAI, Azure OpenAI,
Ollama, vLLM, LM Studio, and others with custom ``base_url``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai.types.chat import ChatCompletionChunk

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

DEFAULT_MODEL = "gpt-4o"


def _map_error(exc: openai.APIError) -> ProviderError:
    """Convert an OpenAI SDK error to our unified error hierarchy.

    Args:
        exc: An ``openai.APIError`` (or subclass) from the SDK.

    Returns:
        A ``ProviderError`` subclass appropriate for the status code.
    """
    status = getattr(exc, "status_code", None)
    message = str(exc)

    if isinstance(exc, openai.RateLimitError):
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

    if isinstance(exc, openai.AuthenticationError | openai.PermissionDeniedError):
        return AuthenticationError(message, status_code=status or 401)

    if status == 529:
        return OverloadedError(message)

    retryable = status is not None and status in {500, 502, 503}
    return ProviderError(message, status_code=status, retryable=retryable)


def _convert_tools(
    tools: list[ToolDefinition] | None,
) -> list[dict[str, Any]] | None:
    """Convert our ToolDefinition list to OpenAI function-calling format.

    Args:
        tools: Tool definitions in our unified format.

    Returns:
        List of OpenAI tool dicts, or ``None`` if no tools.
    """
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _build_messages(
    messages: list[dict[str, Any]],
    system: str | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Prepend the system prompt to messages in OpenAI format.

    OpenAI uses a flat messages list with a system-role message at
    the top, unlike Anthropic's separate ``system`` parameter.

    Args:
        messages: Conversation messages.
        system: System prompt as a string or list of content blocks.

    Returns:
        Messages list with system prompt prepended.
    """
    result: list[dict[str, Any]] = []

    if isinstance(system, str) and system:
        result.append({"role": "system", "content": system})
    elif isinstance(system, list) and system:
        # Anthropic-style system blocks → join into a single string
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict):
                parts.append(block.get("text", str(block)))
            else:
                parts.append(str(block))
        result.append({"role": "system", "content": "\n".join(parts)})

    # Convert Anthropic-style messages to OpenAI format
    for msg in messages:
        result.extend(_convert_message(msg))

    return result


def _convert_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a single message from Anthropic to OpenAI format.

    One Anthropic message may expand into several OpenAI messages: a message
    carrying N ``tool_result`` blocks (parallel tool calls) becomes N separate
    ``role: "tool"`` messages, so callers must not assume a 1:1 mapping.

    Args:
        msg: Message in Anthropic or already-OpenAI format.

    Returns:
        One or more message dicts in OpenAI chat format.
    """
    role = msg.get("role", "user")
    content = msg.get("content", "")

    # If content is a string, pass through
    if isinstance(content, str):
        return [msg]

    # Content is a list of blocks — convert each
    openai_content: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    tool_messages: list[dict[str, Any]] = []

    for block in content:
        if not isinstance(block, dict):
            continue

        block_type = block.get("type", "")

        if block_type == "text":
            openai_content.append({
                "type": "text",
                "text": block.get("text", ""),
            })

        elif block_type == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input", {})),
                },
            })

        elif block_type == "tool_result":
            # Each tool_result becomes its own "tool" role message.
            tool_messages.append({
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": block.get("content", ""),
            })

        elif block_type == "image":
            source = block.get("source", {})
            openai_content.append({
                "type": "image_url",
                "image_url": {
                    "url": (
                        f"data:{source.get('media_type', 'image/png')};"
                        f"base64,{source.get('data', '')}"
                    ),
                },
            })

    out: list[dict[str, Any]] = []

    # Emit the main assistant/user message only when it carries content or
    # tool calls — a pure tool_result message produces only tool messages.
    if openai_content or tool_calls:
        main: dict[str, Any] = {"role": role}
        if openai_content:
            # Simplify: if only one text block, use plain string
            if (
                len(openai_content) == 1
                and openai_content[0].get("type") == "text"
            ):
                main["content"] = openai_content[0]["text"]
            else:
                main["content"] = openai_content
        else:
            main["content"] = ""
        if tool_calls:
            main["tool_calls"] = tool_calls
        out.append(main)

    out.extend(tool_messages)
    return out


async def _stream_events(
    stream: openai.AsyncStream[ChatCompletionChunk],
) -> AsyncIterator[StreamEvent]:
    """Convert OpenAI SDK stream to our unified StreamEvent format.

    Handles:
    - ``chat.completion.chunk`` with text deltas → ``text_delta``
    - ``chat.completion.chunk`` with tool call deltas → ``tool_use_*``
    - Final chunk with ``finish_reason`` → ``message_delta`` + ``message_stop``

    Args:
        stream: Raw OpenAI async stream.

    Yields:
        Normalized ``StreamEvent`` objects.
    """
    # Track tool calls being built across chunks
    tool_calls_buffer: dict[int, dict[str, Any]] = {}
    pending_usage: dict[str, int] = {}
    final_stop_reason: str | None = None

    async for chunk in stream:
        # With stream_options.include_usage, usage arrives in a trailing
        # chunk whose `choices` list is empty — capture it before skipping.
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage:
            pending_usage = {
                "input_tokens": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(chunk_usage, "completion_tokens", 0) or 0,
            }

        if not chunk.choices:
            continue

        choice = chunk.choices[0]
        delta = choice.delta
        finish_reason = choice.finish_reason

        # Text content delta
        if delta.content:
            yield StreamEvent(
                type="text_delta",
                content=delta.content,
                raw=chunk,
            )

        # Tool call deltas
        if delta.tool_calls:
            for tc_delta in delta.tool_calls:
                idx = tc_delta.index

                if idx not in tool_calls_buffer:
                    tool_calls_buffer[idx] = {
                        "id": "",
                        "name": "",
                        "arguments": "",
                    }

                buf = tool_calls_buffer[idx]

                # Tool call ID comes in the first chunk for this index
                if tc_delta.id:
                    buf["id"] = tc_delta.id
                    # Emit tool_use_start
                    yield StreamEvent(
                        type="tool_use_start",
                        tool_id=buf["id"],
                        tool_name=tc_delta.function.name if tc_delta.function else "",
                        raw=chunk,
                    )
                    if tc_delta.function and tc_delta.function.name:
                        buf["name"] = tc_delta.function.name

                # Function name might come in a separate chunk
                if tc_delta.function:
                    if tc_delta.function.name and not buf["name"]:
                        buf["name"] = tc_delta.function.name
                    if tc_delta.function.arguments:
                        buf["arguments"] += tc_delta.function.arguments
                        yield StreamEvent(
                            type="tool_use_delta",
                            tool_id=buf["id"],
                            content=tc_delta.function.arguments,
                            raw=chunk,
                        )

        # Finish reason indicates end of generation
        if finish_reason:
            # Emit tool_use_stop for any pending tool calls
            for idx_key in sorted(tool_calls_buffer.keys()):
                buf = tool_calls_buffer[idx_key]
                yield StreamEvent(
                    type="tool_use_stop",
                    tool_id=buf["id"],
                    tool_name=buf["name"],
                    raw=chunk,
                )

            final_stop_reason = _map_finish_reason(finish_reason)

    # Emit terminal events after the stream drains, so the trailing
    # usage-only chunk is included.
    yield StreamEvent(
        type="message_delta",
        stop_reason=final_stop_reason or "end_turn",
        usage=pending_usage,
    )
    yield StreamEvent(type="message_stop", usage=pending_usage)


def _map_finish_reason(finish_reason: str) -> str:
    """Map OpenAI finish_reason to our stop_reason values.

    Args:
        finish_reason: OpenAI's finish reason string.

    Returns:
        Normalized stop reason: ``end_turn``, ``tool_use``, or ``max_tokens``.
    """
    mapping = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "end_turn",
    }
    return mapping.get(finish_reason, "end_turn")


class OpenAICompatProvider(BaseProvider):
    """Provider for OpenAI-compatible APIs.

    Works with OpenAI, Azure OpenAI, Ollama, vLLM, LM Studio,
    and any other service that implements the OpenAI chat completions
    API.

    Args:
        api_key: API key. Required for OpenAI; can be a dummy for
            local endpoints like Ollama.
        base_url: Custom API base URL. If ``None``, uses the
            OpenAI default.
        default_model: Default model when none is specified.
        **client_kwargs: Additional keyword arguments for
            ``openai.AsyncOpenAI``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str = DEFAULT_MODEL,
        **client_kwargs: Any,
    ) -> None:
        self._default_model = default_model
        self._client = openai.AsyncOpenAI(
            api_key=api_key or "not-needed",
            base_url=base_url,
            **client_kwargs,
        )

    @property
    def provider_name(self) -> str:
        return "openai"

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
        """Send messages to an OpenAI-compatible API.

        Args:
            messages: Conversation messages (Anthropic or OpenAI format;
                auto-converted).
            system: System prompt string or list of content blocks.
            tools: Optional tool definitions.
            model: Model identifier or alias.
            max_tokens: Maximum output tokens.
            stream: Whether to stream the response.
            **kwargs: Extra parameters forwarded to the OpenAI API
                (e.g. ``temperature``, ``top_p``, ``frequency_penalty``).

        Returns:
            Async iterator of ``StreamEvent`` if streaming, otherwise
            a ``ProviderResponse``.

        Raises:
            ProviderError: On API errors (mapped from OpenAI SDK errors).
        """
        resolved_model = self.resolve_model(model) or self._default_model
        api_tools = _convert_tools(tools)
        api_messages = _build_messages(messages, system)

        try:
            if stream:
                response_stream = await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=api_messages,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    tools=api_tools,  # type: ignore[arg-type]
                    stream=True,
                    stream_options={"include_usage": True},
                    **kwargs,
                )
                return _stream_events(response_stream)
            else:
                response = await self._client.chat.completions.create(
                    model=resolved_model,
                    messages=api_messages,  # type: ignore[arg-type]
                    max_tokens=max_tokens,
                    tools=api_tools,  # type: ignore[arg-type]
                    stream=False,
                    **kwargs,
                )
                return _build_response(response, resolved_model)

        except openai.APIError as exc:
            raise _map_error(exc) from exc

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()


def _build_response(
    response: openai.types.chat.ChatCompletion,
    model: str,
) -> ProviderResponse:
    """Convert a complete OpenAI response to our unified format.

    Args:
        response: OpenAI ``ChatCompletion`` response object.
        model: Model identifier used for this request.

    Returns:
        A ``ProviderResponse`` with normalized content blocks.
    """
    if not response.choices:
        return ProviderResponse(
            content=[],
            stop_reason="end_turn",
            usage={},
            model=model,
        )

    choice = response.choices[0]
    message = choice.message
    content_blocks: list[dict[str, Any]] = []

    # Text content
    if message.content:
        content_blocks.append({
            "type": "text",
            "text": message.content,
        })

    # Tool calls
    if message.tool_calls:
        for tc in message.tool_calls:
            try:
                arguments = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}

            content_blocks.append({
                "type": "tool_use",
                "id": tc.id,
                "name": tc.function.name,
                "input": arguments,
            })

    # Usage
    usage: dict[str, int] = {}
    if response.usage:
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
        }

    stop_reason = _map_finish_reason(choice.finish_reason or "stop")

    return ProviderResponse(
        content=content_blocks,
        stop_reason=stop_reason,
        usage=usage,
        model=model,
    )
