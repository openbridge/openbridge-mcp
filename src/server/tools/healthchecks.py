import requests

from src.utils.logging import get_logger
from .base import get_auth_headers
from .remote_identity import get_remote_identity_by_id
from typing import Any, Dict, List, Optional
import os

logger = get_logger("healthchecks")
HEALTHCHECKS_PAGE_SIZE = 20
HEALTHCHECKS_MAX_PAGES = 10  # Limit to prevent infinite loops in pagination


def get_healthchecks(subscription_id: Optional[str] = None, filter_date: Optional[str] = None) -> List[Dict[Any, Any]]:
    """
    Get the healthchecks related to the current user.
    This function retrieves the health checks associated with the user whose token is being used for authentication.
    Args:
        subscription_id (Optional[str]): The ID of the subscription to filter health checks. If None, retrieves all health checks.
        filter_date (Optional[str]): The date to filter health checks. If None, retrieves all health checks. If provided, must be in 'YYYY-MM-DD' format.
    Returns:
        List[Dict[Any, Any]]: A list of health checks with their status.
    """
    headers = get_auth_headers()
    account_id = 3558  # TODO: Replace with dynamic retrieval of account ID as necessary
    params = {"status": "ERROR"}
    if subscription_id is not None:
        params["subscription_id"] = subscription_id
    if filter_date is not None:
        # TODO: Filter date is not being respected currently
        params["modified_at__gt"] = f"{filter_date}T00:00:00"
        params["modified_at__lt"] = f"{filter_date}T23:59:59"
        params["page_size"] = HEALTHCHECKS_PAGE_SIZE
    
    next_page = 1
    healthchecks = []
    while next_page:
        params["page"] = next_page
        response = requests.get(f"{os.getenv('HEALTHCHECKS_API_BASE_URL')}/{account_id}", headers=headers, params=params)
        if response.status_code == 200:
            healthchecks = response.json().get("results", [])
            # Paginate if necessary
            if response.json().get('links', {}).get('next'):
                next_page += 1
                if next_page > HEALTHCHECKS_MAX_PAGES:
                    logger.warning("Reached maximum number of pages for healthchecks.")
                    break
                logger.debug(f"Fetching page {next_page} of healthchecks")
                continue
                # Continue fetching until no more pages or max pages reached
            else:
                logger.debug("No more pages of healthchecks to fetch")
                break
        else:
            logger.error(f"Failed to retrieve healthchecks: {response.status_code} - {response.text}")
            break
    
    logger.debug(f"Retrieved {len(healthchecks)} healthchecks")
    return healthchecks
