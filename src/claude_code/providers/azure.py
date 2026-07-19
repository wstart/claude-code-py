"""Azure OpenAI / Azure AI provider for Claude-compatible models.

Uses the Azure OpenAI REST API via ``httpx`` and normalizes streaming
responses into the unified ``StreamEvent`` format. Supports both
chat completions and tool use.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

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
DEFAULT_API_VERSION = "2024-12-01-preview"


def _convert_tools(
    tools: list[ToolDefinition] | None,
) -> list[dict[str, Any]] | None:
    """Convert ToolDefinition list to Azure OpenAI function-calling format.

    Args:
        tools: Tool definitions in our unified format.

    Returns:
        List of Azure tool dicts, or ``None``.
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
    """Prepend system prompt and convert messages to Azure OpenAI format.

    Azure OpenAI uses the same chat completions message format as
    OpenAI, so we reuse the same conversion logic.

    Args:
        messages: Conversation messages (Anthropic or OpenAI format).
        system: System prompt as a string or list of content blocks.

    Returns:
        Messages list in Azure OpenAI format.
    """
    result: list[dict[str, Any]] = []

    if isinstance(system, str) and system:
        result.append({"role": "system", "content": system})
    elif isinstance(system, list) and system:
        parts: list[str] = []
        for block in system:
            if isinstance(block, dict):
                parts.append(block.get("text", str(block)))
            else:
                parts.append(str(block))
        result.append({"role": "system", "content": "\n".join(parts)})

    for msg in messages:
        result.append(_convert_message(msg))

    return result


def _convert_message(msg: dict[str, Any]) -> dict[str, Any]:
    """Convert a single message from Anthropic to Azure OpenAI format.

    Args:
        msg: Message in Anthropic or OpenAI format.

    Returns:
        Message dict in Azure OpenAI chat format.
    """
    role = msg.get("role", "user")
    content = msg.get("content", "")

    if isinstance(content, str):
        return msg

    openai_content: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []

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
            return {
                "role": "tool",
                "tool_call_id": block.get("tool_use_id", ""),
                "content": block.get("content", ""),
            }
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

    result: dict[str, Any] = {"role": role}

    if openai_content:
        if (
            len(openai_content) == 1
            and openai_content[0].get("type") == "text"
        ):
            result["content"] = openai_content[0]["text"]
        else:
            result["content"] = openai_content
    else:
        result["content"] = ""

    if tool_calls:
        result["tool_calls"] = tool_calls

    return result


def _map_finish_reason(finish_reason: str | None) -> str:
    """Map Azure OpenAI finish_reason to our stop_reason values.

    Args:
        finish_reason: Azure's finish reason string.

    Returns:
        Normalized stop reason.
    """
    if finish_reason is None:
        return "end_turn"
    mapping = {
        "stop": "end_turn",
        "tool_calls": "tool_use",
        "length": "max_tokens",
        "content_filter": "end_turn",
    }
    return mapping.get(finish_reason, "end_turn")


def _map_http_error(status_code: int, body: str) -> ProviderError:
    """Map an HTTP error response from Azure to our error hierarchy.

    Args:
        status_code: HTTP status code.
        body: Response body text.

    Returns:
        A ``ProviderError`` subclass.
    """
    if status_code == 429:
        return RateLimitError(body)
    if status_code in (401, 403):
        return AuthenticationError(body, status_code=status_code)
    if status_code == 529:
        return OverloadedError(body)

    retryable = status_code in {500, 502, 503}
    return ProviderError(body, status_code=status_code, retryable=retryable)


async def _stream_events(
    response: httpx.Response,
) -> AsyncIterator[StreamEvent]:
    """Parse Azure OpenAI SSE stream into our unified StreamEvent format.

    Azure returns OpenAI-compatible ``data: {...}`` SSE events.

    Args:
        response: An httpx streaming response.

    Yields:
        Normalized ``StreamEvent`` objects.
    """
    tool_calls_buffer: dict[int, dict[str, Any]] = {}

    async with response as r:
        if r.status_code != 200:
            body = await r.aread()
            raise _map_http_error(r.status_code, body.decode(errors="replace"))

        async for line in r.aiter_lines():
            line = line.strip()
            if not line:
                continue

            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                logger.warning("Failed to parse Azure SSE data: %s", data_str[:200])
                continue

            choices = data.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            # Text content
            text_content = delta.get("content")
            if text_content:
                yield StreamEvent(
                    type="text_delta",
                    content=text_content,
                    raw=data,
                )

            # Tool call deltas
            tc_deltas = delta.get("tool_calls")
            if tc_deltas:
                for tc_delta in tc_deltas:
                    idx = tc_delta.get("index", 0)

                    if idx not in tool_calls_buffer:
                        tool_calls_buffer[idx] = {
                            "id": "",
                            "name": "",
                            "arguments": "",
                        }

                    buf = tool_calls_buffer[idx]

                    tc_id = tc_delta.get("id")
                    if tc_id:
                        buf["id"] = tc_id
                        fn = tc_delta.get("function", {})
                        buf["name"] = fn.get("name", "")
                        yield StreamEvent(
                            type="tool_use_start",
                            tool_id=buf["id"],
                            tool_name=buf["name"],
                            raw=data,
                        )

                    fn = tc_delta.get("function", {})
                    if fn.get("name") and not buf["name"]:
                        buf["name"] = fn["name"]
                    if fn.get("arguments"):
                        buf["arguments"] += fn["arguments"]
                        yield StreamEvent(
                            type="tool_use_delta",
                            tool_id=buf["id"],
                            content=fn["arguments"],
                            raw=data,
                        )

            # Finish reason
            if finish_reason:
                for idx_key in sorted(tool_calls_buffer.keys()):
                    buf = tool_calls_buffer[idx_key]
                    yield StreamEvent(
                        type="tool_use_stop",
                        tool_id=buf["id"],
                        tool_name=buf["name"],
                        raw=data,
                    )

                stop_reason = _map_finish_reason(finish_reason)
                usage: dict[str, int] = {}
                chunk_usage = data.get("usage")
                if chunk_usage:
                    usage = {
                        "input_tokens": chunk_usage.get("prompt_tokens", 0),
                        "output_tokens": chunk_usage.get("completion_tokens", 0),
                    }

                yield StreamEvent(
                    type="message_delta",
                    stop_reason=stop_reason,
                    usage=usage,
                    raw=data,
                )
                yield StreamEvent(type="message_stop", raw=data)


def _build_response(
    response_body: dict[str, Any],
    model: str,
) -> ProviderResponse:
    """Convert a complete Azure response to our unified format.

    Args:
        response_body: Parsed JSON response from Azure OpenAI.
        model: Model identifier used for this request.

    Returns:
        A ``ProviderResponse`` with normalized content blocks.
    """
    choices = response_body.get("choices", [])
    if not choices:
        return ProviderResponse(
            content=[],
            stop_reason="end_turn",
            usage={},
            model=model,
        )

    choice = choices[0]
    message = choice.get("message", {})
    content_blocks: list[dict[str, Any]] = []

    text_content = message.get("content")
    if text_content:
        content_blocks.append({
            "type": "text",
            "text": text_content,
        })

    for tc in message.get("tool_calls", []):
        try:
            arguments = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, TypeError, KeyError):
            arguments = {}

        content_blocks.append({
            "type": "tool_use",
            "id": tc.get("id", ""),
            "name": tc["function"].get("name", ""),
            "input": arguments,
        })

    usage_data = response_body.get("usage", {})
    usage: dict[str, int] = {
        "input_tokens": usage_data.get("prompt_tokens", 0),
        "output_tokens": usage_data.get("completion_tokens", 0),
    }

    stop_reason = _map_finish_reason(choice.get("finish_reason"))

    return ProviderResponse(
        content=content_blocks,
        stop_reason=stop_reason,
        usage=usage,
        model=model,
    )


