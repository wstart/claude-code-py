"""Exponential backoff retry logic for LLM provider calls."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from claude_code.providers.base import (
    OverloadedError,
    ProviderError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes that should be retried
RETRYABLE_STATUS_CODES: set[int] = {429, 500, 502, 503, 529}

# HTTP status codes that must never be retried
NON_RETRYABLE_STATUS_CODES: set[int] = {400, 401, 403, 404, 422}


class RetryConfig:
    """Configuration for retry behaviour.

    Attributes:
        max_retries: Maximum number of retry attempts (not counting the
            initial call). ``0`` disables retries.
        base_delay: Base delay in seconds before the first retry.
        max_delay: Maximum delay cap in seconds.
        exponential_base: Multiplier applied to the delay after each retry.
        jitter: If ``True``, add random jitter to each delay.
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter


# Module-level default config; callers can override per-call.
DEFAULT_RETRY_CONFIG = RetryConfig()


def _compute_delay(
    attempt: int,
    config: RetryConfig,
    retry_after: float | None = None,
) -> float:
    """Compute the delay before the next retry attempt.

    Args:
        attempt: Zero-based attempt index (0 = first retry).
        config: Retry configuration.
        retry_after: Server-supplied ``Retry-After`` value in seconds.
            When present, it takes precedence over the computed backoff.

    Returns:
        Delay in seconds before the next attempt.
    """
    if retry_after is not None and retry_after > 0:
        # Honour the server's Retry-After as a floor — only add a small
        # positive jitter, never full jitter that could retry too early.
        delay = min(retry_after, config.max_delay)
        if config.jitter:
            delay += random.uniform(0, 1.0)  # noqa: S311
        return delay

    delay = config.base_delay * (config.exponential_base ** attempt)
    delay = min(delay, config.max_delay)

    if config.jitter:
        # Full jitter: uniform random in [0, delay]
        delay = random.uniform(0, delay)  # noqa: S311

    return delay


def is_retryable(error: Exception) -> bool:
    """Determine whether an error should be retried.

    Args:
        error: The exception raised during the API call.

    Returns:
        ``True`` if the call should be retried.
    """
    if isinstance(error, RateLimitError | OverloadedError):
        return True
    if isinstance(error, ProviderError):
        return error.retryable
    # Treat unexpected errors as non-retryable by default
    return False


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> T:
    """Execute an async callable with exponential backoff retry.

    Args:
        fn: Async function to call.
        *args: Positional arguments forwarded to ``fn``.
        config: Retry configuration. Uses ``DEFAULT_RETRY_CONFIG`` if ``None``.
        **kwargs: Keyword arguments forwarded to ``fn``.

    Returns:
        The return value of ``fn``.

    Raises:
        The last exception if all retries are exhausted, or any
        non-retryable exception immediately.
    """
    cfg = config or DEFAULT_RETRY_CONFIG
    last_error: Exception | None = None

    for attempt in range(cfg.max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            last_error = exc

            if not is_retryable(exc):
                raise

            if attempt >= cfg.max_retries:
                logger.warning(
                    "Retry limit reached (%d/%d) for %s",
                    attempt + 1,
                    cfg.max_retries + 1,
                    fn.__name__,
                )
                raise

            retry_after: float | None = None
            if isinstance(exc, RateLimitError):
                retry_after = exc.retry_after

            delay = _compute_delay(attempt, cfg, retry_after)
            logger.info(
                "Retrying %s in %.2fs (attempt %d/%d): %s",
                fn.__name__,
                delay,
                attempt + 1,
                cfg.max_retries,
                exc,
            )
            await asyncio.sleep(delay)

    # Should be unreachable, but satisfies mypy
    assert last_error is not None
    raise last_error


def retry_decorator(
    config: RetryConfig | None = None,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator that wraps an async function with retry logic.

    Usage::

        @retry_decorator(RetryConfig(max_retries=5))
        async def call_api():
            ...

    Args:
        config: Retry configuration. Uses defaults if ``None``.

    Returns:
        Decorator function.
    """

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await with_retry(fn, *args, config=config, **kwargs)

        wrapper.__name__ = fn.__name__
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator


async def retry_stream(
    factory: Callable[..., Awaitable[T]],
    *args: Any,
    config: RetryConfig | None = None,
    **kwargs: Any,
) -> T:
    """Retry wrapper specifically for stream-creating callables.

    Identical to ``with_retry`` but named for clarity when the callable
    returns an async iterator (stream). The retry happens around stream
    *creation*, not around individual events within the stream.

    Args:
        factory: Async callable that creates and returns the stream.
        *args: Positional arguments forwarded to ``factory``.
        config: Retry configuration.
        **kwargs: Keyword arguments forwarded to ``factory``.

    Returns:
        The stream object returned by ``factory``.
    """
    return await with_retry(factory, *args, config=config, **kwargs)
