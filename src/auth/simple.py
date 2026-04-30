"""Minimal Openbridge authentication helper."""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Iterator, Optional, Protocol, Tuple, runtime_checkable

import jwt
import requests

DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = 30

# Default upper bound on cached client refresh-token → JWT mappings.
# Sized for real multi-tenant deployment: with hourly-expiring JWTs,
# 256 active tenants per process keeps re-exchange traffic low without
# unbounded memory growth. Override via OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES.
DEFAULT_CLIENT_CACHE_MAX_ENTRIES = 256

logger = logging.getLogger(__name__)


class AuthenticationError(RuntimeError):
    """Raised when Openbridge authentication fails."""


def _parse_read_timeout() -> int:
    """Parse OPENBRIDGE_API_TIMEOUT once at import, falling back on bad input.

    A malformed value must not raise on every HTTP call — boot-time config
    errors should degrade gracefully to the default rather than causing
    500s across every tool.
    """
    raw = os.getenv("OPENBRIDGE_API_TIMEOUT")
    if raw is None:
        return DEFAULT_READ_TIMEOUT
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "OPENBRIDGE_API_TIMEOUT=%r is not an integer; falling back to default %d",
            raw,
            DEFAULT_READ_TIMEOUT,
        )
        return DEFAULT_READ_TIMEOUT


_CACHED_READ_TIMEOUT: int = _parse_read_timeout()


def get_api_timeout() -> Tuple[int, int]:
    """Return the (connect, read) timeout tuple for Openbridge HTTP calls.

    Values are parsed once at import; see ``_parse_read_timeout``.
    """
    return DEFAULT_CONNECT_TIMEOUT, _CACHED_READ_TIMEOUT


def is_refresh_token(token: str) -> bool:
    """Return True if *token* looks like an Openbridge refresh token.

    Openbridge refresh tokens use the ``xxx:yyy`` format and are not valid
    JWTs.  A quick heuristic: the token contains a colon, is long enough to
    be real, and does *not* have the three-dot-separated segments of a JWT.
    """
    if not token or len(token) < 10:
        return False
    if ":" in token and token.count(".") != 2:
        return True
    return False


@dataclass
class _CachedToken:
    token: str
    expires: float

    def is_valid(self) -> bool:
        # Keep a five-minute buffer to avoid using an about-to-expire token.
        return time.time() < (self.expires - 300)


def _parse_cache_cap() -> int:
    """Resolve ``OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES`` once at import.

    Bad values fall back to the default rather than raising — boot-time
    config errors should degrade gracefully. Values <1 are clamped up to
    1 (a cache of zero entries would defeat the purpose entirely).
    """
    raw = os.getenv("OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES")
    if raw is None:
        return DEFAULT_CLIENT_CACHE_MAX_ENTRIES
    try:
        parsed = int(raw)
    except ValueError:
        logger.warning(
            "OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES=%r is not an integer; falling back to %d",
            raw,
            DEFAULT_CLIENT_CACHE_MAX_ENTRIES,
        )
        return DEFAULT_CLIENT_CACHE_MAX_ENTRIES
    if parsed < 1:
        logger.warning(
            "OPENBRIDGE_TOKEN_CACHE_MAX_ENTRIES=%d is too small; clamping to 1",
            parsed,
        )
        return 1
    return parsed


@runtime_checkable
class TokenCache(Protocol):
    """Pluggable interface for caching refresh-token → JWT mappings.

    The default implementation (:class:`_InMemoryLRUTokenCache`) is
    process-local. A multi-instance production deployment will benefit
    from a shared backend (e.g. Redis) so tenant cache hits survive
    across replicas. A conforming implementation must:

    * **Be safe under concurrent access** — Openbridge tool calls run
      across asyncio tasks within a process; in a Redis adapter, that
      becomes "concurrent across replicas" too. ``get``/``set`` should
      either be atomic or at minimum not corrupt the underlying store.
    * **Honor the ``_CachedToken.expires`` boundary** — callers re-check
      ``is_valid()`` after read, so stale entries are tolerated; but the
      backend should still prefer to expire entries server-side if it
      can (e.g. Redis ``EXPIREAT``) to avoid unbounded growth.
    * **Be bounded** — either by an entry cap (in-memory LRU) or by
      external eviction policy (Redis ``maxmemory-policy``).
    * **Never log refresh-token values** — they are credentials. Eviction
      and miss logging must use only the cap or counters, not keys.

    To install a custom backend at runtime, subclass ``OpenbridgeAuth``
    and override ``__init__`` to assign ``self._client_cache`` to your
    implementation. The :func:`get_auth` singleton can then be replaced
    in process bootstrap if needed.
    """

    def get(self, key: str) -> Optional[_CachedToken]: ...

    def set(self, key: str, value: _CachedToken) -> None: ...

    def __contains__(self, key: object) -> bool: ...

    def __len__(self) -> int: ...

    def __iter__(self) -> Iterator[str]: ...


