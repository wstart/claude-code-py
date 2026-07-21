"""Intelligent conversation context pruning to fit within token limits."""

from __future__ import annotations

from typing import Any

from claude_code.core.context import (
    MIN_RECENT_MESSAGES,
    _total_tokens,
)


class ContextPruner:
    """Prune conversation messages to fit within a token budget.

    Strategy (applied in order):
    1. Always keep system messages (role=``system``).
    2. Always keep the last *min_recent* messages unchanged.
    3. Summarise old tool results in middle messages.
    4. Truncate long text blocks in middle messages.
    5. If still over budget, drop oldest middle messages.

    This complements :mod:`claude_code.core.context` (which triggers
    automatically at a threshold). ``ContextPruner`` gives callers
    explicit control over the process.
    """

    def __init__(
        self,
        max_tokens: int = 200_000,
        min_recent: int = MIN_RECENT_MESSAGES,
        max_content_chars: int = 500,
    ) -> None:
        """Initialise the pruner.

        Args:
            max_tokens: Target token budget for the full message list.
            min_recent: Minimum number of recent messages to always keep.
            max_content_chars: Truncation limit for text in old messages.
        """
        self.max_tokens = max_tokens
        self.min_recent = min_recent
        self.max_content_chars = max_content_chars

    def prune(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prune messages to fit within ``max_tokens``.

        Args:
            messages: The conversation message list (dict format with
                ``role`` and ``content`` keys).

        Returns:
            A new list of messages that fits within the token budget.
        """
        if not messages:
            return messages

        total = self.estimate_tokens(messages)
        if total <= self.max_tokens:
            return messages

        # 1. Separate system / middle / recent
        system_msgs: list[dict[str, Any]] = []
        idx = 0
        while idx < len(messages) and messages[idx].get("role") == "system":
            system_msgs.append(messages[idx])
            idx += 1

        remaining = messages[idx:]
        if len(remaining) <= self.min_recent:
            # Can't prune further — return as-is
            return messages

        recent_count = max(self.min_recent, len(remaining) // 3)
        recent_count = min(recent_count, len(remaining))
        middle = remaining[: len(remaining) - recent_count]
        recent = remaining[len(remaining) - recent_count:]

        # 2. Compress middle messages
        compressed_middle = self._compress_middle(middle)

        # 3. Check budget
        result = system_msgs + compressed_middle + recent
        if self.estimate_tokens(result) <= self.max_tokens:
            return result

        # 4. Drop oldest middle messages until we fit
        while compressed_middle and self.estimate_tokens(
            system_msgs + compressed_middle + recent
        ) > self.max_tokens:
            compressed_middle.pop(0)

        return system_msgs + compressed_middle + recent

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Approximate token count across all messages.

        Uses the same heuristic as :mod:`claude_code.core.context`
        (~4 chars per token).

        Args:
            messages: Message list to estimate.

        Returns:
            Estimated total token count.
        """
        return _total_tokens(messages)

    # -- internals ---------------------------------------------------------

    def _compress_middle(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply summarisation and truncation to compressible messages."""
        compressed: list[dict[str, Any]] = []
        max_chars = self.max_content_chars

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "assistant" and isinstance(content, list):
                compressed.append({**msg, "content": self._compress_blocks(content)})
            elif role == "user" and isinstance(content, list):
                compressed.append({**msg, "content": self._compress_tool_results(content)})
            elif isinstance(content, str) and len(content) > max_chars:
                compressed.append({
                    **msg,
                    "content": content[:max_chars] + "... [pruned]",
                })
            else:
                compressed.append(msg)

        return compressed

    def _compress_blocks(self, blocks: list[Any]) -> list[Any]:
        """Compress assistant content blocks (tool_use inputs, long text)."""
        max_chars = self.max_content_chars
        result: list[Any] = []

        for block in blocks:
            if not isinstance(block, dict):
                result.append(block)
                continue

            btype = block.get("type", "")

            if btype == "tool_use":
                new_block = dict(block)
                inp = new_block.get("input", {})
                if isinstance(inp, dict):
                    truncated = {}
                    for k, v in inp.items():
                        v_str = str(v)
                        truncated[k] = v_str[:100] + "..." if len(v_str) > 100 else v
                    new_block["input"] = truncated
                result.append(new_block)

            elif btype == "text":
                text = block.get("text", "")
                if len(text) > max_chars:
                    result.append({
                        "type": "text",
                        "text": text[:max_chars] + "... [pruned]",
                    })
                else:
                    result.append(block)
            else:
                result.append(block)

        return result

    def _compress_tool_results(self, blocks: list[Any]) -> list[Any]:
        """Compress user-side tool_result blocks."""
        max_chars = self.max_content_chars
        result: list[Any] = []

        for block in blocks:
            if not isinstance(block, dict):
                result.append(block)
                continue

            if block.get("type") != "tool_result":
                result.append(block)
                continue

            rc = block.get("content", "")
            if isinstance(rc, str) and len(rc) > max_chars:
                result.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": rc[:max_chars] + "... [pruned]",
                })
            elif isinstance(rc, list):
                truncated = []
                for sub in rc:
                    if isinstance(sub, dict):
                        t = sub.get("text", "")
                        if len(t) > max_chars:
                            truncated.append({
                                "type": "text",
                                "text": t[:max_chars] + "... [pruned]",
                            })
                        else:
                            truncated.append(sub)
                    else:
                        truncated.append(sub)
                result.append({
                    "type": "tool_result",
                    "tool_use_id": block.get("tool_use_id", ""),
                    "content": truncated,
                })
            else:
                result.append(block)

        return result
