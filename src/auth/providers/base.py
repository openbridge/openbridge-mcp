"""Base authentication provider interfaces.

This module defines the abstract base classes for authentication providers,
allowing different authentication mechanisms to be plugged into the system.

The module provides:
- BaseAuthProvider: Core authentication interface
- BaseIdentityProvider: Multi-identity support interface
- BaseAmazonAdsProvider: Amazon Ads specific functionality
- ProviderConfig: Configuration container class
"""

from abc import ABC, abstractmethod
from typing import Any, List, Optional

from ...models import AuthCredentials, Identity, Token


class BaseAuthProvider(ABC):
    """Base class for all authentication providers.

    This defines the core interface that all authentication providers must
    implement, whether they're OAuth2-based, API key-based, or use other
    mechanisms.

    All providers must implement:
    - Provider type identification
    - Initialization and cleanup
    - Token retrieval and validation
    """

    @property
    @abstractmethod
    def provider_type(self) -> str:
        """Return the provider type identifier.

        :return: Provider type (e.g., 'openbridge', 'direct', 'auth0')
        :rtype: str
        """
        pass

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the provider.

        Perform any setup needed, such as validating configuration
        or establishing connections.
        """
        pass

    @abstractmethod
    async def get_token(self) -> Token:
        """Get current authentication token.

        Returns a valid authentication token, refreshing if necessary.

        :return: Valid authentication token
        :rtype: Token
        """
        pass

    @abstractmethod
    async def validate_token(self, token: Token) -> bool:
        """Validate if token is still valid.

        :param token: Token to validate
        :type token: Token
        :return: True if token is valid, False otherwise
        :rtype: bool
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Clean up provider resources."""
        pass


class BaseIdentityProvider(ABC):
    """Base class for providers that support multiple identities.

    Not all providers need this - for example, direct API key auth
    typically has a single identity. But providers like OpenBridge
    that manage multiple accounts should implement this.

    Providers implementing this interface can:
    - List available identities
    - Retrieve specific identity details
    - Get credentials for specific identities
    """

    @abstractmethod
    async def list_identities(self, **kwargs) -> List[Identity]:
        """List all available identities.

        :param kwargs: Provider-specific filters
        :return: List of available identities
        :rtype: List[Identity]
        """
        pass

    @abstractmethod
    async def get_identity(self, identity_id: str) -> Optional[Identity]:
        """Get specific identity by ID.

        :param identity_id: Unique identifier for the identity
        :type identity_id: str
        :return: Identity if found, None otherwise
        :rtype: Optional[Identity]
        """
        pass

    @abstractmethod
    async def get_identity_credentials(
        self, identity_id: str
    ) -> AuthCredentials:
        """Get credentials for specific identity.

        :param identity_id: Unique identifier for the identity
        :type identity_id: str
        :return: Authentication credentials for the identity
        :rtype: AuthCredentials
        """
        pass


class ProviderConfig:
    """Configuration container for a provider instance.

    This is a simple container for provider configuration that
    can be extended by specific providers. Provides both
    dictionary-style and attribute-style access to config values.

    The configuration supports:
    - Arbitrary key-value pairs
    - Dictionary-style access via .get()
    - Attribute-style access for convenience
    """

    def __init__(self, **kwargs):
        """Initialize configuration with arbitrary keyword arguments.

        :param **kwargs: Provider-specific configuration parameters
        :type **kwargs: Any
        :return: None
        :rtype: None
        """
        self._config = kwargs

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value by key.

        Retrieves a configuration value using dictionary-style access.

        :param key: Configuration key to retrieve
        :type key: str
        :param default: Default value if key not found
        :type default: Any
        :return: Configuration value or default
        :rtype: Any
        """
        return self._config.get(key, default)

    def __getattr__(self, name: str) -> Any:
        """Allow attribute-style access to configuration values.

        Enables dot notation access to configuration values
        for convenience.

        :param name: Attribute name to access
        :type name: str
        :return: Configuration value
        :rtype: Any
        :raises AttributeError: If the attribute is not found
        """
        if name in self._config:
            return self._config[name]
        raise AttributeError(f"Config has no attribute '{name}'")
