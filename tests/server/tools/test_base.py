from types import SimpleNamespace

import pytest
from fastmcp.server.auth import AccessToken

from src.auth.simple import AuthenticationError
from src.server.tools import base


def test_get_auth_headers_uses_native_access_token(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(
        base,
        "get_access_token",
        lambda: AccessToken(
            token="verified.jwt.token",
            client_id="openbridge",
            scopes=[],
        ),
    )

    assert base.get_auth_headers() == {
        "Authorization": "Bearer verified.jwt.token"
    }


def test_get_auth_headers_ignores_legacy_context_attributes(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setattr(base, "get_access_token", lambda: None)
    ctx = SimpleNamespace(
        _openbridge_jwt="legacy-private-token",
        jwt_token="legacy-public-token",
        get_state=lambda _key: "legacy-state-token",
    )

    with pytest.raises(AuthenticationError, match="authenticated access token"):
        base.get_auth_headers(ctx)


def test_auth_enabled_missing_native_context_fails_closed(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "false")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:credential")
    monkeypatch.setattr(base, "get_access_token", lambda: None)
    monkeypatch.setattr(
        base,
        "get_auth",
        lambda: pytest.fail("auth-enabled calls must not use server fallback"),
    )

    with pytest.raises(AuthenticationError, match="authenticated access token"):
        base.get_auth_headers()


def test_auth_disabled_without_server_token_returns_empty_headers(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr(base, "get_access_token", lambda: None)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    assert base.get_auth_headers() == {}


def test_auth_disabled_can_use_server_token_fallback(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")
    monkeypatch.setattr(base, "get_access_token", lambda: None)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr(
        "src.auth.simple.jwt.decode",
        lambda token, options: {"expires_at": 2000},
    )

    def fake_post(url, json, headers, timeout):
        assert url.endswith("/auth/api/ref")
        assert json["data"]["attributes"]["refresh_token"] == "abc:def"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "jwt-token"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)

    assert base.get_auth_headers() == {"Authorization": "Bearer jwt-token"}


def test_auth_disabled_server_exchange_failure_is_actionable(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")
    monkeypatch.setattr(base, "get_access_token", lambda: None)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)
    monkeypatch.setattr(
        "src.auth.simple.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")),
    )

    with pytest.raises(AuthenticationError, match="Failed to convert"):
        base.get_auth_headers()


BASE = "https://remote-identity.api.openbridge.io"


def test_safe_pagination_url_null_input_returns_none():
    assert base.safe_pagination_url(None, BASE) is None
    assert base.safe_pagination_url("", BASE) is None


def test_safe_pagination_url_same_host_absolute_allowed():
    candidate = f"{BASE}/ri?page=2"
    assert base.safe_pagination_url(candidate, BASE) == candidate


def test_safe_pagination_url_same_host_relative_allowed():
    assert base.safe_pagination_url("/ri?page=3", BASE) == f"{BASE}/ri?page=3"


def test_safe_pagination_url_blocks_http_scheme():
    assert (
        base.safe_pagination_url(
            "http://remote-identity.api.openbridge.io/ri?page=2",
            BASE,
        )
        is None
    )


def test_safe_pagination_url_blocks_cross_host():
    assert (
        base.safe_pagination_url("https://evil.example.com/ri?page=2", BASE)
        is None
    )


def test_safe_pagination_url_blocks_subdomain_spoofing():
    assert (
        base.safe_pagination_url(
            "https://evil.openbridge.io.attacker.com/ri?page=2",
            BASE,
        )
        is None
    )


def test_safe_pagination_url_blocks_ftp_scheme():
    assert (
        base.safe_pagination_url(
            "ftp://remote-identity.api.openbridge.io/file",
            BASE,
        )
        is None
    )
