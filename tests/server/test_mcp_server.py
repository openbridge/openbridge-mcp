import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import get_access_token

from src.auth.openbridge_verifier import OpenbridgeCredentialVerifier
from src.server import mcp_server
from src.server.tools.tool_manifest import PRIVILEGED_TOOL_NAMES, TOOL_MANIFEST


class FakeAuthConfig:
    def __init__(self, *, enabled=False, auth_mode="refresh_token"):
        self.enabled = enabled
        self.auth_mode = auth_mode


class FakeFastMCP:
    def __init__(self, *, name, instructions, auth=None):
        self.name = name
        self.instructions = instructions
        self.auth = auth
        self.middleware = []
        self.extensions = []
        self.registered_tools = {}
        self.custom_routes = {}
        self.transforms = []
        # Skills providers (or any other FastMCP provider) registered
        # via ``add_provider``. The shape mirrors ``transforms`` and
        # ``middleware`` — a list of opaque registrations the test can
        # introspect by ``isinstance`` if it cares.
        self.providers = []

    def add_provider(self, provider):
        self.providers.append(provider)

    def add_extension(self, extension):
        self.extensions.append(extension)

    def add_middleware(self, mw):
        self.middleware.append(mw)

    def tool(self, *, name, description, task=None):
        # Capture the optional task config so tests can assert on it
        # without forcing every tool to be a coroutine in the fake.
        def decorator(func):
            self.registered_tools[name] = {
                "description": description,
                "func": func,
                "task": task,
            }
            return func

        return decorator

    def custom_route(self, path, *, methods):
        def decorator(func):
            self.custom_routes[path] = {"methods": methods, "func": func}
            return func

        return decorator

    def add_transform(self, transform):
        self.transforms.append(transform)


@pytest.fixture(autouse=True)
def disable_code_mode_by_default(monkeypatch):
    monkeypatch.setenv("CODE_MODE", "false")
    monkeypatch.delenv("OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS", raising=False)


def test_create_mcp_server_registers_expected_tools_with_api_key(monkeypatch):
    """Test that query validation tools are registered when API key is present."""
    fake_config = FakeAuthConfig()

    # Set an API key to enable query validation tools
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "true")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS", "true")

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert isinstance(server, FakeFastMCP)
    assert len(server.extensions) == 1

    expected_tools = {
        "get_capabilities",
        "list_skills",
        "read_skill",
        "get_remote_identities",
        "get_remote_identity_by_id",
        "validate_query",
        "execute_query",
        "get_amazon_advertising_profiles",
        "get_table_schema",
        "get_suggested_table_names",
        "get_healthchecks",
        "get_jobs",
        "get_job_by_id",
        "get_history_by_id",
        "get_subscriptions",
        "get_subscription_by_id",
        "get_storage_subscriptions",
        "get_product_stage_ids",
        "search_products",
        "list_product_tables",
        "get_product_card",
        "list_all_product_basic_metadata",
    }

    assert expected_tools | PRIVILEGED_TOOL_NAMES == set(server.registered_tools)


def test_create_mcp_server_without_api_key_skips_validation_tools(monkeypatch):
    """Test that query validation tools are NOT registered when API key is missing."""
    fake_config = FakeAuthConfig()

    # Ensure no API keys are set
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    # Should have all tools EXCEPT validate_query and execute_query
    expected_tools = {
        "get_capabilities",
        "list_skills",
        "read_skill",
        "get_remote_identities",
        "get_remote_identity_by_id",
        # validate_query and execute_query should be MISSING
        "get_amazon_advertising_profiles",
        "get_table_schema",
        "get_suggested_table_names",
        "get_healthchecks",
        "get_jobs",
        "get_job_by_id",
        "get_history_by_id",
        "get_subscriptions",
        "get_subscription_by_id",
        "get_storage_subscriptions",
        "get_product_stage_ids",
        "search_products",
        "list_product_tables",
        "get_product_card",
        "list_all_product_basic_metadata",
    }

    assert expected_tools == set(server.registered_tools)
    assert "validate_query" not in server.registered_tools
    assert "execute_query" not in server.registered_tools


