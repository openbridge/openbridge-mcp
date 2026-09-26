import logging

import pytest

import main
from src.auth.path_token_middleware import PathTokenMiddleware
from src.utils import runtime_security
from src.utils.runtime_security import (
    env_flag,
    is_loopback_host,
    positive_int_env,
    validate_secret,
    validate_runtime_security,
)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.10"])
def test_auth_enabled_remote_bind_has_no_server_token_fallback(monkeypatch, host):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "account:secret")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "false")
    monkeypatch.delenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", raising=False)

    validate_runtime_security(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1", "[::1]"])
def test_auth_disabled_loopback_server_fallback_is_allowed(monkeypatch, host):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "account:secret")
    monkeypatch.delenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", raising=False)

    validate_runtime_security(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.10"])
def test_auth_disabled_fails_on_remote_bind(monkeypatch, host):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", raising=False)

    with pytest.raises(RuntimeError, match="client authentication"):
        validate_runtime_security(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1"])
def test_auth_disabled_is_allowed_on_loopback(monkeypatch, host):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.delenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", raising=False)

    validate_runtime_security(host)


def test_remote_explicit_unsafe_override_is_allowed_and_warned(monkeypatch, caplog):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", "true")

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.runtime_security"):
        validate_runtime_security("0.0.0.0")

    message = " ".join(record.getMessage() for record in caplog.records)
    assert "OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH=true" in message
    assert "remote" in message.lower()


def test_deprecated_client_auth_flag_warns_once(monkeypatch, caplog):
    from src.server import mcp_server

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "false")
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)

    with caplog.at_level(logging.WARNING, logger="mcp_query_execution.mcp_server"):
        mcp_server._warn_if_server_token_fallback_open()

    messages = [
        record.getMessage()
        for record in caplog.records
        if "OPENBRIDGE_REQUIRE_CLIENT_AUTH" in record.getMessage()
    ]
    assert len(messages) == 1
    assert "deprecated and ignored" in messages[0]


def test_main_validates_host_before_server_creation(monkeypatch):
    seen = []
    monkeypatch.setenv("MCP_HOST", "192.0.2.10")
    monkeypatch.setattr(main, "load_dotenv", lambda *_args, **_kwargs: None)

    def reject(host):
        seen.append(host)
        raise RuntimeError("unsafe test configuration")

    def fail_server_creation():
        raise AssertionError("server creation must follow runtime validation")

    monkeypatch.setattr(main, "validate_runtime_security", reject)
    monkeypatch.setattr(main, "create_mcp_server", fail_server_creation)

    with pytest.raises(SystemExit):
        main.main()

    assert seen == ["192.0.2.10"]


def test_path_tokens_default_disabled(monkeypatch):
    monkeypatch.delenv("MCP_PATH_TOKEN_ENABLED", raising=False)
    assert runtime_security.path_tokens_enabled() is False


def test_main_does_not_wrap_path_tokens_by_default(monkeypatch):
    base_app = object()
    server = type("Server", (), {"http_app": lambda self, **_kwargs: base_app})()
    captured = {}
    monkeypatch.setattr(main, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "validate_runtime_security", lambda _host: None)
    monkeypatch.setattr(main, "create_mcp_server", lambda: server)
    monkeypatch.setattr(
        main,
        "load_secret",
        lambda: (_ for _ in ()).throw(AssertionError("secret must not load")),
    )
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **_kwargs: captured.update(app=app))
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.delenv("MCP_PATH_TOKEN_ENABLED", raising=False)

    main.main()

    assert captured["app"] is base_app


def test_main_wraps_path_tokens_when_explicitly_enabled(monkeypatch):
    base_app = object()
    server = type("Server", (), {"http_app": lambda self, **_kwargs: base_app})()
    captured = {}
    monkeypatch.setattr(main, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "validate_runtime_security", lambda _host: None)
    monkeypatch.setattr(main, "create_mcp_server", lambda: server)
    monkeypatch.setattr(main, "load_secret", lambda: "s" * 32)
    monkeypatch.setattr(main.uvicorn, "run", lambda app, **_kwargs: captured.update(app=app))
    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "refresh_token")
    monkeypatch.setenv("MCP_PATH_TOKEN_ENABLED", "true")

    main.main()

    assert isinstance(captured["app"], PathTokenMiddleware)


