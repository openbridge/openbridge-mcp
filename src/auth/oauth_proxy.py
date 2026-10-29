"""OAuthProxy authentication support for Openbridge MCP.

Implements the ``oauth_proxy`` auth mode where FastMCP handles the full OAuth
2.0 authorization code flow, proxies to Openbridge's OAuth endpoints, verifies
tokens via introspection, and supplies the native request access-token context.

Usage (set in environment):
    OPENBRIDGE_AUTH_MODE=oauth_proxy
    MCP_BASE_URL=https://your-mcp-server.example.com
    MCP_JWT_SIGNING_KEY=<stable-secret>   # optional but recommended
    OPENBRIDGE_OAUTH_STORAGE_URL=redis://openbridge-mcp-redis:6379/0
        # optional: shared encrypted OAuth state for multi-replica
        # deployments. Unset = per-pod encrypted file store (fastmcp default).
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import TYPE_CHECKING, Iterable, Optional

from fastmcp.server.auth import AccessToken, MultiAuth, OAuthProxy, TokenVerifier
from .openbridge_verifier import (
    create_openbridge_credential_verifier,
    create_openbridge_introspection_verifier,
    normalize_verified_identity,
)

if TYPE_CHECKING:
    from key_value.aio.protocols.key_value import AsyncKeyValue

logger = logging.getLogger(__name__)

_DEFAULT_AUTH_BASE_URL = "https://authentication.api.openbridge.io"
_DEFAULT_VALID_SCOPES = ["openid", "profile"]


class OpenbridgeOAuthProxy(OAuthProxy):
    """OAuth proxy that assigns every verified token an isolated task identity."""

    async def load_access_token(self, token: str) -> AccessToken | None:
        """Load the upstream token and normalize its FastMCP task identity."""
        verified = await super().load_access_token(token)
        if verified is None:
            return None

        normalized = normalize_verified_identity(verified)
        if normalized is not None:
            return normalized

        digest = hashlib.sha256(token.encode()).hexdigest()
        subject = f"oauth-session:{digest}"
        return verified.model_copy(
            update={
                "client_id": "openbridge-oauth",
                "subject": subject,
                "claims": {**verified.claims, "sub": subject},
            }
        )


def _derive_storage_encryption_key(jwt_signing_key: str) -> bytes:
    """Derive the OAuth state encryption key exactly as fastmcp's default store.

    Mirrors ``OAuthProxy.__init__`` in fastmcp 4.0.10
    (``server/auth/oauth_proxy/proxy.py``): a str signing key is first
    resolved via ``derive_jwt_key(low_entropy_material=...,
    salt="fastmcp-jwt-signing-key")``, then the storage key is derived from
    those bytes with ``salt="fastmcp-storage-encryption-key"``.

    Because ``MCP_JWT_SIGNING_KEY`` is shared across pods, every replica
    derives the same Fernet key and can read each other's shared state.
    Rotating the signing key invalidates shared state simultaneously —
    decryption failures are treated as cache misses, forcing clients to
    re-register. Re-verify this chain on any fastmcp upgrade (<5 pin).
    """
    from fastmcp.server.auth.jwt_issuer import derive_jwt_key

    resolved_signing_key = derive_jwt_key(
        low_entropy_material=jwt_signing_key,
        salt="fastmcp-jwt-signing-key",
    )
    return derive_jwt_key(
        high_entropy_material=resolved_signing_key.decode(),
        salt="fastmcp-storage-encryption-key",
    )


def _build_redis_client_storage(
    *, storage_url: str, jwt_signing_key: str
) -> AsyncKeyValue:
    """Return a shared, encrypted-at-rest Redis store for OAuth state.

    The connection is lazy: constructing the store never touches the
    network, so a Redis outage surfaces as OAuth 5xx on first use rather
    than at startup — fail loudly, never silently fall back to per-pod
    state (that would reintroduce the multi-replica bug under failure).
    """
    from cryptography.fernet import Fernet
    from key_value.aio.stores.redis import RedisStore
    from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

    return FernetEncryptionWrapper(
        key_value=RedisStore(url=storage_url),
        fernet=Fernet(key=_derive_storage_encryption_key(jwt_signing_key)),
        # Undecryptable entries (e.g. after MCP_JWT_SIGNING_KEY rotation)
        # are cache misses → clients re-register, matching fastmcp's own
        # default-store behavior.
        raise_on_decryption_error=False,
    )


_STORAGE_URL_SCHEMES = {"redis", "rediss", "unix"}


def _validate_storage_url(storage_url: str) -> None:
    """Reject storage URLs that would silently target the wrong host.

    py-key-value-aio falls back to ``localhost`` when a redis:// URL
    carries no host, so a typo'd, scheme-less, or whitespace-only value
    must fail loudly at startup instead of surfacing as first-use 5xx
    against the wrong host.
    """
    from urllib.parse import urlparse

    if not storage_url.strip():
        raise ValueError(
            "OPENBRIDGE_OAUTH_STORAGE_URL is set but empty/whitespace-only: "
            "unset it to use the per-pod default store, or set a "
            "redis://host:port/db URL."
        )

    parsed = urlparse(storage_url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _STORAGE_URL_SCHEMES:
        raise ValueError(
            "OPENBRIDGE_OAUTH_STORAGE_URL must use redis://, rediss://, or "
            f"unix:// (got {storage_url!r})."
        )
    if scheme in {"redis", "rediss"} and not parsed.hostname:
        raise ValueError(
            "OPENBRIDGE_OAUTH_STORAGE_URL must include a host, e.g. "
            f"redis://openbridge-mcp-redis:6379/0 (got {storage_url!r})."
        )


def _sanitize_storage_url(storage_url: str) -> str:
    """Return the URL without userinfo (user/password), safe for logs."""
    from urllib.parse import urlparse

    parsed = urlparse(storage_url)
    host = parsed.hostname or ""
    netloc = f"{host}:{parsed.port}" if parsed.port else host
    return f"{parsed.scheme}://{netloc}{parsed.path}"


def create_oauth_proxy(
    *,
    base_url: str,
    token_verifier: TokenVerifier | None = None,
) -> OpenbridgeOAuthProxy:
    """Return an OAuthProxy configured for Openbridge's OAuth endpoints.

    Reads all configuration from environment variables (documented in
    CLAUDE.md under 'Authentication (OAuth Proxy Mode)').

    Args:
        base_url: Externally-reachable base URL of this MCP server.  Used by
            FastMCP to construct the OAuth redirect URI.  Example:
            ``"http://127.0.0.1:8000"`` or ``"https://mcp.example.com"``.

    Returns:
        A configured :class:`OAuthProxy` ready to pass to ``FastMCP(auth=...)``.
    """
    auth_base_url = os.getenv("OPENBRIDGE_AUTH_BASE_URL", _DEFAULT_AUTH_BASE_URL)

    signing_key: Optional[str] = os.getenv("MCP_JWT_SIGNING_KEY")
    if signing_key:
        # Whitespace-only keys must not count as configured: they would
        # deterministically key the shared store with blank material.
        signing_key = signing_key.strip()
    signing_key_is_ephemeral = not signing_key
    if not signing_key:
        # A random key per process means MCP sessions break on restart.
        # Set MCP_JWT_SIGNING_KEY to a stable secret for production.
        signing_key = str(uuid.uuid4())
        logger.warning(
            "MCP_JWT_SIGNING_KEY is not set; using a random signing key. "
            "MCP sessions will not survive server restarts. "
            "Set MCP_JWT_SIGNING_KEY to a stable secret for production deployments."
        )

    client_id = os.getenv("OPENBRIDGE_OAUTH_CLIENT_ID", "openbridge-mcp")
    client_secret = os.getenv("OPENBRIDGE_OAUTH_CLIENT_SECRET", "not-used")

    # Shared OAuth state (multi-replica deployments). Unset/empty →
    # fastmcp's per-pod encrypted file store (current behavior, local dev
    # unchanged).
    storage_url = os.getenv("OPENBRIDGE_OAUTH_STORAGE_URL")
    client_storage: AsyncKeyValue | None = None
    if storage_url:
        storage_url = storage_url.strip()
        _validate_storage_url(storage_url)
        if signing_key_is_ephemeral:
            raise ValueError(
                "OPENBRIDGE_OAUTH_STORAGE_URL requires MCP_JWT_SIGNING_KEY: "
                "without a stable signing key every pod derives a different "
                "storage encryption key and cannot read each other's shared "
                "OAuth state in Redis. Set MCP_JWT_SIGNING_KEY (e.g. openssl "
                "rand -hex 32) or unset OPENBRIDGE_OAUTH_STORAGE_URL."
            )
        if len(signing_key.encode("utf-8")) < 32:  # noqa: PLR2004
            raise ValueError(
                "OPENBRIDGE_OAUTH_STORAGE_URL requires an MCP_JWT_SIGNING_KEY "
                "of at least 32 bytes (e.g. openssl rand -hex 32): the key is "
                "the sole at-rest protection for every OAuth token stored in "
                "shared Redis."
            )
        from fastmcp import settings as fastmcp_settings

        if fastmcp_settings.test_mode:
            raise ValueError(
                "OPENBRIDGE_OAUTH_STORAGE_URL cannot be combined with "
                "FASTMCP_TEST_MODE: fastmcp test mode weakens key derivation "
                "(10 PBKDF2 iterations instead of 1,000,000), so this pod "
                "would derive a different storage key than the rest of the "
                "fleet and silently break cross-pod OAuth."
            )
        client_storage = _build_redis_client_storage(
            storage_url=storage_url,
            jwt_signing_key=signing_key,
        )
        logger.info(
            "OAuth state: shared encrypted Redis store (%s)",
            _sanitize_storage_url(storage_url),
        )
    else:
        logger.info(
            "OAuth state: per-pod encrypted file store "
            "(OPENBRIDGE_OAUTH_STORAGE_URL unset)"
        )

    verifier = token_verifier or create_openbridge_introspection_verifier(
        base_url=base_url
    )

    logger.info("Creating OAuthProxy with upstream authorization endpoint: %s/auth/oauth/initialize and base URL: %s", auth_base_url, base_url)
    return OpenbridgeOAuthProxy(
        upstream_authorization_endpoint=f"{auth_base_url}/auth/oauth/initialize",
        upstream_token_endpoint=f"{auth_base_url}/auth/oauth/token",
        # Openbridge reads the upstream client_id from embedded secrets;
        # an empty string is the correct value here.
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        jwt_signing_key=signing_key,
        token_verifier=verifier,
        base_url=base_url,
        valid_scopes=_DEFAULT_VALID_SCOPES,
        # Openbridge's /auth/oauth/initialize only forwards redirect_uri and
        # state to Auth0 — PKCE parameters must not be forwarded.
        forward_pkce=False,
        token_endpoint_auth_method="client_secret_post",
        fallback_access_token_expiry_seconds=3600,
        # Skip the local FastMCP consent page (the
        # `mcp.openbridge.com/consent?txn_id=...` interstitial) and let
        # Auth0 own consent. Removes one hop from the OAuth redirect
        # chain and eliminates the orphaned-tab quirk on the local
        # consent screen specifically. Auth0's own consent screen still
        # has the same post-Allow redirect dynamics — that's a
        # FastMCP/OAuth wire-protocol issue, not solvable here. "external"
        # vs `False` matters: the former skips the screen quietly; the
        # latter logs a "only use for local development" warning at
        # boot, which would mislead operators.
        require_authorization_consent="external",
        client_storage=client_storage,  # None → fastmcp per-pod file default
    )


def create_oauth_auth(*, base_url: str) -> MultiAuth:
    """Compose browser OAuth and direct Openbridge credentials."""
    introspection = create_openbridge_introspection_verifier(base_url=base_url)
    return MultiAuth(
        server=create_oauth_proxy(
            base_url=base_url,
            token_verifier=introspection,
        ),
        verifiers=[
            create_openbridge_credential_verifier(
                base_url=base_url,
                introspection=introspection,
            )
        ],
        base_url=base_url,
    )


__all__: Iterable[str] = [
    "OpenbridgeOAuthProxy",
    "create_oauth_auth",
    "create_oauth_proxy",
]
