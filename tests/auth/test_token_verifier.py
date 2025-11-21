import jwt
import pytest
from starlette.middleware.authentication import AuthenticationMiddleware

from mcp.server.auth.middleware.auth_context import AuthContextMiddleware

from src.auth.token_verifier import OpenbridgeTokenProvider, create_token_middleware


class DummyAuth:
    def __init__(self, jwt_value: str):
        self.jwt_value = jwt_value
        self.called_with = None

    async def get_jwt_async(self, refresh_token=None):
        self.called_with = refresh_token
        return self.jwt_value


@pytest.mark.asyncio
async def test_token_provider_accepts_jwt_without_exchange():
    payload = {"sub": "client-123", "scope": "read write", "exp": 10_000}
    jwt_token = jwt.encode(payload, "secret", algorithm="HS256")
    provider = OpenbridgeTokenProvider(auth=DummyAuth(jwt_token))

    access_token = await provider.verify_token(jwt_token)

    assert access_token is not None
    assert access_token.token == jwt_token
    assert access_token.client_id == "client-123"
    assert set(access_token.scopes) == {"read", "write"}


@pytest.mark.asyncio
async def test_token_provider_exchanges_refresh_token():
    payload = {"sub": "client-123", "scope": ["reader"], "exp": 10_000}
    jwt_token = jwt.encode(payload, "secret", algorithm="HS256")
    auth = DummyAuth(jwt_token)
    provider = OpenbridgeTokenProvider(auth=auth)

    access_token = await provider.verify_token("client:refresh")

    assert access_token is not None
    assert auth.called_with == "client:refresh"
    assert access_token.token == jwt_token


@pytest.mark.asyncio
async def test_token_provider_rejects_invalid_jwt():
    provider = OpenbridgeTokenProvider(auth=DummyAuth("ignored"))

    invalid_token = "not.a.jwt"
    result = await provider.verify_token(invalid_token)

    assert result is None


def test_create_token_middleware_stack(monkeypatch):
    dummy_auth = DummyAuth("token")

    def fake_get_auth():
        return dummy_auth

    monkeypatch.setattr("src.auth.token_verifier.get_auth", fake_get_auth)

    middleware = create_token_middleware()

    assert len(middleware) == 2
    assert middleware[0].cls is AuthenticationMiddleware
    assert middleware[1].cls is AuthContextMiddleware
