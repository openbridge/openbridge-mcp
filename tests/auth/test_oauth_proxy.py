"""Tests for native OAuthProxy authentication mode."""

import hashlib
import logging
from unittest.mock import AsyncMock

import pytest

from fastmcp.server.auth import AccessToken, MultiAuth, OAuthProxy

from src.auth.authentication import AuthConfig, create_openbridge_config
from src.auth.oauth_proxy import (
    OpenbridgeOAuthProxy,
    create_oauth_auth,
    create_oauth_proxy,
)
from src.auth.openbridge_verifier import OpenbridgeCredentialVerifier


# ---------------------------------------------------------------------------
# create_oauth_proxy
# ---------------------------------------------------------------------------


class TestCreateOAuthProxy:
    """Tests for create_oauth_proxy factory."""

    def test_returns_oauth_proxy_instance(self, monkeypatch):
        """Factory returns an OAuthProxy when env vars are set."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "test-signing-key-abc123")
        monkeypatch.setenv("OPENBRIDGE_AUTH_BASE_URL", "https://authentication.api.openbridge.io")

        from fastmcp.server.auth import OAuthProxy

        result = create_oauth_proxy(base_url="http://localhost:8000")
        assert isinstance(result, OAuthProxy)

    def test_warns_when_no_signing_key(self, monkeypatch, caplog):
        """A warning is emitted when MCP_JWT_SIGNING_KEY is not set."""
        monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)

        with caplog.at_level(logging.WARNING, logger="src.auth.oauth_proxy"):
            create_oauth_proxy(base_url="http://localhost:8000")

        assert any("MCP_JWT_SIGNING_KEY" in record.message for record in caplog.records)

    def test_no_warning_when_signing_key_set(self, monkeypatch, caplog):
        """No warning when MCP_JWT_SIGNING_KEY is configured."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")

        with caplog.at_level(logging.WARNING, logger="src.auth.oauth_proxy"):
            create_oauth_proxy(base_url="http://localhost:8000")

        assert not any("MCP_JWT_SIGNING_KEY" in record.message for record in caplog.records)

    def test_uses_custom_auth_base_url(self, monkeypatch):
        """Factory uses OPENBRIDGE_AUTH_BASE_URL when set."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "key")
        monkeypatch.setenv("OPENBRIDGE_AUTH_BASE_URL", "https://auth.custom.example.com")

        from fastmcp.server.auth import OAuthProxy

        result = create_oauth_proxy(base_url="http://localhost:8000")
        assert isinstance(result, OAuthProxy)

    def test_create_oauth_auth_composes_proxy_and_openbridge_verifier(
        self, monkeypatch
    ):
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")

        auth = create_oauth_auth(base_url="https://mcp.example.test")

        assert isinstance(auth, MultiAuth)
        assert isinstance(auth.server, OpenbridgeOAuthProxy)
        assert len(auth.verifiers) == 1
        assert isinstance(auth.verifiers[0], OpenbridgeCredentialVerifier)

    def test_multi_auth_preserves_oauth_routes(self, monkeypatch):
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")
        auth = create_oauth_auth(base_url="https://mcp.example.test")

        route_paths = {
            route.path
            for route in [*auth.get_routes(), *auth.get_well_known_routes()]
        }

        assert any("authorize" in path for path in route_paths)
        assert any("oauth-authorization-server" in path for path in route_paths)


@pytest.fixture
def oauth_proxy(monkeypatch):
    monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")
    return create_oauth_proxy(
        base_url="https://mcp.example.test",
        token_verifier=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_oauth_proxy_uses_verified_account_identity(oauth_proxy, monkeypatch):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        expires_at=123456,
        claims={"account_id": 101, "user_id": 202},
    )
    load_access_token = AsyncMock(return_value=verified)
    monkeypatch.setattr(OAuthProxy, "load_access_token", load_access_token)

    result = await oauth_proxy.load_access_token("fastmcp.reference.token")

    assert result is not None
    assert result.subject == "account:101|user:202"
    assert result.token == "upstream.jwt.token"
    assert result.expires_at == 123456


@pytest.mark.asyncio
async def test_oauth_proxy_falls_back_to_isolated_session(oauth_proxy, monkeypatch):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        expires_at=123456,
        claims={"active": True},
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=verified),
    )

    first = await oauth_proxy.load_access_token("fastmcp.reference.one")
    repeated = await oauth_proxy.load_access_token("fastmcp.reference.one")
    other = await oauth_proxy.load_access_token("fastmcp.reference.two")

    assert first is not None
    assert repeated is not None
    assert other is not None
    assert first.subject == repeated.subject
    assert first.subject == (
        "oauth-session:"
        + hashlib.sha256(b"fastmcp.reference.one").hexdigest()
    )
    assert first.subject != other.subject
    assert first.claims["sub"] == first.subject
    assert first.client_id == "openbridge-oauth"
    assert first.token == "upstream.jwt.token"
    assert first.expires_at == 123456


@pytest.mark.asyncio
async def test_oauth_proxy_preserves_none(oauth_proxy, monkeypatch):
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=None),
    )

    assert await oauth_proxy.load_access_token("fastmcp.reference.token") is None


@pytest.mark.asyncio
async def test_oauth_reference_token_is_not_logged(
    oauth_proxy, monkeypatch, caplog
):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        claims={"active": True},
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=verified),
    )

    with caplog.at_level(logging.DEBUG):
        await oauth_proxy.load_access_token("fastmcp.reference.sensitive")

    assert "fastmcp.reference.sensitive" not in caplog.text


# ---------------------------------------------------------------------------
# AuthConfig auth_mode field
# ---------------------------------------------------------------------------


class TestAuthConfigMode:
    """Tests for OPENBRIDGE_AUTH_MODE env var parsing."""

    def test_auth_mode_defaults_to_refresh_token(self, monkeypatch):
        """Without OPENBRIDGE_AUTH_MODE, mode is 'refresh_token'."""
        monkeypatch.delenv("OPENBRIDGE_AUTH_MODE", raising=False)
        config = create_openbridge_config()
        assert config.auth_mode == "refresh_token"

    def test_auth_mode_oauth_proxy_from_env(self, monkeypatch):
        """OPENBRIDGE_AUTH_MODE=oauth_proxy sets auth_mode to 'oauth_proxy'."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "oauth_proxy")
        config = create_openbridge_config()
        assert config.auth_mode == "oauth_proxy"

    def test_auth_mode_refresh_token_explicit(self, monkeypatch):
        """OPENBRIDGE_AUTH_MODE=refresh_token sets auth_mode to 'refresh_token'."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "refresh_token")
        config = create_openbridge_config()
        assert config.auth_mode == "refresh_token"

    def test_auth_mode_invalid_falls_back(self, monkeypatch, caplog):
        """Unrecognized OPENBRIDGE_AUTH_MODE falls back to 'refresh_token' with a warning."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "magic_tokens")

        with caplog.at_level(logging.WARNING, logger="src.auth.authentication"):
            config = create_openbridge_config()

        assert config.auth_mode == "refresh_token"
        assert any("OPENBRIDGE_AUTH_MODE" in record.message for record in caplog.records)

    def test_auth_mode_case_insensitive(self, monkeypatch):
        """OPENBRIDGE_AUTH_MODE is normalized to lowercase before matching."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "OAuth_Proxy")
        config = create_openbridge_config()
        assert config.auth_mode == "oauth_proxy"

    def test_auth_mode_field_on_authconfig(self):
        """AuthConfig dataclass exposes auth_mode with default 'refresh_token'."""
        config = AuthConfig()
        assert config.auth_mode == "refresh_token"

    def test_auth_mode_field_settable(self):
        """AuthConfig.auth_mode can be set directly."""
        config = AuthConfig(auth_mode="oauth_proxy")
        assert config.auth_mode == "oauth_proxy"
