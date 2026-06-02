"""Tests for PathTokenMiddleware and path token utilities."""
import base64
import datetime
import json
from unittest.mock import AsyncMock

import jwt as pyjwt
import pytest

from src.auth.path_token_middleware import (
    AUDIENCE,
    ISSUER,
    PathTokenMiddleware,
    _sign_path_token,
    _verify_path_token,
    build_connection_url,
)

SECRET = "test-secret-key-for-unit-tests-x"  # 32 bytes — HS256 minimum
OTHER_SECRET = "different-secret-key-for-unit-tests"  # 35 bytes


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_scope(path: str, auth_header: str | None = None) -> dict:
    headers: list[tuple[bytes, bytes]] = []
    if auth_header:
        headers.append((b"authorization", auth_header.encode()))
    return {"type": "http", "path": path, "raw_path": path.encode(), "headers": headers}


def _make_middleware(secret: str = SECRET) -> PathTokenMiddleware:
    return PathTokenMiddleware(AsyncMock(), secret=secret)


def _expired_token(secret: str = SECRET) -> str:
    now = datetime.datetime.now(datetime.UTC)
    payload = {
        "sub": "abc:xyz",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now - datetime.timedelta(days=2),
        "exp": now - datetime.timedelta(days=1),
    }
    return pyjwt.encode(payload, secret, algorithm="HS256")


# ── _verify_path_token ────────────────────────────────────────────────────────

def test_verify_valid_token():
    token = _sign_path_token("acc:sec", SECRET, ttl_days=1)
    assert _verify_path_token(token, SECRET) == "acc:sec"


def test_verify_expired_token():
    assert _verify_path_token(_expired_token(), SECRET) is None


def test_verify_wrong_audience():
    now = datetime.datetime.now(datetime.UTC)
    payload = {"sub": "acc:sec", "iss": ISSUER, "aud": "wrong-audience", "iat": now, "exp": now + datetime.timedelta(days=1)}
    token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    assert _verify_path_token(token, SECRET) is None


def test_verify_missing_audience():
    now = datetime.datetime.now(datetime.UTC)
    payload = {"sub": "acc:sec", "iss": ISSUER, "iat": now, "exp": now + datetime.timedelta(days=1)}
    token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    assert _verify_path_token(token, SECRET) is None


def test_verify_external_issuer_accepted():
    """Tokens from external issuers are accepted as long as signature and audience are valid."""
    now = datetime.datetime.now(datetime.UTC)
    payload = {"sub": "acc:sec", "iss": "https://authentication.api.openbridge.io", "aud": AUDIENCE, "iat": now, "exp": now + datetime.timedelta(days=1)}
    token = pyjwt.encode(payload, SECRET, algorithm="HS256")
    assert _verify_path_token(token, SECRET) == "acc:sec"


def test_verify_wrong_secret():
    token = _sign_path_token("acc:sec", SECRET, ttl_days=1)
    assert _verify_path_token(token, OTHER_SECRET) is None


def test_verify_tampered_payload():
    token = _sign_path_token("acc:sec", SECRET, ttl_days=1)
    header, _, sig = token.split(".")
    fake_payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "hacked:token", "iss": ISSUER, "aud": AUDIENCE}).encode()
    ).rstrip(b"=").decode()
    tampered = f"{header}.{fake_payload}.{sig}"
    assert _verify_path_token(tampered, SECRET) is None


@pytest.mark.parametrize("garbage", ["", "not-a-jwt", "a.b.c", "x" * 10])
def test_verify_garbage_input(garbage):
    assert _verify_path_token(garbage, SECRET) is None


# ── build_connection_url ──────────────────────────────────────────────────────

def test_build_connection_url_format():
    url = build_connection_url("https://mcp.example.com", "acc:sec", SECRET)
    assert url.startswith("https://mcp.example.com/mcp/")
    assert url.split("/mcp/")[1].count(".") == 2  # valid JWT


def test_build_connection_url_strips_trailing_slash():
    url = build_connection_url("https://mcp.example.com/", "acc:sec", SECRET)
    assert url.startswith("https://mcp.example.com/mcp/")
    assert "/mcp/mcp/" not in url


def test_build_connection_url_round_trip():
    refresh = "myaccount:mysecret"
    url = build_connection_url("https://mcp.example.com", refresh, SECRET)
    token = url.split("/mcp/")[1]
    assert _verify_path_token(token, SECRET) == refresh


