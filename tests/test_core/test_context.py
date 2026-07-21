"""Tests for claude_code.core.context — needs_compression / compress behavior."""

from __future__ import annotations

from typing import Any

from claude_code.core.context import (
    MIN_RECENT_MESSAGES,
    compress,
    needs_compression,
)


def _text_msg(role: str, text: str) -> dict[str, Any]:
    return {"role": role, "content": [{"type": "text", "text": text}]}


def _str_msg(role: str, text: str) -> dict[str, Any]:
    """Message whose content is a plain string, not a block list."""
    return {"role": role, "content": text}


# ---------------------------------------------------------------------------
# needs_compression
# ---------------------------------------------------------------------------


def test_needs_compression_false_when_under_threshold() -> None:
    # 4 chars -> 1 token; nowhere near a 1000-token limit.
    msgs = [_str_msg("user", "test")]
    assert needs_compression(msgs, context_limit=1000, threshold=0.8) is False


def test_needs_compression_true_when_over_threshold() -> None:
    # 4000 chars -> 1000 tokens; threshold 0.8 * 1000 = 800.
    msgs = [_str_msg("user", "a" * 4000)]
    assert needs_compression(msgs, context_limit=1000, threshold=0.8) is True


def test_needs_compression_boundary_is_strict_greater_than() -> None:
    # Exactly at the limit must NOT trigger (uses strict >).
    # threshold int = int(1000 * 0.8) = 800 tokens -> 3200 chars.
    msgs = [_str_msg("user", "a" * 3200)]
    assert needs_compression(msgs, context_limit=1000, threshold=0.8) is False
    msgs = [_str_msg("user", "a" * 3204)]  # 801 tokens
    assert needs_compression(msgs, context_limit=1000, threshold=0.8) is True


def test_needs_compression_counts_block_list_content() -> None:
    msgs = [_text_msg("user", "a" * 4000)]
    assert needs_compression(msgs, context_limit=1000, threshold=0.8) is True


def test_needs_compression_empty_is_false() -> None:
    assert needs_compression([], context_limit=1000) is False


# ---------------------------------------------------------------------------
# compress — no-op cases
# ---------------------------------------------------------------------------


def test_compress_returns_unchanged_when_too_few_messages() -> None:
    msgs = [_text_msg("user", f"m{i}") for i in range(MIN_RECENT_MESSAGES + 1)]
    assert compress(msgs) == msgs


def test_compress_returns_unchanged_when_remaining_at_or_below_min() -> None:
    # One system msg + exactly MIN_RECENT_MESSAGES others -> nothing to compress.
    msgs = [_text_msg("system", "sys")]
    msgs += [_text_msg("user", f"m{i}") for i in range(MIN_RECENT_MESSAGES)]
    assert compress(msgs) == msgs


# ---------------------------------------------------------------------------
# compress — actual compression
# ---------------------------------------------------------------------------


def _build_long_conversation() -> list[dict[str, Any]]:
    """1 system + 12 alternating messages; middle=first 6, recent=last 6."""
    msgs: list[dict[str, Any]] = [_text_msg("system", "system prompt")]
    for i in range(12):
        role = "assistant" if i % 2 else "user"
        msgs.append(_text_msg(role, f"body-{i}"))
    return msgs


def test_compress_preserves_leading_system_message() -> None:
    msgs = _build_long_conversation()
    result = compress(msgs)
    assert result[0]["role"] == "system"
    assert result[0]["content"] == [{"type": "text", "text": "system prompt"}]


def test_compress_keeps_recent_messages_verbatim() -> None:
    msgs = _build_long_conversation()
    # Make the last recent message long; it must survive uncompressed.
    long_text = "R" * 500
    msgs[-1] = _text_msg("assistant", long_text)
    result = compress(msgs)
    assert result[-1]["content"][0]["text"] == long_text  # untouched


def test_compress_truncates_long_text_in_middle_assistant_message() -> None:
    msgs = _build_long_conversation()
    # Index 1 (first message after system) is in the middle window.
    long_text = "X" * 300
    msgs[1] = _text_msg("assistant", long_text)
    result = compress(msgs)
    compressed_text = result[1]["content"][0]["text"]
    assert compressed_text.endswith("... [compressed]")
    assert compressed_text.startswith("X" * 200)
    assert len(compressed_text) < 300


def test_compress_truncates_long_tool_result_in_middle_user_message() -> None:
    msgs = _build_long_conversation()
    long_result = "Y" * 300
    msgs[2] = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": long_result}],
    }
    result = compress(msgs)
    block = result[2]["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "t1"
    assert block["content"].endswith("... [compressed]")
    assert block["content"].startswith("Y" * 200)


def test_compress_truncates_long_tool_use_input_in_middle() -> None:
    msgs = _build_long_conversation()
    long_val = "Z" * 250
    msgs[1] = {
        "role": "assistant",
        "content": [
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"content": long_val}}
        ],
    }
    result = compress(msgs)
    truncated = result[1]["content"][0]["input"]["content"]
    assert truncated.endswith("...")
    assert len(truncated) == 103  # 100 chars + "..."


def test_compress_short_middle_content_untouched() -> None:
    msgs = _build_long_conversation()
    result = compress(msgs)
    # Middle message 3 had short text "body-2"; stays exactly the same.
    assert result[3]["content"][0]["text"] == "body-2"


def test_compress_drops_oldest_when_still_over_limit() -> None:
    """If compression can't fit, oldest middle messages are dropped."""
    msgs = _build_long_conversation()
    # Force a tiny limit so the drop-loop engages; recent + system are kept.
    result = compress(msgs, context_limit=1)
    # Recent block (6) + system (1) survive; middle collapses toward 1.
    assert result[0]["role"] == "system"
    assert len(result) < len(msgs)
    # The most recent original message is still present at the tail.
    assert result[-1] == msgs[-1]
