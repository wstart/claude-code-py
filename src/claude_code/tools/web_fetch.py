"""WebFetch tool — fetch a URL and extract text content.

Fetches a web page, converts HTML to readable text using stdlib parsers,
and extracts relevant content based on a prompt.  Results are cached
in-memory for 15 minutes to avoid redundant fetches.
"""

from __future__ import annotations

import time
from html.parser import HTMLParser
from typing import Any

from claude_code.tools.base import Tool, ToolResult

# Cache TTL in seconds (15 minutes)
_CACHE_TTL_SECONDS = 15 * 60

# HTTP timeout
_TIMEOUT_SECONDS = 30

# Maximum response size to process (5 MB)
_MAX_RESPONSE_SIZE = 5 * 1024 * 1024

# In-memory cache: url -> (timestamp, markdown_text)
_fetch_cache: dict[str, tuple[float, str]] = {}


class WebFetchTool(Tool):
    """Fetch a URL and extract its text content."""

    name = "WebFetch"
    description = (
        "Fetches a URL, converts the page to readable text, and extracts "
        "relevant content based on the prompt. HTTP URLs are upgraded to "
        "HTTPS. Results are cached for 15 minutes. Fails on authenticated "
        "or private URLs."
    )
    category = "web"
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "The URL to fetch content from.",
            },
            "prompt": {
                "type": "string",
                "description": "What information to extract from the page.",
            },
        },
        "required": ["url", "prompt"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        import httpx

        url: str = kwargs["url"]
        prompt: str = kwargs["prompt"]

        # Upgrade http -> https
        if url.startswith("http://"):
            url = "https://" + url[7:]

        # Check cache
        cache_key = url
        cached = _fetch_cache.get(cache_key)
        if cached is not None:
            timestamp, markdown_text = cached
            if time.time() - timestamp < _CACHE_TTL_SECONDS:
                extracted = _extract_relevant(markdown_text, prompt)
                return ToolResult.success(
                    f"[cached] {extracted}"
                )

        # Fetch
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=_TIMEOUT_SECONDS,
                headers={
                    "User-Agent": "ClaudeCode/1.0 (compatible; bot)",
                },
            ) as client:
                response = await client.get(url)
        except httpx.TimeoutException:
            return ToolResult.error(f"Timeout fetching {url}")
        except httpx.ConnectError:
            return ToolResult.error(f"Connection refused: {url}")
        except httpx.HTTPError as exc:
            return ToolResult.error(f"HTTP error fetching {url}: {exc}")

        if response.status_code == 404:
            return ToolResult.error(f"Page not found (404): {url}")
        if response.status_code >= 400:
            return ToolResult.error(
                f"HTTP {response.status_code} fetching {url}"
            )

        # Check size
        content = response.text
        if len(content) > _MAX_RESPONSE_SIZE:
            content = content[:_MAX_RESPONSE_SIZE]

        # Convert HTML to text
        markdown_text = _html_to_text(content)

        # Cache result
        _fetch_cache[cache_key] = (time.time(), markdown_text)

        # Extract relevant content
        extracted = _extract_relevant(markdown_text, prompt)

        return ToolResult.success(extracted)


# ---------------------------------------------------------------------------
# HTML-to-text conversion (stdlib only)
# ---------------------------------------------------------------------------


class _HTMLTextExtractor(HTMLParser):
    """Simple HTML parser that extracts text content."""

    def __init__(self) -> None:
        super().__init__()
        self._result: list[str] = []
        self._skip_tags = frozenset({"script", "style", "head", "noscript"})
        self._skip_depth = 0
        self._in_heading = False
        self._heading_level = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()

        if tag in self._skip_tags:
            self._skip_depth += 1

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = True
            self._heading_level = int(tag[1])
            self._result.append(f"\n{'#' * self._heading_level} ")

        if tag == "p":
            self._result.append("\n\n")
        elif tag == "br":
            self._result.append("\n")
        elif tag == "li":
            self._result.append("\n- ")
        elif tag in ("strong", "b"):
            self._result.append("**")
        elif tag in ("em", "i"):
            self._result.append("*")
        elif tag == "a":
            href = dict(attrs).get("href", "")
            if href:
                self._result.append("[")
        elif tag == "code":
            self._result.append("`")
        elif tag == "pre":
            self._result.append("\n```\n")
        elif tag == "hr":
            self._result.append("\n---\n")
        elif tag == "blockquote":
            self._result.append("\n> ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in self._skip_tags:
            self._skip_depth -= 1

        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._in_heading = False
            self._result.append("\n")

        if tag in ("strong", "b"):
            self._result.append("**")
        elif tag in ("em", "i"):
            self._result.append("*")
        elif tag == "a":
            self._result.append("]")
        elif tag == "code":
            self._result.append("`")
        elif tag == "pre":
            self._result.append("\n```\n")
        elif tag == "p":
            self._result.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        self._result.append(data)

    def get_text(self) -> str:
        """Return the extracted text, cleaned up."""
        text = "".join(self._result)
        # Collapse multiple blank lines
        lines = text.split("\n")
        cleaned: list[str] = []
        prev_blank = False
        for line in lines:
            stripped = line.strip()
            if not stripped:
                if not prev_blank:
                    cleaned.append("")
                prev_blank = True
            else:
                cleaned.append(line)
                prev_blank = False
        return "\n".join(cleaned).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to readable text using stdlib parser."""
    parser = _HTMLTextExtractor()
    try:
        parser.feed(html)
        return parser.get_text()
    except Exception:
        # Fallback: strip tags crudely
        import re
        return re.sub(r"<[^>]+>", " ", html).strip()


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def _extract_relevant(text: str, prompt: str) -> str:
    """Extract content relevant to the prompt from the text.

    Uses simple keyword-based extraction: finds paragraphs that contain
    the most prompt keywords and returns them with context.
    """
    if not text:
        return "No text content could be extracted from the page."

    # Tokenize prompt into keywords
    prompt_lower = prompt.lower()
    keywords = [
        w.strip(".,;:!?\"'()[]{}")
        for w in prompt_lower.split()
        if len(w.strip(".,;:!?\"'()[]{}")) > 2
    ]

    if not keywords:
        # No useful keywords; return the first portion of the text
        return text[:3000]

    # Split text into paragraphs and score them
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

    scored: list[tuple[int, int, str]] = []  # (score, index, paragraph)
    for idx, para in enumerate(paragraphs):
        para_lower = para.lower()
        score = sum(1 for kw in keywords if kw in para_lower)
        if score > 0:
            scored.append((score, idx, para))

    if not scored:
        # No keyword matches; return beginning of text
        return text[:3000]

    # Sort by score descending, then by position
    scored.sort(key=lambda x: (-x[0], x[1]))

    # Take top paragraphs, re-order by position
    top = sorted(scored[:10], key=lambda x: x[1])
    result_parts = [p for _, _, p in top]

    result = "\n\n".join(result_parts)

    # Limit output size
    if len(result) > 8000:
        result = result[:8000] + "\n\n[... content truncated ...]"

    return result
