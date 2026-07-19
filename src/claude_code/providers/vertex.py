"""Google Cloud Vertex AI provider for Claude models.

Uses the Vertex AI REST API via ``httpx`` to call Claude models on
GCP and normalizes streaming responses into ``StreamEvent`` format.
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

DEFAULT_MODEL = "claude-sonnet-4@20250514"
DEFAULT_REGION = "us-east5"
API_VERSION = "v1"

# Model alias mapping for Vertex AI model IDs
VERTEX_MODEL_ALIASES: dict[str, str] = {
    "sonnet": "claude-sonnet-4@20250514",
    "opus": "claude-opus-4@20250514",
    "haiku": "claude-haiku-4@20250414",
}


def _get_access_token() -> str:
    """Obtain a GCP access token for Vertex AI API calls.

    Tries the following sources in order:
    1. ``GOOGLE_OAUTH_ACCESS_TOKEN`` environment variable
    2. Application Default Credentials via ``google.auth``

    Returns:
        A valid GCP access token string.

    Raises:
        AuthenticationError: If no credentials are available.
    """
    # Check for explicit token
    token = os.environ.get("GOOGLE_OAUTH_ACCESS_TOKEN")
    if token:
        return token

    # Try Application Default Credentials
    try:
        import google.auth  # type: ignore[import-untyped]
        import google.auth.transport.requests  # type: ignore[import-untyped]

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        credentials.refresh(google.auth.transport.requests.Request())
        return credentials.token  # type: ignore[return-value]
    except ImportError:
        raise AuthenticationError(
            "GCP credentials not found. Install google-auth: "
            "pip install claude-code-py[gcp], or set "
            "GOOGLE_OAUTH_ACCESS_TOKEN environment variable."
        )
    except Exception as exc:
        raise AuthenticationError(
            f"Failed to obtain GCP credentials: {exc}"
        ) from exc


def _get_project_id() -> str:
    """Determine the GCP project ID.

    Checks:
    1. ``GOOGLE_CLOUD_PROJECT`` environment variable
    2. ``google.auth.default()`` project detection

    Returns:
        GCP project ID string.

    Raises:
        ProviderError: If project ID cannot be determined.
    """
    project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get(
        "GCLOUD_PROJECT"
    )
    if project:
        return project

    try:
        import google.auth  # type: ignore[import-untyped]

        _, detected_project = google.auth.default()
        if detected_project:
            return detected_project  # type: ignore[return-value]
    except Exception:
        pass

    raise ProviderError(
        "Could not determine GCP project ID. Set GOOGLE_CLOUD_PROJECT "
        "environment variable or configure Application Default Credentials.",
        retryable=False,
    )


def _map_http_error(status_code: int, body: str) -> ProviderError:
    """Map an HTTP error response from Vertex AI to our error hierarchy.

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


def _convert_tools(
    tools: list[ToolDefinition] | None,
) -> list[dict[str, Any]] | None:
    """Convert ToolDefinition list to Vertex AI tool format.

    Vertex uses the same Anthropic Messages API tool format.

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
    max_tokens: int,
    stream: bool,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build the request body for Vertex AI streaming/raw predict.

    Args:
        messages: Conversation messages.
        system: System prompt.
        tools: Tool definitions.
        max_tokens: Maximum output tokens.
        stream: Whether streaming is requested.
        **kwargs: Additional parameters.

    Returns:
        Request body dict for Vertex AI.
    """
    # Inner Anthropic messages payload
    anthropic_body: dict[str, Any] = {
        "anthropic_version": "vertex-2023-10-16",
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": stream,
    }

    if isinstance(system, str) and system:
        anthropic_body["system"] = system
    elif isinstance(system, list) and system:
        anthropic_body["system"] = system

    api_tools = _convert_tools(tools)
    if api_tools:
        anthropic_body["tools"] = api_tools

    for key in ("temperature", "top_p", "top_k", "stop_sequences", "thinking"):
        if key in kwargs:
            anthropic_body[key] = kwargs[key]

    return anthropic_body


