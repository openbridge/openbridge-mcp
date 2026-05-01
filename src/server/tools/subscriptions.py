import os
from typing import Any, Dict, List, Optional

import requests
from fastmcp.server.context import Context
from pydantic import ConfigDict, StrictInt, validate_call

from src.utils.envelope import make_error, not_found
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
        try:
            response = requests.get(
                next_page_url,
                headers=headers,
                params=params,
                timeout=get_api_timeout(),
            )
        except requests.RequestException as exc:
            # Fail-fast: subscription lists are consumed as complete inventories.
            logger.error("Subscriptions request failed: %s", exc)
            return []
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

@validate_call(config=ConfigDict(strict=True, arbitrary_types_allowed=True))
def get_subscription_by_id(
    subscription_id: StrictInt,
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]] | Dict[str, Any]:
    """
    Retrieve a specific subscription by its ID.
    This function retrieves the subscription associated with the given ID.
    Args:
        subscription_id (int): The ID of the subscription to retrieve.
    Returns:
        Optional[Dict[Any, Any]]: The subscription represented as a dictionary in a format following JSON:API spec, or None if not found.
    """
    headers = get_auth_headers(ctx)
    try:
        response = requests.get(
            f"{SUBSCRIPTIONS_API_BASE_URL}/sub/{subscription_id}",
            headers=headers,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Subscription %s request failed: %s", subscription_id, exc)
        return make_error(
            tool="get_subscription_by_id",
            error_kind="sp_api_client",
            summary=f"Subscription {subscription_id} lookup failed",
            error_code="TOOL_EXECUTION_FAILED",
            retryable=True,
            details=[{
                "path": "subscription_id",
                "issue": str(exc),
                "received_type": type(exc).__name__,
            }],
        )
    if response.status_code != 200:
        logger.error(f"Failed to retrieve subscription {subscription_id}: {response.status_code} - {response.text}")
        return not_found(
            tool="get_subscription_by_id",
            resource_type="subscription",
            resource_id=subscription_id,
            error_code="SUBSCRIPTION_NOT_FOUND",
        )
    try:
        payload = response.json()
    except ValueError:
        logger.warning("Subscription %s returned non-JSON body", subscription_id)
        return not_found(
            tool="get_subscription_by_id",
            resource_type="subscription",
            resource_id=subscription_id,
            error_code="SUBSCRIPTION_NOT_FOUND",
        )
    subscription = payload.get("data") if isinstance(payload, dict) else None
    if subscription:
        logger.debug(f"Retrieved subscription {subscription_id}")
        return subscription
    logger.warning(f"Subscription {subscription_id} not found in response")
    return not_found(
        tool="get_subscription_by_id",
        resource_type="subscription",
        resource_id=subscription_id,
        error_code="SUBSCRIPTION_NOT_FOUND",
    )


def create_subscription(
    attributes: Dict[str, Any],
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Create a subscription.

    Args:
        attributes (Dict[str, Any]): JSON:API attributes payload for the subscription.

    Returns:
        Optional[Dict[Any, Any]]: Created subscription data when successful, otherwise None.
    """
    headers = get_auth_headers(ctx)
    payload = {"data": {"type": "Subscription", "attributes": attributes}}
    try:
        response = requests.post(
            f"{SUBSCRIPTIONS_API_BASE_URL}/sub",
            headers=headers,
            json=payload,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Create subscription request failed: %s", exc)
        return None
    if response.status_code not in (200, 201):
        logger.error("Failed to create subscription: %s - %s", response.status_code, response.text)
        return None
    try:
        body = response.json()
    except ValueError:
        logger.warning("Create subscription returned non-JSON body")
        return None
    return body.get("data") if isinstance(body, dict) else None


@validate_call(config=ConfigDict(strict=True, arbitrary_types_allowed=True))
def update_subscription(
    subscription_id: StrictInt,
    attributes: Dict[str, Any],
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Update an existing subscription.

    Args:
        subscription_id (int): Subscription ID to update.
        attributes (Dict[str, Any]): JSON:API attributes patch payload.

    Returns:
        Optional[Dict[Any, Any]]: Updated subscription data when successful, otherwise None.
    """
    headers = get_auth_headers(ctx)
    payload = {
        "data": {
            "type": "Subscription",
            "id": str(subscription_id),
            "attributes": attributes,
        }
    }
    try:
        response = requests.patch(
            f"{SUBSCRIPTIONS_API_BASE_URL}/sub/{subscription_id}",
            headers=headers,
            json=payload,
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.warning("Update subscription %s request failed: %s", subscription_id, exc)
        return None
    if response.status_code not in (200, 202):
        logger.error(
            "Failed to update subscription %s: %s - %s",
            subscription_id,
            response.status_code,
            response.text,
        )
        return None
    try:
        body = response.json()
    except ValueError:
        logger.warning("Update subscription %s returned non-JSON body", subscription_id)
        return None
    return body.get("data") if isinstance(body, dict) else None


@validate_call(config=ConfigDict(strict=True, arbitrary_types_allowed=True))
def cancel_subscription(
    subscription_id: StrictInt,
    ctx: Optional[Context] = None,
) -> Optional[Dict[Any, Any]]:
    """
    Cancel a subscription by setting its status to cancelled.

    Args:
        subscription_id (int): Subscription ID to cancel.

    Returns:
        Optional[Dict[Any, Any]]: Updated subscription data when successful, otherwise None.
    """
    return update_subscription(subscription_id, {"status": "cancelled"}, ctx=ctx)


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
    # Step 1: fetch active storages. Failure here returns empty;
    # nothing downstream to attempt.
    try:
        storages_response = requests.get(
            f"{SUBSCRIPTIONS_API_BASE_URL}/storages?status=active",
            headers=headers,
            params={},
            timeout=get_api_timeout(),
        )
    except requests.RequestException as exc:
        logger.error("Storages request failed: %s", exc)
        return []
    if storages_response.status_code != 200:
        logger.error(
            "Failed to retrieve storages: %s - %s",
            storages_response.status_code,
            storages_response.text,
        )
        return []
    try:
        sub_response = storages_response.json()
    except ValueError:
        logger.warning("Storages response was not JSON")
        return []
    if not isinstance(sub_response, dict):
        logger.warning("Storages response was not an object")
        return []
    raw_storages = sub_response.get("data")
    if not isinstance(raw_storages, list):
        logger.warning("Storages response missing 'data' list; returning empty")
        return []

    storages = []
    included = sub_response.get("included", [])
    if not isinstance(included, list):
        included = []
    for sub in raw_storages:
        if not isinstance(sub, dict):
            continue
        attributes = sub.get("attributes") or {}
        if not isinstance(attributes, dict):
            continue
        storage_group_id = attributes.get("storage_group_id")
        if storage_group_id is None:
            continue
        name = "Unknown"
        key_name = "unknown"
        for inc in included:
            if not isinstance(inc, dict):
                continue
            if str(inc.get("id")) == str(storage_group_id):
                inc_attrs = inc.get("attributes") or {}
                if isinstance(inc_attrs, dict):
                    key_name = inc_attrs.get("key_name", key_name)
                    name = inc_attrs.get("name", name)
                break
        storages.append({
            "storage_id": storage_group_id,
            "subscription_id": sub.get("id"),
            "name": name,
            "key_name": key_name,
        })

    # Step 2: best-effort SPM per storage. Each failure logged, storage
    # still included with storage_type=unknown and no SPM keys.
    result = []
    for storage in storages:
        url = f'{SUBSCRIPTIONS_API_BASE_URL}/spm?subscription={storage["subscription_id"]}'
        try:
            spm_resp = requests.get(url, headers=headers, timeout=get_api_timeout())
        except requests.RequestException as exc:
            logger.warning(
                "SPM request failed for subscription %s: %s",
                storage["subscription_id"],
                exc,
            )
            result.append({"storage_type": "unknown", **storage})
            continue
        if spm_resp.status_code != 200:
            logger.warning(
                "SPM call for subscription %s failed: %s",
                storage["subscription_id"],
                spm_resp.status_code,
            )
            result.append({"storage_type": "unknown", **storage})
            continue
        try:
            spm_body = spm_resp.json()
        except ValueError:
            logger.warning(
                "SPM response for subscription %s was not JSON",
                storage["subscription_id"],
            )
            result.append({"storage_type": "unknown", **storage})
            continue
        spm_data = spm_body.get("data", []) if isinstance(spm_body, dict) else []
        if not isinstance(spm_data, list):
            spm_data = []
        storage_spm = {
            x["attributes"]["data_key"]: x["attributes"]["data_value"]
            for x in spm_data
            if isinstance(x, dict)
            and isinstance(x.get("attributes"), dict)
            and x["attributes"].get("data_key") in SPM_REQUIRED_PARAMS
        }
        storage_type = "unknown"
        if spm_data and isinstance(spm_data[0], dict):
            first_attrs = spm_data[0].get("attributes") or {}
            if isinstance(first_attrs, dict):
                product = first_attrs.get("product") or {}
                if isinstance(product, dict):
                    product_name = product.get("name")
                    if product_name:
                        storage_type = STORAGE_TYPE_MAPPING.get(product_name, "unknown")
        result.append({"storage_type": storage_type, **storage, **storage_spm})
    return result
