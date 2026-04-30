"""Tests for OAuthProxy authentication mode.

Covers:
- OAuthBridgeMiddleware: primes JWT from verified OAuth access token
- OAuthBridgeMiddleware: sets None when no access token present
- OAuthBridgeMiddleware: skips non-tool requests (no fastmcp_context)
- OAuthBridgeMiddleware: always calls through to call_next
- create_oauth_proxy: returns an OAuthProxy instance using env vars
- create_oauth_proxy: warns when MCP_JWT_SIGNING_KEY is unset
- AuthConfig.auth_mode: defaults to refresh_token
- AuthConfig.auth_mode: reads oauth_proxy from OPENBRIDGE_AUTH_MODE
- AuthConfig.auth_mode: falls back on unrecognized values
"""

import logging
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.auth.authentication import AuthConfig, create_openbridge_config
from src.auth.oauth_proxy import OAuthBridgeMiddleware, create_oauth_proxy
from src.auth.session_state import get_request_jwt, set_request_jwt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_access_token(token: str, scopes: list[str] | None = None) -> MagicMock:
    """Return a mock AccessToken with .token and .scopes attributes."""
    at = MagicMock()
    at.token = token
    at.scopes = scopes or ["openid", "profile"]
    return at


def _make_context(*, has_fastmcp_context: bool = True) -> SimpleNamespace:
    ctx = SimpleNamespace()
    ctx.fastmcp_context = MagicMock() if has_fastmcp_context else None
    return ctx


# ---------------------------------------------------------------------------
# OAuthBridgeMiddleware
# ---------------------------------------------------------------------------


class TestOAuthBridgeMiddleware:
    """Tests for OAuthBridgeMiddleware."""

    @pytest.mark.asyncio
    async def test_primes_jwt_from_access_token(self):
        """Middleware stores access_token.token in the request ContextVar."""
        fake_token = _make_access_token("eyJ.test.jwt")
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        with patch("src.auth.oauth_proxy.get_access_token", return_value=fake_token):
            mw = OAuthBridgeMiddleware()
            result = await mw.on_request(context, call_next)

        assert result == "ok"
        assert get_request_jwt() == "eyJ.test.jwt"

    @pytest.mark.asyncio
    async def test_sets_none_when_no_access_token(self):
        """Middleware sets ContextVar to None when no access token is present."""
        set_request_jwt("leftover-from-previous-request")
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        with patch("src.auth.oauth_proxy.get_access_token", return_value=None):
            mw = OAuthBridgeMiddleware()
            await mw.on_request(context, call_next)

        assert get_request_jwt() is None

    @pytest.mark.asyncio
    async def test_always_calls_call_next(self):
        """Middleware always passes the request on regardless of token presence."""
        context = _make_context()
        call_next = AsyncMock(return_value="response")

        with patch("src.auth.oauth_proxy.get_access_token", return_value=None):
            mw = OAuthBridgeMiddleware()
            result = await mw.on_request(context, call_next)

        call_next.assert_awaited_once_with(context)
        assert result == "response"

    @pytest.mark.asyncio
    async def test_skips_when_no_fastmcp_context(self):
        """Middleware passes through immediately for non-tool requests."""
        context = _make_context(has_fastmcp_context=False)
        call_next = AsyncMock(return_value="passthrough")

        with patch("src.auth.oauth_proxy.get_access_token") as mock_get:
            mw = OAuthBridgeMiddleware()
            result = await mw.on_request(context, call_next)

        mock_get.assert_not_called()
        call_next.assert_awaited_once_with(context)
        assert result == "passthrough"

    @pytest.mark.asyncio
    async def test_sets_context_state_when_token_present(self):
        """JWT is written to both ContextVar and FastMCP context state."""
        fake_token = _make_access_token("eyJ.ctx.jwt")
        context = _make_context()
        call_next = AsyncMock(return_value="ok")

        with patch("src.auth.oauth_proxy.get_access_token", return_value=fake_token):
            mw = OAuthBridgeMiddleware()
            await mw.on_request(context, call_next)

        assert get_request_jwt() == "eyJ.ctx.jwt"
        # FastMCP context set_state should have been called (via _set_context_state)
        ctx = context.fastmcp_context
        ctx.set_state.assert_called()


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