def test_create_mcp_server_with_fastmcp_api_key(monkeypatch):
    """Test that FASTMCP_SAMPLING_API_KEY also enables query validation tools."""
    fake_config = FakeAuthConfig()

    # Set FASTMCP_SAMPLING_API_KEY instead of OPENAI_API_KEY
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-fastmcp-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "true")

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    # validate_query and execute_query should be registered with FASTMCP_SAMPLING_API_KEY
    assert "validate_query" in server.registered_tools
    assert "execute_query" in server.registered_tools


def test_query_execution_defaults_disabled(monkeypatch):
    fake_config = FakeAuthConfig()
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-fastmcp-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", raising=False)
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert "validate_query" in server.registered_tools
    assert "execute_query" not in server.registered_tools


def test_create_mcp_server_with_query_execution_disabled(monkeypatch):
    fake_config = FakeAuthConfig()

    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-fastmcp-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "false")

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert "validate_query" in server.registered_tools
    assert "execute_query" not in server.registered_tools


def test_health_endpoint(monkeypatch):
    """Test that health check endpoint is registered."""
    fake_config = FakeAuthConfig()

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    # Verify the health endpoint was registered
    assert isinstance(server, FakeFastMCP)
    assert "/health" in server.custom_routes
    assert server.custom_routes["/health"]["methods"] == ["GET"]


def test_get_service_version_uses_package_metadata(monkeypatch):
    monkeypatch.setattr(mcp_server, "version", lambda _: "0.1.6")
    assert mcp_server._get_service_version() == "0.1.6"


def test_get_service_version_returns_unknown_when_package_missing(monkeypatch):
    def raise_not_found(_):
        raise mcp_server.PackageNotFoundError

    monkeypatch.setattr(mcp_server, "version", raise_not_found)
    assert mcp_server._get_service_version() == "unknown"


def test_code_mode_enabled_by_default_applies_transform(monkeypatch):
    fake_config = FakeAuthConfig()
    fake_transform = object()

    monkeypatch.delenv("CODE_MODE", raising=False)
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "create_code_mode_transform", lambda: fake_transform)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.transforms == [fake_transform]


def test_code_mode_opt_out_disables_transform(monkeypatch):
    fake_config = FakeAuthConfig()

    monkeypatch.setenv("CODE_MODE", "false")
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    def should_not_be_called():
        raise AssertionError("create_code_mode_transform should not be called when CODE_MODE=false")

    monkeypatch.setattr(mcp_server, "create_code_mode_transform", should_not_be_called)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.transforms == []


def test_code_mode_missing_dependency_falls_back_to_direct_tools(monkeypatch):
    fake_config = FakeAuthConfig()

    monkeypatch.delenv("CODE_MODE", raising=False)
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    def raise_import_error():
        raise ImportError("missing sandbox package")

    monkeypatch.setattr(mcp_server, "create_code_mode_transform", raise_import_error)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.transforms == []
    assert "get_subscriptions" in server.registered_tools


# ---------------------------------------------------------------------------
# Phase 3a — Strict manifest↔registration parity
#
# These tests assert from the manifest, not from a hand-maintained list,
# so adding a tool to TOOL_MANIFEST without registering it (or vice versa)
# fails immediately. They also lock the assumption — verified during the
# Phase 0 audit — that the ONLY conditional registration today is the
# sampling-key gate for validate_query/execute_query.
# ---------------------------------------------------------------------------


def _build_server_with_defaults(monkeypatch) -> "FakeFastMCP":
    """Wire up the common fakes so a single helper can build a test server."""
    fake_config = FakeAuthConfig()

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)
    return mcp_server.create_mcp_server()


SAMPLING_GATED_TOOLS = frozenset({"validate_query", "execute_query"})