async def _stream_events(
    response: httpx.Response,
) -> AsyncIterator[StreamEvent]:
    """Parse Vertex AI SSE stream into our unified StreamEvent format.

    Vertex returns Anthropic-style events via Server-Sent Events.

    Args:
        response: An httpx streaming response from Vertex AI.

    Yields:
        Normalized ``StreamEvent`` objects.
    """
    async with response as r:
        if r.status_code != 200:
            body = await r.aread()
            raise _map_http_error(r.status_code, body.decode(errors="replace"))

        async for line in r.aiter_lines():
            line = line.strip()
            if not line:
                continue

            # SSE format: "data: {...}"
            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if data_str == "[DONE]":
                break

            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                logger.warning("Failed to parse Vertex SSE data: %s", data_str[:200])
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


def _build_response(
    response_body: dict[str, Any],
    model: str,
) -> ProviderResponse:
    """Convert a complete Vertex AI response to our unified format.

    Args:
        response_body: Parsed JSON response from Vertex AI.
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


class VertexProvider(BaseProvider):
    """Provider for Google Cloud Vertex AI (Anthropic Claude models).

    Uses the Vertex AI REST API with SSE streaming and normalizes
    events to our unified ``StreamEvent`` format.

    Args:
        project_id: GCP project ID. If ``None``, auto-detected.
        region: GCP region (e.g. ``"us-east5"``).
        default_model: Default Vertex model ID.
        access_token: Explicit GCP access token. If ``None``, obtained
            from environment or Application Default Credentials.
        timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        project_id: str | None = None,
        region: str = DEFAULT_REGION,
        default_model: str = DEFAULT_MODEL,
        access_token: str | None = None,
        timeout: float = 300.0,
    ) -> None:
        self._project_id = project_id
        self._region = region
        self._default_model = default_model
        self._access_token = access_token
        self._timeout = timeout
        self._http_client: httpx.AsyncClient | None = None

    @property
    def provider_name(self) -> str:
        return "vertex"

    def _get_project(self) -> str:
        """Return the resolved project ID, detecting if necessary."""
        if self._project_id:
            return self._project_id
        self._project_id = _get_project_id()
        return self._project_id

    def _get_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if self._access_token:
            return self._access_token
        self._access_token = _get_access_token()
        return self._access_token

    def _build_url(self, model: str, stream: bool) -> str:
        """Build the Vertex AI endpoint URL.

        Args:
            model: Vertex model ID.
            stream: Whether to use the streaming endpoint.

        Returns:
            Full endpoint URL string.
        """
        project = self._get_project()
        action = "streamRawPredict" if stream else "rawPredict"
        return (
            f"https://{self._region}-aiplatform.googleapis.com/"
            f"{API_VERSION}/projects/{project}/locations/{self._region}/"
            f"publishers/anthropic/models/{model}:{action}"
        )

    def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client.

        Returns:
            An ``httpx.AsyncClient`` instance.
        """
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=30.0),
            )
        return self._http_client

    def _resolve_vertex_model(self, model: str | None) -> str:
        """Resolve a model alias to a Vertex model ID.

        Args:
            model: Model name, alias, or full Vertex ID.

        Returns:
            A Vertex model ID string.
        """
        if model is None:
            return self._default_model

        if model in VERTEX_MODEL_ALIASES:
            return VERTEX_MODEL_ALIASES[model]

        resolved = self.resolve_model(model)
        if resolved and resolved != model:
            return resolved

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
        """Send messages to Vertex AI and get a streaming or complete response.

        Args:
            messages: Conversation messages in Anthropic message format.
            system: System prompt string or list of content blocks.
            tools: Optional tool definitions.
            model: Model identifier, alias, or full Vertex model ID.
            max_tokens: Maximum output tokens.
            stream: Whether to stream the response.
            **kwargs: Extra parameters (temperature, top_p, etc.).

        Returns:
            Async iterator of ``StreamEvent`` if streaming, otherwise
            a ``ProviderResponse``.

        Raises:
            ProviderError: On API errors.
        """
        resolved_model = self._resolve_vertex_model(model)
        body = _build_request_body(
            messages, system, tools, max_tokens, stream, **kwargs
        )
        url = self._build_url(resolved_model, stream)
        token = self._get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

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
