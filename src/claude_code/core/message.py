"""Message and conversation models using Pydantic v2."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from claude_code.utils.text import count_tokens_approx

# --- Content block types ---


class TextContent(BaseModel):
    """A plain text content block."""

    type: Literal["text"] = "text"
    text: str

    def __str__(self) -> str:
        return self.text


class ImageContent(BaseModel):
    """An image content block (base64-encoded or URL)."""

    type: Literal["image"] = "image"
    source: dict[str, Any]
    """Either {"type": "base64", "media_type": "...", "data": "..."}
    or {"type": "url", "url": "..."}."""


class ToolUseContent(BaseModel):
    """A tool_use content block requesting tool invocation."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResultContent(BaseModel):
    """A tool_result content block containing a tool's output."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str | list[ContentBlock] = ""
    is_error: bool = False


# Union of all content block types
ContentBlock = Annotated[
    TextContent | ImageContent | ToolUseContent | ToolResultContent,
    Field(discriminator="type"),
]


# --- Message and Conversation ---


class Message(BaseModel):
    """A single message in a conversation."""

    role: Literal["user", "assistant", "system"]
    content: list[ContentBlock] = Field(default_factory=list)

    @classmethod
    def user(cls, text: str) -> Message:
        """Create a user message with plain text content."""
        return cls(role="user", content=[TextContent(text=text)])

    @classmethod
    def assistant(cls, text: str) -> Message:
        """Create an assistant message with plain text content."""
        return cls(role="assistant", content=[TextContent(text=text)])

    @classmethod
    def system(cls, text: str) -> Message:
        """Create a system message."""
        return cls(role="system", content=[TextContent(text=text)])

    @classmethod
    def with_tool_use(
        cls,
        text: str | None = None,
        tool_uses: list[ToolUseContent] | None = None,
    ) -> Message:
        """Create an assistant message with optional text and tool calls.

        Args:
            text: Optional text content before tool calls.
            tool_uses: List of tool_use blocks.

        Returns:
            An assistant Message.
        """
        blocks: list[ContentBlock] = []
        if text:
            blocks.append(TextContent(text=text))
        if tool_uses:
            blocks.extend(tool_uses)
        return cls(role="assistant", content=blocks)

    @classmethod
    def with_tool_result(
        cls,
        tool_use_id: str,
        content: str,
        is_error: bool = False,
    ) -> Message:
        """Create a user message containing a tool result.

        Args:
            tool_use_id: ID of the tool_use this result corresponds to.
            content: The tool output text.
            is_error: Whether the tool returned an error.

        Returns:
            A user Message with a ToolResultContent block.
        """
        return cls(
            role="user",
            content=[
                ToolResultContent(tool_use_id=tool_use_id, content=content, is_error=is_error)
            ],
        )

    def text(self) -> str:
        """Extract all text content from this message.

        Returns:
            Concatenated text from all TextContent blocks.
        """
        parts: list[str] = []
        for block in self.content:
            if isinstance(block, TextContent):
                parts.append(block.text)
        return "\n".join(parts)

    def tool_uses(self) -> list[ToolUseContent]:
        """Extract all tool_use blocks from this message."""
        return [b for b in self.content if isinstance(b, ToolUseContent)]

    def tool_results(self) -> list[ToolResultContent]:
        """Extract all tool_result blocks from this message."""
        return [b for b in self.content if isinstance(b, ToolResultContent)]

    def has_tool_use(self) -> bool:
        """Check if this message contains any tool_use blocks."""
        return any(isinstance(b, ToolUseContent) for b in self.content)

    def approximate_tokens(self) -> int:
        """Estimate the token count of this message's text content."""
        return count_tokens_approx(self.text())


class CostInfo(BaseModel):
    """Tracks token usage and estimated cost."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class Conversation(BaseModel):
    """A full conversation including messages and metadata."""

    messages: list[Message] = Field(default_factory=list)
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    model: str = ""
    cost: CostInfo = Field(default_factory=CostInfo)

    def add_message(self, message: Message) -> None:
        """Append a message to the conversation."""
        self.messages.append(message)

    def last_message(self) -> Message | None:
        """Get the most recent message, or None if empty."""
        return self.messages[-1] if self.messages else None

    def last_assistant_message(self) -> Message | None:
        """Get the most recent assistant message."""
        for msg in reversed(self.messages):
            if msg.role == "assistant":
                return msg
        return None

    def total_tokens(self) -> int:
        """Approximate total token count across all messages."""
        return sum(m.approximate_tokens() for m in self.messages)

    def message_count(self) -> int:
        """Number of messages in the conversation."""
        return len(self.messages)

    def to_api_messages(self) -> list[dict[str, Any]]:
        """Convert to the format expected by the Anthropic Messages API.

        System messages are excluded (they go in the `system` parameter).
        Tool result blocks are formatted for the API.

        Returns:
            List of message dicts suitable for the API request.
        """
        result: list[dict[str, Any]] = []
        for msg in self.messages:
            if msg.role == "system":
                continue

            api_blocks: list[dict[str, Any]] = []
            for block in msg.content:
                if isinstance(block, TextContent):
                    # Drop empty text blocks — the API rejects
                    # {"type": "text", "text": ""} with a 400.
                    if not block.text:
                        continue
                    api_blocks.append({"type": "text", "text": block.text})
                elif isinstance(block, ImageContent):
                    api_blocks.append({"type": "image", "source": block.source})
                elif isinstance(block, ToolUseContent):
                    api_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                elif isinstance(block, ToolResultContent):
                    content_val = block.content
                    if isinstance(content_val, list):
                        # Flatten nested content blocks
                        content_val = [
                            {"type": "text", "text": b.text}
                            if isinstance(b, TextContent)
                            else b.model_dump()
                            for b in content_val
                        ]
                    api_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.tool_use_id,
                        "content": content_val,
                        "is_error": block.is_error,
                    })

            # A message with no content blocks is also rejected by the API;
            # skip it entirely rather than send an empty content array.
            if api_blocks:
                result.append({"role": msg.role, "content": api_blocks})

        return result

    def system_prompt(self) -> str:
        """Extract concatenated system prompt from system messages."""
        parts: list[str] = []
        for msg in self.messages:
            if msg.role == "system":
                parts.append(msg.text())
        return "\n\n".join(parts)


def api_message_text(content: Any) -> str:
    """Extract displayable text from an API-format message ``content`` value.

    Accepts a plain string or a list of content-block dicts; returns the
    concatenated text of any ``text`` blocks (used to replay a resumed
    session's messages in the UI).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""