def test_registered_tools_match_manifest_without_sampling_key(monkeypatch):
    """Without a sampling key, the registered set MUST equal
    TOOL_MANIFEST minus the sampling-gated tools — derived, not hard-coded."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    server = _build_server_with_defaults(monkeypatch)

    expected = set(TOOL_MANIFEST.keys()) - SAMPLING_GATED_TOOLS - PRIVILEGED_TOOL_NAMES
    assert set(server.registered_tools) == expected, (
        "Manifest↔registration drift detected. Either a tool was added to "
        "TOOL_MANIFEST without a register_tool call in mcp_server.py, or a "
        "register_tool call was added without a TOOL_MANIFEST entry. "
        "If a new conditional registration was intentionally added, update "
        "SAMPLING_GATED_TOOLS in this test to reflect the new gate."
    )


def test_registered_tools_match_manifest_with_sampling_key(monkeypatch):
    """With a sampling key set, the registered set MUST equal the full
    TOOL_MANIFEST keyset — no orphan tools, no missing registrations."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "true")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS", "true")

    server = _build_server_with_defaults(monkeypatch)

    assert set(server.registered_tools) == set(TOOL_MANIFEST.keys()), (
        "Manifest↔registration drift with sampling key present. See the "
        "without-sampling-key test for diagnostics."
    )


def test_privileged_tools_are_absent_by_default(monkeypatch):
    server = _build_server_with_defaults(monkeypatch)
    assert PRIVILEGED_TOOL_NAMES.isdisjoint(server.registered_tools)


def test_production_shaped_opt_in_registers_privileged_tools(monkeypatch):
    synthetic_production = {
        "OPENBRIDGE_AUTH_MODE": "oauth_proxy",
        "MCP_HOST": "0.0.0.0",
        "MCP_BASE_URL": "https://mcp.example.test",
        "MCP_JWT_SIGNING_KEY": "x" * 32,
        "OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS": "true",
    }
    for name, value in synthetic_production.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: FakeAuthConfig())

    server = _build_server_with_defaults(monkeypatch)

    assert PRIVILEGED_TOOL_NAMES.issubset(server.registered_tools)
    for name in PRIVILEGED_TOOL_NAMES:
        assert server.registered_tools[name]["task"].mode == "forbidden"


def test_health_endpoint_returns_documented_shape(monkeypatch):
    """The public health response is stable and discloses no version."""
    server = _build_server_with_defaults(monkeypatch)

    handler = server.custom_routes["/health"]["func"]
    response = asyncio.run(handler(request=None))

    # JSONResponse exposes the body as bytes; decode and parse.
    body = json.loads(response.body)
    assert body == {
        "status": "healthy",
        "service": "openbridge-mcp",
    }
    assert "version" not in body


def test_no_orphan_manifest_entries(monkeypatch):
    """Every TOOL_MANIFEST key must be reachable as a registered tool in
    AT LEAST one of the two registration regimes (with/without sampling
    key). Catches manifest entries that no register_tool call references."""
    # Regime 1 — no sampling key
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    no_key_tools = set(_build_server_with_defaults(monkeypatch).registered_tools)

    # Regime 2 — sampling key present
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "true")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_PRIVILEGED_TOOLS", "true")
    with_key_tools = set(_build_server_with_defaults(monkeypatch).registered_tools)

    reachable = no_key_tools | with_key_tools
    orphans = set(TOOL_MANIFEST.keys()) - reachable
    assert not orphans, f"TOOL_MANIFEST contains orphan entries: {sorted(orphans)}"

    # And the inverse: nothing registered should be missing from the manifest.
    extras = reachable - set(TOOL_MANIFEST.keys())
    assert not extras, f"Tools registered without a manifest entry: {sorted(extras)}"


def test_oauth_mode_uses_multi_auth_provider(monkeypatch):
    oauth_auth = object()
    config = FakeAuthConfig(enabled=True, auth_mode="oauth_proxy")
    monkeypatch.setenv("MCP_BASE_URL", "https://mcp.example.test")
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: config)
    monkeypatch.setattr(mcp_server, "create_oauth_auth", lambda **_kwargs: oauth_auth)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.auth is oauth_auth
    assert len(server.middleware) == 1


def test_refresh_token_mode_uses_native_credential_verifier(monkeypatch):
    credential_verifier = object()
    config = FakeAuthConfig(enabled=True, auth_mode="refresh_token")
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: config)
    monkeypatch.setattr(
        mcp_server,
        "create_openbridge_credential_verifier",
        lambda: credential_verifier,
    )
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.auth is credential_verifier
    assert len(server.middleware) == 1


