"""QueryEngine must count input_tokens reported in message_start."""

from __future__ import annotations

from typing import Any

from claude_code.core.config import AppConfig
from claude_code.core.query_engine import QueryEngine
from claude_code.providers.base import StreamEvent
from claude_code.tools.registry import ToolRegistry


class _AnthropicStyleProvider:
    """Reports input_tokens in message_start (Anthropic/Bedrock/Vertex shape)."""

    async def create_message(self, **kwargs: Any):
        async def gen():
            yield StreamEvent(
                type="message_start",
                usage={"input_tokens": 1000, "output_tokens": 1},
            )
            yield StreamEvent(type="text_delta", content="hi")
            yield StreamEvent(
                type="message_delta",
                stop_reason="end_turn",
                usage={"output_tokens": 42},
            )
            yield StreamEvent(type="message_stop", stop_reason="end_turn")
        return gen()


async def test_message_start_input_tokens_counted() -> None:
    eng = QueryEngine(
        provider=_AnthropicStyleProvider(),
        tool_registry=ToolRegistry(),
        config=AppConfig(model="claude-opus-4-8"),
    )
    result = await eng.run("hello")
    assert result.error is None
    cost = eng.get_cost_info()
    # input_tokens from message_start must not be dropped.
    assert cost.input_tokens == 1000
    assert cost.output_tokens == 42
    # Cost reflects both sides (opus-4-8: $5 in / $25 out per 1M).
    assert cost.estimated_cost_usd > 0