class _InMemoryLRUTokenCache:
    """Process-local LRU cache of refresh-token → JWT mappings.

    Reference implementation of :class:`TokenCache`. Replaces the
    previous FIFO-eviction dict. Important properties:

    * **LRU semantics**: ``get`` and ``set`` move the entry to the
      most-recently-used end. When the cap is exceeded, the
      least-recently-used entry is evicted via ``popitem(last=False)``.
      This keeps the most-active tenants resident even when the cache
      is under churn from low-frequency callers.
    * **Bounded**: capped at *max_entries* (default
      :data:`DEFAULT_CLIENT_CACHE_MAX_ENTRIES`), env-tunable.
    * **Process-local**: a multi-instance deployment will see independent
      caches per process. Swap in a Redis-backed
      :class:`TokenCache` implementation when shared cache hits across
      replicas matter.
    """

    def __init__(self, max_entries: int = DEFAULT_CLIENT_CACHE_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._data: OrderedDict[str, _CachedToken] = OrderedDict()

    @property
    def max_entries(self) -> int:
        return self._max_entries

    def get(self, key: str) -> Optional[_CachedToken]:
        item = self._data.get(key)
        if item is None:
            return None
        # Touch: a successful read marks this entry MRU.
        self._data.move_to_end(key)
        return item

    def set(self, key: str, value: _CachedToken) -> None:
        if key in self._data:
            # Update-in-place must also count as a touch.
            self._data.move_to_end(key)
        self._data[key] = value
        # Prune from the LRU end. Use a loop in case the cap was lowered
        # at runtime (defensive — current code does not lower it).
        while len(self._data) > self._max_entries:
            evicted_key, _ = self._data.popitem(last=False)
            logger.debug(
                "Token cache LRU evicted: cap=%d", self._max_entries,
            )
            del evicted_key  # not logged; PII-like

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)


class OpenbridgeAuth:
    """Convert an Openbridge refresh token into a short-lived JWT."""

    def __init__(self) -> None:
        self.refresh_token: Optional[str] = os.getenv("OPENBRIDGE_REFRESH_TOKEN")
        self.auth_base_url = os.getenv(
            "OPENBRIDGE_AUTH_BASE_URL",
            "https://authentication.api.openbridge.io",
        )
        self._cache: Optional[_CachedToken] = None
        # Per-token cache for client-provided refresh tokens. Typed as
        # the TokenCache Protocol so a subclass / bootstrap shim can
        # replace it with a Redis-backed adapter without changing the
        # call sites in exchange_token.
        self._client_cache: TokenCache = _InMemoryLRUTokenCache(
            max_entries=_parse_cache_cap(),
        )

    def get_jwt(self) -> str:
        """Return a cached JWT, refreshing when needed."""
        if not self.refresh_token:
            raise AuthenticationError(
                "OPENBRIDGE_REFRESH_TOKEN not available for JWT generation"
            )
        if self._cache and self._cache.is_valid():
            return self._cache.token
        return self._refresh()

    def exchange_token(self, refresh_token: str) -> str:
        """Exchange an arbitrary refresh token for a JWT.

        This is the client-side auth path: the caller provides a refresh
        token (typically from an ``Authorization: Bearer xxx:yyy`` header)
        and the server exchanges it for a short-lived JWT via the Openbridge
        auth API.  Results are cached per refresh-token value so repeated
        tool calls within the same session do not re-exchange.

        Caching is LRU-bounded (see :class:`_InMemoryLRUTokenCache`); a
        successful cache hit also marks the entry most-recently-used so
        active tenants stay resident under load.
        """
        cached = self._client_cache.get(refresh_token)
        if cached and cached.is_valid():
            logger.debug("Using cached JWT for client refresh token")
            return cached.token

        logger.info("Exchanging client refresh token for JWT")
        jwt_token = self._do_exchange(refresh_token)

        decoded = jwt.decode(jwt_token, options={"verify_signature": False})
        expires_at = float(
            decoded.get("expires_at") or decoded.get("exp") or (time.time() + 3600)
        )
        # The cache enforces the LRU cap internally; no manual eviction.
        self._client_cache.set(
            refresh_token,
            _CachedToken(token=jwt_token, expires=expires_at),
        )

        return jwt_token

    def _refresh(self) -> str:
        """Exchange the server's refresh token for a JWT."""
        jwt_token = self._do_exchange(self.refresh_token)

        decoded = jwt.decode(jwt_token, options={"verify_signature": False})
        expires_at = float(
            decoded.get("expires_at") or decoded.get("exp") or (time.time() + 3600)
        )

        self._cache = _CachedToken(token=jwt_token, expires=expires_at)
        return jwt_token

    def _do_exchange(self, refresh_token: str) -> str:
        """Exchange a refresh token for a JWT via the Openbridge auth API."""
        try:
            response = requests.post(
                f"{self.auth_base_url}/auth/api/ref",
                json={
                    "data": {
                        "type": "APIAuth",
                        "attributes": {"refresh_token": refresh_token},
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

        return jwt_token

    def get_headers(self) -> dict[str, str]:
        """Return Authorization headers for Openbridge API calls."""
        return {"Authorization": f"Bearer {self.get_jwt()}"}


_AUTH_INSTANCE: Optional[OpenbridgeAuth] = None


def get_auth() -> OpenbridgeAuth:
    """Return the shared ``OpenbridgeAuth`` singleton."""
    global _AUTH_INSTANCE
    if _AUTH_INSTANCE is None:
        _AUTH_INSTANCE = OpenbridgeAuth()
    return _AUTH_INSTANCE
