"""Shared Pydantic models for the Amazon Ads MCP project.

This module contains all the data models used throughout the Amazon Ads MCP
server, including authentication tokens, identity management, API responses,
and OpenBridge integration models.

The models provide type safety and validation for:
- Authentication and token management
- Identity and profile management
- API request/response handling
- OpenBridge integration
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# Auth Models
class Token(BaseModel):
    access_token: str
    expires_at: Optional[datetime] = None
    base_url: Optional[str] = None
    token_type: str = Field(default="Bearer")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Identity(BaseModel):
    relationships: Optional[Dict[str, Any]] = None


class AuthCredentials(BaseModel):
    token: Token
    base_url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)


# API Models
class AccountInfo(BaseModel):
    """Account information model.
    
    Represents account information returned by the Amazon Advertising API.
    Contains marketplace details and account status information.
    
    :param marketplace_string_id: Marketplace identifier string
    :type marketplace_string_id: str
    :param id: Account ID
    :type id: str
    :param type: Type of account
    :type type: str
    :param name: Account name
    :type name: str
    :param valid_payment_method: Whether account has valid payment method
    :type valid_payment_method: Optional[bool]
    """
    marketplace_string_id: str = Field(alias="marketplaceStringId")
    id: str
    type: str
    name: str
    valid_payment_method: Optional[bool] = Field(
        alias="validPaymentMethod", default=None
    )


class Profile(BaseModel):
    """Amazon Ads Profile model.
    
    Represents an Amazon Advertising profile with location, currency,
    and budget information. Profiles are used to organize campaigns
    and manage advertising accounts.
    
    :param profile_id: Unique profile identifier
    :type profile_id: int
    :param country_code: ISO country code for the profile
    :type country_code: str
    :param currency_code: ISO currency code for the profile
    :type currency_code: str
    :param timezone: Timezone for the profile
    :type timezone: str
    :param daily_budget: Optional daily budget limit
    :type daily_budget: Optional[float]
    :param account_info: Associated account information
    :type account_info: AccountInfo
    """
    profile_id: int = Field(alias="profileId")
    country_code: str = Field(alias="countryCode")
    currency_code: str = Field(alias="currencyCode")
    timezone: str
    daily_budget: Optional[float] = Field(alias="dailyBudget", default=None)
    account_info: AccountInfo = Field(alias="accountInfo")

    model_config = {"populate_by_name": True}


# Request/Response Models
class IdentityListResponse(BaseModel):
    """Response model for identity list.
    
    Represents a paginated response containing a list of available
    identities that can be used for Amazon Ads API access.
    
    :param identities: List of available identities
    :type identities: List[Identity]
    :param total: Total number of identities available
    :type total: int
    :param has_more: Whether there are more identities to fetch
    :type has_more: bool
    """
    identities: List[Identity]
    total: int
    has_more: bool = False


class SetActiveIdentityRequest(BaseModel):
    """Request model for setting active identity.
    
    Used when requesting to set a specific identity as the active
    identity for Amazon Ads API operations.
    
    :param identity_id: ID of the identity to activate
    :type identity_id: str
    :param persist: Whether to persist this choice across sessions
    :type persist: bool
    """
    identity_id: str
    persist: bool = False


class SetActiveIdentityResponse(BaseModel):
    """Response model for setting active identity.
    
    Response indicating the result of setting an identity as active,
    including whether credentials were successfully loaded.
    
    :param success: Whether the operation was successful
    :type success: bool
    :param identity: The identity that was set as active
    :type identity: Identity
    :param credentials_loaded: Whether credentials were loaded for identity
    :type credentials_loaded: bool
    :param message: Optional message about the operation result
    :type message: Optional[str]
    """
    success: bool
    identity: Identity
    credentials_loaded: bool = False
    message: Optional[str] = None


# OpenBridge API Models
# OpenbridgeIdentity is now just an alias for Identity since we're passing through
OpenbridgeIdentity = Identity


class OpenbridgeTokenResponse(BaseModel):
    """OpenBridge token response model.
    
    Represents the response from OpenBridge when requesting an
    access token for Amazon Ads API authentication.
    
    :param data: Raw response data containing token information
    :type data: Dict[str, Any]
    """
    data: Dict[str, Any]

    def get_token(self) -> Optional[str]:
        """Extract token from response.
        
        Extracts the access token from the OpenBridge response data.
        
        :return: Access token string if available, None otherwise
        :rtype: Optional[str]
        """
        # The response has data.access_token directly
        return self.data.get("access_token")

    def get_client_id(self) -> Optional[str]:
        """Extract client ID from response.
        
        Extracts the client ID from the OpenBridge response data.
        
        :return: Client ID string if available, None otherwise
        :rtype: Optional[str]
        """
        return self.data.get("client_id")
