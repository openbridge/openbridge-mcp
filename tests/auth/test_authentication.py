"""Tests for dual-mode authentication (client Bearer token vs server refresh token).

Covers:
- Client sends a refresh token (xxx:yyy) → middleware exchanges it for a JWT
- Client sends an existing JWT → middleware passes it through
- No client token → falls back to server OPENBRIDGE_REFRESH_TOKEN
- Client token takes precedence over server token
- No auth at all → continues gracefully
- Malformed headers → falls back to server token
- exchange_token caches per refresh token
- is_refresh_token heuristic
"""

import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.auth.authentication import (
    AuthConfig,
    OpenbridgeAuthMiddleware,
    JWT_PUBLIC_ATTR,
    create_auth_middleware,
    create_openbridge_config,
)
from src.auth.simple import OpenbridgeAuth, is_refresh_token


# ---------------------------------------------------------------------------
# AuthConfig and create_openbridge_config
# ---------------------------------------------------------------------------


class TestAuthConfig:
    """Tests for AuthConfig and create_openbridge_config."""

    def test_default_config_enabled(self, monkeypatch):
        """Without AUTH_ENABLED env var, auth is enabled by default."""
        monkeypatch.delenv("AUTH_ENABLED", raising=False)
        config = create_openbridge_config()
        assert config.enabled is True
        assert config.refresh_token_enabled is True
        assert config.jwt_validation_enabled is True
        assert config.jwt_verify_signature is True

    def test_auth_disabled_by_env_var(self, monkeypatch):
        """AUTH_ENABLED=false disables authentication."""
        monkeypatch.setenv("AUTH_ENABLED", "false")
        config = create_openbridge_config()
        assert config.enabled is False
        assert config.refresh_token_enabled is False
        assert config.jwt_validation_enabled is False
        assert config.jwt_verify_signature is True  # Always verify when enabled

    def test_auth_disabled_case_insensitive(self, monkeypatch):
        """AUTH_ENABLED=False (mixed case) also disables."""
        monkeypatch.setenv("AUTH_ENABLED", "False")
        config = create_openbridge_config()
        assert config.enabled is False

    def test_middleware_empty_when_disabled(self):
        """create_auth_middleware returns empty list when config.enabled=False."""
        config = AuthConfig(enabled=False)
        middleware = create_auth_middleware(config)
        assert middleware == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class DummyMiddlewareContext:
    """Mock middleware context for testing."""

    def __init__(self, fastmcp_context=None):
        self.fastmcp_context = fastmcp_context


class DummyFastMCPContext:
    """Mock FastMCP context for testing."""

    def __init__(self):
        self._state = {}

    def set_state(self, key, value):
        self._state[key] = value
        setattr(self, key, value)

    def get_state(self, key):
        return self._state.get(key)


class DummyAsyncFastMCPContext:
    """Mock FastMCP context with async set_state for testing."""

    def __init__(self):
        self._state = {}

    async def set_state(self, key, value):
        self._state[key] = value
        setattr(self, key, value)

    def get_state(self, key):
        return self._state.get(key)


FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyX2lkIjoxLCJhY2NvdW50X2lkIjoyLCJleHBpcmVzX2F0IjozMDAwfQ.sig"
"""A minimal JWT-shaped string for testing (three dot-separated segments)."""


def _make_fake_post(jwt_token=FAKE_JWT):
    """Return a fake ``requests.post`` that always succeeds."""

    def fake_post(url, json, headers, timeout):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": jwt_token}}},
        )

    return fake_post


# ---------------------------------------------------------------------------
# is_refresh_token heuristic
# ---------------------------------------------------------------------------

class TestIsRefreshToken:
    """Tests for the refresh-token pattern detector."""

    def test_classic_refresh_token(self):
        assert is_refresh_token("abc123456:def789012") is True

    def test_short_token_rejected(self):
        assert is_refresh_token("a:b") is False

    def test_jwt_not_refresh(self):
        assert is_refresh_token(FAKE_JWT) is False

    def test_empty_string(self):
        assert is_refresh_token("") is False

    def test_none_returns_false(self):
        assert is_refresh_token(None) is False

    def test_plain_string_no_colon(self):
        assert is_refresh_token("some-opaque-token-without-colon") is False


