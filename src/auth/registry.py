"""Provider registry for authentication providers.

This module manages registration and discovery of authentication providers,
allowing new providers to be added without modifying core code.
"""

import logging
from typing import Dict, Optional, Type

from .providers.base import BaseAuthProvider, ProviderConfig

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Registry for authentication providers.

    This class manages the registration and instantiation of authentication
    providers, allowing the system to dynamically discover and use different
    providers without modifying core code.

    The registry provides:
    - Provider registration and discovery
    - Dynamic provider instantiation
    - Provider type management
    - Testing support with clear/reset functionality
    """

    _providers: Dict[str, Type[BaseAuthProvider]] = {}

    @classmethod
    def register(
        cls, provider_type: str, provider_class: Type[BaseAuthProvider]
    ) -> None:
        """Register a provider class.

        :param provider_type: Unique identifier for the provider type
        :type provider_type: str
        :param provider_class: Provider class to register
        :type provider_class: Type[BaseAuthProvider]
        :raises ValueError: If provider type is already registered
        """
        if provider_type in cls._providers:
            raise ValueError(
                f"Provider type '{provider_type}' is already registered"
            )

        cls._providers[provider_type] = provider_class
        logger.info(
            f"Registered provider: {provider_type} -> {provider_class.__name__}"
        )

    @classmethod
    def unregister(cls, provider_type: str) -> None:
        """Unregister a provider.

        Removes a provider from the registry, making it no longer
        available for instantiation.

        :param provider_type: Provider type to unregister
        :type provider_type: str
        :return: None
        :rtype: None
        """
        if provider_type in cls._providers:
            del cls._providers[provider_type]
            logger.info(f"Unregistered provider: {provider_type}")

    @classmethod
    def get_provider_class(
        cls, provider_type: str
    ) -> Optional[Type[BaseAuthProvider]]:
        """Get a registered provider class.

        :param provider_type: Provider type to retrieve
        :type provider_type: str
        :return: Provider class if registered, None otherwise
        :rtype: Optional[Type[BaseAuthProvider]]
        """
        return cls._providers.get(provider_type)

    @classmethod
    def create_provider(
        cls, provider_type: str, config: ProviderConfig
    ) -> BaseAuthProvider:
        """Create a provider instance.

        :param provider_type: Type of provider to create
        :type provider_type: str
        :param config: Configuration for the provider
        :type config: ProviderConfig
        :return: Provider instance
        :rtype: BaseAuthProvider
        :raises ValueError: If provider type is not registered
        """
        provider_class = cls.get_provider_class(provider_type)
        if not provider_class:
            available = ", ".join(cls._providers.keys())
            raise ValueError(
                f"Unknown provider type: '{provider_type}'. "
                f"Available providers: {available or 'none'}"
            )

        return provider_class(config)

    @classmethod
    def list_providers(cls) -> Dict[str, Type[BaseAuthProvider]]:
        """List all registered providers.

        :return: Dictionary of provider types to classes
        :rtype: Dict[str, Type[BaseAuthProvider]]
        """
        return cls._providers.copy()

    @classmethod
    def clear(cls) -> None:
        """Clear all registered providers.

        Removes all registered providers from the registry.
        Primarily used for testing to ensure clean state.

        :return: None
        :rtype: None
        """
        cls._providers.clear()


def register_provider(provider_type: str):
    """Decorator to auto-register a provider class.

    Usage:
        @register_provider("my_provider")
        class MyProvider(BaseAuthProvider):
            ...

    :param provider_type: Type identifier for the provider
    :type provider_type: str
    :return: Decorator function
    """

    def decorator(provider_class: Type[BaseAuthProvider]):
        ProviderRegistry.register(provider_type, provider_class)
        return provider_class

    return decorator
