"""Tests for QueryEngine provider retry behavior."""

from __future__ import annotations

from typing import Any

from claude_code.core.config import AppConfig
from claude_code.core.query_engine import QueryEngine
from claude_code.providers.base import ProviderError, RateLimitError, StreamEvent
from claude_code.providers.retry import RetryConfig
from claude_code.tools.registry import ToolRegistry

_OK = [
    StreamEvent(type="text_delta", content="hi"),
    StreamEvent(type="message_stop", stop_reason="end_turn"),
]


class _Flaky:
    """Provider stub: raise on the first ``fail_times`` calls, then succeed."""

    def __init__(self, fail_times: int, exc: Exception) -> None:
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    async def create_message(self, **kwargs: Any):
        self.calls += 1
        fail = self.calls <= self.fail_times
        exc = self.exc

        async def gen():
            if fail:
                raise exc
                yield  # pragma: no cover
            for event in _OK:
                yield event

        return gen()


class _MidStreamFail:
    """Provider stub that streams output, then fails — must not be retried."""

    def __init__(self) -> None:
        self.calls = 0

    async def create_message(self, **kwargs: Any):
        self.calls += 1

        async def gen():
            yield StreamEvent(type="text_delta", content="partial ")
            raise RateLimitError("mid-stream", retry_after=0.001)

        return gen()


def _engine(provider: Any) -> QueryEngine:
    e = QueryEngine(provider=provider, tool_registry=ToolRegistry(), config=AppConfig())
    e._retry_config = RetryConfig(max_retries=3, base_delay=0.001, jitter=False)
    return e


async def test_retryable_error_then_success() -> None:
    p = _Flaky(2, RateLimitError("rl", retry_after=0.001))
    result = await _engine(p).run("x")
    assert result.error is None
    assert result.text == "hi"
    assert p.calls == 3  # 2 failures + 1 success


async def test_non_retryable_not_retried() -> None:
    p = _Flaky(1, ProviderError("bad request", retryable=False))
    result = await _engine(p).run("x")
    assert result.error is not None
    assert p.calls == 1


async def test_retries_exhausted_surfaces_error() -> None:
    p = _Flaky(99, RateLimitError("rl", retry_after=0.001))
    result = await _engine(p).run("x")
    assert result.error is not None
    assert p.calls == 4  # 1 initial + 3 retries


async def test_no_retry_after_output_streamed() -> None:
    p = _MidStreamFail()
    result = await _engine(p).run("x")
    assert result.error is not None
    assert p.calls == 1  # output already streamed — retry would duplicate it
