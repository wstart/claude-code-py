"""Tests for the skip-SSL-verify switch on custom base_url endpoints."""

from __future__ import annotations

import httpx

from claude_code.providers.anthropic_provider import AnthropicProvider
from claude_code.providers.openai_compat import OpenAICompatProvider


def test_anthropic_skip_ssl_injects_insecure_client() -> None:
    p = AnthropicProvider(
        api_key="x", base_url="https://self-signed.internal", verify_ssl=False
    )
    # The Anthropic SDK holds the injected httpx client on ._client._client.
    assert isinstance(getattr(p._client, "_client", None), httpx.AsyncClient)


def test_openai_skip_ssl_injects_insecure_client() -> None:
    o = OpenAICompatProvider(
        api_key="x", base_url="http://localhost:1234/v1", verify_ssl=False
    )
    assert isinstance(getattr(o._client, "_client", None), httpx.AsyncClient)


def test_verify_ssl_default_constructs() -> None:
    # Default (verify on) constructs without injecting a custom client.
    AnthropicProvider(api_key="x", base_url="https://api.anthropic.com")
    OpenAICompatProvider(api_key="x", base_url="https://api.openai.com/v1")


def test_explicit_http_client_not_overridden() -> None:
    # If the caller passes their own http_client, skip_ssl must not clobber it.
    custom = httpx.AsyncClient()
    p = AnthropicProvider(
        api_key="x", base_url="https://x", verify_ssl=False, http_client=custom
    )
    assert p._client._client is custom


def test_config_reads_skip_ssl_verify(monkeypatch) -> None:
    from claude_code.core.config import load_config

    monkeypatch.delenv("CLAUDE_SKIP_SSL_VERIFY", raising=False)
    assert load_config().skip_ssl_verify is False
    monkeypatch.setenv("CLAUDE_SKIP_SSL_VERIFY", "1")
    assert load_config().skip_ssl_verify is True
