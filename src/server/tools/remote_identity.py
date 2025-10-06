from src.utils.logging import get_logger
from .base import get_auth_headers
from typing import List, Optional
import requests
import os
from fastmcp.server.context import Context

logger = get_logger("remote_identities")


def get_remote_identities(
    remote_identity_type_id: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> List[dict]:
    """
    Get the remote identities for the current user.
    This function retrieves the remote identities associated with the user whose token is being used for authentication.

    Args:
        remote_identity_type_id (Optional[str]): The ID of the remote identity type to filter by.
            If provided, only remote identities of this type will be returned.
    Returns:
        List[dict]: A list of remote identities.
    """
    headers = get_auth_headers()
    params = {}
    logger.debug(f"Auth headers: {headers}")
    if remote_identity_type_id:
        params['type'] = remote_identity_type_id
    response = requests.get(f"{os.getenv('REMOTE_IDENTITY_API_BASE_URL')}/ri", headers=headers, params=params)
    if response.status_code == 200:
        remote_identities = response.json().get("data", [])
        logger.debug(f"Retrieved {len(remote_identities)} remote identities")
        return remote_identities
    else:
        logger.warning(f"Failed to retrieve remote identities: {response.status_code}")
        return []

def get_remote_identity_by_id(
    remote_identity_id: str,
    ctx: Optional[Context] = None,
) -> dict:
    """
    Get a specific remote identity by its ID.
    
    Args:
        remote_identity_id (str): The ID of the remote identity.
    Returns:
        dict: The remote identity data if found, or an error message otherwise.
    """
    headers = get_auth_headers()
    response = requests.get(f"{os.getenv('REMOTE_IDENTITY_API_BASE_URL')}/sri/{remote_identity_id}", headers=headers)
    if response.status_code == 200:
        remote_identity = response.json().get("data", {})
        logger.debug(f"Retrieved remote identity {remote_identity_id}: {remote_identity}")
        for key in remote_identity['attributes']:
            remote_identity[key] = remote_identity['attributes'][key]
        del remote_identity['attributes']
        return remote_identity
    else:
        logger.warning(f"Failed to retrieve remote identity {remote_identity_id}: {response.status_code}")
        return {"error": f"Remote identity {remote_identity_id} not found."}
