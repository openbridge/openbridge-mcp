import os
from typing import List, Optional

import requests
from fastmcp.server.context import Context
from pydantic import ConfigDict, StrictInt, validate_call

from src.utils.envelope import auth_error, make_error, not_found
from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers, safe_pagination_url

logger = get_logger("remote_identities")

REMOTE_IDENTITY_API_BASE_URL = os.getenv("REMOTE_IDENTITY_API_BASE_URL", 'https://remote-identity.api.openbridge.io')

# HTTP statuses where we refuse to return a silent empty list. Returning
# [] on an auth failure would mislead the caller into thinking the account
# has no remote identities, when in fact the credential was rejected.
_AUTH_FAILURE_STATUSES = frozenset({401, 403})

@validate_call(config=ConfigDict(strict=True, arbitrary_types_allowed=True))
def get_remote_identities(
    remote_identity_type_id: Optional[StrictInt] = None,
    ctx: Optional[Context] = None,
) -> List[dict] | dict:
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
    remote_identities: List[dict] = []
    headers = get_auth_headers(ctx)

    if remote_identity_type_id:
        params['remote_identity_type'] = str(remote_identity_type_id)
    next_page_url = f"{REMOTE_IDENTITY_API_BASE_URL}/ri?page=1"
    first_page = True
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
            if first_page:
                return make_error(
                    tool="get_remote_identities",
                    error_kind="sp_api_client",
                    summary="Remote identities request failed",
                    error_code="TOOL_EXECUTION_FAILED",
                    retryable=True,
                    details=[{
                        "path": "",
                        "issue": str(exc),
                        "received_type": type(exc).__name__,
                    }],
                )
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
            first_page = False
        elif response.status_code in _AUTH_FAILURE_STATUSES and first_page:
            # Auth rejected on the very first call — returning [] here would
            # silently masquerade as "no identities." Raise so the caller
            # (and the MCP client) sees the actual failure mode.
            logger.warning(
                "Remote identities auth failure: %s. The supplied JWT was "
                "rejected by %s — verify the Authorization header carries a "
                "valid Openbridge credential.",
                response.status_code,
                REMOTE_IDENTITY_API_BASE_URL,
            )
            return auth_error(
                tool="get_remote_identities",
                summary=f"Remote identity API rejected credentials (HTTP {response.status_code})",
                hints=[
                    "Check that Authorization: Bearer contains a valid Openbridge refresh token or unexpired JWT.",
                    "Verify the token belongs to the intended account/tenant.",
                ],
            )
        else:
            # Non-auth error (5xx, 404, or mid-pagination auth failure):
            # preserve the partial-results contract — log and return what
            # we already collected.
            logger.warning(f"Failed to retrieve remote identities: {response.status_code}")
            break
    return remote_identities


@validate_call(config=ConfigDict(strict=True, arbitrary_types_allowed=True))
def get_remote_identity_by_id(
    remote_identity_id: StrictInt,
    ctx: Optional[Context] = None,
) -> dict:
    """
    Get a specific remote identity by its ID.
    
    Args:
        remote_identity_id (int): The ID of the remote identity.
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
        return make_error(
            tool="get_remote_identity_by_id",
            error_kind="sp_api_client",
            summary=f"Remote identity {remote_identity_id} lookup failed",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=True,
            details=[{
                "path": "remote_identity_id",
                "issue": str(exc),
                "received_type": type(exc).__name__,
            }],
        )
    if response.status_code != 200:
        logger.warning(f"Failed to retrieve remote identity {remote_identity_id}: {response.status_code}")
        if response.status_code in _AUTH_FAILURE_STATUSES:
            return auth_error(
                tool="get_remote_identity_by_id",
                summary=f"Remote identity API rejected credentials (HTTP {response.status_code})",
            )
        return not_found(
            tool="get_remote_identity_by_id",
            resource_type="remote_identity",
            resource_id=remote_identity_id,
            error_code="REMOTE_IDENTITY_NOT_FOUND",
        )
    try:
        payload = response.json()
    except ValueError:
        logger.warning("Remote identity %s returned non-JSON body", remote_identity_id)
        return not_found(
            tool="get_remote_identity_by_id",
            resource_type="remote_identity",
            resource_id=remote_identity_id,
            error_code="REMOTE_IDENTITY_NOT_FOUND",
        )
    if not isinstance(payload, dict):
        logger.warning("Remote identity %s payload is not an object", remote_identity_id)
        return not_found(
            tool="get_remote_identity_by_id",
            resource_type="remote_identity",
            resource_id=remote_identity_id,
            error_code="REMOTE_IDENTITY_NOT_FOUND",
        )
    remote_identity = payload.get("data") or {}
    if not isinstance(remote_identity, dict) or not remote_identity:
        logger.warning("Remote identity %s response had no data", remote_identity_id)
        return not_found(
            tool="get_remote_identity_by_id",
            resource_type="remote_identity",
            resource_id=remote_identity_id,
            error_code="REMOTE_IDENTITY_NOT_FOUND",
        )
    attributes = remote_identity.get("attributes")
    if not isinstance(attributes, dict):
        logger.warning(
            "Remote identity %s response missing 'attributes'; returning not-found shape",
            remote_identity_id,
        )
        return not_found(
            tool="get_remote_identity_by_id",
            resource_type="remote_identity",
            resource_id=remote_identity_id,
            error_code="REMOTE_IDENTITY_NOT_FOUND",
        )
    logger.debug(f"Retrieved remote identity {remote_identity_id}: {remote_identity}")
    for key, value in attributes.items():
        remote_identity[key] = value
    del remote_identity['attributes']
    return remote_identity
