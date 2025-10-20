from src.server import mcp_server


class FakeFastMCP:
    def __init__(self, *, name, instructions, middleware, sampling_handler):
        self.name = name
        self.instructions = instructions
        self.middleware = middleware
        self.sampling_handler = sampling_handler
        self.registered_tools = {}

    def tool(self, *, name, description):
        def decorator(func):
            self.registered_tools[name] = {"description": description, "func": func}
            return func

        return decorator


def test_create_mcp_server_registers_expected_tools(monkeypatch):
    fake_middleware = object()
    fake_sampling_handler = object()

    monkeypatch.setattr(mcp_server, "create_openbridge_config", lambda: "config")
    monkeypatch.setattr(mcp_server, "get_auth_manager", lambda: "auth-manager")

    def fake_create_auth_middleware(config, *, jwt_middleware, auth_manager):
        assert config == "config"
        assert jwt_middleware is False
        assert auth_manager == "auth-manager"
        return fake_middleware

    monkeypatch.setattr(mcp_server, "create_auth_middleware", fake_create_auth_middleware)
    monkeypatch.setattr(mcp_server, "create_sampling_handler", lambda: fake_sampling_handler)
    monkeypatch.setattr(mcp_server, "FastMCP", FakeFastMCP)

    server = mcp_server.create_mcp_server()

    assert isinstance(server, FakeFastMCP)
    assert server.middleware is fake_middleware
    assert server.sampling_handler is fake_sampling_handler

    expected_tools = {
        "get_remote_identities",
        "get_remote_identity_by_id",
        "validate_query",
        "execute_query",
        "get_amazon_api_access_token",
        "get_amazon_advertising_profiles",
        "get_table_rules",
        "get_suggested_table_names",
        "get_healthchecks",
        "get_jobs",
        "create_oneoff_jobs",
        "get_subscriptions",
        "get_storage_subscriptions",
        "get_product_stage_ids",
    }

    assert expected_tools == set(server.registered_tools)
