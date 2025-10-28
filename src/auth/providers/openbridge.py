"""OpenBridge authentication provider.

This module implements the OpenBridge authentication provider,
which manages multiple Amazon Ads identities through OpenBridge's
remote identity service.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import httpx
import jwt
from pydantic import ValidationError

from ...models import AuthCredentials, Identity, OpenbridgeTokenResponse, Token
from ...utils.http import get_http_client
from .base import BaseAuthProvider, ProviderConfig
from ..registry import register_provider

logger = logging.getLogger(__name__)


@register_provider("openbridge")
class OpenBridgeProvider(BaseAuthProvider):
    """OpenBridge authentication provider.

    Provides authentication and identity management through the OpenBridge
    platform, handling JWT token conversion and remote identity access.
    """

    def __init__(self, config: ProviderConfig):
        """Initialize OpenBridge provider.

        :param config: Provider configuration
        :type config: ProviderConfig
        """
        self.refresh_token = config.get("refresh_token")

        self._region = config.get("region", "na")

        # OpenBridge API endpoints - configurable via config or env
        self.auth_base_url = config.get("auth_base_url") or os.environ.get(
            "OPENBRIDGE_AUTH_BASE_URL",
            "https://authentication.api.openbridge.io",
        )
        self.identity_base_url = config.get(
            "identity_base_url"
        ) or os.environ.get(
            "OPENBRIDGE_IDENTITY_BASE_URL",
            "https://remote-identity.api.openbridge.io",
        )
        self.service_base_url = config.get(
            "service_base_url"
        ) or os.environ.get(
            "OPENBRIDGE_SERVICE_BASE_URL", "https://service.api.openbridge.io"
        )

        self._jwt_token: Optional[Token] = None
        self._identities_cache: Dict[tuple, List[Identity]] = {}

    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return "openbridge"

    @property
    def region(self) -> str:
        """Get the current region."""
        return self._region

    async def initialize(self) -> None:
        """Initialize the provider."""
        logger.info("Initializing OpenBridge provider")
        # Could validate the refresh token here if needed

    async def _get_client(self) -> httpx.AsyncClient:
        """Get shared HTTP client."""
        return await get_http_client()

    async def get_token(self) -> Token:
        """Get current JWT token from OpenBridge."""
        if self._jwt_token and await self.validate_token(self._jwt_token):
            return self._jwt_token

        return await self._refresh_jwt_token()

    async def _refresh_jwt_token(self) -> Token:
        """Convert refresh token to JWT via OpenBridge."""
        logger.debug("Converting OpenBridge refresh token to JWT")

        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.auth_base_url}/auth/api/ref",
                json={
                    "data": {
                        "type": "APIAuth",
                        "attributes": {"refresh_token": self.refresh_token},
                    }
                },
                headers={"Content-Type": "application/json"},
            )

            if response.status_code not in [200, 202]:
                response.raise_for_status()

            data = response.json()
            token_value = (
                data.get("data", {}).get("attributes", {}).get("token")
            )

            if not token_value:
                raise ValueError("No token in OpenBridge response")

            # Parse the JWT to get expiration
            payload = jwt.decode(
                token_value, options={"verify_signature": False}
            )
            expires_at_timestamp = payload.get("expires_at", 0)
            expires_at = datetime.fromtimestamp(expires_at_timestamp, tz=timezone.utc)

            self._jwt_token = Token(
                access_token=token_value,
                expires_at=expires_at,
                token_type="Bearer",
                base_url=self.auth_base_url,
                metadata={
                    "user_id": payload.get("user_id"),
                    "account_id": payload.get("account_id"),
                },
            )

            logger.debug(f"OpenBridge JWT obtained, expires at {expires_at}")
            return self._jwt_token

        except httpx.HTTPError as e:
            logger.error(f"Failed to get OpenBridge JWT: {e}")
            raise
        except Exception as e:
            logger.error(f"Error processing OpenBridge token: {e}")
            raise

    async def validate_token(self, token: Token) -> bool:
        """Validate if token is still valid."""
        buffer = timedelta(minutes=5)
        now = datetime.now(timezone.utc)
        expiry = token.expires_at
        # Ensure both datetimes are timezone-aware for comparison
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        return now < (expiry - buffer)

    async def list_identities(self, **kwargs) -> List[Identity]:
        """List all remote identities from OpenBridge.

        :param kwargs: Optional filters (identity_type, page_size)
        :return: List of identities
        """
        identity_type = kwargs.get(
            "identity_type", "14"
        )  # Default to Amazon Ads
        page_size = kwargs.get("page_size", 100)

        cache_key = (identity_type, page_size)
        if cache_key in self._identities_cache:
            logger.debug(f"Using cached identities for {cache_key}")
            return self._identities_cache[cache_key]

        identities = await self._fetch_identities(identity_type, page_size)

        # Simple cache management
        if len(self._identities_cache) >= 32:
            oldest_key = next(iter(self._identities_cache))
            del self._identities_cache[oldest_key]

        self._identities_cache[cache_key] = identities
        return identities

    async def _fetch_identities(
        self, identity_type: str, page_size: int
    ) -> List[Identity]:
        """Fetch identities from OpenBridge API."""
        logger.info(
            f"Fetching remote identities from OpenBridge (type={identity_type})"
        )

        jwt_token = await self.get_token()
        client = await self._get_client()
        identities = []
        page = 1
        has_more = True

        try:
            while has_more:
                logger.debug(f"Fetching page {page} of identities")
                params = {"page": page, "page_size": page_size}

                if identity_type:
                    params["remote_identity_type"] = identity_type

                response = await client.get(
                    f"{self.identity_base_url}/sri",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {jwt_token.value}",
                        "x-api-key": self.refresh_token,
                    },
                    timeout=httpx.Timeout(30.0, connect=10.0),
                )
                response.raise_for_status()

                data = response.json()
                items = data.get("data", [])
                logger.debug(f"Page {page} has {len(items)} items")

                for item in items:
                    try:
                        identity = Identity(**item)
                        identities.append(identity)
                    except ValidationError as e:
                        logger.warning(f"Failed to parse identity: {e}")
                        continue

                # Check for more pages
                links = data.get("links", {})
                has_more = bool(links.get("next"))

                if not items:
                    logger.debug(
                        f"No items on page {page}, stopping pagination"
                    )
                    has_more = False

                page += 1

                if page > 100:
                    logger.warning("Reached maximum page limit (100)")
                    break

            logger.info(f"Found {len(identities)} remote identities")
            return identities

        except httpx.HTTPError as e:
            logger.error(f"Failed to list identities: {e}")
            raise

    async def get_identity(self, identity_id: str) -> Optional[Identity]:
        """Get specific identity by ID."""
        identities = await self.list_identities()
        for identity in identities:
            if identity.id == identity_id:
                return identity
        return None

    async def get_identity_credentials(
        self, identity_id: str
    ) -> AuthCredentials:
        """Get Amazon Ads credentials for specific identity."""
        logger.info(f"Getting credentials for identity {identity_id}")

        identity = await self.get_identity(identity_id)
        if not identity:
            raise ValueError(f"Identity {identity_id} not found")

        jwt_token = await self.get_token()
        client = await self._get_client()

        try:
            response = await client.get(
                f"{self.service_base_url}/service/amzadv/token/{identity_id}",
                headers={
                    "Authorization": f"Bearer {jwt_token.value}",
                    "x-api-key": self.refresh_token,
                },
            )
            response.raise_for_status()

            data = response.json()
            token_data = OpenbridgeTokenResponse(data=data.get("data", {}))
            amazon_ads_token = token_data.get_token()
            client_id = token_data.get_client_id()

            if not amazon_ads_token:
                raise ValueError("No Amazon Ads token in response")

            # Handle client ID fallback
            if not client_id or client_id.lower() == "openbridge":
                env_client_id = os.getenv("AMAZON_ADS_CLIENT_ID")
                if env_client_id:
                    logger.info(
                        "Using client ID from AMAZON_ADS_CLIENT_ID env var"
                    )
                    client_id = env_client_id
                else:
                    raise ValueError(
                        "No valid client ID from OpenBridge and AMAZON_ADS_CLIENT_ID not set"
                    )

            expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

            return AuthCredentials(
                identity_id=identity_id,
                access_token=amazon_ads_token,
                expires_at=expires_at,
                base_url=self.get_region_endpoint(),
                headers={
                    "Authorization": f"Bearer {amazon_ads_token}",
                    "Amazon-Advertising-API-ClientId": client_id,
                },
            )

        except httpx.HTTPError as e:
            logger.error(f"Failed to get identity credentials: {e}")
            raise

    async def get_headers(self) -> Dict[str, str]:
        """Get authentication headers.
        """
        token = await self.get_token()
        return {"Authorization": f"Bearer {token.access_token}"}
        

    async def close(self) -> None:
        """Clean up provider resources."""
        self._identities_cache.clear()
        self._jwt_token = None
