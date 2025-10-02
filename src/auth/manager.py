"""Central authentication management for Amazon Ads MCP.

This module provides centralized authentication management using the
pluggable provider architecture. It handles identity management,
credential caching, and provider coordination for seamless API access.

The module supports:
- Multiple authentication providers (direct, OpenBridge, etc.)
- Identity-based credential management
- Automatic token refresh and caching
- Profile scope management
- Region-specific endpoint handling
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from ..config.settings import Settings
from ..models import AuthCredentials, Identity, Token
from .token_store import TokenStore, create_token_store, TokenKey, TokenKind, TokenEntry

# Import providers to trigger registration
from . import providers  # This imports all providers and registers them
from .providers.base import BaseAuthProvider, BaseIdentityProvider, ProviderConfig
from .registry import ProviderRegistry

logger = logging.getLogger(__name__)


class AuthManager:
    """Central manager for authentication and identity management.

    This manager uses the provider registry to dynamically load
    authentication providers based on configuration. It implements
    a singleton pattern to ensure consistent state across the
    application.

    The manager handles:
    - Provider initialization and configuration
    - Identity management and switching
    - Credential caching and refresh
    - Profile scope management
    - Region-specific endpoint handling
    """

    _instance: Optional["AuthManager"] = None

    def __new__(cls):
        """Create or return singleton instance.

        Implements singleton pattern to ensure single auth manager
        instance across the application.

        :return: Singleton instance of AuthManager
        :rtype: AuthManager
        """
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """Initialize the authentication manager.

        Sets up the manager with settings, provider configuration,
        and initial state. Only initializes once due to singleton
        pattern.

        :return: None
        :rtype: None
        """
        if hasattr(self, "_initialized"):
            return

        self._initialized = True
        self.settings = Settings()
        self.provider: Optional[BaseAuthProvider] = None
        self._active_identity: Optional[Identity] = None
        self._active_credentials: Optional[AuthCredentials] = None
        
        # Initialize unified token store - persistence enabled by default
        # Users can disable with AMAZON_ADS_TOKEN_PERSIST=false if needed
        persist_tokens = os.getenv("AMAZON_ADS_TOKEN_PERSIST", "true").lower() != "false"
        self._token_store: TokenStore = create_token_store(persist=persist_tokens)

        # Initialize provider based on settings
        self._setup_provider()

    def _setup_provider(self):
        """Setup authentication provider using the registry.

        This method determines which provider to use based on configuration
        and creates it using the provider registry.

        :return: None
        :rtype: None
        :raises ValueError: If no provider can be initialized
        """
        # Determine auth method
        auth_method = self._determine_auth_method()

        # Build provider config based on method
        config = self._build_provider_config(auth_method)

        # Create provider from registry
        try:
            self.provider = ProviderRegistry.create_provider(
                auth_method, config
            )
            logger.info(f"Initialized {auth_method} authentication provider")

            # Set default identity for providers that have one
            if auth_method == "direct":
                self._default_identity_id = "direct-auth"
            elif (
                auth_method == "openbridge"
                and self.settings.openbridge_remote_identity_id
            ):
                self._default_identity_id = (
                    self.settings.openbridge_remote_identity_id
                )

        except ValueError as e:
            # Provide helpful error message
            available = list(ProviderRegistry.list_providers().keys())
            raise ValueError(
                f"Failed to initialize auth provider '{auth_method}': {e}\n"
                f"Available providers: {', '.join(available)}\n"
                f"Make sure you have the required configuration for your chosen provider."
            )

    def _determine_auth_method(self) -> str:
        """Determine which authentication method to use based on available config.

        Auto-detects the authentication method from environment variables
        or uses the explicitly configured method.

        :return: Authentication method name (e.g., 'direct', 'openbridge')
        :rtype: str
        :raises ValueError: If no authentication method is configured
        """
        # Check if explicitly set
        if self.settings.auth_method:
            return self.settings.auth_method

        # Auto-detect based on available credentials
        if all(
            [
                self.settings.effective_client_id,
                self.settings.effective_client_secret,
                self.settings.effective_refresh_token,
            ]
        ):
            logger.info("Auto-detected direct authentication from environment")
            return "direct"

        if self.settings.openbridge_refresh_token:
            logger.info(
                "Auto-detected OpenBridge authentication from environment"
            )
            return "openbridge"

        # Check for other provider configs here as needed
        # For example, check for AUTH0_DOMAIN, OKTA_DOMAIN, etc.

        raise ValueError(
            "No authentication method configured. Please set one of:\n"
            "- For direct auth: AD_API_CLIENT_ID, AD_API_CLIENT_SECRET, AD_API_REFRESH_TOKEN\n"
            "- For OpenBridge: OPENBRIDGE_REFRESH_TOKEN\n"
            "- Or explicitly set AUTH_METHOD environment variable"
        )

    def _build_provider_config(self, auth_method: str) -> ProviderConfig:
        """Build configuration for the specified authentication provider.

        Creates a ProviderConfig instance with the appropriate
        configuration data based on the authentication method.

        :param auth_method: Authentication method to configure
        :type auth_method: str
        :return: Provider configuration object
        :rtype: ProviderConfig
        """
        config_data = {}

        if auth_method == "direct":
            config_data = {
                "client_id": self.settings.effective_client_id,
                "client_secret": self.settings.effective_client_secret,
                "refresh_token": self.settings.effective_refresh_token,
                "profile_id": self.settings.effective_profile_id,
                "region": self.settings.amazon_ads_region,
            }

        elif auth_method == "openbridge":
            config_data = {
                "refresh_token": self.settings.openbridge_refresh_token,
                "region": self.settings.amazon_ads_region,
                "auth_base_url": os.getenv("OPENBRIDGE_AUTH_BASE_URL"),
                "identity_base_url": os.getenv("OPENBRIDGE_IDENTITY_BASE_URL"),
                "service_base_url": os.getenv("OPENBRIDGE_SERVICE_BASE_URL"),
            }

        # Add more provider configs here as needed
        # elif auth_method == "auth0":
        #     config_data = {
        #         "domain": os.getenv("AUTH0_DOMAIN"),
        #         "client_id": os.getenv("AUTH0_CLIENT_ID"),
        #         ...
        #     }
    
        return ProviderConfig(**config_data)

    async def initialize_provider(self) -> None:
        """Initialize the authentication provider.

        Performs any asynchronous initialization required by the
        configured provider.

        :return: None
        :rtype: None
        """
        if self.provider:
            await self.provider.initialize()

    # Identity management methods (for providers that support multiple identities)

    async def list_identities(self, **kwargs) -> List[Identity]:
        """List all available identities.

        Retrieves a list of all available identities from the
        configured provider. For providers that don't support
        multiple identities, returns a synthetic default identity.

        :param **kwargs: Provider-specific filter parameters
        :type **kwargs: Any
        :return: List of available identities
        :rtype: List[Identity]
        :raises RuntimeError: If no auth provider is configured
        """
        if not self.provider:
            raise RuntimeError("No auth provider configured")

        if not isinstance(self.provider, BaseIdentityProvider):
            # Provider doesn't support multiple identities
            # Return a synthetic single identity
            return [
                Identity(
                    id="default",
                    type=self.provider.provider_type,
                    attributes={
                        "name": f"Default {self.provider.provider_type} identity"
                    },
                )
            ]

        return await self.provider.list_identities(**kwargs)

    async def get_identity(self, identity_id: str) -> Optional[Identity]:
        """Get a specific identity by ID.

        Retrieves a specific identity from the configured provider.
        For providers that don't support multiple identities,
        returns the default identity if the ID matches.

        :param identity_id: Unique identifier for the identity
        :type identity_id: str
        :return: Identity if found, None otherwise
        :rtype: Optional[Identity]
        :raises RuntimeError: If no auth provider is configured
        """
        if not self.provider:
            raise RuntimeError("No auth provider configured")

        if not isinstance(self.provider, BaseIdentityProvider):
            # Return synthetic identity if it matches
            if identity_id == "default":
                identities = await self.list_identities()
                return identities[0] if identities else None
            return None

        return await self.provider.get_identity(identity_id)

    async def set_active_identity(self, identity_id: str) -> Identity:
        """Set the active identity for API operations.

        Sets the specified identity as the active identity for
        subsequent API operations. Clears cached credentials
        for the previous identity.

        :param identity_id: ID of the identity to activate
        :type identity_id: str
        :return: The activated identity
        :rtype: Identity
        :raises ValueError: If the specified identity is not found
        """
        logger.info(f"Setting active identity: {identity_id}")

        identity = await self.get_identity(identity_id)
        if not identity:
            raise ValueError(f"Identity {identity_id} not found")

        self._active_identity = identity

        # Clear cached credentials for previous identity
        if (
            self._active_credentials
            and self._active_credentials.identity_id != identity_id
        ):
            self._active_credentials = None

        logger.info(f"Active identity set to: {identity_id}")
        return identity

    async def ensure_default_identity(self) -> None:
        """Ensure the default identity is loaded if configured.

        Attempts to set the default identity if no active identity
        is currently set and a default is configured.

        :return: None
        :rtype: None
        """
        if not self._active_identity and hasattr(self, "_default_identity_id"):
            try:
                await self.set_active_identity(self._default_identity_id)
            except Exception as e:
                logger.debug(f"Could not set default identity: {e}")

    def get_active_identity(self) -> Optional[Identity]:
        """Get the current active identity.

        Returns the currently active identity for API operations.

        :return: Active identity or None if none set
        :rtype: Optional[Identity]
        """
        return self._active_identity

    async def get_active_credentials(self) -> AuthCredentials:
        """Get credentials for the active identity.

        Retrieves valid credentials for the currently active identity,
        refreshing them if necessary. For single-identity providers,
        creates synthetic credentials.

        :return: Valid credentials for the active identity
        :rtype: AuthCredentials
        :raises RuntimeError: If no auth provider is configured or no active identity
        """
        if not self.provider:
            raise RuntimeError("No auth provider configured")

        # For providers that support multiple identities
        if isinstance(self.provider, BaseAuthProvider):
            identity_id = "default"

            # Try to get cached credentials from token store
            cached_access = await self.get_token(
                provider_type=self.provider.provider_type,
                token_kind=TokenKind.ACCESS,
            )
            
            if cached_access and not cached_access.is_expired():
                # Reconstruct credentials from cached token
                creds = AuthCredentials(
                    token=self.provider.get_token(),
                    base_url=self.provider.auth_base_url,
                    headers=await self.provider.get_headers() if hasattr(self.provider, 'get_headers') else {}
                )
                self._active_credentials = creds
                return creds
            elif cached_access:
                logger.info(f"Credentials for {identity_id} expired, refreshing")

            # Get new credentials
            token = await self.provider.get_token()
            credentials = AuthCredentials(
                token=token,
                headers=await self.provider.get_headers() if hasattr(self.provider, 'get_headers') else {},
            )
            
            # Store access token in unified store
            await self.set_token(
                provider_type=self.provider.provider_type,
                identity_id=identity_id,
                token_kind=TokenKind.ACCESS,
                token=token.access_token,
                expires_at=token.expires_at,
                metadata={"base_url": credentials.base_url},
                region="global"
            )
            
            self._active_credentials = credentials

            logger.info(
                f"Got credentials for {self.provider.provider_type}, expires at {token.expires_at}"
            )
            return credentials

        # For single-identity providers, create synthetic credentials
        else:
            # Check if we have cached credentials
            if (
                self._active_credentials
                and datetime.now(timezone.utc) < self._active_credentials.expires_at
            ):
                return self._active_credentials

            # Get token and headers from provider
            token = await self.provider.get_token()
            headers = await self.provider.get_headers()

            credentials = AuthCredentials(
                identity_id="default",
                access_token=token.value,
                expires_at=token.expires_at,
                base_url=(
                    self.provider.get_region_endpoint()
                    if hasattr(self.provider, "get_region_endpoint")
                    else ""
                ),
                headers=headers,
            )

            self._active_credentials = credentials
            return credentials

    async def get_headers(self) -> Dict[str, str]:
        """Get authentication headers for API requests.

        Retrieves authentication headers including the profile scope
        if an active profile is set.

        :return: Dictionary of authentication headers
        :rtype: Dict[str, str]
        """
        credentials = await self.get_active_credentials()
        headers = {"Authorization": f"Bearer {credentials.token.access_token}"}
        return headers

    # Token Store interface methods    
    @property
    def token_store(self) -> TokenStore:
        """Get the unified token store instance.
        
        :return: Token store instance
        :rtype: TokenStore
        """
        return self._token_store
    
    async def get_token(
        self, 
        provider_type: str,
        token_kind: TokenKind,
        identity_id: Optional[str] = None,
        region: Optional[str] = None,
        profile_id: Optional[str] = None
    ) -> Optional[TokenEntry]:
        """Get a token from the unified store.
        
        :param provider_type: Provider type (e.g., 'direct', 'openbridge')
        :type provider_type: str
        :param identity_id: Identity identifier
        :type identity_id: str
        :param token_kind: Type of token
        :type token_kind: TokenKind
        :param region: Optional region
        :type region: Optional[str]
        :param profile_id: Optional profile ID
        :type profile_id: Optional[str]
        :return: Token entry if found and not expired
        :rtype: Optional[TokenEntry]
        """
        key = TokenKey(
            provider_type=provider_type,
            identity_id=identity_id,
            token_kind=token_kind,
            region=region,
            profile_id=profile_id
        )
        return await self._token_store.get(key)
    
    async def set_token(
        self,
        provider_type: str,
        token_kind: TokenKind,
        token: str,
        expires_at: datetime,
        identity_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        region: Optional[str] = None,
        profile_id: Optional[str] = None
    ) -> None:
        """Store a token in the unified store.
        
        :param provider_type: Provider type (e.g., 'direct', 'openbridge')
        :type provider_type: str
        :param identity_id: Identity identifier
        :type identity_id: str
        :param token_kind: Type of token
        :type token_kind: TokenKind
        :param token: The token value
        :type token: str
        :param expires_at: When the token expires
        :type expires_at: datetime
        :param metadata: Optional metadata
        :type metadata: Optional[Dict[str, Any]]
        :param region: Optional region
        :type region: Optional[str]
        :param profile_id: Optional profile ID
        :type profile_id: Optional[str]
        """
        key = TokenKey(
            provider_type=provider_type,
            identity_id=identity_id,
            token_kind=token_kind,
            region=region,
            profile_id=profile_id
        )
        entry = TokenEntry(
            value=token,
            expires_at=expires_at,
            metadata=metadata or {}
        )
        await self._token_store.set(key, entry)
    
    async def invalidate_token(
        self,
        provider_type: str,
        identity_id: str,
        token_kind: TokenKind,
        region: Optional[str] = None,
        profile_id: Optional[str] = None
    ) -> None:
        """Invalidate a specific token.
        
        :param provider_type: Provider type
        :type provider_type: str
        :param identity_id: Identity identifier
        :type identity_id: str
        :param token_kind: Type of token
        :type token_kind: TokenKind
        :param region: Optional region
        :type region: Optional[str]
        :param profile_id: Optional profile ID
        :type profile_id: Optional[str]
        """
        key = TokenKey(
            provider_type=provider_type,
            identity_id=identity_id,
            token_kind=token_kind,
            region=region,
            profile_id=profile_id
        )
        await self._token_store.invalidate(key)

    async def close(self):
        """Clean up resources.

        Closes the authentication provider and clears cached
        credentials to free up resources.

        :return: None
        :rtype: None
        """
        if self.provider:
            await self.provider.close()
        await self._token_store.clear()

    @classmethod
    def reset(cls):
        """Reset singleton instance.

        Resets the singleton instance, primarily used for
        testing purposes to ensure clean state.

        :return: None
        :rtype: None
        """
        global _auth_manager
        if cls._instance:
            cls._instance = None
        _auth_manager = None


# Global auth manager instance
_auth_manager: Optional[AuthManager] = None


def get_auth_manager() -> AuthManager:
    """Get or create the global authentication manager instance.

    Returns the global singleton instance of AuthManager,
    creating it if it doesn't exist.

    :return: Global authentication manager instance
    :rtype: AuthManager
    """
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager
