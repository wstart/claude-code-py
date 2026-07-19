"""AWS Bedrock provider for Claude models.

Uses ``boto3`` to call the Bedrock Runtime API and normalizes streaming
responses into the unified ``StreamEvent`` format.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

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

DEFAULT_MODEL = "anthropic.claude-sonnet-4-20250514-v1:0"

# Model alias mapping for Bedrock model IDs
BEDROCK_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "anthropic.claude-sonnet-4-20250514-v1:0",
    "opus": "anthropic.claude-opus-4-20250514-v1:0",
    "haiku": "anthropic.claude-haiku-4-20250414-v1:0",
}


def _import_boto3() -> Any:
    """Lazily import boto3, raising a clear error if not installed.

    Returns:
        The ``boto3`` module.

    Raises:
        ImportError: If ``boto3`` is not installed.
    """
    try:
        import boto3
        return boto3
    except ImportError:
        raise ImportError(
            "boto3 is required for the Bedrock provider. "
            "Install it with: pip install claude-code-py[aws]"
        ) from None


def _map_bedrock_error(exc: Exception) -> ProviderError:
    """Convert a boto3 / Bedrock error to our unified error hierarchy.

    Inspects the botocore error code and HTTP status to determine the
    appropriate error type.

    Args:
        exc: An exception from the boto3 SDK.

    Returns:
        A ``ProviderError`` subclass.
    """
    message = str(exc)

    # botocore wraps errors in ClientError with a response dict
    error_code = ""
    status_code: int | None = None
    try:
        response = exc.response  # type: ignore[attr-defined]
        error_info = response.get("Error", {})
        error_code = error_info.get("Code", "")
        status_code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    except (AttributeError, KeyError, TypeError):
        pass

    if error_code == "ThrottlingException" or status_code == 429:
        return RateLimitError(message)

    if error_code in ("AccessDeniedException", "UnrecognizedClientException"):
        return AuthenticationError(message, status_code=status_code or 403)

    if error_code == "ModelStreamThrottledException":
        return RateLimitError(message)

    if error_code in ("ServiceQuotaExceededException",):
        return OverloadedError(message)

    retryable = status_code is not None and status_code in {500, 502, 503, 529}
    return ProviderError(message, status_code=status_code, retryable=retryable)


def _convert_tools(
    tools: list[ToolDefinition] | None,
) -> list[dict[str, Any]] | None:
    """Convert our ToolDefinition list to Bedrock Anthropic tool format.

    Bedrock uses the same tool format as the Anthropic Messages API.

    Args:
        tools: Tool definitions in our unified format.

    Returns:
        List of tool dicts, or ``None``.
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


