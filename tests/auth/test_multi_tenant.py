"""Multi-tenant safety regression tests.

These tests lock in the contract for active multi-tenant deployment:

1. Two concurrent requests with distinct tenant credentials must never
   see each other's resolved JWT. This re-asserts the ContextVar
   isolation already covered by ``test_session_state.py`` *at the
   middleware level* — i.e. through the realistic call path that primes
   the per-request JWT.

2. With ``OPENBRIDGE_REQUIRE_CLIENT_AUTH=true``, a request that arrives
   without an ``Authorization: Bearer`` header is **rejected** with
   ``McpError(-32001)``. The server's ``OPENBRIDGE_REFRESH_TOKEN`` is
   never silently substituted.

3. The fail-closed path does not invoke ``OpenbridgeAuth.get_jwt`` at
   all — verified via a mock that explodes if called.

4. The tool-layer backstop in ``src/server/tools/base.py`` raises
   ``AuthenticationError`` when require-client-auth is on and no JWT
   was primed onto the context.

5. ``main._stateless_http_enabled`` defaults to True (multi-instance
   safe) and honors explicit env overrides.

If any of these tests fail, multi-tenant production is unsafe.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp.exceptions import McpError

import main
from src.auth import session_state
from src.auth.authentication import (
    AUTH_ERROR_CODE,
    JWT_PUBLIC_ATTR,
    OpenbridgeAuthMiddleware,
    create_openbridge_config,
)
from src.auth.simple import OpenbridgeAuth
from src.server.tools import base as base_tools


# ---------------------------------------------------------------------------
# Shared fakes
# ---------------------------------------------------------------------------


class DummyMiddlewareContext:
    def __init__(self, fastmcp_context=None):
        self.fastmcp_context = fastmcp_context


class DummyFastMCPContext:
    def __init__(self):
        self._state = {}

    def set_state(self, key, value):
        self._state[key] = value
        setattr(self, key, value)

    def get_state(self, key):
        return self._state.get(key)


def _per_token_post(url, json, headers, timeout):
    """Echo each refresh token into a tenant-tagged JWT.

    Lets us tell which tenant's token a downstream call resolved to.
    """
    refresh = json["data"]["attributes"]["refresh_token"]
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": {"attributes": {"token": f"jwt::{refresh}"}}},
    )


# ---------------------------------------------------------------------------
# 1. Two-tenant concurrent isolation through the middleware path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_tenants_concurrent_no_cross_leak(monkeypatch):
    """Concurrent requests with distinct tenant tokens must each resolve
    to their own JWT. The boundary is ``session_state._jwt_var``
    (a ContextVar): each asyncio task sees only its own primed value.

    If anyone downgrades that ContextVar to a module-level variable, or
    leaks ``OpenbridgeAuthMiddleware`` instance state across requests,
    this test fails because tenant A would observe B's tagged JWT in
    its own task context (or vice versa).

    Note on the request mock: the middleware reads the request and
    primes the ContextVar synchronously — no ``await`` happens between
    those steps — so a globally-patched ``get_http_request`` is fine
    for this test. The shared ContextVar is what isolates tasks.
    """
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)
    monkeypatch.setattr("src.auth.simple.requests.post", _per_token_post)
    monkeypatch.setattr(
        "src.auth.simple.jwt.decode",
        lambda token, options: {"expires_at": 9999999999},
    )

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth)
    holder: dict[str, str | None] = {}

    async def task(label: str, refresh: str, yield_for: float):
        local_request = SimpleNamespace(
            headers={"authorization": f"Bearer {refresh}"},
        )
        # Patch is global, but the synchronous portion of on_request
        # (header read → token exchange → set_request_jwt) completes
        # before any await yields, so each task reads its own request.
        monkeypatch.setattr(
            "src.auth.authentication.get_http_request",
            lambda req=local_request: req,
        )

        async def call_next(_ctx):
            # Yield long enough for the other task to also have run
            # and primed *its* JWT into *its own* ContextVar context.
            await asyncio.sleep(yield_for)
            holder[label] = session_state.get_request_jwt()
            return "ok"

        fastmcp_ctx = DummyFastMCPContext()
        context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
        await middleware.on_request(context, call_next)

    await asyncio.gather(
        task("A", "tenantA00:secretA", 0.01),
        task("B", "tenantB00:secretB", 0.005),
    )

    assert holder["A"] == "jwt::tenantA00:secretA", (
        f"Cross-tenant leak: tenant A observed {holder['A']!r}"
    )
    assert holder["B"] == "jwt::tenantB00:secretB", (
        f"Cross-tenant leak: tenant B observed {holder['B']!r}"
    )


# ---------------------------------------------------------------------------
# 2. Fail-closed when require-client-auth=true and no Authorization header
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_client_auth_rejects_missing_header(monkeypatch):
    """With ``OPENBRIDGE_REQUIRE_CLIENT_AUTH=true``, a request that
    arrives over HTTP with no Bearer header must be rejected with
    ``McpError(-32001)``. The server token must NOT be substituted.
    """
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:should-not-be-used")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    # If anyone tries to call the auth API, this explodes. The
    # fail-closed branch must raise *before* invoking the server token.
    def explode(*args, **kwargs):
        raise AssertionError(
            "Auth API must not be called when OPENBRIDGE_REQUIRE_CLIENT_AUTH=true "
            "and the request has no Bearer header"
        )

    monkeypatch.setattr("src.auth.simple.requests.post", explode)

    # Simulate a real HTTP request with no Authorization header.
    mock_request = SimpleNamespace(headers={})
    monkeypatch.setattr(
        "src.auth.authentication.get_http_request", lambda: mock_request
    )

    config = create_openbridge_config()
    assert config.require_client_auth is True

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(
        auth, require_client_auth=config.require_client_auth
    )

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    with pytest.raises(McpError) as exc_info:
        await middleware.on_request(context, call_next)

    assert exc_info.value.error.code == AUTH_ERROR_CODE
    assert "OPENBRIDGE_REQUIRE_CLIENT_AUTH" in exc_info.value.error.message
    call_next.assert_not_called()
    assert JWT_PUBLIC_ATTR not in fastmcp_ctx._state


@pytest.mark.asyncio
async def test_require_client_auth_does_not_invoke_server_token_path(monkeypatch):
    """REGRESSION: in fail-closed mode, ``OpenbridgeAuth.get_jwt`` must
    not be invoked for missing-header requests. Asserted with a mock
    that fails the test on call."""
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    mock_request = SimpleNamespace(headers={})
    monkeypatch.setattr(
        "src.auth.authentication.get_http_request", lambda: mock_request
    )

    auth = OpenbridgeAuth()

    def fail_get_jwt():
        raise AssertionError(
            "OpenbridgeAuth.get_jwt was invoked despite require_client_auth=True"
        )

    auth.get_jwt = fail_get_jwt  # type: ignore[assignment]

    middleware = OpenbridgeAuthMiddleware(auth, require_client_auth=True)
    context = DummyMiddlewareContext(fastmcp_context=DummyFastMCPContext())

    with pytest.raises(McpError):
        await middleware.on_request(context, AsyncMock())


# ---------------------------------------------------------------------------
# 3. Non-HTTP / no-request path keeps working under require_client_auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_require_client_auth_does_not_block_non_http_paths(monkeypatch):
    """``get_http_request()`` returning None means we're not on an HTTP
    transport (stdio, list_tools at startup, etc.). Those paths must
    keep working in fail-closed mode — only real client requests are
    gated.
    """
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)
    monkeypatch.setattr(
        "src.auth.authentication.get_http_request", lambda: None
    )

    auth = OpenbridgeAuth()
    middleware = OpenbridgeAuthMiddleware(auth, require_client_auth=True)

    fastmcp_ctx = DummyFastMCPContext()
    context = DummyMiddlewareContext(fastmcp_context=fastmcp_ctx)
    call_next = AsyncMock(return_value="response")

    result = await middleware.on_request(context, call_next)

    assert result == "response"
    call_next.assert_called_once()
    assert JWT_PUBLIC_ATTR not in fastmcp_ctx._state


# ---------------------------------------------------------------------------
# 4. Tool-layer backstop in src/server/tools/base.py
# ---------------------------------------------------------------------------


def test_base_get_auth_headers_fails_closed_when_required(monkeypatch):
    """When ``OPENBRIDGE_REQUIRE_CLIENT_AUTH=true`` and no JWT was
    primed for this request, ``base.get_auth_headers`` must raise
    ``AuthenticationError`` instead of falling back to the server token
    or returning empty headers.
    """
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    session_state.set_request_jwt(None)

    # If get_auth() is reached, that's a fail-closed regression.
    def fail_get_auth():
        raise AssertionError(
            "get_auth() should not be called when require_client_auth is on "
            "and no per-request JWT was primed"
        )

    monkeypatch.setattr(base_tools, "get_auth", fail_get_auth)

    with pytest.raises(base_tools.AuthenticationError) as exc_info:
        base_tools.get_auth_headers(ctx=None)

    assert "OPENBRIDGE_REQUIRE_CLIENT_AUTH" in str(exc_info.value)


def test_base_get_auth_headers_passes_through_primed_jwt(monkeypatch):
    """Sanity check: when the middleware primed a per-request JWT, the
    backstop is irrelevant and the helper returns the tenant Bearer
    header normally."""
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")
    session_state.set_request_jwt("tenant-jwt-123")
    try:
        headers = base_tools.get_auth_headers(ctx=None)
        assert headers == {"Authorization": "Bearer tenant-jwt-123"}
    finally:
        session_state.set_request_jwt(None)


# ---------------------------------------------------------------------------
# 5. main._stateless_http_enabled defaults & overrides
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, True),
        ("", True),
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("garbage", True),  # malformed → safe default
    ],
)
def test_stateless_http_default_safe(monkeypatch, raw, expected):
    """``MCP_STATELESS_HTTP`` defaults to True (multi-instance safe) and
    only flips to False when explicitly set falsey."""
    if raw is None:
        monkeypatch.delenv("MCP_STATELESS_HTTP", raising=False)
    else:
        monkeypatch.setenv("MCP_STATELESS_HTTP", raw)
    assert main._stateless_http_enabled() is expected


# ---------------------------------------------------------------------------
# 6. Boot-time WARNING when server-token fallback is reachable
# ---------------------------------------------------------------------------
#
# Per the security review: the fallback is gated by an env var that
# *defaults* to permissive. The fix isn't to flip the default (would
# break single-tenant installs); it's to make the dangerous combination
# loud at boot. These tests lock that behavior in.


def test_warn_emitted_when_server_token_set_and_require_client_auth_off(monkeypatch, caplog):
    """The flagged combination — server token configured + require-client-auth
    not enabled — must produce a WARNING that explicitly names the env
    var an operator should flip. If the warning is downgraded to INFO or
    removed, this test fails."""
    import logging

    from src.server import mcp_server

    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:fallback")
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    matching = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert matching, "expected a WARNING when server-token fallback is reachable"
    msg = matching[0].getMessage()
    assert "OPENBRIDGE_REQUIRE_CLIENT_AUTH" in msg
    assert "OPENBRIDGE_REFRESH_TOKEN" in msg
    assert "multi-tenant" in msg.lower()


def test_no_warn_when_require_client_auth_enabled(monkeypatch, caplog):
    """Operator already opted into fail-closed mode — no warning needed."""
    import logging

    from src.server import mcp_server

    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:fallback")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "true")

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "no warning should fire when OPENBRIDGE_REQUIRE_CLIENT_AUTH=true"
    )


def test_no_warn_when_no_server_token(monkeypatch, caplog):
    """Pure client-side auth deployments don't have a fallback to warn about."""
    import logging

    from src.server import mcp_server

    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "no warning should fire when there is no server refresh token to fall back to"
    )
