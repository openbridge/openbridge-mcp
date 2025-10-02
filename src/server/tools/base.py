from src.utils.logging import get_logger
from typing import Dict
import requests
import os

logger = get_logger("base_tools")


def get_auth_headers() -> Dict[str, str]:
    """Get authentication headers for API calls.
    
    This function converts refresh tokens to JWT tokens using the exact same
    logic as the working auth.py file.
    
    Returns:
        Dictionary with Authorization header if authentication is available
    """
    headers = {}
    
    # Check if authentication is enabled
    refresh_token = os.getenv("OPENBRIDGE_REFRESH_TOKEN")
    if not refresh_token:
        logger.warning("No refresh token available for authentication")
        return headers
    
    # Check if this looks like a refresh token (OpenBridge format: xxx:yyy)
    if ":" in refresh_token and len(refresh_token.split(":")) == 2:
        try:
            # Convert refresh token to JWT using OpenBridge auth server
            auth_endpoint = os.getenv("OPENBRIDGE_AUTH_BASE_URL", "https://authentication.api.openbridge.io") + "/auth/api/ref"

            payload = {
                "data": {
                    "type": "APIAuth",
                    "attributes": {"refresh_token": refresh_token},
                }
            }
            
            response = requests.post(
                auth_endpoint,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            response.raise_for_status()
            
            auth_data = response.json()
            logger.debug(f"Auth response: {auth_data}")
            jwt_token = auth_data.get("data", {}).get("attributes", {}).get("token")
            
            if jwt_token:
                headers["Authorization"] = f"Bearer {jwt_token}"
                logger.debug("Successfully generated JWT from refresh token")
            else:
                logger.warning("No JWT token found in auth response")
                
        except Exception as e:
            logger.warning(f"Failed to convert refresh token to JWT: {e}")
            # Fallback: use refresh token directly
            headers["Authorization"] = f"Bearer {refresh_token}"
            logger.debug("Using refresh token directly as fallback")
    else:
        # If it doesn't look like a refresh token, use it directly
        headers["Authorization"] = f"Bearer {refresh_token}"
        logger.debug("Using token directly (not in refresh token format)")
    logger.debug("Returning auth headers")
    return headers
