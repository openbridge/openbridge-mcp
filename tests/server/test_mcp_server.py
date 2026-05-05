import pytest

from src.server import mcp_server


class FakeAuthConfig:
    def __init__(self):
        self.enabled = False
        self.auth_mode = "refresh_token"


class FakeFastMCP:
    def __init__(self, *, name, instructions, sampling_handler, tasks=False):
        self.name = name
        self.instructions = instructions
        self.middleware = []
        self.sampling_handler = sampling_handler
        # tasks=True flips on background-task support; the production
        # construction site passes it unconditionally so the fake must
        # accept and remember the value for assertion.
        self.tasks_enabled = tasks
        self.registered_tools = {}
        self.custom_routes = {}
        self.transforms = []

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


def test_create_mcp_server_registers_expected_tools_with_api_key(monkeypatch):
    """Test that query validation tools are registered when API key is present."""
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    # Set an API key to enable query validation tools
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")

    def fake_create_auth_middleware(config, *, jwt_middleware, auth_manager):
        assert config is fake_config
        assert jwt_middleware is False
        assert auth_manager == "auth-manager"
        return [fake_middleware]

    monkeypatch.setattr(mcp_server, "create_auth_middleware", fake_create_auth_middleware)
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert isinstance(server, FakeFastMCP)
    assert fake_middleware in server.middleware
    assert server.sampling_handler is fake_sampling_handler

    expected_tools = {
        "get_capabilities",
        "get_remote_identities",
        "get_remote_identity_by_id",
        "validate_query",
        "execute_query",
        "get_amazon_api_access_token",
        "get_amazon_advertising_profiles",
        "get_table_schema",
        "get_suggested_table_names",
        "get_healthchecks",
        "get_jobs",
        "get_job_by_id",
        "get_history_by_id",
        "update_history_status",
        "create_job",
        "get_subscriptions",
        "get_subscription_by_id",
        "create_subscription",
        "update_subscription",
        "cancel_subscription",
        "get_storage_subscriptions",
        "get_product_stage_ids",
        "search_products",
        "list_product_tables",
        "get_product_card",
        "list_all_product_basic_metadata",
    }

    assert expected_tools == set(server.registered_tools)


def test_create_mcp_server_without_api_key_skips_validation_tools(monkeypatch):
    """Test that query validation tools are NOT registered when API key is missing."""
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    # Ensure no API keys are set
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")

    def fake_create_auth_middleware(config, *, jwt_middleware, auth_manager):
        return [fake_middleware]

    monkeypatch.setattr(mcp_server, "create_auth_middleware", fake_create_auth_middleware)
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    # Should have all tools EXCEPT validate_query and execute_query
    expected_tools = {
        "get_capabilities",
        "get_remote_identities",
        "get_remote_identity_by_id",
        # validate_query and execute_query should be MISSING
        "get_amazon_api_access_token",
        "get_amazon_advertising_profiles",
        "get_table_schema",
        "get_suggested_table_names",
        "get_healthchecks",
        "get_jobs",
        "get_job_by_id",
        "get_history_by_id",
        "update_history_status",
        "create_job",
        "get_subscriptions",
        "get_subscription_by_id",
        "create_subscription",
        "update_subscription",
        "cancel_subscription",
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
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    # Set FASTMCP_SAMPLING_API_KEY instead of OPENAI_API_KEY
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-fastmcp-key")

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    # validate_query and execute_query should be registered with FASTMCP_SAMPLING_API_KEY
    assert "validate_query" in server.registered_tools
    assert "execute_query" in server.registered_tools


def test_create_mcp_server_with_query_execution_disabled(monkeypatch):
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    monkeypatch.setenv("FASTMCP_SAMPLING_API_KEY", "test-fastmcp-key")
    monkeypatch.setenv("OPENBRIDGE_ENABLE_QUERY_EXECUTION", "false")

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert "validate_query" in server.registered_tools
    assert "execute_query" not in server.registered_tools


def test_health_endpoint(monkeypatch):
    """Test that health check endpoint is registered."""
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
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
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()
    fake_transform = object()

    monkeypatch.delenv("CODE_MODE", raising=False)
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "create_code_mode_transform", lambda: fake_transform)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.transforms == [fake_transform]


def test_code_mode_opt_out_disables_transform(monkeypatch):
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    monkeypatch.setenv("CODE_MODE", "false")
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)

    def should_not_be_called():
        raise AssertionError("create_code_mode_transform should not be called when CODE_MODE=false")

    monkeypatch.setattr(mcp_server, "create_code_mode_transform", should_not_be_called)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert server.transforms == []


def test_code_mode_missing_dependency_falls_back_to_direct_tools(monkeypatch):
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    monkeypatch.delenv("CODE_MODE", raising=False)
    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)

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
    fake_middleware = object()
    fake_sampling_handler = object()
    fake_config = FakeAuthConfig()

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: fake_config)
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")
    monkeypatch.setattr(mcp_server, "create_auth_middleware", lambda *args, **kwargs: [fake_middleware])
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)
    return mcp_server.create_mcp_server()


SAMPLING_GATED_TOOLS = frozenset({"validate_query", "execute_query"})


def test_registered_tools_match_manifest_without_sampling_key(monkeypatch):
    """Without a sampling key, the registered set MUST equal
    TOOL_MANIFEST minus the sampling-gated tools — derived, not hard-coded."""
    from src.server.tools.tool_manifest import TOOL_MANIFEST

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    server = _build_server_with_defaults(monkeypatch)

    expected = set(TOOL_MANIFEST.keys()) - SAMPLING_GATED_TOOLS
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
    from src.server.tools.tool_manifest import TOOL_MANIFEST

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)

    server = _build_server_with_defaults(monkeypatch)

    assert set(server.registered_tools) == set(TOOL_MANIFEST.keys()), (
        "Manifest↔registration drift with sampling key present. See the "
        "without-sampling-key test for diagnostics."
    )


def test_health_endpoint_returns_documented_shape(monkeypatch):
    """Lock the /health JSON contract: status, service, version. Load
    balancers and uptime monitors depend on these field names."""
    import asyncio
    import json

    monkeypatch.setattr(mcp_server, "version", lambda _: "9.9.9")
    server = _build_server_with_defaults(monkeypatch)

    handler = server.custom_routes["/health"]["func"]
    response = asyncio.run(handler(request=None))

    # JSONResponse exposes the body as bytes; decode and parse.
    body = json.loads(response.body)
    assert body == {
        "status": "healthy",
        "service": "openbridge-mcp",
        "version": "9.9.9",
    }


def test_no_orphan_manifest_entries(monkeypatch):
    """Every TOOL_MANIFEST key must be reachable as a registered tool in
    AT LEAST one of the two registration regimes (with/without sampling
    key). Catches manifest entries that no register_tool call references."""
    from src.server.tools.tool_manifest import TOOL_MANIFEST

    # Regime 1 — no sampling key
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FASTMCP_SAMPLING_API_KEY", raising=False)
    no_key_tools = set(_build_server_with_defaults(monkeypatch).registered_tools)

    # Regime 2 — sampling key present
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    with_key_tools = set(_build_server_with_defaults(monkeypatch).registered_tools)

    reachable = no_key_tools | with_key_tools
    orphans = set(TOOL_MANIFEST.keys()) - reachable
    assert not orphans, f"TOOL_MANIFEST contains orphan entries: {sorted(orphans)}"

    # And the inverse: nothing registered should be missing from the manifest.
    extras = reachable - set(TOOL_MANIFEST.keys())
    assert not extras, f"Tools registered without a manifest entry: {sorted(extras)}"
