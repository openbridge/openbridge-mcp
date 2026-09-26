"""OAuthProxy authentication support for Openbridge MCP.

Implements the ``oauth_proxy`` auth mode where FastMCP handles the full OAuth
2.0 authorization code flow, proxies to Openbridge's OAuth endpoints, verifies
tokens via introspection, and supplies the native request access-token context.

Usage (set in environment):
    OPENBRIDGE_AUTH_MODE=oauth_proxy
    MCP_BASE_URL=https://your-mcp-server.example.com
    MCP_JWT_SIGNING_KEY=<stable-secret>   # optional but recommended
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from typing import Iterable, Optional

from fastmcp.server.auth import AccessToken, MultiAuth, OAuthProxy, TokenVerifier
from .openbridge_verifier import (
    create_openbridge_credential_verifier,
    create_openbridge_introspection_verifier,
    normalize_verified_identity,
)

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
