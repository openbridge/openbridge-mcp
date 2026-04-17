import os
from datetime import UTC, datetime as dt, timedelta as td
from typing import Any, Dict, List, Optional

import requests
from fastmcp.server.context import Context

from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers

logger = get_logger("jobs")

JOBS_API_BASE_URL = os.getenv('JOBS_API_BASE_URL', 'https://service.api.openbridge.io/service/jobs/production')
HISTORY_API_BASE_URL = os.getenv("HISTORY_API_BASE_URL", "https://history.api.openbridge.io")


def get_jobs(
    subscription_id: int,
    status: Optional[str] = 'active',
    is_primary: Optional[str] = 'true',
    ctx: Optional[Context] = None,
) -> List[Dict[Any, Any]]:
    """
    Fetches jobs from the Openbridge API.

    Args:
        subscription_id (int): The subscription ID to filter jobs. This is required; only jobs associated with this subscription will be returned.
        status (Optional[str]): The status to filter jobs.
        is_primary (Optional[str]): Whether to filter for primary jobs. 
            - If 'true', only primary jobs are returned.
            - If 'false', only one-off/history jobs are returned.
            - If not set, both primary and one-off/history jobs are returned.

    Returns:
        List[Dict[Any, Any]]: A list of job dictionaries.
    """
    headers = get_auth_headers(ctx)
    params = {}
    
    if subscription_id:
        params['subscription_ids'] = subscription_id
    if status:
        params['status'] = status
    if is_primary is not None:
        params['is_primary'] = is_primary.lower()

    try:
        response = requests.get(
            f"{JOBS_API_BASE_URL}/jobs",
            headers=headers,
            params=params,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
        return response.json().get('data', [])
    except requests.RequestException as e:
        logger.error("Error fetching jobs: %s", e)
        return []


def get_job_by_id(
    job_id: int,
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Fetch a single job by job ID.

    Args:
        job_id (int): The job ID to fetch.

    Returns:
        Optional[Dict[Any, Any]]: Job data when found, otherwise None.
    """
    headers = get_auth_headers(ctx)
    try:
        response = requests.get(
            f"{JOBS_API_BASE_URL}/jobs/{job_id}",
            headers=headers,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Error fetching job %s: %s", job_id, e)
        return None

    return response.json().get("data")


def get_history_by_id(
    history_id: int,
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Fetch a history transaction by ID.

    Args:
        history_id (int): History transaction ID.

    Returns:
        Optional[Dict[Any, Any]]: History transaction data when found, otherwise None.
    """
    headers = get_auth_headers(ctx)
    try:
        response = requests.get(
            f"{HISTORY_API_BASE_URL}/history/{history_id}",
            headers=headers,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Error fetching history %s: %s", history_id, e)
        return None
    return response.json().get("data")


def update_history_status(
    history_id: int,
    status: str,
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Update a history transaction status.

    Args:
        history_id (int): History transaction ID.
        status (str): New status value.

    Returns:
        Optional[Dict[Any, Any]]: Updated history transaction data when successful, otherwise None.
    """
    headers = get_auth_headers(ctx)
    payload = {
        "data": {
            "type": "HistoryTransaction",
            "id": str(history_id),
            "attributes": {"status": status},
        }
    }
    try:
        response = requests.patch(
            f"{HISTORY_API_BASE_URL}/history/{history_id}",
            headers=headers,
            json=payload,
            timeout=get_api_timeout(),
        )
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error("Error updating history %s: %s", history_id, e)
        return None
    return response.json().get("data")


def create_job(
    subscription_id: int,
    date_start: str,
    date_end: str,
    stage_ids: List[int],
    ctx: Optional[Context] = None,
) -> List[Dict[Any, Any]]:
    """
    Create a job for a given subscription.

    Args:
        subscription_id (int): The subscription ID to create jobs for.
        date_start (str): The earliest date for the job in ISO format (YYYY-MM-DD).
        date_end (str): The latest date for the job in ISO format (YYYY-MM-DD).
        stage_ids (List[int]): The stage IDs for the jobs. These IDs can be found by calling the `get_product_stage_ids` tool if needed.

    Returns:
        Optional[List[Dict[Any, Any]]]: The created job data if successful. If unsuccessful, returns a dict with an "errors" key.
    """
    headers = get_auth_headers(ctx)
    job_data = []
    for stage_id in stage_ids:
        payload = {
            "data": {
                "type": "HistoryTransaction",
                "attributes": {
                    "subscription_id": subscription_id,
                    "start_date": date_start,
                    "end_date": date_end,
                    "stage_id": stage_id,
                    "start_time": (dt.now(UTC) + td(minutes=5)).isoformat()
                }
            }
        }

        response = None
        try:
            response = requests.post(
                f"{HISTORY_API_BASE_URL}/history/{subscription_id}",
                headers=headers,
                json=payload,
                timeout=get_api_timeout(),
            )
            response.raise_for_status()
            job_data.append(response.json().get('data', {}).get('attributes', {}))
            logger.debug("Created one-off job: %s", job_data)
        except requests.RequestException as e:
            error_detail = response.text if response is not None else str(e)
            logger.error("Error creating one-off job: %s", error_detail)
            return [{"errors": error_detail}]
    return job_data
