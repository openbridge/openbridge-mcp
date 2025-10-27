"""Enhanced HTTP client for Amazon Ads API authentication.

This module provides an authenticated HTTP client that manages headers
for Amazon Advertising API calls.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from src.auth.manager import AuthManager
from src.config.settings import Settings
from src.utils.header_resolver import HeaderNameResolver
from src.utils.media import MediaTypeRegistry

logger = logging.getLogger(__name__)

# Global state for region/marketplace overrides (used by routing tools)
_REGION_OVERRIDE: Optional[str] = None
_MARKETPLACE_OVERRIDE: Optional[str] = None
_ROUTING_STATE: Dict[str, Any] = {}


class AuthenticatedClient(httpx.AsyncClient):
    """Enhanced HTTP client that manages Openbridge API authentication headers.

    This client extends httpx.AsyncClient to provide automatic header management for
    Openbridge API calls. It handles header scrubbing, injection, and media
    type negotiation.

    Key Features:
        - Removes polluted headers from FastMCP
        - Injects correct Openbridge authentication headers
        - Supports profile-specific header rules

    :param auth_manager: Authentication manager for header generation
    :type auth_manager: Optional[AuthManager]
    :param media_registry: Registry for content type negotiation
    :type media_registry: Optional[MediaTypeRegistry]
    :param header_resolver: Resolver for header name variations
    :type header_resolver: Optional[HeaderNameResolver]
    :raises httpx.RequestError: When required auth headers are missing
    """

    # anything we consider "polluted" and must remove if present
    _FORBID_SUBSTRS = (
        "authorization",  # bearer from MCP client
    )

    def __init__(
        self,
        *args,
        auth_manager=None,
        media_registry=None,
        header_resolver=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.auth_manager = auth_manager
        self.media_registry: Optional[MediaTypeRegistry] = media_registry
        self.header_resolver: HeaderNameResolver = (
            header_resolver or HeaderNameResolver()
        )

    async def send(self, request: httpx.Request, **kwargs) -> httpx.Response:
        """Single interception point for all HTTP requests.

        This method is called for ALL requests, whether from:
        - Direct client.request() calls (which build a Request then call send)
        - FastMCP's OpenAPITool (which builds a Request then calls send)

        It handles:
            1. Media type negotiation based on OpenAPI specs
            2. Header scrubbing to remove polluted auth headers
            3. Injection of correct Amazon authentication headers
            4. Special handling for profiles API endpoints

        :param request: The HTTP request to send
        :type request: httpx.Request
        :param kwargs: Additional arguments to pass to the parent send method
        :type kwargs: Dict[str, Any]
        :return: The HTTP response
        :rtype: httpx.Response
        :raises httpx.RequestError: When required auth headers are missing
        """
        # Log incoming request for debugging
        logger.debug(f"=== SEND: {request.method} {request.url}")
        logger.debug(
            f"    Headers before injection: {list(request.headers.keys())}"
        )

        # Check if already processed (idempotent)
        if not request.extensions.get("auth_injected"):
            await self._inject_headers(request)
            request.extensions["auth_injected"] = True
            logger.debug(
                f"Headers injected for {request.method} {request.url}"
            )
            logger.debug(
                f"    Headers after injection: {list(request.headers.keys())}"
            )
            # Log critical headers for debugging
            logger.debug(
                f"    Accept: {request.headers.get('accept', 'NOT SET')}"
            )
            logger.debug(
                f"    Content-Type: {request.headers.get('content-type', 'NOT SET')}"
            )
            # Verify auth header is present
            if "authorization" in request.headers:
                auth_val = request.headers["authorization"]
                logger.debug(
                    f"    Authorization present: Bearer [{len(auth_val) - 7} chars]"
                )

        # Log the actual request headers right before sending
        logger.debug("=== ACTUAL REQUEST BEING SENT ===")
        logger.debug(f"URL: {request.url}")
        logger.debug("Headers:")
        for k, v in request.headers.items():
            if k.lower() in [
                "authorization",
            ]:
                logger.debug(f"  {k}: [REDACTED]")
            else:
                logger.debug(f"  {k}: {v}")

        # Call parent's send
        resp = await super().send(request, **kwargs)
        logger.debug(
            f"=== RESPONSE: {resp.status_code} for {request.method} {request.url}"
        )

        return resp

    def _truncate_lists(self, data: Any, n: int) -> Any:
        try:

            def walk(obj: Any) -> Any:
                if isinstance(obj, list):
                    return [walk(x) for x in obj[: max(0, n)]]
                if isinstance(obj, dict):
                    return {k: walk(v) for k, v in obj.items()}
                return obj

            return walk(data)
        except Exception:
            return data

    async def _inject_headers(self, request: httpx.Request) -> None:
        """Inject authentication and media headers into a request.

        Handles media negotiation, header scrubbing, auth header injection,
        and profiles endpoint special-casing.
        """
        method = request.method
        url = str(request.url)

        # 1) MEDIA NEGOTIATION
        if self.media_registry:
            content_type, accepts = self.media_registry.resolve(method, url)
            if content_type and method.lower() != "get":
                request.headers["Content-Type"] = content_type
            # Respect pre-existing Accept header from upstream transforms/tools
            if accepts and "Accept" not in request.headers:
                preferred = next(
                    (a for a in accepts if a.startswith("application/vnd.")),
                    accepts[0],
                )
                request.headers["Accept"] = preferred

        # 2) STRIP POLLUTED HEADERS
        removed = []
        for key in list(request.headers.keys()):
            if any(s in key.lower() for s in self._FORBID_SUBSTRS):
                removed.append(key)
                del request.headers[key]
        if removed:
            logger.debug("🧹 Scrubbed headers from request: %s", removed)

        # 3) ADD CORRECT AUTH HEADERS (OB API calls)
        u_host = urlparse(url).netloc.lower()
        if "openbridge.io" in u_host:
            headers = await self.auth_manager.get_headers()
            request.headers['Authorization'] = headers.get('Authorization', '')

        # Ensure Accept header for JSON responses
        if "Accept" not in request.headers:
            request.headers["Accept"] = "application/json"

def get_routing_state() -> Dict[str, Any]:
    """Get the current routing state."""
    return _ROUTING_STATE
