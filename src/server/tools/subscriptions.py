import os
from typing import Any, Dict, List, Optional

import requests
from fastmcp.server.context import Context

from src.utils.logging import get_logger
from .base import get_api_timeout, get_auth_headers, safe_pagination_url

logger = get_logger("subscriptions")
SUBSCRIPTIONS_PAGE_SIZE = 1000
SUBSCRIPTIONS_MAX_PAGES = 10  # Limit to prevent infinite loops in pagination
SUBSCRIPTIONS_API_BASE_URL = os.getenv("SUBSCRIPTIONS_API_BASE_URL", 'https://subscriptions.api.openbridge.io')

# TODO: This should come from the subscription or product API
STORAGE_PRODUCT_IDS = {
    14: "redshift",
    29: "redshift",
    31: "athena",
    36: "spectrum",
    37: "bigquery",
    46: "azure_blob",
    47: "azure_datalake",
    52: "snowflake",
    68: "databricks",
    73: "databricks_external",
    90: "snowflake_oauth",
    91: "snowflake_ext_az",
    92: "snowflake_ext_gcs",
    93: "snowflake_ext_s3",
}
STORAGE_TYPE_MAPPING = {  # TODO: Add more storage types
    'Google BigQuery': 'bigquery',
    'Amazon Redshift Self Serve': 'redshift',
    'Snowflake': 'snowflake'
}
SPM_REQUIRED_PARAMS = ['dataset_id',]


def get_subscriptions(
    status: str = 'active',
    ctx: Optional[Context] = None,
) -> List[Dict[Any, Any]]:
    """
    Get the subscriptions related to the current user.
    This function retrieves the subscriptions associated with the user whose token is being used for authentication.
    Args:
        status str: The status to filter subscriptions. If None, retrieves all subscriptions. Valid values are 'active' and 'cancelled'. Default is 'active'.
    Returns:
        List[Dict[Any, Any]]: A list of subscriptions, each represented as a dictionary in a format following JSON:API spec.
    """
    headers = get_auth_headers(ctx)
    params = {}
    if status is not None:
        params["status"] = status
    next_page_url = f"{SUBSCRIPTIONS_API_BASE_URL}/sub?page=1&page_size={SUBSCRIPTIONS_PAGE_SIZE}"
    subscriptions = []
    page_count = 0
    while next_page_url and page_count < SUBSCRIPTIONS_MAX_PAGES:
        page_count += 1
        response = requests.get(
            next_page_url,
            headers=headers,
            params=params,
            timeout=get_api_timeout(),
        )
        if response.status_code == 200:
            subscriptions.extend(response.json().get("data", []))
            # Paginate if necessary
            next_page_url = safe_pagination_url(
                response.json().get('links', {}).get('next'),
                SUBSCRIPTIONS_API_BASE_URL,
            )
            if next_page_url:
                logger.debug("Fetching next page of subscriptions: %s", next_page_url)
                continue
            break
        else:
            logger.error(
                "Failed to retrieve subscriptions: %s - %s",
                response.status_code,
                response.text
            )
            return []
    if page_count >= SUBSCRIPTIONS_MAX_PAGES:
        logger.warning("Reached maximum number of pages (%d) for subscriptions", SUBSCRIPTIONS_MAX_PAGES)
    logger.debug("Retrieved %d subscriptions", len(subscriptions))
    return subscriptions

def get_subscription_by_id(
    subscription_id: str,
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Retrieve a specific subscription by its ID.
    This function retrieves the subscription associated with the given ID.
    Args:
        subscription_id (str): The ID of the subscription to retrieve.
    Returns:
        Optional[Dict[Any, Any]]: The subscription represented as a dictionary in a format following JSON:API spec, or None if not found.
    """
    headers = get_auth_headers(ctx)
    response = requests.get(
        f"{SUBSCRIPTIONS_API_BASE_URL}/sub/{subscription_id}",
        headers=headers,
        timeout=get_api_timeout(),
    )
    if response.status_code == 200:
        subscription = response.json().get("data", None)
        if subscription:
            logger.debug(f"Retrieved subscription {subscription_id}")
            return subscription
        else:
            logger.warning(f"Subscription {subscription_id} not found in response")
            return None
    else:
        logger.error(f"Failed to retrieve subscription {subscription_id}: {response.status_code} - {response.text}")
        return None


def get_storage_subscriptions(
    ctx: Optional[Context] = None,
) -> List[Dict[Any, Any]]:
    """
    Get the storage subscriptions related to the current user.
    This function retrieves the subscriptions associated with the user whose token is being used for authentication,
    and filters them to only include storage subscriptions.
    Returns:
        List[Dict[Any, Any]]: A list of storage subscriptions, each represented as a dictionary in a format following JSON:API spec.
    """
    headers = get_auth_headers(ctx)
    params = {}
    storages = []
    sub_response = requests.get(
        f"{SUBSCRIPTIONS_API_BASE_URL}/storages?status=active",
        headers=headers,
        params=params,
        timeout=get_api_timeout(),
    ).json()
    for sub in sub_response['data']:
        for included in sub_response['included']:
            if str(included['id']) == str(sub['attributes']['storage_group_id']):
                key_name = included['attributes']['key_name']
                name = included['attributes']['name']
                break
        storages.append({"storage_id": sub['attributes']['storage_group_id'], "subscription_id": sub['id'], "name": name, "key_name": key_name})
    
    # Get SPM for each storage
    result = []
    for storage in storages:
        url = f'{SUBSCRIPTIONS_API_BASE_URL}/spm?subscription={storage["subscription_id"]}'
        spm_resp = requests.get(url, headers=headers, timeout=get_api_timeout())
        spm_resp.raise_for_status()
        spm_data = spm_resp.json().get('data', [])
        storage_spm = {
            x['attributes']['data_key']: x['attributes']['data_value']
            for x in spm_data
            if x.get('attributes', {}).get('data_key') in SPM_REQUIRED_PARAMS
        }
        # Safely get storage type from first SPM entry if available
        storage_type = 'unknown'
        if spm_data:
            product_name = (
                spm_data[0]
                .get('attributes', {})
                .get('product', {})
                .get('name')
            )
            if product_name:
                storage_type = STORAGE_TYPE_MAPPING.get(product_name, 'unknown')
        result.append({"storage_type": storage_type, **storage, **storage_spm})
    return result
