"""Session persist/restore must preserve the full tool history."""

from __future__ import annotations

from claude_code.core.config import AppConfig
from claude_code.core.message import (
    Conversation,
    Message,
    TextContent,
    ToolResultContent,
    ToolUseContent,
    api_message_text,
)
from claude_code.core.query_engine import QueryEngine
from claude_code.core.session import SessionManager
from claude_code.tools.registry import ToolRegistry


def _conversation_with_tools() -> Conversation:
    conv = Conversation()
    conv.add_message(Message.user("read the file"))
    conv.add_message(Message(role="assistant", content=[
        TextContent(text="I'll read it."),
        ToolUseContent(id="tu_1", name="Read", input={"file_path": "/x"}),
    ]))
    conv.add_message(Message(role="user", content=[
        ToolResultContent(tool_use_id="tu_1", content="file contents", is_error=True),
    ]))
    conv.add_message(Message(role="assistant", content=[TextContent(text="Done.")]))
    return conv


def test_resume_round_trip_preserves_tool_blocks(tmp_path) -> None:
    api = _conversation_with_tools().to_api_messages()

    sm = SessionManager(working_directory=str(tmp_path))
    sess = sm.create_session(model="claude-opus-4-8")
    sess.replace_messages(api)
    sm.save_session(sess)
    reloaded = sm.load_session(sess.metadata.id)
    assert reloaded is not None

    eng = QueryEngine(provider=object(), tool_registry=ToolRegistry(), config=AppConfig())
    eng.restore_conversation(reloaded.messages)
    round_tripped = eng.conversation.to_api_messages()

    assert round_tripped == api
    types = [b["type"] for m in round_tripped for b in m["content"]]
    assert "tool_use" in types
    assert "tool_result" in types
    # is_error flag survives the round trip
    tool_result = next(
        b for m in round_tripped for b in m["content"] if b["type"] == "tool_result"
    )
    assert tool_result["is_error"] is True


def test_api_message_text_extraction() -> None:
    assert api_message_text("plain") == "plain"
    assert api_message_text([
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "a", "name": "X", "input": {}},
    ]) == "hello"
    # Pure tool_result content has no displayable text.
    assert api_message_text([
        {"type": "tool_result", "tool_use_id": "a", "content": "r"},
    ]) == ""
