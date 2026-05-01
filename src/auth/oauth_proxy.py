"""OAuthProxy authentication support for Openbridge MCP.

Implements the ``oauth_proxy`` auth mode where FastMCP handles the full OAuth
2.0 authorization code flow.  FastMCP proxies to Openbridge's OAuth endpoints
and verifies tokens via introspection.  A lightweight bridge middleware then
writes the already-verified access token into the per-request ContextVar so
all existing tools work without changes.

Usage (set in environment):
    OPENBRIDGE_AUTH_MODE=oauth_proxy
    MCP_BASE_URL=https://your-mcp-server.example.com
    MCP_JWT_SIGNING_KEY=<stable-secret>   # optional but recommended
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Iterable, Optional

from fastmcp.server.auth import OAuthProxy
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from .authentication import (
    JWT_CONTEXT_ATTR,
    JWT_PUBLIC_ATTR,
    _log_jwt_identity,
    _set_context_state,
)
from .session_state import set_request_jwt

logger = logging.getLogger(__name__)

_DEFAULT_AUTH_BASE_URL = "https://authentication.api.openbridge.io"
_DEFAULT_VALID_SCOPES = ["openid", "profile"]


def create_oauth_proxy(*, base_url: str) -> OAuthProxy:
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

    # Expected for introspection.
    # Expose env vars so operators can override if the endpoint changes.
    client_id = os.getenv("OPENBRIDGE_OAUTH_CLIENT_ID", "openbridge-mcp")
    client_secret = os.getenv("OPENBRIDGE_OAUTH_CLIENT_SECRET", "not-used")

    logger.info("Configuring OAuthProxy with introspection endpoint: %s/auth/oauth/introspect", auth_base_url)
    token_verifier = IntrospectionTokenVerifier(
        introspection_url=f"{auth_base_url}/auth/oauth/introspect",
        client_id=client_id,
        client_secret=client_secret,
        client_auth_method="client_secret_post",
    )

    logger.info("Creating OAuthProxy with upstream authorization endpoint: %s/auth/oauth/initialize and base URL: %s", auth_base_url, base_url)
    return OAuthProxy(
        upstream_authorization_endpoint=f"{auth_base_url}/auth/oauth/initialize",
        upstream_token_endpoint=f"{auth_base_url}/auth/oauth/token",
        # Openbridge reads the upstream client_id from embedded secrets;
        # an empty string is the correct value here.
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        jwt_signing_key=signing_key,
        token_verifier=token_verifier,
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


class OAuthBridgeMiddleware(Middleware):
    """Bridge between FastMCP's OAuthProxy and the ContextVar-based token store.

    When FastMCP is configured with ``OAuthProxy``, it verifies the Bearer
    token via introspection *before* middleware runs.  The verified access
    token is then available via ``get_access_token()``.  This middleware reads
    that token and writes it into ``session_state._jwt_var`` via
    ``set_request_jwt()``, so all existing tools that call
    ``get_auth_headers()`` continue to work without modification.

    In ``oauth_proxy`` mode, ``access_token.token`` is the raw Openbridge JWT
    returned by the upstream ``/auth/oauth/token`` endpoint — directly usable
    for downstream Openbridge API calls.
    """

    async def on_request(self, context: MiddlewareContext, call_next):
        if not context.fastmcp_context:
            return await call_next(context)

        access_token = get_access_token()
        if access_token is not None:
            jwt_token = access_token.token
            _log_jwt_identity(jwt_token)
            set_request_jwt(jwt_token)
            await _set_context_state(context.fastmcp_context, JWT_CONTEXT_ATTR, jwt_token)
            await _set_context_state(context.fastmcp_context, JWT_PUBLIC_ATTR, jwt_token)
            logger.debug(
                "OAuthBridgeMiddleware: primed JWT from OAuth access token "
                "(scopes=%s)",
                getattr(access_token, "scopes", None),
            )
        else:
            set_request_jwt(None)
            logger.debug("OAuthBridgeMiddleware: no OAuth access token present")

        return await call_next(context)


__all__: Iterable[str] = [
    "OAuthBridgeMiddleware",
    "create_oauth_proxy",
]