# ── PathTokenMiddleware ASGI interface ────────────────────────────────────────

@pytest.mark.asyncio
async def test_injects_header_and_rewrites_path():
    token = _sign_path_token("user:pass", SECRET, ttl_days=1)
    scope = _make_scope(f"/mcp/{token}")

    received = {}
    async def fake_app(s, receive, send):
        received.update({"path": s["path"], "headers": list(s["headers"])})

    mw = PathTokenMiddleware(fake_app, secret=SECRET)
    await mw(scope, None, None)

    assert received["path"] == "/mcp"
    auth_values = [v for k, v in received["headers"] if k == b"authorization"]
    assert auth_values == [b"Bearer user:pass"]


@pytest.mark.asyncio
async def test_raw_path_also_rewritten():
    token = _sign_path_token("user:pass", SECRET, ttl_days=1)
    scope = _make_scope(f"/mcp/{token}")

    async def fake_app(s, receive, send):
        pass

    mw = PathTokenMiddleware(fake_app, secret=SECRET)
    await mw(scope, None, None)

    assert scope["raw_path"] == b"/mcp"


@pytest.mark.asyncio
async def test_preserves_subpath():
    token = _sign_path_token("user:pass", SECRET, ttl_days=1)
    scope = _make_scope(f"/mcp/{token}/sse")

    async def fake_app(s, receive, send):
        pass

    mw = PathTokenMiddleware(fake_app, secret=SECRET)
    await mw(scope, None, None)

    assert scope["path"] == "/mcp/sse"
    assert scope["raw_path"] == b"/mcp/sse"


@pytest.mark.asyncio
async def test_skips_when_auth_header_present():
    token = _sign_path_token("user:pass", SECRET, ttl_days=1)
    scope = _make_scope(f"/mcp/{token}", auth_header="Bearer existing")
    original_path = scope["path"]

    mw = _make_middleware()
    await mw(scope, None, None)

    assert scope["path"] == original_path
    auth_values = [v for k, v in scope["headers"] if k == b"authorization"]
    assert auth_values == [b"Bearer existing"]


@pytest.mark.asyncio
async def test_skips_non_mcp_path():
    token = _sign_path_token("user:pass", SECRET, ttl_days=1)
    scope = _make_scope(f"/health/{token}")

    mw = _make_middleware()
    await mw(scope, None, None)

    assert scope["path"] == f"/health/{token}"
    assert not any(k == b"authorization" for k, _ in scope["headers"])


@pytest.mark.asyncio
async def test_skips_bare_mcp_path():
    scope = _make_scope("/mcp")

    mw = _make_middleware()
    await mw(scope, None, None)

    assert scope["path"] == "/mcp"
    assert not any(k == b"authorization" for k, _ in scope["headers"])


@pytest.mark.asyncio
async def test_skips_invalid_path_token():
    scope = _make_scope("/mcp/not-a-valid-jwt")

    mw = _make_middleware()
    await mw(scope, None, None)

    assert scope["path"] == "/mcp/not-a-valid-jwt"
    assert not any(k == b"authorization" for k, _ in scope["headers"])


@pytest.mark.asyncio
async def test_skips_expired_path_token():
    scope = _make_scope(f"/mcp/{_expired_token()}")

    mw = _make_middleware()
    await mw(scope, None, None)

    assert not any(k == b"authorization" for k, _ in scope["headers"])


@pytest.mark.asyncio
async def test_skips_wrong_secret():
    token = _sign_path_token("user:pass", OTHER_SECRET, ttl_days=1)
    scope = _make_scope(f"/mcp/{token}")

    mw = _make_middleware(SECRET)
    await mw(scope, None, None)

    assert scope["path"] == f"/mcp/{token}"
    assert not any(k == b"authorization" for k, _ in scope["headers"])


@pytest.mark.asyncio
async def test_passes_through_non_http_scope():
    scope = {"type": "websocket", "path": "/mcp/sometoken"}

    called = False
    async def fake_app(s, receive, send):
        nonlocal called
        called = True

    mw = PathTokenMiddleware(fake_app, secret=SECRET)
    await mw(scope, None, None)

    assert called
    assert scope["path"] == "/mcp/sometoken"  # untouched


# ── PathTokenMiddleware.generate_token ────────────────────────────────────────

def test_generate_token_round_trip():
    mw = _make_middleware()
    token = mw.generate_token("myaccount:mypass", ttl_days=7)
    assert _verify_path_token(token, SECRET) == "myaccount:mypass"
