"""Tests for native OAuthProxy authentication mode."""

import hashlib
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, MultiAuth, OAuthProxy
from fastmcp.server.dependencies import get_access_token

from src.auth.authentication import AuthConfig, create_openbridge_config
from src.auth.oauth_proxy import (
    OpenbridgeOAuthProxy,
    create_oauth_auth,
    create_oauth_proxy,
)
from src.auth.openbridge_verifier import OpenbridgeCredentialVerifier


# ---------------------------------------------------------------------------
# create_oauth_proxy
# ---------------------------------------------------------------------------


class TestCreateOAuthProxy:
    """Tests for create_oauth_proxy factory."""

    def test_returns_oauth_proxy_instance(self, monkeypatch):
        """Factory returns an OAuthProxy when env vars are set."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "test-signing-key-abc123")
        monkeypatch.setenv("OPENBRIDGE_AUTH_BASE_URL", "https://authentication.api.openbridge.io")

        from fastmcp.server.auth import OAuthProxy

        result = create_oauth_proxy(base_url="http://localhost:8000")
        assert isinstance(result, OAuthProxy)

    def test_warns_when_no_signing_key(self, monkeypatch, caplog):
        """A warning is emitted when MCP_JWT_SIGNING_KEY is not set."""
        monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)

        with caplog.at_level(logging.WARNING, logger="src.auth.oauth_proxy"):
            create_oauth_proxy(base_url="http://localhost:8000")

        assert any("MCP_JWT_SIGNING_KEY" in record.message for record in caplog.records)

    def test_no_warning_when_signing_key_set(self, monkeypatch, caplog):
        """No warning when MCP_JWT_SIGNING_KEY is configured."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")

        with caplog.at_level(logging.WARNING, logger="src.auth.oauth_proxy"):
            create_oauth_proxy(base_url="http://localhost:8000")

        assert not any("MCP_JWT_SIGNING_KEY" in record.message for record in caplog.records)

    def test_uses_custom_auth_base_url(self, monkeypatch):
        """Factory uses OPENBRIDGE_AUTH_BASE_URL when set."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "key")
        monkeypatch.setenv("OPENBRIDGE_AUTH_BASE_URL", "https://auth.custom.example.com")

        from fastmcp.server.auth import OAuthProxy

        result = create_oauth_proxy(base_url="http://localhost:8000")
        assert isinstance(result, OAuthProxy)

    def test_create_oauth_auth_composes_proxy_and_openbridge_verifier(
        self, monkeypatch
    ):
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")

        auth = create_oauth_auth(base_url="https://mcp.example.test")

        assert isinstance(auth, MultiAuth)
        assert isinstance(auth.server, OpenbridgeOAuthProxy)
        assert len(auth.verifiers) == 1
        assert isinstance(auth.verifiers[0], OpenbridgeCredentialVerifier)

    def test_multi_auth_preserves_oauth_routes(self, monkeypatch):
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")
        auth = create_oauth_auth(base_url="https://mcp.example.test")

        route_paths = {
            route.path
            for route in [*auth.get_routes(), *auth.get_well_known_routes()]
        }

        assert any("authorize" in path for path in route_paths)
        assert any("oauth-authorization-server" in path for path in route_paths)


@pytest.fixture
def oauth_proxy(monkeypatch):
    monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")
    return create_oauth_proxy(
        base_url="https://mcp.example.test",
        token_verifier=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_oauth_proxy_uses_verified_account_identity(oauth_proxy, monkeypatch):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        expires_at=123456,
        claims={"account_id": 101, "user_id": 202},
    )
    load_access_token = AsyncMock(return_value=verified)
    monkeypatch.setattr(OAuthProxy, "load_access_token", load_access_token)

    result = await oauth_proxy.load_access_token("fastmcp.reference.token")

    assert result is not None
    assert result.subject == "account:101|user:202"
    assert result.token == "upstream.jwt.token"
    assert result.expires_at == 123456


@pytest.mark.asyncio
async def test_oauth_proxy_falls_back_to_isolated_session(oauth_proxy, monkeypatch):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        expires_at=123456,
        claims={"active": True},
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=verified),
    )

    first = await oauth_proxy.load_access_token("fastmcp.reference.one")
    repeated = await oauth_proxy.load_access_token("fastmcp.reference.one")
    other = await oauth_proxy.load_access_token("fastmcp.reference.two")

    assert first is not None
    assert repeated is not None
    assert other is not None
    assert first.subject == repeated.subject
    assert first.subject == (
        "oauth-session:"
        + hashlib.sha256(b"fastmcp.reference.one").hexdigest()
    )
    assert first.subject != other.subject
    assert first.claims["sub"] == first.subject
    assert first.client_id == "openbridge-oauth"
    assert first.token == "upstream.jwt.token"
    assert first.expires_at == 123456


@pytest.mark.asyncio
async def test_oauth_proxy_preserves_none(oauth_proxy, monkeypatch):
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=None),
    )

    assert await oauth_proxy.load_access_token("fastmcp.reference.token") is None


@pytest.mark.asyncio
async def test_oauth_reference_token_is_not_logged(
    oauth_proxy, monkeypatch, caplog
):
    verified = AccessToken(
        token="upstream.jwt.token",
        client_id="unknown",
        scopes=[],
        claims={"active": True},
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(return_value=verified),
    )

    with caplog.at_level(logging.DEBUG):
        await oauth_proxy.load_access_token("fastmcp.reference.sensitive")

    assert "fastmcp.reference.sensitive" not in caplog.text


@pytest.mark.asyncio
async def test_oauth_reference_token_reaches_tool_with_upstream_jwt(
    monkeypatch,
):
    monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-signing-key-for-tests")
    proxy = create_oauth_proxy(base_url="http://localhost")
    auth = MultiAuth(server=proxy, verifiers=[], base_url="http://localhost")
    server = FastMCP("oauth-boundary-test", auth=auth)
    observed_tokens = []

    @server.tool(name="spy")
    async def spy() -> str:
        access_token = get_access_token()
        observed_tokens.append(access_token.token if access_token else None)
        return "called"

    app = server.http_app(stateless_http=True)
    reference_token = proxy.jwt_issuer.issue_access_token(
        client_id="test-client",
        scopes=["openid"],
        jti="test-reference-jti",
    )
    monkeypatch.setattr(
        OAuthProxy,
        "load_access_token",
        AsyncMock(
            return_value=AccessToken(
                token="verified.upstream.jwt",
                client_id="upstream-client",
                scopes=["openid"],
                claims={"active": True},
            )
        ),
    )
    transport = httpx.ASGITransport(app=app)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://localhost",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Authorization": f"Bearer {reference_token}"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "spy", "arguments": {}},
                },
            )

    assert response.status_code == 200
    assert observed_tokens == ["verified.upstream.jwt"]


# ---------------------------------------------------------------------------
# AuthConfig auth_mode field
# ---------------------------------------------------------------------------


class TestAuthConfigMode:
    """Tests for OPENBRIDGE_AUTH_MODE env var parsing."""

    def test_auth_mode_defaults_to_refresh_token(self, monkeypatch):
        """Without OPENBRIDGE_AUTH_MODE, mode is 'refresh_token'."""
        monkeypatch.delenv("OPENBRIDGE_AUTH_MODE", raising=False)
        config = create_openbridge_config()
        assert config.auth_mode == "refresh_token"

    def test_auth_mode_oauth_proxy_from_env(self, monkeypatch):
        """OPENBRIDGE_AUTH_MODE=oauth_proxy sets auth_mode to 'oauth_proxy'."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "oauth_proxy")
        config = create_openbridge_config()
        assert config.auth_mode == "oauth_proxy"

    def test_auth_mode_refresh_token_explicit(self, monkeypatch):
        """OPENBRIDGE_AUTH_MODE=refresh_token sets auth_mode to 'refresh_token'."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "refresh_token")
        config = create_openbridge_config()
        assert config.auth_mode == "refresh_token"

    def test_auth_mode_invalid_falls_back(self, monkeypatch, caplog):
        """Unrecognized OPENBRIDGE_AUTH_MODE falls back to 'refresh_token' with a warning."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "magic_tokens")

        with caplog.at_level(logging.WARNING, logger="src.auth.authentication"):
            config = create_openbridge_config()

        assert config.auth_mode == "refresh_token"
        assert any("OPENBRIDGE_AUTH_MODE" in record.message for record in caplog.records)

    def test_auth_mode_case_insensitive(self, monkeypatch):
        """OPENBRIDGE_AUTH_MODE is normalized to lowercase before matching."""
        monkeypatch.setenv("OPENBRIDGE_AUTH_MODE", "OAuth_Proxy")
        config = create_openbridge_config()
        assert config.auth_mode == "oauth_proxy"

    def test_auth_mode_field_on_authconfig(self):
        """AuthConfig dataclass exposes auth_mode with default 'refresh_token'."""
        config = AuthConfig()
        assert config.auth_mode == "refresh_token"

    def test_auth_mode_field_settable(self):
        """AuthConfig.auth_mode can be set directly."""
        config = AuthConfig(auth_mode="oauth_proxy")
        assert config.auth_mode == "oauth_proxy"