def test_auth_disabled_installs_no_auth_provider(monkeypatch):
    config = FakeAuthConfig(enabled=False, auth_mode="oauth_proxy")
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: config)
    monkeypatch.setattr(
        mcp_server,
        "create_oauth_auth",
        lambda **_kwargs: pytest.fail("OAuth provider must not be constructed"),
    )
    monkeypatch.setattr(
        mcp_server,
        "create_openbridge_credential_verifier",
        lambda: pytest.fail("Credential verifier must not be constructed"),
    )
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.auth is None
    assert len(server.middleware) == 1


async def _post_tool_call(app, authorization):
    headers = {}
    if authorization is not None:
        headers["Authorization"] = authorization
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as client:
        return await client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "spy", "arguments": {}},
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "Basic malformed",
        "Bearer bogus-token",
        "Bearer header.payload.bad",
        "Bearer account123:bad-secret",
    ],
)
async def test_native_http_boundary_rejects_invalid_credentials_before_tool(
    authorization,
):
    auth = MagicMock()
    auth.exchange_token.side_effect = RuntimeError("exchange rejected")
    introspection = AsyncMock(return_value=None)
    introspection.verify_token.return_value = None
    verifier = OpenbridgeCredentialVerifier(
        auth=auth,
        introspection=introspection,
    )
    server = FastMCP("auth-boundary-test", auth=verifier)
    calls = 0

    @server.tool(name="spy")
    async def spy() -> str:
        nonlocal calls
        calls += 1
        return "called"

    app = server.http_app(stateless_http=True)
    async with app.router.lifespan_context(app):
        response = await _post_tool_call(app, authorization)

    assert response.status_code == 401
    assert calls == 0
    assert '"isError":false' not in response.text
    assert '"result":[]' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential", "expected_jwt"),
    [
        ("header.payload.signature", "header.payload.signature"),
        ("account123:api-secret", "exchanged.jwt.token"),
    ],
)
async def test_native_http_boundary_exposes_verified_jwt_to_tool(
    credential,
    expected_jwt,
):
    auth = MagicMock()
    auth.exchange_token.return_value = "exchanged.jwt.token"
    introspection = AsyncMock()

    async def verify(candidate):
        return AccessToken(
            token=candidate,
            client_id="unknown",
            scopes=[],
            claims={"active": True, "account_id": 101, "user_id": 202},
        )

    introspection.verify_token.side_effect = verify
    verifier = OpenbridgeCredentialVerifier(
        auth=auth,
        introspection=introspection,
    )
    server = FastMCP("auth-boundary-test", auth=verifier)
    observed_tokens = []

    @server.tool(name="spy")
    async def spy() -> str:
        access_token = get_access_token()
        observed_tokens.append(access_token.token if access_token else None)
        return "called"

    app = server.http_app(stateless_http=True)
    async with app.router.lifespan_context(app):
        response = await _post_tool_call(app, f"Bearer {credential}")

    assert response.status_code == 200
    assert observed_tokens == [expected_jwt]


@pytest.mark.asyncio
async def test_native_http_boundary_rejects_malformed_verified_expiry():
    auth = MagicMock()
    introspection = AsyncMock()
    introspection.verify_token.return_value = AccessToken(
        token="header.payload.signature",
        client_id="unknown",
        scopes=[],
        claims={
            "active": True,
            "account_id": 101,
            "user_id": 202,
            "expires_at": "not-a-timestamp",
        },
    )
    verifier = OpenbridgeCredentialVerifier(
        auth=auth,
        introspection=introspection,
    )
    server = FastMCP("auth-boundary-test", auth=verifier)
    calls = 0

    @server.tool(name="spy")
    async def spy() -> str:
        nonlocal calls
        calls += 1
        return "called"

    app = server.http_app(stateless_http=True)
    async with app.router.lifespan_context(app):
        response = await _post_tool_call(
            app,
            "Bearer header.payload.signature",
        )

    assert response.status_code == 401
    assert calls == 0
