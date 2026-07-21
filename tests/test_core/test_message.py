"""Tests for claude_code.core.message — Message/Conversation/content blocks."""

from __future__ import annotations

from claude_code.core.message import (
    Conversation,
    CostInfo,
    ImageContent,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
)

# ---------------------------------------------------------------------------
# Content block construction & discrimination
# ---------------------------------------------------------------------------


def test_message_from_dict_content_discriminates_by_type() -> None:
    """Building a Message from raw dicts routes each block to the right class."""
    msg = Message.model_validate(
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"x": 1}},
            ],
        }
    )
    assert isinstance(msg.content[0], TextContent)
    assert isinstance(msg.content[1], ToolUseContent)
    assert msg.content[1].name == "Read"
    assert msg.content[1].input == {"x": 1}


def test_tool_use_input_defaults_to_empty_dict() -> None:
    block = ToolUseContent(id="t1", name="Glob")
    assert block.input == {}


def test_tool_result_defaults() -> None:
    block = ToolResultContent(tool_use_id="t1")
    assert block.content == ""
    assert block.is_error is False


# ---------------------------------------------------------------------------
# Message factory constructors
# ---------------------------------------------------------------------------


def test_user_assistant_system_factories() -> None:
    u = Message.user("hello")
    a = Message.assistant("hi there")
    s = Message.system("be nice")

    assert u.role == "user"
    assert a.role == "assistant"
    assert s.role == "system"
    for m in (u, a, s):
        assert len(m.content) == 1
        assert isinstance(m.content[0], TextContent)
    assert u.text() == "hello"


def test_with_tool_use_text_and_tools() -> None:
    tu = ToolUseContent(id="t1", name="Read", input={"file_path": "/x"})
    msg = Message.with_tool_use(text="thinking", tool_uses=[tu])

    assert msg.role == "assistant"
    assert isinstance(msg.content[0], TextContent)
    assert msg.content[0].text == "thinking"
    assert isinstance(msg.content[1], ToolUseContent)
    assert msg.has_tool_use() is True


def test_with_tool_use_tools_only() -> None:
    tu = ToolUseContent(id="t1", name="Read")
    msg = Message.with_tool_use(tool_uses=[tu])
    assert len(msg.content) == 1
    assert isinstance(msg.content[0], ToolUseContent)


def test_with_tool_use_empty_text_produces_no_text_block() -> None:
    """Falsy text (None or "") yields no leading text block."""
    tu = ToolUseContent(id="t1", name="Read")
    assert len(Message.with_tool_use(text="", tool_uses=[tu]).content) == 1
    assert len(Message.with_tool_use(text=None, tool_uses=[tu]).content) == 1


def test_with_tool_use_nothing_gives_empty_content() -> None:
    msg = Message.with_tool_use()
    assert msg.role == "assistant"
    assert msg.content == []
    assert msg.has_tool_use() is False


def test_with_tool_result_builds_user_message() -> None:
    msg = Message.with_tool_result("t1", "output text", is_error=True)
    assert msg.role == "user"
    assert len(msg.content) == 1
    block = msg.content[0]
    assert isinstance(block, ToolResultContent)
    assert block.tool_use_id == "t1"
    assert block.content == "output text"
    assert block.is_error is True


# ---------------------------------------------------------------------------
# Message accessors
# ---------------------------------------------------------------------------


def test_text_joins_only_text_blocks_with_newline() -> None:
    msg = Message(
        role="assistant",
        content=[
            TextContent(text="line one"),
            ToolUseContent(id="t1", name="Read"),
            TextContent(text="line two"),
        ],
    )
    assert msg.text() == "line one\nline two"


def test_text_empty_when_no_text_blocks() -> None:
    msg = Message(role="assistant", content=[ToolUseContent(id="t1", name="Read")])
    assert msg.text() == ""


def test_tool_uses_and_tool_results_filtering() -> None:
    msg = Message(
        role="assistant",
        content=[
            TextContent(text="x"),
            ToolUseContent(id="t1", name="Read"),
            ToolUseContent(id="t2", name="Write"),
        ],
    )
    assert [b.id for b in msg.tool_uses()] == ["t1", "t2"]
    assert msg.tool_results() == []

    result_msg = Message.with_tool_result("t1", "done")
    assert len(result_msg.tool_results()) == 1
    assert result_msg.tool_uses() == []


