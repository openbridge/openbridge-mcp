from types import SimpleNamespace

import pytest

from src.auth import simple
from src.server.tools import base, remote_identity
from src.server.tools.service import validate_query


def test_openbridge_auth_caches_jwt(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "cached-token"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda *_args, **_kwargs: {"expires_at": 3600})
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 0)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = simple.get_auth()
    assert auth.get_jwt() == "cached-token"
    assert auth.get_jwt() == "cached-token"
    assert len(calls) == 1


def test_openbridge_auth_cache_refreshes_after_expiry(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")
    calls = []

    def fake_post(*args, **kwargs):
        calls.append(1)
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": f"token-{len(calls)}"}}},
        )

    times = iter([4000, 4000, 4000])

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda *_args, **_kwargs: {"expires_at": 3600})
    monkeypatch.setattr("src.auth.simple.time.time", lambda: next(times))
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = simple.get_auth()
    assert auth.get_jwt() == "token-1"
    assert auth.get_jwt() == "token-2"
    assert len(calls) == 2


def test_safe_pagination_url_blocks_unexpected_host(monkeypatch):
    # Use helper directly to ensure SSRF protection
    malicious = base.safe_pagination_url(
        "https://evil.example.com/page/2",
        "https://remote-identity.api.openbridge.io/ri?page=1",
    )
    assert malicious is None


@pytest.mark.asyncio
async def test_validate_query_heuristics_without_llm(monkeypatch):
    class DummyContext:
        async def sample(self, *args, **kwargs):
            raise AssertionError("LLM sampling should be disabled")

    monkeypatch.delenv("OPENBRIDGE_ENABLE_LLM_VALIDATION", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    ctx = DummyContext()

    result = await validate_query(
        query="SELECT * FROM test_table",
        key_name="abc",
        allow_unbounded=False,
        ctx=ctx,
    )

    assert result["sampling"]["supported"] is False
    assert result["decision"]["allowed"] is False  # Missing LIMIT


def test_remote_identity_uses_timeout(monkeypatch):
    headers = {"Authorization": "Bearer token"}
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(timeout)
        return SimpleNamespace(
            status_code=200,
            json=lambda: {"data": [], "links": {"next": None}},
        )

    monkeypatch.setattr("src.server.tools.base.get_auth_headers", lambda ctx=None: headers)
    monkeypatch.setattr("src.server.tools.remote_identity.requests.get", fake_get)

    remote_identity.get_remote_identities()

    assert calls and isinstance(calls[0], tuple)
    assert calls[0][0] == simple.DEFAULT_CONNECT_TIMEOUT
