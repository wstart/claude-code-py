"""WebSearch tool — search the web using DuckDuckGo Instant Answer API.

Returns result blocks with title, URL, and snippet.  Supports domain
allow/block filtering.  No API key required.

Note: This uses DuckDuckGo's free instant answer API which has limited
results compared to commercial search APIs.  It is US-only in practice.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from claude_code.tools.base import Tool, ToolResult

# DuckDuckGo Instant Answer API endpoint
_DDG_API = "https://api.duckduckgo.com/"

# HTTP timeout
_TIMEOUT_SECONDS = 15


class WebSearchTool(Tool):
    """Search the web and return results with titles, URLs, and snippets."""

    name = "WebSearch"
    description = (
        "Searches the web and returns result blocks with title, URL, and "
        "snippet. Uses DuckDuckGo instant answer API (no API key needed). "
        "Supports domain allow/block filtering. US-only results."
    )
    category = "web"
    read_only = True
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The search query.",
                "minLength": 2,
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include results from these domains.",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exclude results from these domains.",
            },
        },
        "required": ["query"],
    }

    async def execute(self, **kwargs: Any) -> ToolResult:
        import httpx

        query: str = kwargs["query"]
        allowed_domains: list[str] | None = kwargs.get("allowed_domains")
        blocked_domains: list[str] | None = kwargs.get("blocked_domains")

        try:
            results = await _search_ddg(query)
        except httpx.TimeoutException:
            return ToolResult.error("Search request timed out")
        except httpx.ConnectError:
            return ToolResult.error("Cannot connect to search API")
        except httpx.HTTPError as exc:
            return ToolResult.error(f"Search API error: {exc}")

        if not results:
            return ToolResult.success(f"No results found for: {query}")

        # Apply domain filters
        filtered = _filter_domains(results, allowed_domains, blocked_domains)

        if not filtered:
            return ToolResult.success(
                f"No results found for '{query}' after domain filtering."
            )

        # Format output
        lines: list[str] = [f"Search results for: {query}\n"]
        for i, result in enumerate(filtered, 1):
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            snippet = result.get("snippet", "No description available.")
            lines.append(f"{i}. **{title}**")
            lines.append(f"   URL: {url}")
            lines.append(f"   {snippet}")
            lines.append("")

        lines.append("Sources:")
        for result in filtered:
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            lines.append(f"- [{title}]({url})")

        return ToolResult.success("\n".join(lines))


# ---------------------------------------------------------------------------
# DuckDuckGo API integration
# ---------------------------------------------------------------------------


async def _search_ddg(query: str) -> list[dict[str, str]]:
    """Query DuckDuckGo Instant Answer API and parse results.

    Returns a list of dicts with keys: title, url, snippet.
    """
    import httpx

    params = {
        "q": query,
        "format": "json",
        "no_html": "1",
        "skip_disambig": "1",
    }

    async with httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS,
        headers={"User-Agent": "ClaudeCode/1.0 (compatible; bot)"},
    ) as client:
        response = await client.get(_DDG_API, params=params)
        response.raise_for_status()
        data = response.json()

    results: list[dict[str, str]] = []

    # Abstract (main answer)
    if data.get("Abstract"):
        results.append({
            "title": data.get("Heading", query),
            "url": data.get("AbstractURL", ""),
            "snippet": data["Abstract"],
        })

    # Related topics
    for topic in data.get("RelatedTopics", []):
        if isinstance(topic, dict) and "Text" in topic:
            results.append({
                "title": topic.get("Text", "")[:80],
                "url": topic.get("FirstURL", ""),
                "snippet": topic.get("Text", "No description available."),
            })
        elif isinstance(topic, dict) and "Topics" in topic:
            # Nested topics (grouped results)
            for sub in topic.get("Topics", []):
                if isinstance(sub, dict) and "Text" in sub:
                    results.append({
                        "title": sub.get("Text", "")[:80],
                        "url": sub.get("FirstURL", ""),
                        "snippet": sub.get("Text", "No description available."),
                    })

    # Results section
    for result in data.get("Results", []):
        if isinstance(result, dict) and "Text" in result:
            results.append({
                "title": result.get("Text", "")[:80],
                "url": result.get("FirstURL", ""),
                "snippet": result.get("Text", "No description available."),
            })

    return results


# ---------------------------------------------------------------------------
# Domain filtering
# ---------------------------------------------------------------------------


def _filter_domains(
    results: list[dict[str, str]],
    allowed: list[str] | None,
    blocked: list[str] | None,
) -> list[dict[str, str]]:
    """Filter results by allowed/blocked domain lists."""
    if not allowed and not blocked:
        return results

    filtered: list[dict[str, str]] = []

    for result in results:
        url = result.get("url", "")
        if not url:
            continue

        try:
            domain = urlparse(url).hostname or ""
        except Exception:
            continue

        domain = domain.lower()

        # Check allowed
        if allowed:
            if not any(_domain_matches(domain, a.lower()) for a in allowed):
                continue

        # Check blocked
        if blocked:
            if any(_domain_matches(domain, b.lower()) for b in blocked):
                continue

        filtered.append(result)

    return filtered


def _domain_matches(url_domain: str, filter_domain: str) -> bool:
    """Check if a URL domain matches a filter domain.

    Supports exact match and subdomain matching:
    - 'example.com' matches 'example.com' and 'www.example.com'
    """
    if url_domain == filter_domain:
        return True
    if url_domain.endswith("." + filter_domain):
        return True
    return False
