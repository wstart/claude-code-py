"""StreamEvent must tolerate null fields from providers/gateways."""

from __future__ import annotations

import pytest

from claude_code.providers.base import StreamEvent


@pytest.mark.parametrize("field", ["content", "tool_id", "tool_name", "stop_reason"])
def test_null_str_field_coerced_to_empty(field: str) -> None:
    # OpenAI-compatible gateways often send explicit null for these.
    ev = StreamEvent(type="message_delta", **{field: None})
    assert getattr(ev, field) == ""


@pytest.mark.parametrize("field", ["tool_input", "usage"])
def test_null_dict_field_coerced_to_empty(field: str) -> None:
    ev = StreamEvent(type="message_delta", **{field: None})
    assert getattr(ev, field) == {}


def test_usage_inner_null_coerced_to_zero() -> None:
    # Gateways may send output_tokens: null inside the usage dict; must not
    # crash (usage is dict[str, int]).
    ev = StreamEvent(type="message_delta", usage={"input_tokens": 100, "output_tokens": None})
    assert ev.usage == {"input_tokens": 100, "output_tokens": 0}


def test_normal_values_preserved() -> None:
    ev = StreamEvent(
        type="message_delta",
        stop_reason="end_turn",
        content="hi",
        tool_name="Read",
        tool_input={"file_path": "/x"},
        usage={"input_tokens": 5, "output_tokens": 2},
    )
    assert ev.stop_reason == "end_turn"
    assert ev.content == "hi"
    assert ev.tool_name == "Read"
    assert ev.tool_input == {"file_path": "/x"}
    assert ev.usage == {"input_tokens": 5, "output_tokens": 2}
