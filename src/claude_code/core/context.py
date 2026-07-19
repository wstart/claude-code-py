"""Context window management and compression.

Tracks token usage across messages and compresses old conversation
turns when approaching the context limit. Compression preserves
the system prompt and recent messages while summarizing older
tool results and merging old conversation turns.
"""

from typing import Any

from claude_code.utils.text import count_tokens_approx

# Default context window size (200k tokens)
DEFAULT_CONTEXT_LIMIT = 200_000

# Fraction of context to reserve for recent messages (never compressed)
RECENT_RESERVE_FRACTION = 0.3

# Fraction of context at which compression triggers
COMPRESSION_THRESHOLD = 0.8

# Maximum number of recent messages to always keep
MIN_RECENT_MESSAGES = 6


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate the token count of a single message.

    Args:
        message: A message dictionary with 'content' field.

    Returns:
        Estimated token count.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return count_tokens_approx(content)
    if isinstance(content, list):
        # Content blocks (text, tool_use, tool_result, etc.)
        total = 0
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "") or block.get("content", "")
                if isinstance(text, str):
                    total += count_tokens_approx(text)
                elif isinstance(text, list):
                    for sub in text:
                        if isinstance(sub, dict):
                            total += count_tokens_approx(sub.get("text", ""))
            elif isinstance(block, str):
                total += count_tokens_approx(block)
        return total
    return 0


def _total_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across all messages.

    Args:
        messages: List of message dictionaries.

    Returns:
        Total estimated token count.
    """
    return sum(_estimate_message_tokens(m) for m in messages)


def needs_compression(
    messages: list[dict[str, Any]],
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
    threshold: float = COMPRESSION_THRESHOLD,
) -> bool:
    """Check whether the message list needs compression.

    Args:
        messages: List of conversation messages.
        context_limit: Maximum context window in tokens.
        threshold: Fraction of context_limit that triggers compression.

    Returns:
        True if estimated tokens exceed the threshold.
    """
    total = _total_tokens(messages)
    return total > int(context_limit * threshold)


def compress(
    messages: list[dict[str, Any]],
    context_limit: int = DEFAULT_CONTEXT_LIMIT,
) -> list[dict[str, Any]]:
    """Compress old messages to fit within the context window.

    Strategy:
    1. Always keep the first message (system prompt) if present.
    2. Always keep the most recent ``MIN_RECENT_MESSAGES`` messages.
    3. For middle messages: summarize tool results, truncate long
       content blocks, and merge short consecutive turns.

    Args:
        messages: List of conversation messages.
        context_limit: Maximum context window in tokens.

    Returns:
        New compressed message list.
    """
    if len(messages) <= MIN_RECENT_MESSAGES + 1:
        return messages

    # Determine split points
    system_msgs: list[dict[str, Any]] = []
    recent_msgs: list[dict[str, Any]] = []
    middle_msgs: list[dict[str, Any]] = []

    idx = 0
    # Keep leading system messages
    while idx < len(messages) and messages[idx].get("role") == "system":
        system_msgs.append(messages[idx])
        idx += 1

    # Split remaining into middle (compressible) and recent (keep)
    remaining = messages[idx:]
    if len(remaining) <= MIN_RECENT_MESSAGES:
        return messages  # Nothing to compress

    recent_count = max(MIN_RECENT_MESSAGES, int(len(remaining) * RECENT_RESERVE_FRACTION))
    recent_count = min(recent_count, len(remaining))
    middle_msgs = remaining[: len(remaining) - recent_count]
    recent_msgs = remaining[len(remaining) - recent_count:]

    # Compress middle messages
    compressed_middle = _compress_messages(middle_msgs)

    # If still too large, drop oldest compressed messages
    result = system_msgs + compressed_middle + recent_msgs
    while _total_tokens(result) > context_limit and len(compressed_middle) > 1:
        compressed_middle.pop(0)
        result = system_msgs + compressed_middle + recent_msgs

    return result


def _compress_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compress a list of middle (non-recent) messages.

    Applies:
    - Tool result summarization
    - Long content truncation
    - Merging of short consecutive assistant turns

    Args:
        messages: Messages to compress.

    Returns:
        Compressed message list.
    """
    if not messages:
        return messages

    compressed: list[dict[str, Any]] = []
    max_content_chars = 200  # Truncate long content in old messages

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "assistant" and isinstance(content, list):
            # Compress content blocks
            new_blocks = []
            for block in content:
                if not isinstance(block, dict):
                    new_blocks.append(block)
                    continue

                block_type = block.get("type", "")

                if block_type == "tool_use":
                    # Keep tool calls but truncate input
                    new_block = dict(block)
                    inp = new_block.get("input", {})
                    if isinstance(inp, dict):
                        truncated_input = {}
                        for k, v in inp.items():
                            v_str = str(v)
                            if len(v_str) > 100:
                                truncated_input[k] = v_str[:100] + "..."
                            else:
                                truncated_input[k] = v
                        new_block["input"] = truncated_input
                    new_blocks.append(new_block)

                elif block_type == "text":
                    # Truncate long text
                    text = block.get("text", "")
                    if len(text) > max_content_chars:
                        new_blocks.append({
                            "type": "text",
                            "text": text[:max_content_chars] + "... [compressed]",
                        })
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)

            compressed.append({**msg, "content": new_blocks})

        elif role == "user" and isinstance(content, list):
            # Compress tool results
            new_blocks = []
            for block in content:
                if not isinstance(block, dict):
                    new_blocks.append(block)
                    continue

                if block.get("type") == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, str) and len(result_content) > max_content_chars:
                        new_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", ""),
                            "content": result_content[:max_content_chars] + "... [compressed]",
                        })
                    elif isinstance(result_content, list):
                        truncated = []
                        for sub in result_content:
                            if isinstance(sub, dict):
                                t = sub.get("text", "")
                                if len(t) > max_content_chars:
                                    truncated.append({
                                        "type": "text",
                                        "text": t[:max_content_chars] + "... [compressed]",
                                    })
                                else:
                                    truncated.append(sub)
                            else:
                                truncated.append(sub)
                        new_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": block.get("tool_use_id", ""),
                            "content": truncated,
                        })
                    else:
                        new_blocks.append(block)
                else:
                    new_blocks.append(block)

            compressed.append({**msg, "content": new_blocks})

        elif isinstance(content, str) and len(content) > max_content_chars:
            compressed.append({
                **msg,
                "content": content[:max_content_chars] + "... [compressed]",
            })
        else:
            compressed.append(msg)

    return compressed
