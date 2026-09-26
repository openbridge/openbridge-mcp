from types import SimpleNamespace

import pytest

from src.auth.authentication import AuthConfig, create_openbridge_config
from src.auth.simple import (
    AuthenticationError,
    OpenbridgeAuth,
    TokenCache,
    _InMemoryLRUTokenCache,
    is_refresh_token,
)


FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiJ9."
    "eyJ1c2VyX2lkIjoxLCJhY2NvdW50X2lkIjoyLCJleHBpcmVzX2F0IjozMDAwfQ."
    "sig"
)


def test_default_config_enables_native_auth(monkeypatch):
    monkeypatch.delenv("AUTH_ENABLED", raising=False)
    monkeypatch.delenv("OPENBRIDGE_AUTH_MODE", raising=False)

    config = create_openbridge_config()

    assert config == AuthConfig(enabled=True, auth_mode="refresh_token")
    assert not hasattr(config, "require_client_auth")


@pytest.mark.parametrize("value", ["false", "False", "0", "off"])
def test_auth_disabled_by_env_var(monkeypatch, value):
    monkeypatch.setenv("AUTH_ENABLED", value)
    assert create_openbridge_config().enabled is False


def test_deprecated_client_auth_flag_does_not_change_config(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("OPENBRIDGE_REQUIRE_CLIENT_AUTH", "false")

    assert create_openbridge_config() == AuthConfig(
        enabled=True,
        auth_mode="refresh_token",
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("abc123456:def789012", True),
        ("a:b", False),
        (FAKE_JWT, False),
        ("", False),
        (None, False),
        ("some-opaque-token-without-colon", False),
    ],
)
def test_api_credential_classifier(token, expected):
    assert is_refresh_token(token) is expected


def test_exchange_token_caches_result(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    calls = 0

    def fake_post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"data": {"attributes": {"token": FAKE_JWT}}},
        )

    monkeypatch.setattr("src.auth.simple.requests.post", fake_post)
    monkeypatch.setattr(
        "src.auth.simple.jwt.decode",
        lambda token, options: {"expires_at": 9999999999},
    )
    auth = OpenbridgeAuth()

    assert auth.exchange_token("client:refresh") == FAKE_JWT
    assert auth.exchange_token("client:refresh") == FAKE_JWT
    assert calls == 1


def test_openbridge_auth_constructs_without_server_credential(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)
    assert OpenbridgeAuth().refresh_token is None


def test_get_jwt_requires_server_credential(monkeypatch):
    monkeypatch.delenv("OPENBRIDGE_REFRESH_TOKEN", raising=False)

    with pytest.raises(AuthenticationError, match="not available"):
        OpenbridgeAuth().get_jwt()


def test_client_cache_is_bounded_and_conforms_to_protocol(monkeypatch):
    monkeypatch.setenv("OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES", "2")
    auth = OpenbridgeAuth()
    monkeypatch.setattr(
        OpenbridgeAuth,
        "_do_exchange",
        lambda self, token: f"jwt-{token}",
    )
    monkeypatch.setattr(
        "src.auth.simple.jwt.decode",
        lambda token, options: {"expires_at": 9999999999},
    )

    for credential in ("account1:secret", "account2:secret", "account3:secret"):
        auth.exchange_token(credential)

    assert isinstance(auth._client_cache, TokenCache)
    assert isinstance(auth._client_cache, _InMemoryLRUTokenCache)
    assert len(auth._client_cache) == 2


def test_exchange_network_failure_is_authentication_error(monkeypatch):
    monkeypatch.setattr(
        "src.auth.simple.requests.post",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("network")),
    )

    with pytest.raises(AuthenticationError):
        OpenbridgeAuth().exchange_token("account1:secret")
