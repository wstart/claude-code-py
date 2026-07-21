"""Tests for OpenAI/Azure message conversion (parallel tool_result handling)."""

from __future__ import annotations

import pytest

from claude_code.providers.azure import _convert_message as az_convert
from claude_code.providers.openai_compat import _convert_message as oc_convert


@pytest.mark.parametrize("convert", [oc_convert, az_convert])
def test_parallel_tool_results_expand_to_multiple_messages(convert) -> None:
    msg = {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "ra"},
            {"type": "tool_result", "tool_use_id": "b", "content": "rb"},
        ],
    }
    out = convert(msg)
    assert [m["role"] for m in out] == ["tool", "tool"]
    assert [m["tool_call_id"] for m in out] == ["a", "b"]
    assert [m["content"] for m in out] == ["ra", "rb"]


@pytest.mark.parametrize("convert", [oc_convert, az_convert])
def test_single_tool_result_still_converts(convert) -> None:
    out = convert(
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "x", "content": "r"},
        ]}
    )
    assert out == [{"role": "tool", "tool_call_id": "x", "content": "r"}]


@pytest.mark.parametrize("convert", [oc_convert, az_convert])
def test_assistant_text_plus_tool_use_is_single_message(convert) -> None:
    out = convert(
        {"role": "assistant", "content": [
            {"type": "text", "text": "hi"},
            {"type": "tool_use", "id": "t1", "name": "Echo", "input": {"x": 1}},
        ]}
    )
    assert len(out) == 1
    assert out[0]["content"] == "hi"
    assert out[0]["tool_calls"][0]["id"] == "t1"
    assert out[0]["tool_calls"][0]["function"]["name"] == "Echo"


@pytest.mark.parametrize("convert", [oc_convert, az_convert])
def test_string_content_passthrough(convert) -> None:
    msg = {"role": "user", "content": "hello"}
    assert convert(msg) == [msg]


@pytest.mark.parametrize("convert", [oc_convert, az_convert])
def test_mixed_text_and_tool_results_order(convert) -> None:
    # An assistant message with text plus a trailing tool_result: main
    # message first, then the tool message.
    out = convert(
        {"role": "assistant", "content": [
            {"type": "text", "text": "done"},
            {"type": "tool_result", "tool_use_id": "z", "content": "ok"},
        ]}
    )
    assert out[0]["role"] == "assistant"
    assert out[0]["content"] == "done"
    assert out[1] == {"role": "tool", "tool_call_id": "z", "content": "ok"}
