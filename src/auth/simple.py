"""Minimal Openbridge authentication helper."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import jwt
import requests

DEFAULT_CONNECT_TIMEOUT = 10


class AuthenticationError(RuntimeError):
    """Raised when Openbridge authentication fails."""


def get_api_timeout() -> Tuple[int, int]:
    """Return the (connect, read) timeout tuple for Openbridge HTTP calls."""
    read_timeout = int(os.getenv("OPENBRIDGE_API_TIMEOUT", "30"))
    return DEFAULT_CONNECT_TIMEOUT, read_timeout


@dataclass
class _CachedToken:
    token: str
    expires: float

    def is_valid(self) -> bool:
        # Keep a five-minute buffer to avoid using an about-to-expire token.
        return time.time() < (self.expires - 300)


class OpenbridgeAuth:
    """Convert an Openbridge refresh token into a short-lived JWT."""

    def __init__(self) -> None:
        self.refresh_token: Optional[str] = os.getenv("OPENBRIDGE_REFRESH_TOKEN")
        self.auth_base_url = os.getenv(
            "OPENBRIDGE_AUTH_BASE_URL",
            "https://authentication.api.openbridge.io",
        )
        self._cache: Optional[_CachedToken] = None

    def get_jwt(self) -> str:
        """Return a cached JWT, refreshing when needed."""
        if not self.refresh_token:
            raise AuthenticationError(
                "OPENBRIDGE_REFRESH_TOKEN not available for JWT generation"
            )
        if self._cache and self._cache.is_valid():
            return self._cache.token
        return self._refresh()

    def _refresh(self) -> str:
        """Exchange the refresh token for a JWT."""
        try:
            response = requests.post(
                f"{self.auth_base_url}/auth/api/ref",
                json={
                    "data": {
                        "type": "APIAuth",
                        "attributes": {"refresh_token": self.refresh_token},
                    }
                },
                timeout=get_api_timeout(),
                headers={"Content-Type": "application/json"},
            )
        except Exception as exc:
            raise AuthenticationError("Openbridge auth request failed") from exc

        try:
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            raise AuthenticationError(
                f"Failed to convert refresh token to JWT: {exc}"
            ) from exc

        try:
            jwt_token = payload["data"]["attributes"]["token"]
        except KeyError as exc:
            raise AuthenticationError(
                "Openbridge auth response did not include a token"
            ) from exc

        decoded = jwt.decode(jwt_token, options={"verify_signature": False})
        expires_at = float(
            decoded.get("expires_at") or decoded.get("exp") or (time.time() + 3600)
        )

        self._cache = _CachedToken(token=jwt_token, expires=expires_at)
        return jwt_token

    def get_headers(self) -> Dict[str, str]:
        """Return Authorization headers for Openbridge API calls."""
        return {"Authorization": f"Bearer {self.get_jwt()}"}


_AUTH_INSTANCE: Optional[OpenbridgeAuth] = None


def get_auth() -> OpenbridgeAuth:
    """Return the shared ``OpenbridgeAuth`` singleton."""
    global _AUTH_INSTANCE
    if _AUTH_INSTANCE is None:
        _AUTH_INSTANCE = OpenbridgeAuth()
    return _AUTH_INSTANCE