# ---------------------------------------------------------------------------
# Redis-backed client_storage (multi-replica OAuth state)
# ---------------------------------------------------------------------------


class TestRedisClientStorage:
    """Tests for OPENBRIDGE_OAUTH_STORAGE_URL shared-state wiring."""

    # ≥32 bytes: the shared-storage guard requires production-grade keys.
    STABLE_KEY = "stable-signing-key-0123456789abcdef0123456789abcdef"

    def test_storage_url_unset_keeps_default_file_store(self, monkeypatch):
        """Unset OPENBRIDGE_OAUTH_STORAGE_URL → fastmcp's per-pod file default."""
        from key_value.aio.stores.filetree import FileTreeStore
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        monkeypatch.delenv("OPENBRIDGE_OAUTH_STORAGE_URL", raising=False)
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")

        proxy = create_oauth_proxy(base_url="https://mcp.example.test")

        assert isinstance(proxy._client_storage, FernetEncryptionWrapper)
        assert isinstance(proxy._client_storage.key_value, FileTreeStore)

    def test_storage_url_set_builds_redis_wrapped_storage(self, monkeypatch):
        """Set OPENBRIDGE_OAUTH_STORAGE_URL → encrypted RedisStore passed to the proxy."""
        from key_value.aio.stores.redis import RedisStore
        from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", self.STABLE_KEY)
        monkeypatch.setenv(
            "OPENBRIDGE_OAUTH_STORAGE_URL", "redis://openbridge-mcp-redis:6379/0"
        )

        proxy = create_oauth_proxy(base_url="https://mcp.example.test")

        storage = proxy._client_storage
        assert isinstance(storage, FernetEncryptionWrapper)
        assert isinstance(storage.key_value, RedisStore)
        # URL wiring: lazy client constructed from the configured URL.
        conn = storage.key_value._client.connection_pool.connection_kwargs
        assert conn["host"] == "openbridge-mcp-redis"
        assert conn["port"] == 6379
        assert conn["db"] == 0
        # Rotation safety: undecryptable entries are cache misses, not crashes.
        assert storage.raise_on_decryption_error is False

    def test_storage_url_requires_stable_signing_key(self, monkeypatch):
        """Storage URL set without MCP_JWT_SIGNING_KEY fails loudly at startup.

        An ephemeral per-process key would give every pod a different storage
        encryption key — silently reintroducing the cross-pod OAuth failure.
        """
        monkeypatch.delenv("MCP_JWT_SIGNING_KEY", raising=False)
        monkeypatch.setenv(
            "OPENBRIDGE_OAUTH_STORAGE_URL", "redis://openbridge-mcp-redis:6379/0"
        )

        with pytest.raises(ValueError, match="MCP_JWT_SIGNING_KEY"):
            create_oauth_proxy(base_url="https://mcp.example.test")

    def test_derivation_matches_fastmcp_default_store(self, monkeypatch):
        """Our derivation must decrypt fastmcp's own default-store ciphertext.

        Builds a REAL default OAuthProxy (storage URL unset) and proves its
        Fernet key equals ours: Fernet is authenticated, so successfully
        decrypting fastmcp's ciphertext with our derived key is proof of key
        identity — not a restatement of the implementation. This is the
        regression tripwire for fastmcp changing its default derivation
        inside the <5 range: the change fails here in CI at upgrade time
        instead of silently forking shared state across pod generations at
        runtime.
        """
        from cryptography.fernet import Fernet

        from src.auth.oauth_proxy import _derive_storage_encryption_key

        signing_key = self.STABLE_KEY
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", signing_key)
        monkeypatch.delenv("OPENBRIDGE_OAUTH_STORAGE_URL", raising=False)

        default_proxy = create_oauth_proxy(base_url="https://mcp.example.test")
        fastmcp_ciphertext = default_proxy._client_storage._encryption_fn(
            b"parity payload"
        )

        ours = Fernet(key=_derive_storage_encryption_key(signing_key))

        assert ours.decrypt(fastmcp_ciphertext) == b"parity payload"

    def test_derivation_deterministic_across_signing_keys(self):
        """Same signing key → identical key on every pod; different key → different."""
        from src.auth.oauth_proxy import _derive_storage_encryption_key

        first = _derive_storage_encryption_key("stable-key-material-0123456789")
        second = _derive_storage_encryption_key("stable-key-material-0123456789")
        other = _derive_storage_encryption_key("a-completely-different-key")

        assert first == second
        assert first != other

    def test_cross_wrapper_decrypts_each_other(self):
        """Two independently built stores with the same key interoperate.

        Unit-level stand-in for cross-pod behavior: state encrypted by one
        store (one pod) decrypts cleanly in another (another pod).
        """
        from src.auth.oauth_proxy import _build_redis_client_storage

        url = "redis://openbridge-mcp-redis:6379/0"
        signing_key = "stable-key-material-0123456789"
        pod_a = _build_redis_client_storage(storage_url=url, jwt_signing_key=signing_key)
        pod_b = _build_redis_client_storage(storage_url=url, jwt_signing_key=signing_key)

        ciphertext = pod_a._encryption_fn(b"cross-pod oauth payload")

        # encryption_version: public attribute of the wrapper (currently 1) —
        # the envelope version the decrypt closure expects.
        assert (
            pod_b._decryption_fn(ciphertext, pod_b.encryption_version)
            == b"cross-pod oauth payload"
        )

    def test_rotation_decrypt_failure_is_cache_miss(self):
        """State written under an old key reads as a miss, never an exception.

        Behavioral check of the raise_on_decryption_error=False contract the
        rotation story relies on (MCP_JWT_SIGNING_KEY rotation → fleet-wide
        re-registration, not OAuth 500s): the OLD store's envelope is valid
        (positive control below) but undecryptable by the NEW store.
        """
        from src.auth.oauth_proxy import _build_redis_client_storage

        url = "redis://openbridge-mcp-redis:6379/0"
        old = _build_redis_client_storage(
            storage_url=url,
            jwt_signing_key="old-stable-signing-key-0123456789abcdef",
        )
        new = _build_redis_client_storage(
            storage_url=url,
            jwt_signing_key="new-stable-signing-key-987654321fedcba",
        )

        envelope = old._encrypt_value({"client_id": "written-before-rotation"})

        # Positive control: the envelope decrypts fine under its own key.
        assert old._decrypt_value(envelope) == {
            "client_id": "written-before-rotation"
        }
        # Under the rotated key it is a clean miss (None), not a raise.
        assert new._decrypt_value(envelope) is None

    def test_whitespace_storage_url_rejected(self, monkeypatch):
        """A whitespace-only URL fails loudly instead of hitting localhost."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", self.STABLE_KEY)
        monkeypatch.setenv("OPENBRIDGE_OAUTH_STORAGE_URL", "   ")

        with pytest.raises(ValueError, match="whitespace"):
            create_oauth_proxy(base_url="https://mcp.example.test")

    @pytest.mark.parametrize(
        "bad_url",
        [
            "openbridge-mcp-redis:6379/0",  # no scheme
            "http://openbridge-mcp-redis:6379/0",  # wrong scheme
        ],
    )
    def test_storage_url_scheme_validated(self, monkeypatch, bad_url):
        """Only redis://, rediss://, and unix:// schemes are accepted."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", self.STABLE_KEY)
        monkeypatch.setenv("OPENBRIDGE_OAUTH_STORAGE_URL", bad_url)

        with pytest.raises(ValueError, match="redis"):  # noqa: PT012
            create_oauth_proxy(base_url="https://mcp.example.test")

    def test_storage_url_requires_host(self, monkeypatch):
        """A host-less redis:// URL fails loudly (py-key-value-aio would
        silently fall back to localhost)."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", self.STABLE_KEY)
        monkeypatch.setenv("OPENBRIDGE_OAUTH_STORAGE_URL", "redis://:6379/0")

        with pytest.raises(ValueError, match="host"):  # noqa: PT012
            create_oauth_proxy(base_url="https://mcp.example.test")

    def test_storage_url_short_signing_key_rejected(self, monkeypatch):
        """Shared storage requires ≥32 bytes of signing-key material."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "stable-key")  # 10 bytes
        monkeypatch.setenv(
            "OPENBRIDGE_OAUTH_STORAGE_URL", "redis://openbridge-mcp-redis:6379/0"
        )

        with pytest.raises(ValueError, match="32 bytes"):
            create_oauth_proxy(base_url="https://mcp.example.test")

    def test_whitespace_signing_key_treated_as_ephemeral(self, monkeypatch):
        """A whitespace-only signing key counts as unset, triggering the
        shared-storage guard."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", "   ")
        monkeypatch.setenv(
            "OPENBRIDGE_OAUTH_STORAGE_URL", "redis://openbridge-mcp-redis:6379/0"
        )

        with pytest.raises(ValueError, match="MCP_JWT_SIGNING_KEY"):
            create_oauth_proxy(base_url="https://mcp.example.test")

    def test_storage_url_password_not_logged(self, monkeypatch, caplog):
        """URL credentials never reach the log stream."""
        monkeypatch.setenv("MCP_JWT_SIGNING_KEY", self.STABLE_KEY)
        monkeypatch.setenv(
            "OPENBRIDGE_OAUTH_STORAGE_URL",
            "redis://:s3cret@openbridge-mcp-redis:6379/0",
        )

        with caplog.at_level(logging.INFO, logger="src.auth.oauth_proxy"):
            create_oauth_proxy(base_url="https://mcp.example.test")

        logged = " ".join(r.getMessage() for r in caplog.records)
        assert "s3cret" not in logged
        assert "openbridge-mcp-redis:6379/0" in logged
