"""Tests for dual-mode authentication (client Bearer token vs server refresh token)."""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.auth.authentication import OpenbridgeAuthMiddleware, JWT_PUBLIC_ATTR
from src.auth.simple import OpenbridgeAuth


class DummyMiddlewareContext:
    """Mock middleware context for testing."""

    def __init__(self, fastmcp_context=None):
        self.fastmcp_context = fastmcp_context


class DummyFastMCPContext:
    """Mock FastMCP context for testing."""

    def __init__(self, auth_header=None):
        self._auth_header = auth_header
        self._state = {}

    def get_http_request(self):
        """Return mock HTTP request with headers."""
        if self._auth_header is None:
            return None
        return SimpleNamespace(headers={"authorization": self._auth_header})

    def set_state(self, key, value):
        """Store state."""
        self._state[key] = value
        setattr(self, key, value)


@pytest.mark.asyncio
async def test_middleware_uses_client_bearer_token(monkeypatch):
    """Test that middleware prefers client-provided Bearer token."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # Create auth without refresh token
    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Mock get_http_request to return request with Authorization header
    mock_request = SimpleNamespace(headers={"authorization": "Bearer client-token-123"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    # Mock context
    fastmcp_ctx = DummyFastMCPContext(auth_header="Bearer client-token-123")
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)

    # Mock call_next
    call_next = AsyncMock(return_value="response")

    # Execute middleware
    result = await middleware.on_request(context, call_next)

    # Verify client token was used
    assert result == "response"
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "client-token-123"
    call_next.assert_called_once()


@pytest.mark.asyncio
async def test_middleware_falls_back_to_server_token(monkeypatch):
    """Test that middleware uses server refresh token when no client token."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    def fake_post(url, json, headers, timeout):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "server-jwt-456"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    # Mock get_http_request to return None (no client token)
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: None)

    # Create auth with refresh token
    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Mock context without client token
    fastmcp_ctx = DummyFastMCPContext(auth_header=None)
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)

    call_next = AsyncMock(return_value="response")

    # Execute middleware
    result = await middleware.on_request(context, call_next)

    # Verify server token was used
    assert result == "response"
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "server-jwt-456"


@pytest.mark.asyncio
async def test_middleware_prefers_client_over_server_token(monkeypatch):
    """Test that client token is used even when server token is available."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    def fake_post(*args, **kwargs):
        raise AssertionError("Server refresh token should not be called when client provides token")

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Mock get_http_request to return request with Authorization header
    mock_request = SimpleNamespace(headers={"authorization": "Bearer client-priority-token"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    # Mock context
    fastmcp_ctx = DummyFastMCPContext(auth_header="Bearer client-priority-token")
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)

    call_next = AsyncMock(return_value="response")

    await middleware.on_request(context, call_next)

    # Client token should be used
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "client-priority-token"


@pytest.mark.asyncio
async def test_middleware_handles_no_auth_gracefully(monkeypatch):
    """Test that middleware continues when neither client nor server token available."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # Mock get_http_request to return None (no client token)
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: None)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # No client token
    fastmcp_ctx = DummyFastMCPContext(auth_header=None)
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)

    call_next = AsyncMock(return_value="response")

    # Should not raise error
    result = await middleware.on_request(context, call_next)

    assert result == "response"
    # No JWT should be set
    assert JWT_PUBLIC_ATTR not in fastmcp_ctx._state


@pytest.mark.asyncio
async def test_middleware_handles_malformed_auth_header(monkeypatch):
    """Test that middleware handles malformed Authorization headers gracefully."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    def fake_post(url, json, headers, timeout):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "fallback-jwt"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    # Mock get_http_request to return malformed auth header
    mock_request = SimpleNamespace(headers={"authorization": "InvalidFormat token123"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Malformed auth header (no "Bearer " prefix)
    fastmcp_ctx = DummyFastMCPContext(auth_header="InvalidFormat token123")
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)

    call_next = AsyncMock(return_value="response")

    await middleware.on_request(context, call_next)

    # Should fall back to server token
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "fallback-jwt"


def test_openbridge_auth_init_without_token(monkeypatch):
    """Test that OpenbridgeAuth can be instantiated without OPENBRIDGE_REFRESH_TOKEN."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # Should not raise error at initialization
    auth = OpenbridgeAuth()

    assert auth.refresh_token is None


def test_openbridge_auth_get_jwt_fails_without_token(monkeypatch):
    """Test that get_jwt() raises error when called without refresh token."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = OpenbridgeAuth()

    # Should raise error when trying to get JWT
    with pytest.raises(Exception) as exc_info:
        auth.get_jwt()

    assert "OPENBRIDGE_REFRESH_TOKEN not available" in str(exc_info.value)
