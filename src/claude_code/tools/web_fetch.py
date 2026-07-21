"""WebFetch tool — fetch a URL and extract text content.

Fetches a web page, converts HTML to readable text using stdlib parsers,
and extracts relevant content based on a prompt.  Results are cached
in-memory for 15 minutes to avoid redundant fetches.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

from claude_code.tools.base import Tool, ToolResult

# Cache TTL in seconds (15 minutes)
_CACHE_TTL_SECONDS = 15 * 60

# HTTP timeout
_TIMEOUT_SECONDS = 30

# Maximum response size to process (5 MB)
_MAX_RESPONSE_SIZE = 5 * 1024 * 1024

# Maximum redirects to follow (each is re-validated against the SSRF policy).
_MAX_REDIRECTS = 5

# In-memory cache: url -> (timestamp, markdown_text)
_fetch_cache: dict[str, tuple[float, str]] = {}


def _host_is_private(host: str) -> bool:
    """Return True if *host* resolves to a non-public address.

    Blocks loopback, private, link-local, reserved, multicast, and
    unspecified ranges. If the host cannot be resolved, it is blocked.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return True
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return True
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


async def _validate_public_url(url: str) -> str | None:
    """Return an error message if *url* is not a fetchable public URL."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Unsupported URL scheme: {parsed.scheme or '(none)'}"
    host = parsed.hostname
    if not host:
        return f"URL has no host: {url}"
    # DNS resolution can block; run it off the event loop.
    if await asyncio.to_thread(_host_is_private, host):
        return f"Refusing to fetch private or non-public address: {host}"
    return None


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
    read_only = True
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

        # Fetch, following redirects manually so every hop is re-validated
        # against the SSRF policy (a public URL can 302 to an internal one).
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                timeout=_TIMEOUT_SECONDS,
                headers={
                    "User-Agent": "ClaudeCode/1.0 (compatible; bot)",
                },
            ) as client:
                current_url = url
                status_code = 0
                encoding = "utf-8"
                raw = b""
                got_final = False
                for _ in range(_MAX_REDIRECTS + 1):
                    err = await _validate_public_url(current_url)
                    if err:
                        return ToolResult.error(err)
                    # Stream so a huge body is bounded, not fully buffered
                    # into memory (the previous response.text read it all).
                    async with client.stream("GET", current_url) as response:
                        status_code = response.status_code
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                got_final = True
                                break
                            current_url = str(response.url.join(location))
                            continue
                        encoding = response.encoding or "utf-8"
                        chunks: list[bytes] = []
                        got = 0
                        async for chunk in response.aiter_bytes():
                            chunks.append(chunk)
                            got += len(chunk)
                            if got >= _MAX_RESPONSE_SIZE:
                                break
                        raw = b"".join(chunks)[:_MAX_RESPONSE_SIZE]
                        got_final = True
                        break
                if not got_final:
                    return ToolResult.error(f"Too many redirects fetching {url}")
        except httpx.TimeoutException:
            return ToolResult.error(f"Timeout fetching {url}")
        except httpx.ConnectError:
            return ToolResult.error(f"Connection refused: {url}")
        except httpx.HTTPError as exc:
            return ToolResult.error(f"HTTP error fetching {url}: {exc}")

        if status_code == 404:
            return ToolResult.error(f"Page not found (404): {url}")
        if status_code >= 400:
            return ToolResult.error(f"HTTP {status_code} fetching {url}")

        content = raw.decode(encoding, errors="replace")

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
