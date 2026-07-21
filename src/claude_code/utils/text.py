"""Text utility functions."""

import re

# Approximate ratio: 1 token ≈ 4 characters for English text
_CHARS_PER_TOKEN = 4

# Precompiled ANSI escape pattern
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]")


def truncate_text(text: str, max_length: int, suffix: str = "…") -> str:
    """Truncate text to a maximum length, appending a suffix if truncated.

    Args:
        text: The text to truncate.
        max_length: Maximum length of the returned string (including suffix).
        suffix: String to append when text is truncated.

    Returns:
        The original text if within bounds, or a truncated version with suffix.
    """
    if len(text) <= max_length:
        return text
    if max_length <= len(suffix):
        return suffix[:max_length]
    return text[: max_length - len(suffix)] + suffix


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from a string.

    Args:
        text: Text potentially containing ANSI codes.

    Returns:
        Clean text with all ANSI escape sequences removed.
    """
    return _ANSI_RE.sub("", text)


def count_tokens_approx(text: str, chars_per_token: int = _CHARS_PER_TOKEN) -> int:
    """Estimate token count based on character count.

    This is a rough approximation. Actual token counts depend on the
    tokenizer used by the model. English text averages ~4 chars/token.

    Args:
        text: The text to estimate.
        chars_per_token: Average characters per token (default 4).

    Returns:
        Estimated token count (always >= 1 for non-empty text).
    """
    if not text:
        return 0
    return max(1, len(text) // chars_per_token)


def wrap_text(text: str, width: int, indent: int = 0) -> str:
    """Simple word-wrap for plain text.

    Args:
        text: Text to wrap.
        width: Maximum line width.
        indent: Number of spaces to indent continuation lines.

    Returns:
        Wrapped text.
    """
    if width <= 0:
        return text

    prefix = " " * indent
    lines: list[str] = []

    for paragraph in text.split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue

        words = paragraph.split()
        current_line = ""
        for word in words:
            candidate = f"{current_line} {word}".strip() if current_line else word
            effective_width = width if not current_line else width - indent
            if len(candidate) <= effective_width:
                current_line = candidate
            else:
                if current_line:
                    lines.append(current_line)
                current_line = prefix + word if indent else word
        if current_line:
            lines.append(current_line)

    return "\n".join(lines)


def pluralize(count: int, singular: str, plural: str | None = None) -> str:
    """Return singular or plural form based on count.

    Args:
        count: The count to check.
        singular: Singular form of the word.
        plural: Plural form (defaults to singular + 's').

    Returns:
        Formatted string like "3 items" or "1 item".
    """
    if plural is None:
        plural = singular + "s"
    word = singular if count == 1 else plural
    return f"{count} {word}"
