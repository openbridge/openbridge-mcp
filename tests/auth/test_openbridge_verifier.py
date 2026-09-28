import logging
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastmcp.server.auth import AccessToken

from src.auth.openbridge_verifier import (
    OpenbridgeCredentialVerifier,
    create_openbridge_introspection_verifier,
    credential_cache_key,
    normalize_verified_identity,
)
from src.auth.simple import AuthenticationError


def _verified_token(**claims):
    return AccessToken(
        token="verified.jwt.token",
        client_id="unknown",
        scopes=[],
        claims={"active": True, "account_id": 101, "user_id": 202, **claims},
    )


@pytest.mark.asyncio
async def test_api_credential_is_exchanged_then_introspected():
    introspection = AsyncMock()
    introspection.verify_token.return_value = _verified_token()
    auth = MagicMock()
    auth.exchange_token.return_value = "verified.jwt.token"
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    result = await verifier.verify_token("account123:api-secret")

    assert result is not None
    assert result.token == "verified.jwt.token"
    assert result.client_id == "openbridge"
    assert result.subject == "account:101|user:202"
    assert result.claims["sub"] == "account:101|user:202"
    auth.exchange_token.assert_called_once_with("account123:api-secret")
    introspection.verify_token.assert_awaited_once_with("verified.jwt.token")


@pytest.mark.asyncio
async def test_direct_jwt_is_introspected_without_exchange():
    introspection = AsyncMock()
    introspection.verify_token.return_value = _verified_token()
    auth = MagicMock()
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    result = await verifier.verify_token("header.payload.signature")

    assert result is not None
    auth.exchange_token.assert_not_called()
    introspection.verify_token.assert_awaited_once_with("header.payload.signature")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token", "exchange_error"),
    [
        ("bogus-token", None),
        ("account123:bad-secret", AuthenticationError("rejected")),
        ("header.payload.bad", None),
    ],
)
async def test_invalid_credentials_return_none(token, exchange_error):
    introspection = AsyncMock()
    introspection.verify_token.return_value = None
    auth = MagicMock()
    if exchange_error is not None:
        auth.exchange_token.side_effect = exchange_error
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    assert await verifier.verify_token(token) is None


@pytest.mark.asyncio
async def test_exchanged_inactive_jwt_returns_none():
    introspection = AsyncMock()
    introspection.verify_token.return_value = None
    auth = MagicMock()
    auth.exchange_token.return_value = "inactive.jwt.token"
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    assert await verifier.verify_token("account123:api-secret") is None
    introspection.verify_token.assert_awaited_once_with("inactive.jwt.token")


@pytest.mark.parametrize(
    ("account_id", "user_id"),
    [
        (None, 202),
        (101, None),
        ("", 202),
        (101, ""),
        ("   ", 202),
        (101, "   "),
    ],
)
def test_normalize_rejects_missing_or_blank_identity(account_id, user_id):
    token = _verified_token(account_id=account_id, user_id=user_id)

    assert normalize_verified_identity(token) is None


def test_normalize_keeps_tenant_subjects_distinct():
    first = normalize_verified_identity(_verified_token(account_id=101, user_id=202))
    second = normalize_verified_identity(_verified_token(account_id=303, user_id=202))

    assert first is not None
    assert second is not None
    assert f"{first.client_id}|{first.claims['sub']}" != (
        f"{second.client_id}|{second.claims['sub']}"
    )


def test_normalize_copies_claim_expiry():
    result = normalize_verified_identity(_verified_token(expires_at="123456"))

    assert result is not None
    assert result.expires_at == 123456


def test_normalize_preserves_fastmcp_expiry_when_claim_is_absent():
    token = _verified_token()
    token = token.model_copy(update={"expires_at": 654321})

    result = normalize_verified_identity(token)

    assert result is not None
    assert result.expires_at == 654321


@pytest.mark.asyncio
async def test_exchange_decode_error_returns_none_without_secret_logs(caplog):
    introspection = AsyncMock()
    auth = MagicMock()
    auth.exchange_token.side_effect = jwt.DecodeError("returned-jwt-secret")
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    with caplog.at_level(logging.WARNING):
        result = await verifier.verify_token("account123:api-secret")

    assert result is None
    assert "DecodeError" in caplog.text
    assert "account123:api-secret" not in caplog.text
    assert "returned-jwt-secret" not in caplog.text
    introspection.verify_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_introspection_exception_returns_none_without_secret_logs(caplog):
    introspection = AsyncMock()
    introspection.verify_token.side_effect = RuntimeError("sensitive upstream body")
    auth = MagicMock()
    verifier = OpenbridgeCredentialVerifier(auth=auth, introspection=introspection)

    with caplog.at_level(logging.WARNING):
        result = await verifier.verify_token("header.payload.signature")

    assert result is None
    assert "credential validation failed" in caplog.text
    assert "header.payload.signature" not in caplog.text
    assert "sensitive upstream body" not in caplog.text


def test_credential_cache_key_is_digest_only():
    key = credential_cache_key("account123:api-secret")

    assert key.startswith("credential:")
    assert "account123:api-secret" not in key
    assert len(key) == len("credential:") + 64


def test_introspection_factory_configures_bounded_cache_and_safe_logging(
    monkeypatch,
):
    library_logger = logging.getLogger(
        "fastmcp.server.auth.providers.introspection"
    )
    previous_level = library_logger.level
    library_logger.setLevel(logging.DEBUG)
    monkeypatch.setenv("OPENBRIDGE_AUTH_BASE_URL", "https://auth.example.test/")
    try:
        verifier = create_openbridge_introspection_verifier(
            base_url="https://mcp.example.test"
        )

        assert verifier.introspection_url == (
            "https://auth.example.test/auth/oauth/introspect"
        )
        assert verifier.client_auth_method == "client_secret_post"
        assert verifier._cache._ttl == 30
        assert verifier._cache._max_size == 256
        assert library_logger.getEffectiveLevel() >= logging.INFO
    finally:
        library_logger.setLevel(previous_level)
