"""FastMCP-native validation for direct Openbridge credentials."""

from __future__ import annotations

import hashlib
import logging
import os

from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.auth.providers.introspection import IntrospectionTokenVerifier

from .exchange_coordinator import TokenExchangeCoordinator
from .simple import OpenbridgeAuth, get_auth, is_refresh_token

logger = logging.getLogger(__name__)


def normalize_verified_identity(access_token: AccessToken) -> AccessToken | None:
    """Return a copy with a stable FastMCP task identity, if claims allow it."""
    account_id = str(access_token.claims.get("account_id") or "").strip()
    user_id = str(access_token.claims.get("user_id") or "").strip()
    if not account_id or not user_id:
        return None

    subject = f"account:{account_id}|user:{user_id}"
    expires_at = access_token.claims.get("expires_at") or access_token.expires_at
    return access_token.model_copy(
        update={
            "client_id": "openbridge",
            "subject": subject,
            "expires_at": int(expires_at) if expires_at else None,
            "claims": {**access_token.claims, "sub": subject},
        }
    )


def credential_cache_key(token: str) -> str:
    """Return an opaque exchange-coordinator key for an API credential."""
    return "credential:" + hashlib.sha256(token.encode()).hexdigest()


class OpenbridgeCredentialVerifier(TokenVerifier):
    """Exchange API credentials and introspect every resulting bearer token."""

    def __init__(
        self,
        *,
        auth: OpenbridgeAuth,
        introspection: IntrospectionTokenVerifier,
        exchange_coordinator: TokenExchangeCoordinator | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(base_url=base_url)
        self._auth = auth
        self._introspection = introspection
        self._exchange_coordinator = (
            exchange_coordinator or TokenExchangeCoordinator()
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """Validate a direct JWT or exchange and validate an API credential."""
        candidate = token
        if is_refresh_token(token):
            try:
                candidate = await self._exchange_coordinator.resolve(
                    credential_cache_key(token),
                    lambda: self._auth.exchange_token(token),
                )
            except Exception as exc:
                logger.warning(
                    "Openbridge API credential exchange failed (%s)",
                    type(exc).__name__,
                )
                return None

        try:
            verified = await self._introspection.verify_token(candidate)
        except Exception:
            logger.warning("Openbridge credential validation failed")
            return None

        if verified is None:
            return None
        return normalize_verified_identity(verified)


def create_openbridge_introspection_verifier(
    *, base_url: str | None = None
) -> IntrospectionTokenVerifier:
    """Create the bounded Openbridge introspection delegate."""
    library_logger = logging.getLogger(
        "fastmcp.server.auth.providers.introspection"
    )
    if library_logger.getEffectiveLevel() < logging.INFO:
        library_logger.setLevel(logging.INFO)

    auth_base_url = os.getenv(
        "OPENBRIDGE_AUTH_BASE_URL",
        "https://authentication.api.openbridge.io",
    ).rstrip("/")
    return IntrospectionTokenVerifier(
        introspection_url=f"{auth_base_url}/auth/oauth/introspect",
        client_id=os.getenv("OPENBRIDGE_OAUTH_CLIENT_ID", "openbridge-mcp"),
        client_secret=os.getenv("OPENBRIDGE_OAUTH_CLIENT_SECRET", "not-used"),
        client_auth_method="client_secret_post",
        cache_ttl_seconds=30,
        max_cache_size=256,
        base_url=base_url,
    )


def create_openbridge_credential_verifier(
    *,
    base_url: str | None = None,
    introspection: IntrospectionTokenVerifier | None = None,
) -> OpenbridgeCredentialVerifier:
    """Create the native verifier for direct Openbridge credentials."""
    delegate = introspection or create_openbridge_introspection_verifier(
        base_url=base_url
    )
    return OpenbridgeCredentialVerifier(
        auth=get_auth(),
        introspection=delegate,
        base_url=base_url,
    )