def test_approximate_tokens_counts_only_text() -> None:
    # 40 chars of text -> 40 // 4 == 10 tokens; tool_use adds nothing.
    msg = Message(
        role="assistant",
        content=[
            TextContent(text="a" * 40),
            ToolUseContent(id="t1", name="Read", input={"file_path": "x" * 999}),
        ],
    )
    assert msg.approximate_tokens() == 10


# ---------------------------------------------------------------------------
# CostInfo
# ---------------------------------------------------------------------------


def test_cost_info_total_tokens() -> None:
    cost = CostInfo(input_tokens=100, output_tokens=25)
    assert cost.total_tokens == 125


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


def test_conversation_add_and_counts() -> None:
    conv = Conversation()
    assert conv.message_count() == 0
    assert conv.last_message() is None
    assert conv.last_assistant_message() is None

    conv.add_message(Message.user("q"))
    conv.add_message(Message.assistant("a1"))
    conv.add_message(Message.user("q2"))

    assert conv.message_count() == 3
    assert conv.last_message().role == "user"
    assert conv.last_assistant_message().text() == "a1"


def test_conversation_last_assistant_returns_most_recent() -> None:
    conv = Conversation()
    conv.add_message(Message.assistant("first"))
    conv.add_message(Message.user("mid"))
    conv.add_message(Message.assistant("second"))
    assert conv.last_assistant_message().text() == "second"


def test_conversation_total_tokens_sums_messages() -> None:
    conv = Conversation()
    conv.add_message(Message.user("a" * 40))  # 10 tokens
    conv.add_message(Message.assistant("b" * 20))  # 5 tokens
    assert conv.total_tokens() == 15


def test_conversation_has_unique_session_id() -> None:
    a = Conversation()
    b = Conversation()
    assert a.session_id != b.session_id
    assert a.created_at.tzinfo is not None  # timezone-aware


def test_system_prompt_concatenates_system_messages() -> None:
    conv = Conversation()
    conv.add_message(Message.system("rule one"))
    conv.add_message(Message.user("hi"))
    conv.add_message(Message.system("rule two"))
    assert conv.system_prompt() == "rule one\n\nrule two"


def test_system_prompt_empty_when_no_system_messages() -> None:
    conv = Conversation()
    conv.add_message(Message.user("hi"))
    assert conv.system_prompt() == ""


# ---------------------------------------------------------------------------
# to_api_messages serialization
# ---------------------------------------------------------------------------


def test_to_api_messages_excludes_system() -> None:
    conv = Conversation()
    conv.add_message(Message.system("sys"))
    conv.add_message(Message.user("hi"))
    api = conv.to_api_messages()
    assert len(api) == 1
    assert api[0]["role"] == "user"


def test_to_api_messages_serializes_all_block_types() -> None:
    conv = Conversation()
    conv.add_message(
        Message(
            role="assistant",
            content=[
                TextContent(text="thought"),
                ImageContent(source={"type": "url", "url": "http://x/y.png"}),
                ToolUseContent(id="t1", name="Read", input={"file_path": "/x"}),
            ],
        )
    )
    conv.add_message(Message.with_tool_result("t1", "result body"))

    api = conv.to_api_messages()
    assistant_blocks = api[0]["content"]
    assert assistant_blocks[0] == {"type": "text", "text": "thought"}
    assert assistant_blocks[1] == {
        "type": "image",
        "source": {"type": "url", "url": "http://x/y.png"},
    }
    assert assistant_blocks[2] == {
        "type": "tool_use",
        "id": "t1",
        "name": "Read",
        "input": {"file_path": "/x"},
    }

    result_block = api[1]["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "t1"
    assert result_block["content"] == "result body"
    assert result_block["is_error"] is False


def test_to_api_messages_flattens_list_tool_result_content() -> None:
    """A tool_result whose content is a list of blocks is flattened for the API."""
    conv = Conversation()
    conv.add_message(
        Message(
            role="user",
            content=[
                ToolResultContent(
                    tool_use_id="t1",
                    content=[
                        TextContent(text="hello"),
                        ImageContent(
                            source={"type": "base64", "media_type": "image/png", "data": "AAA"}
                        ),
                    ],
                )
            ],
        )
    )
    api = conv.to_api_messages()
    content_val = api[0]["content"][0]["content"]
    assert content_val[0] == {"type": "text", "text": "hello"}
    # Non-text blocks are dumped via model_dump.
    assert content_val[1]["type"] == "image"
    assert content_val[1]["source"]["data"] == "AAA"
