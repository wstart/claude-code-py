"""Tests for Vertex access-token refresh (no live GCP calls)."""

from __future__ import annotations

import claude_code.providers.vertex as vertex
from claude_code.providers.vertex import VertexProvider


class _FakeCreds:
    def __init__(self) -> None:
        self.token = "tok-1"
        self.valid = True
        self.refreshes = 0

    def expire(self) -> None:
        self.valid = False


async def test_env_token_used_directly() -> None:
    p = VertexProvider(project_id="proj", access_token="explicit-token")
    assert await p._ensure_token() == "explicit-token"


async def test_adc_loaded_once_then_refreshed_on_expiry(monkeypatch) -> None:
    creds = _FakeCreds()
    load_calls = {"n": 0}

    def fake_load() -> _FakeCreds:
        load_calls["n"] += 1
        return creds

    def fake_refresh(c: _FakeCreds) -> None:
        c.refreshes += 1
        c.token = f"tok-{c.refreshes + 1}"
        c.valid = True

    monkeypatch.setattr(vertex, "_load_credentials", fake_load)
    monkeypatch.setattr(vertex, "_refresh_credentials", fake_refresh)
    monkeypatch.delenv("GOOGLE_OAUTH_ACCESS_TOKEN", raising=False)

    p = VertexProvider(project_id="proj")

    # First call loads credentials.
    assert await p._ensure_token() == "tok-1"
    assert load_calls["n"] == 1

    # Still valid → no reload, no refresh.
    assert await p._ensure_token() == "tok-1"
    assert load_calls["n"] == 1
    assert creds.refreshes == 0

    # Expired → refresh (not reload).
    creds.expire()
    assert await p._ensure_token() == "tok-2"
    assert load_calls["n"] == 1
    assert creds.refreshes == 1