# ---------------------------------------------------------------------------
# Client sends refresh token → middleware exchanges for JWT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_exchanges_client_refresh_token(monkeypatch):
    """When the client sends a refresh token (xxx:yyy), the middleware
    exchanges it for a JWT via the auth API and stores the JWT in context."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # Stub the auth API call
    monkeypatch.setattr("src.auth.simple.requests.post", _make_fake_post())
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 9999999999})

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Client sends a refresh token
    client_refresh = "abc123456789:def987654321"
    mock_request = SimpleNamespace(headers={"authorization": f"Bearer {client_refresh}"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    # The stored value should be the exchanged JWT, NOT the raw refresh token
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == FAKE_JWT
    call_next.assert_called_once()


# ---------------------------------------------------------------------------
# Client sends an existing JWT → middleware passes through
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_passes_through_client_jwt(monkeypatch):
    """When the client sends an already-valid JWT, the middleware uses it directly."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # Should NOT call the auth API — if it does, this will explode
    def explode(*args, **kwargs):
        raise AssertionError("Auth API should not be called for a JWT token")

    monkeypatch.setattr("src.auth.simple.requests.post", explode)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Client sends a JWT (three dot-separated segments)
    client_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiam9obiJ9.signature"
    mock_request = SimpleNamespace(headers={"authorization": f"Bearer {client_jwt}"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == client_jwt


# ---------------------------------------------------------------------------
# No client token → fall back to server refresh token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_falls_back_to_server_token(monkeypatch):
    """When no client token is provided, the middleware uses OPENBRIDGE_REFRESH_TOKEN."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    monkeypatch.setattr("src.auth.simple.requests.post", _make_fake_post("server-jwt-456"))
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    # No client token
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: None)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "server-jwt-456"


# ---------------------------------------------------------------------------
# Client token takes precedence over server token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_prefers_client_over_server_token(monkeypatch):
    """Client-provided token takes precedence even when OPENBRIDGE_REFRESH_TOKEN is set."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # The server path should NOT be called
    call_count = {"server": 0, "client": 0}

    def tracking_post(url, json, headers, timeout):
        refresh = json["data"]["attributes"]["refresh_token"]
        if refresh == "server:token":
            call_count["server"] += 1
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": {"attributes": {"token": "server-jwt"}}},
            )
        else:
            call_count["client"] += 1
            return SimpleNamespace(
                raise_for_status=lambda: None,
                json=lambda: {"data": {"attributes": {"token": "client-jwt"}}},
            )

    monkeypatch.setattr("src.auth.simple.requests.post", tracking_post)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 9999999999})

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Client sends a refresh token
    client_refresh = "client123456:client789012"
    mock_request = SimpleNamespace(headers={"authorization": f"Bearer {client_refresh}"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    await middleware.on_request(context, call_next)

    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "client-jwt"
    assert call_count["server"] == 0, "Server refresh token should not be called"
    assert call_count["client"] == 1


# ---------------------------------------------------------------------------
# No auth at all → continues gracefully
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_handles_no_auth_gracefully(monkeypatch):
    """Middleware continues when neither client nor server token is available."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: None)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    assert JWT_PUBLIC_ATTR not in fastmcp_ctx._state


# ---------------------------------------------------------------------------
# Malformed Authorization header → falls back to server token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_handles_malformed_auth_header(monkeypatch):
    """Malformed Authorization header (no Bearer prefix) falls back to server token."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    monkeypatch.setattr("src.auth.simple.requests.post", _make_fake_post("fallback-jwt"))
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    mock_request = SimpleNamespace(headers={"authorization": "InvalidFormat token123"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    await middleware.on_request(context, call_next)

    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "fallback-jwt"


# ---------------------------------------------------------------------------
# exchange_token caching
# ---------------------------------------------------------------------------

def test_exchange_token_caches_result(monkeypatch):
    """exchange_token should return cached JWT on second call for the same refresh token."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    call_count = {"n": 0}

    def counting_post(url, json, headers, timeout):
        call_count["n"] += 1
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": FAKE_JWT}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", counting_post)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 9999999999})

    auth = OpenbridgeAuth()

    # First call — should hit the API
    result1 = auth.exchange_token("client:refresh")
    assert result1 == FAKE_JWT
    assert call_count["n"] == 1

    # Second call — should be cached
    result2 = auth.exchange_token("client:refresh")
    assert result2 == FAKE_JWT
    assert call_count["n"] == 1, "Second call should use cache, not hit API"


# ---------------------------------------------------------------------------
# OpenbridgeAuth basic contract tests
# ---------------------------------------------------------------------------

def test_openbridge_auth_init_without_token(monkeypatch):
    """OpenbridgeAuth can be instantiated without OPENBRIDGE_REFRESH_TOKEN."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = OpenbridgeAuth()
    assert auth.refresh_token is None


def test_openbridge_auth_get_jwt_fails_without_token(monkeypatch):
    """get_jwt() raises error when called without refresh token."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = OpenbridgeAuth()

    with pytest.raises(Exception) as exc_info:
        auth.get_jwt()

    assert "OPENBRIDGE_REFRESH_TOKEN not available" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Client exchange failure → falls back to server token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_middleware_falls_back_when_client_exchange_fails(monkeypatch):
    """If the client refresh-token exchange fails, fall back to server token."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    call_count = {"n": 0}

    def selective_post(url, json, headers, timeout):
        call_count["n"] += 1
        refresh = json["data"]["attributes"]["refresh_token"]
        if refresh == "bad_client:token":
            # Simulate API failure for client token
            raise ConnectionError("Auth API unreachable")
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "server-fallback-jwt"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", selective_post)
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 9999999999})

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    # Client sends a bad refresh token
    mock_request = SimpleNamespace(headers={"authorization": "Bearer bad_client:token"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    # Should have fallen back to server token
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "server-fallback-jwt"


@pytest.mark.asyncio
async def test_middleware_supports_async_context_set_state(monkeypatch):
    """Middleware should await async FastMCP context set_state."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    client_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJ1c2VyIjoiam9obiJ9.signature"
    mock_request = SimpleNamespace(headers={"authorization": f"Bearer {client_jwt}"})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    fastmcp_ctx = DummyAsyncFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == client_jwt


# ---------------------------------------------------------------------------
# Phase 1b — middleware branch coverage fill-ins
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_middleware_noop_when_no_fastmcp_context(monkeypatch):
    """When the outer request has no fastmcp_context, the middleware
    must still invoke call_next (MCP health/list-tools paths, etc.)
    without attempting any auth resolution."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    def explode(*args, **kwargs):
        raise AssertionError("Auth API must not be called when fastmcp_context is None")

    monkeypatch.setattr("src.auth.simple.requests.post", explode)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    context = DummyMiddlewareContext(fastmcp_context=None)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    call_next.assert_called_once()


@pytest.mark.asyncio
async def test_middleware_handles_empty_bearer(monkeypatch):
    """'Authorization: Bearer ' (with empty or whitespace-only token)
    must fall through to the server-token path, not attempt to exchange
    an empty string."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    seen_refresh_tokens = []

    def tracking_post(url, json, headers, timeout):
        seen_refresh_tokens.append(json["data"]["attributes"]["refresh_token"])
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "server-jwt"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", tracking_post)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 9999999999})

    mock_request = SimpleNamespace(headers={"authorization": "Bearer   "})
    monkeypatch.setattr("src.auth.authentication.get_http_request", lambda: mock_request)

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    await middleware.on_request(context, call_next)

    # Exchange must have used the SERVER token, not the empty client token.
    assert seen_refresh_tokens == ["server:token"]
    assert fastmcp_ctx._state[JWT_PUBLIC_ATTR] == "server-jwt"


def test_client_cache_evicts_at_32_entries(monkeypatch):
    """exchange_token cache is bounded — after >32 distinct refresh tokens,
    oldest entry must be evicted to prevent unbounded memory growth."""
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    def fake_post(url, json, headers, timeout):
        token = json["data"]["attributes"]["refresh_token"]
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": f"jwt-for-{token}"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 9999999999})

    auth = OpenbridgeAuth()

    # Drive 40 distinct tokens through the cache.
    for i in range(40):
        auth.exchange_token(f"client{i:04d}:secret{i:04d}")

    # Cache caps at 32 entries (implementation evicts once size > 32).
    assert len(auth._client_cache) <= 32, (
        f"exchange_token cache grew unbounded: {len(auth._client_cache)} entries"
    )
    # Oldest entry (client0000) must no longer be cached.
    assert "client0000:secret0000" not in auth._client_cache


# ---------------------------------------------------------------------------
# Phase 1c — OpenbridgeAuth error branches
# ---------------------------------------------------------------------------


def test_get_api_timeout_returns_default_on_non_numeric_env(monkeypatch):
    """Per contract: non-numeric OPENBRIDGE_API_TIMEOUT must not raise on
    every call. Parsing happens once at import; malformed values fall
    back to the default (10, 30) with a warn-log."""
    # Re-import-style evaluation: call the parser directly to simulate
    # startup-time re-parse under a malformed env var.
    from src.auth import simple

    monkeypatch.setenv("OPENBRIDGE_API_TIMEOUT", "not-a-number")
    parsed = simple._parse_read_timeout()
    assert parsed == simple.DEFAULT_READ_TIMEOUT


def test_get_api_timeout_parses_valid_env(monkeypatch):
    from src.auth import simple

    monkeypatch.setenv("OPENBRIDGE_API_TIMEOUT", "45")
    assert simple._parse_read_timeout() == 45


def test_do_exchange_raises_on_network_error(monkeypatch):
    """When requests.post raises, _do_exchange wraps it in AuthenticationError."""
    from src.auth.simple import AuthenticationError

    def raising_post(*args, **kwargs):
        raise ConnectionError("DNS failure")

    monkeypatch.setattr("src.auth.simple.requests.post", raising_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = OpenbridgeAuth()
    with pytest.raises(AuthenticationError) as exc_info:
        auth._do_exchange("client:token")

    assert "Openbridge auth request failed" in str(exc_info.value)


def test_do_exchange_raises_on_non_200(monkeypatch):
    """Non-200 response from the auth API must surface as AuthenticationError."""
    from src.auth.simple import AuthenticationError

    class FakeResponse:
        def raise_for_status(self):
            raise RuntimeError("401 Unauthorized")

        def json(self):  # pragma: no cover - not reached after raise_for_status
            return {}

    def fake_post(*args, **kwargs):
        return FakeResponse()

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = OpenbridgeAuth()
    with pytest.raises(AuthenticationError) as exc_info:
        auth._do_exchange("client:token")

    assert "Failed to convert refresh token to JWT" in str(exc_info.value)


def test_do_exchange_raises_when_token_missing_from_payload(monkeypatch):
    """200 response that omits data.attributes.token must be an auth error,
    not a KeyError or a silent None."""
    from src.auth.simple import AuthenticationError

    def fake_post(*args, **kwargs):
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {}}},  # token key missing
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    auth = OpenbridgeAuth()
    with pytest.raises(AuthenticationError) as exc_info:
        auth._do_exchange("client:token")

    assert "did not include a token" in str(exc_info.value)


def test_get_jwt_refreshes_when_cache_expired(monkeypatch):
    """When the cached server-token JWT has expired (accounting for the
    5-minute buffer), the next get_jwt call must re-exchange."""
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:token")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    call_count = {"n": 0}

    def counting_post(url, json, headers, timeout):
        call_count["n"] += 1
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": f"jwt-v{call_count['n']}"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", counting_post)
    # Each refresh sets expires_at=2000. Time starts at 1000 (cache valid),
    # then jumps past the 5-min buffer so the cache is stale.
    clock = {"t": 1000.0}
    monkeypatch.setattr("src.auth.simple.time.time", lambda: clock["t"])
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    auth = OpenbridgeAuth()
    first = auth.get_jwt()
    assert first == "jwt-v1"
    assert call_count["n"] == 1

    # Advance clock past the buffered expiry (2000 - 300 = 1700).
    clock["t"] = 1800.0
    second = auth.get_jwt()
    assert second == "jwt-v2"
    assert call_count["n"] == 2