def _build_request_body(
    messages: list[dict[str, Any]],
    system: str | list[dict[str, Any]],
    tools: list[ToolDefinition] | None,
    model: str,
    max_tokens: int,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build the JSON body for a Bedrock invoke_model call.

    Uses the Anthropic Messages API format that Bedrock expects.

    Args:
        messages: Conversation messages.
        system: System prompt.
        tools: Tool definitions.
        model: Bedrock model ID.
        max_tokens: Maximum output tokens.
        **kwargs: Additional parameters (temperature, top_p, etc.).

    Returns:
        Request body dict for Bedrock.
    """
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }

    # System prompt
    if isinstance(system, str) and system:
        body["system"] = system
    elif isinstance(system, list) and system:
        body["system"] = system

    # Tools
    api_tools = _convert_tools(tools)
    if api_tools:
        body["tools"] = api_tools

    # Forward extra parameters
    for key in ("temperature", "top_p", "top_k", "stop_sequences", "thinking"):
        if key in kwargs:
            body[key] = kwargs[key]

    return body


async def _stream_events(
    response_stream: Any,
) -> AsyncIterator[StreamEvent]:
    """Convert Bedrock streaming response to our unified StreamEvent format.

    Bedrock's ``invoke_model_with_response_stream`` returns a stream of
    chunks containing JSON payloads with Anthropic-style events.

    Args:
        response_stream: The ``body`` from a Bedrock
            ``invoke_model_with_response_stream`` response.

    Yields:
        Normalized ``StreamEvent`` objects.
    """
    import asyncio

    for event in response_stream:
        chunk = event.get("chunk")
        if chunk is None:
            continue

        raw_bytes = chunk.get("bytes")
        if raw_bytes is None:
            continue

        try:
            data = json.loads(raw_bytes)
        except (json.JSONDecodeError, TypeError):
            logger.warning("Failed to parse Bedrock chunk: %s", raw_bytes[:200])
            continue

        event_type = data.get("type", "")

        if event_type == "message_start":
            message = data.get("message", {})
            usage = message.get("usage", {})
            yield StreamEvent(
                type="message_start",
                usage={
                    "input_tokens": usage.get("input_tokens", 0),
                    "output_tokens": usage.get("output_tokens", 0),
                },
                raw=data,
            )

        elif event_type == "content_block_start":
            block = data.get("content_block", {})
            block_type = block.get("type", "")

            if block_type == "text":
                text = block.get("text", "")
                if text:
                    yield StreamEvent(
                        type="text_delta",
                        content=text,
                        raw=data,
                    )
            elif block_type == "tool_use":
                yield StreamEvent(
                    type="tool_use_start",
                    tool_id=block.get("id", ""),
                    tool_name=block.get("name", ""),
                    raw=data,
                )

        elif event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")

            if delta_type == "text_delta":
                yield StreamEvent(
                    type="text_delta",
                    content=delta.get("text", ""),
                    raw=data,
                )
            elif delta_type == "input_json_delta":
                idx = data.get("index", 0)
                yield StreamEvent(
                    type="tool_use_delta",
                    tool_id=str(idx),
                    content=delta.get("partial_json", ""),
                    raw=data,
                )

        elif event_type == "content_block_stop":
            idx = data.get("index", 0)
            yield StreamEvent(
                type="tool_use_stop",
                tool_id=str(idx),
                raw=data,
            )

        elif event_type == "message_delta":
            delta = data.get("delta", {})
            usage_data = data.get("usage", {})
            yield StreamEvent(
                type="message_delta",
                stop_reason=delta.get("stop_reason", ""),
                usage={"output_tokens": usage_data.get("output_tokens", 0)},
                raw=data,
            )

        elif event_type == "message_stop":
            yield StreamEvent(type="message_stop", raw=data)

        # Give other async tasks a chance to run
        await asyncio.sleep(0)


def _build_response(
    response_body: dict[str, Any],
    model: str,
) -> ProviderResponse:
    """Convert a complete Bedrock response to our unified format.

    Args:
        response_body: Parsed JSON response from Bedrock.
        model: Model identifier used for this request.

    Returns:
        A ``ProviderResponse`` with normalized content blocks.
    """
    content_blocks: list[dict[str, Any]] = []

    for block in response_body.get("content", []):
        block_type = block.get("type", "")
        if block_type == "text":
            content_blocks.append({
                "type": "text",
                "text": block.get("text", ""),
            })
        elif block_type == "tool_use":
            content_blocks.append({
                "type": "tool_use",
                "id": block.get("id", ""),
                "name": block.get("name", ""),
                "input": block.get("input", {}),
            })

    usage_data = response_body.get("usage", {})
    usage: dict[str, int] = {
        "input_tokens": usage_data.get("input_tokens", 0),
        "output_tokens": usage_data.get("output_tokens", 0),
    }

    return ProviderResponse(
        content=content_blocks,
        stop_reason=response_body.get("stop_reason", "") or "",
        usage=usage,
        model=model,
    )


class BedrockProvider(BaseProvider):
    """Provider for AWS Bedrock (Anthropic Claude models).

    Wraps the boto3 ``bedrock-runtime`` client and normalizes streaming
    events to our unified ``StreamEvent`` format.

    Args:
        region_name: AWS region (e.g. ``"us-east-1"``). If ``None``,
            uses the ``AWS_DEFAULT_REGION`` env var or the boto3 default.
        aws_access_key_id: Explicit AWS access key ID. If ``None``,
            uses the standard boto3 credential chain.
        aws_secret_access_key: Explicit AWS secret access key.
        aws_session_token: Optional session token for temporary credentials.
        profile_name: AWS profile name from ``~/.aws/credentials``.
        default_model: Default Bedrock model ID.
        **client_kwargs: Extra keyword arguments for ``boto3.client``.
    """

    def __init__(
        self,
        region_name: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        aws_session_token: str | None = None,
        profile_name: str | None = None,
        default_model: str = DEFAULT_MODEL,
        **client_kwargs: Any,
    ) -> None:
        boto3 = _import_boto3()

        self._default_model = default_model

        session_kwargs: dict[str, Any] = {}
        if region_name:
            session_kwargs["region_name"] = region_name
        if profile_name:
            session_kwargs["profile_name"] = profile_name

        session = boto3.Session(**session_kwargs)

        client_kwargs_final: dict[str, Any] = {**client_kwargs}
        if aws_access_key_id:
            client_kwargs_final["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            client_kwargs_final["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            client_kwargs_final["aws_session_token"] = aws_session_token

        self._client = session.client("bedrock-runtime", **client_kwargs_final)

    @property
    def provider_name(self) -> str:
        return "bedrock"

    def _resolve_bedrock_model(self, model: str | None) -> str:
        """Resolve a model alias to a Bedrock model ID.

        Args:
            model: Model name, alias, or full Bedrock ID.

        Returns:
            A full Bedrock model ID string.
        """
        if model is None:
            return self._default_model

        # Check short aliases first
        if model in BEDROCK_MODEL_ALIASES:
            return BEDROCK_MODEL_ALIASES[model]

        # Check base class aliases
        resolved = self.resolve_model(model)
        if resolved and resolved != model:
            return resolved

        # Assume it's already a full Bedrock model ID
        return model

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
        """Send messages to Bedrock and get a streaming or complete response.

        Args:
            messages: Conversation messages in Anthropic message format.
            system: System prompt string or list of content blocks.
            tools: Optional tool definitions.
            model: Model identifier, alias, or full Bedrock model ID.
            max_tokens: Maximum output tokens.
            stream: Whether to stream the response.
            **kwargs: Extra parameters (temperature, top_p, etc.).

        Returns:
            Async iterator of ``StreamEvent`` if streaming, otherwise
            a ``ProviderResponse``.

        Raises:
            ProviderError: On API errors (mapped from boto3 errors).
        """
        resolved_model = self._resolve_bedrock_model(model)
        body = _build_request_body(
            messages, system, tools, resolved_model, max_tokens, **kwargs
        )
        body_json = json.dumps(body)

        try:
            if stream:
                response = self._client.invoke_model_with_response_stream(
                    modelId=resolved_model,
                    body=body_json,
                )
                response_stream = response.get("body")
                return _stream_events(response_stream)
            else:
                response = self._client.invoke_model(
                    modelId=resolved_model,
                    body=body_json,
                )
                response_body = json.loads(response["body"].read())
                return _build_response(response_body, resolved_model)

        except Exception as exc:
            # boto3 raises various exception types; map them all
            if hasattr(exc, "response"):
                raise _map_bedrock_error(exc) from exc
            raise ProviderError(str(exc), retryable=False) from exc

    async def close(self) -> None:
        """Close the underlying boto3 client (no-op for boto3)."""
        # boto3 clients don't require explicit closing, but we close
        # the underlying HTTP session if accessible.
        try:
            self._client.close()
        except (AttributeError, TypeError):
            pass
