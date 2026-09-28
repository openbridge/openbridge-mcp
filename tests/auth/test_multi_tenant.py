import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token

import main
from src.auth.openbridge_verifier import OpenbridgeCredentialVerifier
from src.server import mcp_server


@pytest.mark.parametrize(("configured", "expected"), [("75", 75), (None, 100)])
def test_main_passes_concurrency_limit_to_uvicorn(monkeypatch, configured, expected):
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.delenv("MCP_PATH_TOKEN_ENABLED", raising=False)
    if configured is None:
        monkeypatch.delenv("MCP_LIMIT_CONCURRENCY", raising=False)
    else:
        monkeypatch.setenv("MCP_LIMIT_CONCURRENCY", configured)
    captured = {}
    fake_server = SimpleNamespace(http_app=lambda **_kwargs: object())
    monkeypatch.setattr(main, "load_dotenv", lambda _path: None)
    monkeypatch.setattr(main, "create_mcp_server", lambda: fake_server)
    monkeypatch.setattr(main, "validate_runtime_security", lambda _host: None)
    monkeypatch.setattr(
        main.uvicorn,
        "run",
        lambda _app, **kwargs: captured.update(kwargs),
    )

    main.main()

    assert captured["limit_concurrency"] == expected


@pytest.mark.asyncio
async def test_two_tenants_concurrently_receive_distinct_native_identity():
    identities = {
        "header.tenant-a.signature": (101, 201),
        "header.tenant-b.signature": (102, 202),
    }

    class Introspection:
        async def verify_token(self, token):
            account_id, user_id = identities[token]
            return AccessToken(
                token=token,
                client_id="unknown",
                scopes=[],
                claims={
                    "active": True,
                    "account_id": account_id,
                    "user_id": user_id,
                },
            )

    verifier = OpenbridgeCredentialVerifier(
        auth=MagicMock(),
        introspection=Introspection(),
    )
    server = FastMCP("tenant-isolation", auth=verifier)

    @server.tool(name="identity")
    async def identity() -> str:
        token = get_access_token()
        assert token is not None
        await asyncio.sleep(0)
        return f"{token.client_id}|{token.claims['sub']}"

    app = server.http_app(stateless_http=True)

    async def request(credential):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {credential}"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "identity", "arguments": {}},
                },
            )
        assert response.status_code == 200
        return response.text

    async with app.router.lifespan_context(app):
        tenant_a, tenant_b = await asyncio.gather(*map(request, identities))

    assert "openbridge|account:101|user:201" in tenant_a
    assert "openbridge|account:102|user:202" in tenant_b
    assert "account:102|user:202" not in tenant_a
    assert "account:101|user:201" not in tenant_b


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, True),
        ("", True),
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
        ("garbage", True),
    ],
)
def test_stateless_http_default_safe(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("MCP_STATELESS_HTTP", raising=False)
    else:
        monkeypatch.setenv("MCP_STATELESS_HTTP", raw)
    assert main._stateless_http_enabled() is expected


def test_auth_disabled_server_fallback_emits_warning(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:fallback")
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    assert "authentication is disabled" in caplog.text


def test_remote_auth_disabled_override_warning_is_high_signal(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:fallback")
    monkeypatch.setenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", "true")
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    assert "DANGER" in caplog.text
    assert "OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true" in caplog.text


def test_auth_enabled_server_credential_has_no_fallback_warning(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "server:fallback")
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    assert not caplog.records
