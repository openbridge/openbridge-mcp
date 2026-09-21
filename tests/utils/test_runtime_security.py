import logging

import pytest

import main
from src.utils.runtime_security import (
    env_flag,
    is_loopback_host,
    require_client_auth_enabled,
    validate_runtime_security,
)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.10"])
def test_remote_server_token_fallback_fails_startup(monkeypatch, host):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "account:secret")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "false")
    monkeypatch.delenv("OPENBRIDGE_ALLOW_INSECURE_REMOTE_AUTH", raising=False)

    with pytest.raises(RuntimeError, match="client authentication"):
        validate_runtime_security(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "::1", "[::1]"])
def test_loopback_explicit_fallback_is_allowed(monkeypatch, host):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REFRESH_TOKEN", "account:secret")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "false")
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


def test_client_auth_defaults_true(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", raising=False)
    assert require_client_auth_enabled() is True


def test_invalid_client_auth_flag_uses_safe_default(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "typo")
    assert require_client_auth_enabled() is True


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


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_accepts_truthy_values(monkeypatch, value):
    monkeypatch.setenv("TEST_RUNTIME_FLAG", value)
    assert env_flag("TEST_RUNTIME_FLAG", default=False) is True


@pytest.mark.parametrize(
    ("host", "expected"),
    [("localhost", True), ("127.0.0.2", True), ("::1", True), ("example.com", False)],
)
def test_is_loopback_host(host, expected):
    assert is_loopback_host(host) is expected