def test_path_tokens_rejected_in_oauth_proxy_mode(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_PATH_TOKEN_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "oauth_proxy")
    monkeypatch.setenv("MCP_PATH_TOKEN_SECRET", "s" * 32)
    with pytest.raises(RuntimeError, match="refresh_token"):
        validate_runtime_security("127.0.0.1")


def test_path_tokens_rejected_when_authentication_is_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    monkeypatch.setenv("MCP_PATH_TOKEN_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "refresh_token")
    monkeypatch.setenv("MCP_PATH_TOKEN_SECRET", "s" * 32)

    with pytest.raises(RuntimeError, match="AUTH_ENABLED=true"):
        validate_runtime_security("127.0.0.1")


@pytest.mark.parametrize(
    "secret",
    [None, "short", "your-strong-secret-here", "your-32-byte-hex-secret-here"],
)
def test_enabled_path_tokens_require_strong_non_placeholder_secret(monkeypatch, secret):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_PATH_TOKEN_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "refresh_token")
    if secret is None:
        monkeypatch.delenv("MCP_PATH_TOKEN_SECRET", raising=False)
    else:
        monkeypatch.setenv("MCP_PATH_TOKEN_SECRET", secret)

    with pytest.raises(RuntimeError, match="MCP_PATH_TOKEN_SECRET"):
        validate_runtime_security("127.0.0.1")


@pytest.mark.parametrize("name", ["MCP_PATH_TOKEN_TTL_DAYS", "MCP_PATH_TOKEN_MAX_AGE_DAYS"])
@pytest.mark.parametrize("value", ["0", "8", "not-an-integer"])
def test_enabled_path_tokens_reject_invalid_lifetime_settings(monkeypatch, name, value):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("MCP_PATH_TOKEN_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "refresh_token")
    monkeypatch.setenv("MCP_PATH_TOKEN_SECRET", "s" * 32)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        validate_runtime_security("127.0.0.1")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_accepts_truthy_values(monkeypatch, value):
    monkeypatch.setenv("TEST_RUNTIME_FLAG", value)
    assert env_flag("TEST_RUNTIME_FLAG", default=False) is True


@pytest.mark.parametrize("value", ["0", "-1", "not-an-integer"])
def test_auth_exchange_concurrency_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY", value)
    with pytest.raises(RuntimeError, match="OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY"):
        validate_runtime_security("127.0.0.1")


def test_positive_int_env_accepts_positive_value(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY", "3")
    assert positive_int_env("OPENBRIDGE_AUTH_EXCHANGE_CONCURRENCY", default=8) == 3


@pytest.mark.parametrize(
    ("host", "expected"),
    [("localhost", True), ("127.0.0.2", True), ("::1", True), ("example.com", False)],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected


@pytest.mark.parametrize(
    "secret",
    [None, "short", "your-strong-secret-here", "your-strong-stable-secret-here"],
)
def test_remote_oauth_proxy_requires_strong_signing_key(monkeypatch, secret):
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "oauth_proxy")
    if secret is None:
        monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)
    else:
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", secret)

    with pytest.raises(RuntimeError, match="MCP_JWT_SIGNING_KEY"):
        validate_runtime_security("0.0.0.0")


def test_remote_oauth_proxy_accepts_32_byte_signing_key(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "oauth_proxy")
    monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "s" * 32)
    validate_runtime_security("0.0.0.0")


def test_loopback_oauth_proxy_allows_ephemeral_signing_key(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "oauth_proxy")
    monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)
    validate_runtime_security("127.0.0.1")


def test_validate_secret_accepts_multibyte_minimum():
    validate_secret("TEST_SECRET", "🔐" * 8)
