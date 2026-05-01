import os
from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

import jwt
import requests
from fastmcp.server.context import Context
from pydantic import ConfigDict, StrictInt, validate_call

from src.utils.envelope import make_error
from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers, safe_pagination_url

HC_BASE_URL = os.getenv(
    'HEALTHCHECKS_API_BASE_URL', 
    'https://service.api.openbridge.io/service/healthchecks/production/healthchecks/account'
)
logger = get_logger("healthchecks")
HEALTHCHECKS_PAGE_SIZE = 20
HEALTHCHECKS_MAX_PAGES = 10  # Limit to prevent infinite loops in pagination


@validate_call(config=ConfigDict(strict=True, arbitrary_types_allowed=True))
def get_healthchecks(
    subscription_id: Optional[StrictInt] = None,
    filter_date: Optional[str] = None,
    last_days: Optional[StrictInt] = None,
    page: Optional[StrictInt] = None,
    ctx: Optional[Context] = None,
) -> List[Dict[Any, Any]] | Dict[str, Any]:
    """
    Get the health checks related to the current user.
    This function retrieves the health checks associated with the user whose token is being used for authentication.
    Only health checks with status 'ERROR' are retrieved.
    Args:
        subscription_id (Optional[str]): The ID of the subscription to filter health checks. If None, retrieves all health checks.
        filter_date (Optional[str]): The date to filter health checks. If None, retrieves all health checks. If provided, must be in 'YYYY-MM-DD' format.
        last_days (Optional[int]): Relative filter window in days (today - N days).
            Mutually exclusive with filter_date.
        page (Optional[int]): Return a single page when provided. If omitted,
            auto-paginates until exhausted (or max pages reached).
    Returns:
        List[Dict[Any, Any]]: A list of health checks with their status.
    """
    if filter_date is not None and last_days is not None:
        return make_error(
            tool="get_healthchecks",
            error_kind="mcp_input_validation",
            summary="filter_date and last_days are mutually exclusive",
            error_code="INPUT_VALIDATION_FAILED",
            retryable=False,
            details=[{
                "path": "filter_date,last_days",
                "issue": "Use either filter_date or last_days, not both",
                "received_type": "str,int",
            }],
        )

    headers = get_auth_headers(ctx)
    # Get the account ID from the JWT
    auth_header = headers.get("Authorization", "")
    if not auth_header or not auth_header.startswith("Bearer "):
        logger.error("No valid Authorization header found")
        return make_error(
            tool="get_healthchecks",
            error_kind="auth_error",
            summary="No valid Authorization header found",
            error_code="AUTHENTICATION_ERROR",
            retryable=False,
        )

    jwt_token = auth_header.split(" ", 1)[1]
    if not jwt_token:
        logger.error("Empty JWT token in Authorization header")
        return make_error(
            tool="get_healthchecks",
            error_kind="auth_error",
            summary="Empty JWT token in Authorization header",
            error_code="AUTHENTICATION_ERROR",
            retryable=False,
        )

    try:
        jwt_payload = jwt.decode(
            jwt_token,
            options={"verify_signature": False, "verify_aud": False, "verify_iss": False}
        )
    except jwt.exceptions.DecodeError as e:
        logger.error("Failed to decode JWT token: %s", e)
        return make_error(
            tool="get_healthchecks",
            error_kind="auth_error",
            summary="Failed to decode JWT token",
            error_code="AUTHENTICATION_ERROR",
            retryable=False,
            details=[{
                "path": "Authorization",
                "issue": str(e),
                "received_type": type(e).__name__,
            }],
        )

    account_id = jwt_payload.get("account_id")
    if not account_id:
        logger.error("No account_id found in JWT token")
        return make_error(
            tool="get_healthchecks",
            error_kind="auth_error",
            summary="No account_id found in JWT token",
            error_code="AUTHENTICATION_ERROR",
            retryable=False,
        )
    
    params = {"status": "ERROR"}
    if subscription_id is not None:
        params["subscription_id"] = str(subscription_id)
    if filter_date is not None:
        params["modified_at__gt"] = f"{filter_date}T00:00:00"
        params["modified_at__lt"] = f"{filter_date}T23:59:59"
    if last_days is not None:
        since = (datetime.now(UTC) - timedelta(days=int(last_days))).isoformat()
        params["modified_at__gt"] = since
    params["page_size"] = HEALTHCHECKS_PAGE_SIZE
    
    next_page_url = f"{HC_BASE_URL}/{account_id}"
    request_params = {**params, "page": int(page) if page is not None else 1}
    single_page_mode = page is not None
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
            if single_page_mode:
                break
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
            return make_error(
                tool="get_healthchecks",
                error_kind="sp_api_http",
                summary="Failed to retrieve healthchecks",
                error_code="TOOL_EXECUTION_FAILED",
                retryable=response.status_code >= 500,
                details=[{
                    "path": "",
                    "issue": "Healthchecks API returned a non-200 status",
                    "received_type": "HTTPStatusError",
                    "status": response.status_code,
                }],
            )
    if page_count >= HEALTHCHECKS_MAX_PAGES and next_page_url:
        logger.warning(
            "Reached maximum number of pages (%d) for healthchecks.",
            HEALTHCHECKS_MAX_PAGES,
        )
    logger.debug("Retrieved %d healthchecks", len(healthchecks))
    return healthchecks
