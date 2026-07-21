"""Tests for the QueryEngine agentic loop with a mocked provider."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from claude_code.core.config import AppConfig
from claude_code.core.query_engine import QueryEngine
from claude_code.providers.base import StreamEvent
from claude_code.tools.base import Tool, ToolResult
from claude_code.tools.registry import ToolRegistry


class FakeProvider:
    """Provider stub that replays scripted StreamEvent lists, one per call."""

    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        self._scripts = scripts
        self.calls = 0

    async def create_message(self, **kwargs: Any):
        script = self._scripts[min(self.calls, len(self._scripts) - 1)]
        self.calls += 1

        async def _stream():
            for event in script:
                yield event

        return _stream()


class EchoTool(Tool):
    name = "Echo"
    description = "Echoes the given text."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    read_only = True

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult.success(f"echo: {kwargs.get('text', '')}")


class SlowMutatingTool(Tool):
    """Mutating tool that records interleaving to detect concurrent runs."""

    name = "Mutate"
    description = "Simulated mutating tool."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    read_only = False

    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def execute(self, **kwargs: Any) -> ToolResult:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return ToolResult.success("done")


def _text_only_script(text: str, stop_reason: str = "end_turn") -> list[StreamEvent]:
    return [
        StreamEvent(type="text_delta", content=text),
        StreamEvent(type="message_stop", stop_reason=stop_reason),
    ]


def _tool_call_script(tool_name: str, tool_id: str, input_json: str) -> list[StreamEvent]:
    return [
        StreamEvent(type="tool_use_start", tool_id=tool_id, tool_name=tool_name),
        StreamEvent(type="tool_use_delta", content=input_json),
        StreamEvent(type="tool_use_stop", tool_id=tool_id),
        StreamEvent(type="message_stop", stop_reason="tool_use"),
    ]


def _make_engine(
    scripts: list[list[StreamEvent]],
    tools: list[Tool] | None = None,
    **config_kwargs: Any,
) -> QueryEngine:
    registry = ToolRegistry()
    for tool in tools or []:
        registry.register(tool)
    config = AppConfig(**config_kwargs)
    provider = FakeProvider(scripts)
    return QueryEngine(provider=provider, tool_registry=registry, config=config)


async def test_plain_text_turn() -> None:
    engine = _make_engine([_text_only_script("Hello!")])
    result = await engine.run("hi")
    assert result.text == "Hello!"
    assert result.stop_reason == "end_turn"
    assert result.num_turns == 1
    assert result.error is None


async def test_real_stop_reason_is_surfaced() -> None:
    """A max_tokens truncation must not be reported as end_turn."""
    engine = _make_engine([_text_only_script("partial", stop_reason="max_tokens")])
    result = await engine.run("hi")
    assert result.stop_reason == "max_tokens"


async def test_tool_round_trip() -> None:
    engine = _make_engine(
        [
            _tool_call_script("Echo", "tu_1", '{"text": "ping"}'),
            _text_only_script("The echo said ping."),
        ],
        tools=[EchoTool()],
    )
    result = await engine.run("echo ping")
    assert result.tool_calls == [{"name": "Echo", "input": {"text": "ping"}}]
    assert result.text == "The echo said ping."
    assert result.num_turns == 2

    # The tool_result message must reference the tool_use id.
    api_messages = engine.conversation.to_api_messages()
    tool_results = [
        block
        for msg in api_messages
        if isinstance(msg.get("content"), list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["tool_use_id"] == "tu_1"


async def test_unknown_tool_returns_error_result() -> None:
    engine = _make_engine(
        [
            _tool_call_script("Nope", "tu_1", "{}"),
            _text_only_script("recovered"),
        ],
    )
    result = await engine.run("call a missing tool")
    # The loop must keep going and feed the error back to the model.
    assert result.text == "recovered"
    assert result.error is None


async def test_max_turns_limit() -> None:
    # Provider always requests another tool call — loop must stop at max_turns.
    engine = _make_engine(
        [_tool_call_script("Echo", "tu_1", '{"text": "x"}')],
        tools=[EchoTool()],
        max_turns=3,
    )
    result = await engine.run("loop forever")
    assert result.stop_reason == "max_turns"
    assert result.num_turns == 3


async def test_budget_limit_stops_loop() -> None:
    scripts = [
        _tool_call_script("Echo", "tu_1", '{"text": "x"}'),
        _text_only_script("should not get here"),
    ]
    # Big usage on the first turn so the second turn exceeds the budget.
    scripts[0][-1] = StreamEvent(
        type="message_stop",
        stop_reason="tool_use",
        usage={"input_tokens": 1_000_000, "output_tokens": 1_000_000},
    )
    engine = _make_engine(
        scripts,
        tools=[EchoTool()],
        model="claude-opus-4-8",
        max_budget_usd=0.01,
    )
    result = await engine.run("spend money")
    assert result.stop_reason == "budget_exceeded"
    assert "Budget limit" in (result.error or "")


async def test_mutating_tools_run_serially() -> None:
    tool = SlowMutatingTool()
    script = [
        StreamEvent(type="tool_use_start", tool_id="tu_1", tool_name="Mutate"),
        StreamEvent(type="tool_use_delta", content='{"text": "a"}'),
        StreamEvent(type="tool_use_stop", tool_id="tu_1"),
        StreamEvent(type="tool_use_start", tool_id="tu_2", tool_name="Mutate"),
        StreamEvent(type="tool_use_delta", content='{"text": "b"}'),
        StreamEvent(type="tool_use_stop", tool_id="tu_2"),
        StreamEvent(type="message_stop", stop_reason="tool_use"),
    ]
    engine = _make_engine([script, _text_only_script("done")], tools=[tool])
    result = await engine.run("mutate twice")
    assert result.text == "done"
    assert tool.max_active == 1  # never ran concurrently


async def test_usage_and_cost_tracking() -> None:
    script = [
        StreamEvent(type="text_delta", content="hi"),
        StreamEvent(
            type="message_stop",
            stop_reason="end_turn",
            usage={"input_tokens": 1000, "output_tokens": 500},
        ),
    ]
    engine = _make_engine([script], model="claude-opus-4-8")
    await engine.run("hello")
    cost = engine.get_cost_info()
    assert cost.input_tokens == 1000
    assert cost.output_tokens == 500
    assert cost.estimated_cost_usd == pytest.approx(
        1000 * 5.0 / 1_000_000 + 500 * 25.0 / 1_000_000
    )
