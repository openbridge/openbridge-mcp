import os
from datetime import datetime as dt, timedelta as td
from typing import Any, Dict, List, Optional

import requests
from fastmcp.server.context import Context

from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers

logger = get_logger("jobs")

JOBS_API_BASE_URL = os.getenv('JOBS_API_BASE_URL', 'https://service.api.openbridge.io/service/jobs/production/jobs')


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
        logger.error(f"Error fetching jobs: {e}")
        return []


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
        start_date (str): The start date for the job in ISO format. This should be the MOST recent date for the job.
        end_date (str): The end date for the job in ISO format. This should be the LEAST recent date for the job.
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
                    "start_date": date_end,  # TODO: The LLM refuses to set the start date as the most recent date, so swap them.
                    "end_date": date_start,
                    "stage_id": stage_id,
                    "start_time": dt.strftime(dt.utcnow() + td(minutes=5), "%Y-%m-%d %H:%M:%S")
                }
            }
        }

        response = None
        try:
            response = requests.post(
                f"{os.getenv('HISTORY_API_BASE_URL')}/history/{subscription_id}",
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
