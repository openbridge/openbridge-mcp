import os
from typing import Any, Dict, List, Optional

import jwt
import requests
from fastmcp.server.context import Context

from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers, safe_pagination_url

HC_BASE_URL = os.getenv(
    'HEALTHCHECKS_API_BASE_URL', 
    'https://service.api.openbridge.io/service/healthchecks/production/healthchecks/account'
)
logger = get_logger("healthchecks")
HEALTHCHECKS_PAGE_SIZE = 20
HEALTHCHECKS_MAX_PAGES = 10  # Limit to prevent infinite loops in pagination


def get_healthchecks(
    subscription_id: Optional[str] = None,
    filter_date: Optional[str] = None,
    ctx: Optional[Context] = None,
) -> List[Dict[Any, Any]]:
    """
    Get the health checks related to the current user.
    This function retrieves the health checks associated with the user whose token is being used for authentication.
    Only health checks with status 'ERROR' are retrieved.
    Args:
        subscription_id (Optional[str]): The ID of the subscription to filter health checks. If None, retrieves all health checks.
        filter_date (Optional[str]): The date to filter health checks. If None, retrieves all health checks. If provided, must be in 'YYYY-MM-DD' format.
    Returns:
        List[Dict[Any, Any]]: A list of health checks with their status.
    """
    headers = get_auth_headers(ctx)
    # Get the account ID from the JWT
    auth_header = headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.error("No valid Authorization header found")
        return []

    jwt_token = auth_header.split(" ", 1)[1]
    if not jwt_token:
        logger.error("Empty JWT token in Authorization header")
        return []

    try:
        jwt_payload = jwt.decode(
            jwt_token,
            options={"verify_signature": False, "verify_aud": False, "verify_iss": False}
        )
    except jwt.exceptions.DecodeError as e:
        logger.error("Failed to decode JWT token: %s", e)
        return []

    account_id = jwt_payload.get("account_id")
    if not account_id:
        logger.error("No account_id found in JWT token")
        return []    
    
    params = {"status": "ERROR"}
    if subscription_id is not None:
        params["subscription_id"] = subscription_id
    if filter_date is not None:
        # TODO: Filter date is not being respected currently
        params["modified_at__gt"] = f"{filter_date}T00:00:00"
        params["modified_at__lt"] = f"{filter_date}T23:59:59"
        params["page_size"] = HEALTHCHECKS_PAGE_SIZE
    
    next_page_url = f"{HC_BASE_URL}/{account_id}"
    request_params = {**params, "page": 1}
    page_count = 0
    healthchecks = []
    while next_page_url and page_count < HEALTHCHECKS_MAX_PAGES:
        page_count += 1
        response = requests.get(
            next_page_url,
            headers=headers,
            params=request_params,
            timeout=get_api_timeout(),
        )
        request_params = None
        if response.status_code == 200:
            hcs = response.json().get("results", [])
            healthchecks.extend(hcs)
            logger.debug("Fetched %d healthchecks from page %d", len(hcs), page_count)
            # Paginate if necessary
            next_page_url = safe_pagination_url(
                response.json().get('links', {}).get('next'),
                HC_BASE_URL,
            )
            if next_page_url:
                logger.debug("Fetching next page of healthchecks: %s", next_page_url)
                continue
            logger.debug("No more pages of healthchecks to fetch")
            break
        else:
            logger.error(
                "Failed to retrieve healthchecks: %s - %s",
                response.status_code,
                response.text,
            )
            break
    if page_count >= HEALTHCHECKS_MAX_PAGES and next_page_url:
        logger.warning(
            "Reached maximum number of pages (%d) for healthchecks.",
            HEALTHCHECKS_MAX_PAGES,
        )
    logger.debug("Retrieved %d healthchecks", len(healthchecks))
    return healthchecks
