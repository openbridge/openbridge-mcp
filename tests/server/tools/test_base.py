from types import SimpleNamespace

import pytest

from src.server.tools import base
from src.auth import session_state
from src.auth.simple import AuthenticationError


def test_get_auth_headers_without_token(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    calls = []

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace()

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    headers = base.get_auth_headers()

    assert headers == {}
    assert calls == []


def test_get_auth_headers_converts_refresh_token(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")

    def fake_post(url, json, headers, timeout):
        assert url.endswith("/auth/api/ref")
        assert json["data"]["attributes"]["refresh_token"] == "abc:def"
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": "jwt-token"}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)
    monkeypatch.setattr("src.auth.simple.time.time", lambda: 1000)
    monkeypatch.setattr("src.auth.simple.jwt.decode", lambda token, options: {"expires_at": 2000})

    headers = base.get_auth_headers()

    assert headers == {"Authorization": "Bearer jwt-token"}

def test_get_auth_headers_prefers_context_jwt(monkeypatch):
    ctx = SimpleNamespace(_openbridge_jwt="ctx-token")

    def fail_get_auth():
        raise AssertionError("get_auth should not be called when ctx JWT is available")

    monkeypatch.setattr(base, "get_auth", fail_get_auth)

    headers = base.get_auth_headers(ctx)

    assert headers == {"Authorization": "Bearer ctx-token"}


def test_get_auth_headers_uses_context_get_state(monkeypatch):
    class Ctx:
        def get_state(self, key):
            assert key == "jwt_token"
            return "ctx-token"

    def fail_get_auth():
        raise AssertionError("get_auth should not be called when ctx JWT is available")

    monkeypatch.setattr(base, "get_auth", fail_get_auth)

    headers = base.get_auth_headers(Ctx())

    assert headers == {"Authorization": "Bearer ctx-token"}

def test_regression_get_auth_headers_uses_contextvar_when_ctx_is_fresh(monkeypatch):
    """REGRESSION: Code Mode sandbox-context mismatch (2026-04-15 incident).

    Code Mode's sandbox hands tools a fresh ``Context`` that never saw
    the middleware's ``set_state`` calls. Before the ContextVar fix, the
    middleware wrote the JWT onto the outer request's context only; the
    sandbox-provided ctx was bare, so ``get_auth_headers`` fell through
    to the server-side refresh-token path (empty in prod), producing
    silent ``Authorization`` headers containing a coroutine repr and
    403s from Openbridge.

    If someone removes the ContextVar fallback in
    ``src.server.tools.base._get_context_jwt``, this test fails with the
    exact shape of the production incident.
    """
    session_state.set_request_jwt("cv-token")
    try:
        def fail_get_auth():
            raise AssertionError("get_auth should not be called when ctx JWT is available")

        monkeypatch.setattr(base, "get_auth", fail_get_auth)

        # Pass a bare ctx with no JWT attributes (as the sandbox would)
        headers = base.get_auth_headers(SimpleNamespace())

        assert headers == {"Authorization": "Bearer cv-token"}
    finally:
        session_state.set_request_jwt(None)


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_get_auth_headers_ignores_async_get_state(monkeypatch):
    """FastMCP 3.2+ exposes get_state as async. The sync helper must not
    return the resulting coroutine as a JWT — it should fall through to
    the attribute-based path written by the middleware."""

    async def async_get_state(key):  # returns a coroutine when called
        return "should-not-be-used"

    ctx = SimpleNamespace(
        get_state=async_get_state,
        _openbridge_jwt="ctx-token",
    )

    def fail_get_auth():
        raise AssertionError("get_auth should not be called when ctx JWT is available")

    monkeypatch.setattr(base, "get_auth", fail_get_auth)

    headers = base.get_auth_headers(ctx)

    assert headers == {"Authorization": "Bearer ctx-token"}


def test_get_auth_headers_raises_on_conversion_failure(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "abc:def")

    def fake_post(*args, **kwargs):
        raise RuntimeError("network failure")

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr("src.auth.simple._AUTH_INSTANCE", None)

    try:
        base.get_auth_headers()
        assert False, "Expected AuthenticationError"
    except AuthenticationError as exc:
        assert "Failed to convert OPENBRIDGE_REFRESH_TOKEN to JWT" in str(exc)


# ---------------------------------------------------------------------------
# Phase 1d — safe_pagination_url SSRF guard
#
# These tests protect the pagination helper that resolves `links.next`
# URLs from Openbridge API responses. A compromised or misconfigured
# upstream could serve a `next` link pointing at an attacker-controlled
# host; the guard must refuse to follow it.
# ---------------------------------------------------------------------------


BASE = "https://remote-identity.api.openbridge.io"


def test_safe_pagination_url_null_input_returns_none():
    assert base.safe_pagination_url(None, BASE) is None
    assert base.safe_pagination_url("", BASE) is None


def test_safe_pagination_url_same_host_absolute_allowed():
    candidate = f"{BASE}/ri?page=2"
    assert base.safe_pagination_url(candidate, BASE) == candidate


def test_safe_pagination_url_same_host_relative_allowed():
    """Relative next URLs must resolve against the base URL."""
    resolved = base.safe_pagination_url("/ri?page=3", BASE)
    assert resolved == f"{BASE}/ri?page=3"


def test_safe_pagination_url_blocks_http_scheme():
    """SSRF defense: non-https schemes must be rejected."""
    assert base.safe_pagination_url("http://remote-identity.api.openbridge.io/ri?page=2", BASE) is None


def test_safe_pagination_url_blocks_cross_host():
    """SSRF defense: an upstream redirect to a different host is blocked."""
    assert base.safe_pagination_url("https://evil.example.com/ri?page=2", BASE) is None


def test_safe_pagination_url_blocks_subdomain_spoofing():
    """Even same-TLD subdomain spoofing must be blocked."""
    assert base.safe_pagination_url("https://evil.openbridge.io.attacker.com/ri?page=2", BASE) is None


def test_safe_pagination_url_blocks_ftp_scheme():
    assert base.safe_pagination_url("ftp://remote-identity.api.openbridge.io/file", BASE) is None
