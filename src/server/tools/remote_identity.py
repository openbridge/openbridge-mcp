import os
from typing import List, Optional

import requests
from fastmcp.server.context import Context

from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers, safe_pagination_url

logger = get_logger("remote_identities")

REMOTE_IDENTITY_API_BASE_URL = os.getenv("REMOTE_IDENTITY_API_BASE_URL", 'https://remote-identity.api.openbridge.io')

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
    params = {}
    remote_identities = []
    headers = get_auth_headers(ctx)

    if remote_identity_type_id:
        params['remote_identity_type'] = remote_identity_type_id
    next_page_url = f"{REMOTE_IDENTITY_API_BASE_URL}/ri?page=1"
    while next_page_url:
        try:
            response = requests.get(
                next_page_url,
                params=params,
                headers=headers,
                timeout=get_api_timeout(),
            )
        except requests.RequestException as exc:
            logger.warning("Remote identities request failed: %s", exc)
            break
        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                logger.warning("Remote identities returned non-JSON body")
                break
            ris = payload.get("data", []) if isinstance(payload, dict) else []
            remote_identities.extend(ris)
            logger.debug(f"Retrieved {len(ris)} remote identities")
            next_page_url = safe_pagination_url(
                (payload.get('links', {}) if isinstance(payload, dict) else {}).get('next', None),
                REMOTE_IDENTITY_API_BASE_URL,
            )
        else:
            logger.warning(f"Failed to retrieve remote identities: {response.status_code}")
            break
    return remote_identities

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
    headers = get_auth_headers(ctx)
    try:
        response = requests.get(
            f"{REMOTE_IDENTITY_API_BASE_URL}/ri/{remote_identity_id}",
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Remote identity %s lookup failed: %s", remote_identity_id, exc)
        return {"error": f"Remote identity {remote_identity_id} lookup failed", "details": str(exc)}
    if response.status_code != 200:
        logger.warning(f"Failed to retrieve remote identity {remote_identity_id}: {response.status_code}")
        return {"error": f"Remote identity {remote_identity_id} not found."}
    try:
        payload = response.json()
    except ValueError:
        logger.warning("Remote identity %s returned non-JSON body", remote_identity_id)
        return {"error": f"Remote identity {remote_identity_id} not found."}
    if not isinstance(payload, dict):
        logger.warning("Remote identity %s payload is not an object", remote_identity_id)
        return {"error": f"Remote identity {remote_identity_id} not found."}
    remote_identity = payload.get("data") or {}
    if not isinstance(remote_identity, dict) or not remote_identity:
        logger.warning("Remote identity %s response had no data", remote_identity_id)
        return {"error": f"Remote identity {remote_identity_id} not found."}
    attributes = remote_identity.get("attributes")
    if not isinstance(attributes, dict):
        logger.warning(
            "Remote identity %s response missing 'attributes'; returning not-found shape",
            remote_identity_id,
        )
        return {"error": f"Remote identity {remote_identity_id} not found."}
    logger.debug(f"Retrieved remote identity {remote_identity_id}: {remote_identity}")
    for key, value in attributes.items():
        remote_identity[key] = value
    del remote_identity['attributes']
    return remote_identity