class AzureProvider(BaseProvider):
    """Provider for Azure OpenAI / Azure AI (chat completions + tool use).

    Uses the Azure OpenAI REST API directly via ``httpx``, supporting
    both streaming and non-streaming responses.

    Args:
        endpoint: Azure resource endpoint, e.g.
            ``"https://myresource.openai.azure.com"``. If ``None``,
            reads from ``AZURE_OPENAI_ENDPOINT`` env var.
        api_key: Azure API key. If ``None``, reads from
            ``AZURE_OPENAI_API_KEY`` env var.
        deployment: Azure deployment name (model deployment). If ``None``,
            reads from ``AZURE_OPENAI_DEPLOYMENT`` env var.
        api_version: Azure API version string.
        default_model: Default model identifier for responses.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        endpoint: str | None = None,
        api_key: str | None = None,
        deployment: str | None = None,
        api_version: str = DEFAULT_API_VERSION,
        default_model: str = DEFAULT_MODEL,
        timeout: float = 300.0,
    ) -> None:
        self._endpoint = (
            endpoint
            or os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        ).rstrip("/")
        self._api_key = api_key or os.environ.get("AZURE_OPENAI_API_KEY", "")
        self._deployment = deployment or os.environ.get(
            "AZURE_OPENAI_DEPLOYMENT", ""
        )
        self._api_version = api_version
        self._default_model = default_model
        self._timeout = timeout
        self._http_client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "azure"

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=30.0),
            )
        return self._http_client

    def _build_url(self, deployment: str, stream: bool) -> str:
        """Build the Azure OpenAI chat completions endpoint URL.

        Args:
            deployment: Azure deployment name.
            stream: Whether this is a streaming request (appended as
                query param for Azure).

        Returns:
            Full endpoint URL.
        """
        base = (
            f"{self._endpoint}/openai/deployments/{deployment}"
            f"/chat/completions?api-version={self._api_version}"
        )
        return base

    def _build_headers(self) -> dict[str, str]:
        """Build request headers including authentication.

        Returns:
            Headers dict with ``api-key`` for Azure API key auth.
        """
        return {
            "api-key": self._api_key,
            "Content-Type": "application/json",
        }

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
        """Send messages to Azure OpenAI and get a response.

        Args:
            messages: Conversation messages (Anthropic or OpenAI format;
                auto-converted).
            system: System prompt string or list of content blocks.
            tools: Optional tool definitions.
            model: Model/deployment name. If ``None``, uses the
                configured deployment.
            max_tokens: Maximum output tokens.
            stream: Whether to stream the response.
            **kwargs: Extra parameters (temperature, top_p, etc.).

        Returns:
            Async iterator of ``StreamEvent`` if streaming, otherwise
            a ``ProviderResponse``.

        Raises:
            ProviderError: On API errors.
            ValueError: If endpoint, API key, or deployment is missing.
        """
        if not self._endpoint:
            raise ProviderError(
                "Azure endpoint is required. Set AZURE_OPENAI_ENDPOINT "
                "or pass endpoint= to the constructor.",
                retryable=False,
            )
        if not self._api_key:
            raise ProviderError(
                "Azure API key is required. Set AZURE_OPENAI_API_KEY "
                "or pass api_key= to the constructor.",
                retryable=False,
            )

        deployment = model or self._deployment or self._default_model
        resolved_model = self.resolve_model(model) or self._default_model

        api_messages = _build_messages(messages, system)
        api_tools = _convert_tools(tools)

        body: dict[str, Any] = {
            "messages": api_messages,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if api_tools:
            body["tools"] = api_tools
        if stream:
            body["stream_options"] = {"include_usage": True}

        for key in ("temperature", "top_p", "frequency_penalty", "presence_penalty"):
            if key in kwargs:
                body[key] = kwargs[key]

        url = self._build_url(deployment, stream)
        headers = self._build_headers()
        client = self._get_http_client()

        try:
            if stream:
                request = client.build_request(
                    "POST", url, json=body, headers=headers
                )
                response = await client.send(request, stream=True)

                if response.status_code != 200:
                    body_bytes = await response.aread()
                    raise _map_http_error(
                        response.status_code,
                        body_bytes.decode(errors="replace"),
                    )

                return _stream_events(response)
            else:
                response = await client.post(url, json=body, headers=headers)

                if response.status_code != 200:
                    raise _map_http_error(
                        response.status_code, response.text
                    )

                response_body = response.json()
                return _build_response(response_body, resolved_model)

        except httpx.HTTPError as exc:
            raise ProviderError(str(exc), retryable=True) from exc

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
