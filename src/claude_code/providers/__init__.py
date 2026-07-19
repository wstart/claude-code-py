"""LLM provider layer — unified interface for Anthropic, OpenAI, and others."""

from claude_code.providers.base import (
    AuthenticationError,
    BaseProvider,
    MODEL_ALIASES,
    OverloadedError,
    ProviderError,
    ProviderResponse,
    RateLimitError,
    StreamEvent,
    ToolDefinition,
    resolve_model_alias,
)
from claude_code.providers.retry import (
    DEFAULT_RETRY_CONFIG,
    RetryConfig,
    is_retryable,
    retry_decorator,
    retry_stream,
    with_retry,
)
from claude_code.providers.streaming import (
    CancelToken,
    StreamCancelled,
    StreamProcessor,
    TextBuffer,
    ToolUseBuffer,
    UsageTracker,
    collect_stream,
)

__all__ = [
    # Base types
    "AuthenticationError",
    "BaseProvider",
    "MODEL_ALIASES",
    "OverloadedError",
    "ProviderError",
    "ProviderResponse",
    "RateLimitError",
    "StreamEvent",
    "ToolDefinition",
    "resolve_model_alias",
    # Retry
    "DEFAULT_RETRY_CONFIG",
    "RetryConfig",
    "is_retryable",
    "retry_decorator",
    "retry_stream",
    "with_retry",
    # Streaming
    "CancelToken",
    "StreamCancelled",
    "StreamProcessor",
    "TextBuffer",
    "ToolUseBuffer",
    "UsageTracker",
    "collect_stream",
]


def create_provider(
    provider: str = "anthropic",
    **kwargs,  # type: ignore[no-untyped-def]
) -> BaseProvider:
    """Factory to create a provider by name.

    Args:
        provider: Provider identifier — ``"anthropic"`` or ``"openai"``.
        **kwargs: Forwarded to the provider constructor.

    Returns:
        A configured ``BaseProvider`` instance.

    Raises:
        ValueError: If the provider name is not recognized.
    """
    if provider == "anthropic":
        from claude_code.providers.anthropic_provider import AnthropicProvider

        return AnthropicProvider(**kwargs)
    elif provider in ("openai", "ollama", "vllm"):
        from claude_code.providers.openai_compat import OpenAICompatProvider

        return OpenAICompatProvider(**kwargs)
    else:
        raise ValueError(f"Unknown provider: {provider!r}. Use 'anthropic' or 'openai'.")
