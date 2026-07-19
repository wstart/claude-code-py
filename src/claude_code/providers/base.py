"""Abstract base classes and data models for LLM providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    """Tool definition for the LLM API.

    Describes a tool the model can invoke, including its name,
    description, and expected input schema (JSON Schema).
    """

    name: str
    description: str
    input_schema: dict[str, Any] = Field(
        description="JSON Schema describing the tool's input parameters."
    )


class StreamEvent(BaseModel):
    """Unified stream event emitted by any provider.

    All providers normalize their streaming output into this format
    so the UI and agent loop can consume a single event type.
    """

    type: str = Field(
        description=(
            "Event type: text_delta, tool_use_start, tool_use_delta, "
            "tool_use_stop, message_start, message_delta, message_stop, "
            "error, done"
        )
    )
    content: str = Field(default="", description="Text content for text_delta events.")
    tool_id: str = Field(default="", description="Unique tool-use block identifier.")
    tool_name: str = Field(default="", description="Tool name for tool_use events.")
    tool_input: dict[str, Any] = Field(
        default_factory=dict,
        description="Accumulated tool input JSON (populated on tool_use_stop).",
    )
    stop_reason: str = Field(
        default="",
        description="Why the model stopped: end_turn, tool_use, max_tokens.",
    )
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage: input_tokens, output_tokens.",
    )
    raw: Any = Field(default=None, description="Raw provider event for debugging.")


class ProviderResponse(BaseModel):
    """Complete (non-streaming) response from a provider."""

    content: list[dict[str, Any]] = Field(
        description="List of content blocks (text, tool_use, etc.)."
    )
    stop_reason: str = Field(description="Why generation stopped.")
    usage: dict[str, int] = Field(
        default_factory=dict,
        description="Token usage breakdown.",
    )
    model: str = Field(description="Model identifier that produced this response.")


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class RateLimitError(ProviderError):
    """Raised when the provider rate-limits the request (HTTP 429)."""

    def __init__(self, message: str, *, retry_after: float | None = None):
        super().__init__(message, status_code=429, retryable=True)
        self.retry_after = retry_after


class AuthenticationError(ProviderError):
    """Raised on authentication / authorization failures (HTTP 401/403)."""

    def __init__(self, message: str, *, status_code: int = 401):
        super().__init__(message, status_code=status_code, retryable=False)


class OverloadedError(ProviderError):
    """Raised when the provider is overloaded (HTTP 529)."""

    def __init__(self, message: str = "Provider is overloaded"):
        super().__init__(message, status_code=529, retryable=True)


# Model alias mapping: short name -> full model identifier
MODEL_ALIASES: dict[str, dict[str, str]] = {
    "anthropic": {
        "sonnet": "claude-sonnet-4-20250514",
        "opus": "claude-opus-4-20250514",
        "haiku": "claude-haiku-4-20250414",
    },
    "openai": {
        "gpt-4o": "gpt-4o",
        "gpt-4o-mini": "gpt-4o-mini",
        "o1": "o1",
        "o3-mini": "o3-mini",
    },
}


def resolve_model_alias(model: str, provider: str) -> str:
    """Resolve a short model alias to its full identifier.

    Args:
        model: Model name or alias (e.g. "sonnet", "gpt-4o").
        provider: Provider key to look up aliases for.

    Returns:
        The resolved full model identifier, or the original model
        string if no alias is found.
    """
    aliases = MODEL_ALIASES.get(provider, {})
    return aliases.get(model, model)


class BaseProvider(ABC):
    """Abstract base for all LLM providers.

    Subclasses must implement ``create_message``, ``close``, and the
    ``provider_name`` property. The provider is responsible for:

    - Sending messages to the LLM API
    - Streaming back normalized ``StreamEvent`` objects
    - Handling provider-specific error codes and retries (via retry.py)
    - Resolving model aliases
    """

    @abstractmethod
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
        """Send messages and get a response (streaming or non-streaming).

        Args:
            messages: Conversation messages in the provider's expected format.
            system: System prompt (string or list of content blocks).
            tools: Optional list of tool definitions the model may invoke.
            model: Model identifier or alias. ``None`` uses the provider default.
            max_tokens: Maximum output tokens to generate.
            stream: If ``True``, return an async iterator of ``StreamEvent``.
                If ``False``, return a complete ``ProviderResponse``.
            **kwargs: Provider-specific options.

        Returns:
            An async iterator of ``StreamEvent`` (streaming) or a
            ``ProviderResponse`` (non-streaming).

        Raises:
            ProviderError: On API errors (auth, rate limit, overload, etc.).
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release any resources held by the provider (HTTP clients, etc.)."""
        ...

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier for this provider (e.g. 'anthropic', 'openai')."""
        ...

    def resolve_model(self, model: str | None) -> str | None:
        """Resolve a model alias using this provider's alias map.

        Args:
            model: Model name or alias.

        Returns:
            Resolved model identifier, or ``None`` if input was ``None``.
        """
        if model is None:
            return None
        return resolve_model_alias(model, self.provider_name)

    async def __aenter__(self) -> BaseProvider:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()
