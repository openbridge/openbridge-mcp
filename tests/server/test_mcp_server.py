from src.server import mcp_server


class FakeAuthConfig:
    def __init__(self):
        self.enabled = False


class FakeFastMCP:
    def __init__(self, *, name, instructions, sampling_handler):
        self.name = name
        self.instructions = instructions
        self.middleware = []
        self.sampling_handler = sampling_handler
        self.registered_tools = {}
        self.custom_routes = {}

    def add_middleware(self, mw):
        self.middleware.append(mw)

    def tool(self, *, name, description):
        def decorator(func):
            self.registered_tools[name] = {"description": description, "func": func}
            return func

        return decorator

    def custom_route(self, path, *, methods):
        def decorator(func):
            self.custom_routes[path] = {"methods": methods, "func": func}
            return func

        return decorator


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
    assert server.middleware == [fake_middleware]
    assert server.sampling_handler is fake_sampling_handler

    expected_tools = {
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
        "create_job",
        "get_subscriptions",
        "get_storage_subscriptions",
        "get_product_stage_ids",
        "search_products",
        "list_product_tables",
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
        "get_remote_identities",
        "get_remote_identity_by_id",
        # validate_query and execute_query should be MISSING
        "get_amazon_api_access_token",
        "get_amazon_advertising_profiles",
        "get_table_schema",
        "get_suggested_table_names",
        "get_healthchecks",
        "get_jobs",
        "create_job",
        "get_subscriptions",
        "get_storage_subscriptions",
        "get_product_stage_ids",
        "search_products",
        "list_product_tables",
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
